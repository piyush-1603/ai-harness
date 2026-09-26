"""Comprehensive unit and integration tests for Track C tools subsystem."""

from unittest.mock import patch
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from src.common.types import ToolCall, ToolName, ToolResult
from src.tools.file_ops import edit_file, read_file, resolve_workspace_path, write_file
from src.tools.registry import ToolEngine
from src.tools.search import grep_search, list_directory
from src.tools.shell_ops import git_diff, git_status, run_bash, run_tests


class TestFileOps(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

    def tearDown(self):
        self.test_dir.cleanup()

    def test_successful_read(self):
        sample = self.ws / "sample.txt"
        lines = [f"line {i}" for i in range(1, 11)]
        sample.write_text("\n".join(lines), encoding="utf-8")

        res = read_file("sample.txt", start_line=3, end_line=5, workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.READ_FILE)
        self.assertIn("line 3", res.output)
        self.assertIn("line 5", res.output)
        self.assertNotIn("line 1", res.output)
        self.assertNotIn("line 7", res.output)

    def test_read_empty_file(self):
        empty_file = self.ws / "empty.txt"
        empty_file.write_text("", encoding="utf-8")

        res = read_file("empty.txt", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("<empty file>", res.output)

    def test_read_path_with_spaces(self):
        spaced_dir = self.ws / "folder with space"
        spaced_dir.mkdir()
        spaced_file = spaced_dir / "my document.txt"
        spaced_file.write_text("content with space", encoding="utf-8")

        res = read_file("folder with space/my document.txt", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("content with space", res.output)

    def test_missing_file(self):
        res = read_file("nonexistent.txt", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertIn("File not found", res.error)

    def test_read_directory_fails(self):
        sub = self.ws / "sub"
        sub.mkdir()
        res = read_file("sub", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertIn("is a directory", res.error)

    def test_invalid_line_range(self):
        sample = self.ws / "sample.txt"
        sample.write_text("a\nb\nc\n", encoding="utf-8")

        res_zero = read_file("sample.txt", start_line=0, workspace_dir=str(self.ws))
        self.assertFalse(res_zero.success)
        self.assertIn("Invalid start_line", res_zero.error)

        res_inverted = read_file("sample.txt", start_line=5, end_line=2, workspace_dir=str(self.ws))
        self.assertFalse(res_inverted.success)
        self.assertIn("Invalid line range", res_inverted.error)

    def test_successful_write(self):
        res = write_file("new_file.txt", "hello world", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.WRITE_FILE)
        written = (self.ws / "new_file.txt").read_text()
        self.assertEqual(written, "hello world")

    def test_write_to_existing_directory_fails(self):
        sub = self.ws / "existing_dir"
        sub.mkdir()
        res = write_file("existing_dir", "content", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertIn("existing directory", res.error)

    def test_nested_directory_write(self):
        res = write_file("deep/nested/path/file.py", "x = 42\n", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        nested_file = self.ws / "deep" / "nested" / "path" / "file.py"
        self.assertTrue(nested_file.exists())
        self.assertEqual(nested_file.read_text(), "x = 42\n")

    def test_successful_edit(self):
        target = self.ws / "script.py"
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

        res = edit_file(
            path="script.py",
            search_block="    return a - b",
            replace_block="    return a + b",
            workspace_dir=str(self.ws),
        )
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.EDIT_FILE)
        self.assertEqual(target.read_text(), "def add(a, b):\n    return a + b\n")

    def test_zero_match_edit(self):
        target = self.ws / "script.py"
        target.write_text("x = 1\n", encoding="utf-8")

        res = edit_file(
            path="script.py",
            search_block="y = 2",
            replace_block="y = 3",
            workspace_dir=str(self.ws),
        )
        self.assertFalse(res.success)
        self.assertIn("EDIT_FAILED", res.error)
        self.assertIn("search_block not found", res.error)

    def test_multiple_match_edit(self):
        target = self.ws / "script.py"
        target.write_text("val = 1\nval = 1\n", encoding="utf-8")

        res = edit_file(
            path="script.py",
            search_block="val = 1",
            replace_block="val = 2",
            workspace_dir=str(self.ws),
        )
        self.assertFalse(res.success)
        self.assertIn("EDIT_FAILED", res.error)
        self.assertIn("matched 2 locations", res.error)

    def test_path_traversal_relative_prevention(self):
        res = read_file("../../escaped.txt", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertIn("Path traversal detected", res.error)

    def test_path_traversal_absolute_outside_prevention(self):
        res = read_file("/etc/passwd", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertIn("Path traversal detected", res.error)

    def test_absolute_path_inside_workspace_allowed(self):
        target = self.ws / "inside.txt"
        target.write_text("inside content", encoding="utf-8")
        res = read_file(str(target), workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("inside content", res.output)

    def test_symlink_escape_prevention(self):
        with tempfile.TemporaryDirectory() as ext_dir:
            secret_file = Path(ext_dir) / "secret.txt"
            secret_file.write_text("sensitive_data", encoding="utf-8")

            leak_link = self.ws / "leak_link"
            leak_link.symlink_to(secret_file)

            res = read_file("leak_link", workspace_dir=str(self.ws))
            self.assertFalse(res.success)
            self.assertIn("Path traversal detected", res.error)

    def test_read_file_beyond_eof(self):
        sample = self.ws / "short.txt"
        sample.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

        # Beyond EOF request
        res = read_file("short.txt", start_line=20, end_line=30, workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.READ_FILE)
        self.assertEqual(res.output, "<start_line 20 is beyond end of file (3 total lines)>")

        # Normal valid request remains unchanged
        res_normal = read_file("short.txt", start_line=1, end_line=2, workspace_dir=str(self.ws))
        self.assertTrue(res_normal.success)
        self.assertIn("line 1", res_normal.output)
        self.assertIn("line 2", res_normal.output)
        self.assertNotIn("line 3", res_normal.output)

    def test_atomic_edit_file_write(self):
        target = self.ws / "atomic.txt"
        target.write_text("alpha beta gamma\n", encoding="utf-8")

        res = edit_file(
            path="atomic.txt",
            search_block="beta",
            replace_block="omega",
            workspace_dir=str(self.ws),
        )
        self.assertTrue(res.success)
        self.assertEqual(target.read_text(), "alpha omega gamma\n")

        # Ensure no temporary artifacts (.tmp_) are left behind in target parent
        temp_artifacts = list(self.ws.glob(".*.tmp_*"))
        self.assertEqual(len(temp_artifacts), 0)


class TestShellOps(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

    def tearDown(self):
        self.test_dir.cleanup()

    def test_successful_bash_command(self):
        res = run_bash('echo "hello from test"', workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.tool_name, ToolName.RUN_BASH)
        self.assertIn("hello from test", res.output)
        self.assertGreaterEqual(res.duration_sec, 0.0)

    def test_failed_bash_command(self):
        res = run_bash('python3 -c "import sys; sys.exit(42)"', workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 42)

    def test_timeout(self):
        res = run_bash('python3 -c "import time; time.sleep(5)"', timeout=1, workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 124)
        self.assertIn("timed out", res.error)

    def test_child_process_group_termination_on_timeout(self):
        pid_file = self.ws / "child.pid"
        cmd = f'sleep 60 & echo $! > {pid_file.name} && wait'
        res = run_bash(cmd, timeout=1, workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 124)

        self.assertTrue(pid_file.exists(), "child.pid file should be created by background process")
        child_pid = int(pid_file.read_text().strip())
        self.assertGreater(child_pid, 0)

        # Confirm child process is terminated (not running)
        terminated = False
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.kill(child_pid, 0)
                time.sleep(0.05)
            except OSError:
                terminated = True
                break

        self.assertTrue(terminated, f"Child process {child_pid} was not terminated on timeout")
        with self.assertRaises(OSError):
            os.kill(child_pid, 0)

    def test_environment_inheritance(self):
        res = run_bash('echo "CUSTOM=$MY_TEST_VAR"', workspace_dir=str(self.ws), env={"MY_TEST_VAR": "passed_val"})
        self.assertTrue(res.success)
        self.assertIn("CUSTOM=passed_val", res.output)

    def test_stdout_and_stderr_captured(self):
        res = run_bash('python3 -c "import sys; sys.stdout.write(\'out_part\\n\'); sys.stderr.write(\'err_part\\n\')"', workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("out_part", res.output)
        self.assertIn("err_part", res.output)

    def test_run_tests_helper(self):
        test_file = self.ws / "test_simple.py"
        test_file.write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")

        res = run_tests('python3 -c "assert 1 == 1"', workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.RUN_TESTS)

    def test_silent_shell_failure(self):
        res = run_bash("exit 7", workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 7)
        self.assertEqual(res.error, "Command exited with non-zero exit code 7")


class TestSearchOps(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

        (self.ws / "src").mkdir()
        (self.ws / "src" / "alpha.py").write_text("def calculate_tax():\n    return 0.1\n")
        (self.ws / "src" / "beta.py").write_text("def calculate_discount():\n    return 0.2\n")

        (self.ws / ".venv").mkdir()
        (self.ws / ".venv" / "calculate_ignored.py").write_text("def calculate_tax(): pass\n")
        (self.ws / ".git").mkdir()
        (self.ws / ".git" / "git_search.py").write_text("def calculate_tax(): pass\n")
        (self.ws / "__pycache__").mkdir()
        (self.ws / "__pycache__" / "cached.py").write_text("def calculate_tax(): pass\n")

        with open(self.ws / "src" / "binary.bin", "wb") as f:
            f.write(b"def calculate_tax()\x00binarystuff")

    def tearDown(self):
        self.test_dir.cleanup()

    def test_grep_match(self):
        res = grep_search(query="calculate_discount", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GREP_SEARCH)
        self.assertIn("beta.py:1: def calculate_discount():", res.output)
        self.assertNotIn(".venv", res.output)
        self.assertNotIn(".git", res.output)
        self.assertNotIn("__pycache__", res.output)
        self.assertNotIn("binary.bin", res.output)

    def test_grep_no_match(self):
        res = grep_search(query="non_existent_function_123", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("No matches found", res.output)

    def test_list_directory(self):
        res = list_directory(path=".", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.LIST_DIRECTORY)
        self.assertIn("[DIR]  src/", res.output)
        self.assertNotIn(".venv", res.output)
        self.assertNotIn(".git", res.output)

    def test_ds_store_noise_filtering(self):
        ds_store = self.ws / ".DS_Store"
        ds_store.write_text("calculate_tax inside ds_store")
        nested_ds = self.ws / "src" / ".DS_Store"
        nested_ds.write_text("calculate_tax inside nested ds_store")

        # list_directory must not list .DS_Store
        list_res = list_directory(path=".", workspace_dir=str(self.ws))
        self.assertTrue(list_res.success)
        self.assertNotIn(".DS_Store", list_res.output)

        # grep_search must not match .DS_Store
        grep_res = grep_search(query="inside ds_store", workspace_dir=str(self.ws))
        self.assertTrue(grep_res.success)
        self.assertIn("No matches found", grep_res.output)

    def test_grep_streaming_and_early_termination(self):
        for i in range(5):
            (self.ws / f"match_{i}.txt").write_text(f"target_key = {i}\n")

        res = grep_search(query="target_key", max_matches=2, workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        lines = [line for line in res.output.splitlines() if "target_key" in line]
        self.assertEqual(len(lines), 2)
        # Matches come only from the first two deterministically sorted files
        self.assertIn("match_0.txt", res.output)
        self.assertIn("match_1.txt", res.output)
        # Early termination ensures remaining files are not included
        self.assertNotIn("match_2.txt", res.output)
        self.assertNotIn("match_3.txt", res.output)
        self.assertNotIn("match_4.txt", res.output)

    def test_grep_recursive_search(self):
        nested = self.ws / "level1" / "level2"
        nested.mkdir(parents=True)
        (nested / "deep.py").write_text("def nested_function():\n    return 42\n")

        res = grep_search(query="nested_function", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertIn("level1/level2/deep.py:1: def nested_function():", res.output)

    def test_grep_deterministic_directory_ordering(self):
        (self.ws / "z_dir").mkdir()
        (self.ws / "z_dir" / "file.py").write_text("common_token = 1\n")

        (self.ws / "a_dir").mkdir()
        (self.ws / "a_dir" / "file.py").write_text("common_token = 2\n")

        (self.ws / "m_dir").mkdir()
        (self.ws / "m_dir" / "file.py").write_text("common_token = 3\n")

        res = grep_search(query="common_token", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        lines = [line for line in res.output.splitlines() if "common_token" in line]
        # Should deterministically be a_dir first, then m_dir, then z_dir
        self.assertTrue(lines[0].startswith("a_dir/"))
        self.assertTrue(lines[1].startswith("m_dir/"))
        self.assertTrue(lines[2].startswith("z_dir/"))


class TestGitTools(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

        subprocess.run(["git", "init"], cwd=str(self.ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(self.ws), check=True)

        initial = self.ws / "initial.txt"
        initial.write_text("version 1\n")
        subprocess.run(["git", "add", "initial.txt"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.ws), check=True)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_git_diff_and_status(self):
        (self.ws / "initial.txt").write_text("version 2\n")

        status_res = git_status(workspace_dir=str(self.ws))
        self.assertTrue(status_res.success)
        self.assertEqual(status_res.tool_name, ToolName.GIT_STATUS)
        self.assertIn("initial.txt", status_res.output)

        diff_res = git_diff(workspace_dir=str(self.ws))
        self.assertTrue(diff_res.success)
        self.assertEqual(diff_res.tool_name, ToolName.GIT_DIFF)
        self.assertIn("-version 1", diff_res.output)
        self.assertIn("+version 2", diff_res.output)

    def test_git_diff_path_with_single_quote(self):
        quote_dir = self.ws / "test's"
        quote_dir.mkdir()
        quote_file = quote_dir / "file.py"
        quote_file.write_text("orig = 1\n")
        subprocess.run(["git", "add", "."], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "add quote file"], cwd=str(self.ws), check=True)

        quote_file.write_text("orig = 2\n")
        res = git_diff(path="test's/file.py", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_DIFF)
        self.assertIn("-orig = 1", res.output)
        self.assertIn("+orig = 2", res.output)

    def test_git_diff_path_with_spaces(self):
        space_dir = self.ws / "path with spaces"
        space_dir.mkdir()
        space_file = space_dir / "file.txt"
        space_file.write_text("space 1\n")
        subprocess.run(["git", "add", "."], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "add spaces file"], cwd=str(self.ws), check=True)

        space_file.write_text("space 2\n")
        res = git_diff(path="path with spaces/file.txt", workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_DIFF)
        self.assertIn("-space 1", res.output)
        self.assertIn("+space 2", res.output)

    def test_git_diff_clean_tree(self):
        res = git_diff(workspace_dir=str(self.ws))
        self.assertTrue(res.success)
        self.assertEqual(res.output, "")

    def test_git_tools_non_git_workspace(self):
        with tempfile.TemporaryDirectory() as non_git:
            diff_res = git_diff(workspace_dir=non_git)
            self.assertFalse(diff_res.success)
            self.assertNotEqual(diff_res.exit_code, 0)
            self.assertIn("Not a git repository", diff_res.output)

            status_res = git_status(workspace_dir=non_git)
            self.assertFalse(status_res.success)
            self.assertNotEqual(status_res.exit_code, 0)
            self.assertIn("not a git repository", status_res.output)


class TestToolEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()
        self.engine = ToolEngine(workspace_dir=str(self.ws))

        subprocess.run(["git", "init"], cwd=str(self.ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "EngineTester"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "config", "user.email", "engine@test.com"], cwd=str(self.ws), check=True)
        (self.ws / "init.txt").write_text("initial\n")
        subprocess.run(["git", "add", "."], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(self.ws), check=True)

    def tearDown(self):
        self.test_dir.cleanup()

    # -------------------------------------------------------------------------
    # Phase 2 Dispatch & Hardening Tests
    # -------------------------------------------------------------------------

    def test_all_canonical_tool_dispatches(self):
        """Verifies every canonical ToolName dispatches and returns expected ToolResult shape."""
        # 1. WRITE_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.WRITE_FILE,
            tool_args={"path": "mod.py", "content": "x = 10\n"},
            call_id="c1",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.WRITE_FILE)
        self.assertEqual(res.exit_code, 0)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 2. READ_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": "mod.py", "start_line": 1, "end_line": 1},
            call_id="c2",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.READ_FILE)
        self.assertIn("x = 10", res.output)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 3. EDIT_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.EDIT_FILE,
            tool_args={"path": "mod.py", "search_block": "x = 10", "replace_block": "x = 20"},
            call_id="c3",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.EDIT_FILE)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 4. RUN_BASH
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "python3 mod.py"},
            call_id="c4",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.RUN_BASH)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 5. RUN_TESTS
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_TESTS,
            tool_args={"test_command": "python3 -c 'assert 1 == 1'"},
            call_id="c5",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.RUN_TESTS)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 6. GIT_DIFF
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GIT_DIFF,
            tool_args={},
            call_id="c6",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_DIFF)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 7. GIT_STATUS
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GIT_STATUS,
            tool_args={},
            call_id="c7",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_STATUS)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 8. GREP_SEARCH
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GREP_SEARCH,
            tool_args={"query": "x = 20"},
            call_id="c8",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GREP_SEARCH)
        self.assertIn("mod.py", res.output)
        self.assertGreaterEqual(res.duration_sec, 0.0)

        # 9. LIST_DIRECTORY
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.LIST_DIRECTORY,
            tool_args={"path": "."},
            call_id="c9",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.LIST_DIRECTORY)
        self.assertIn("mod.py", res.output)
        self.assertGreaterEqual(res.duration_sec, 0.0)

    def test_unknown_tool_name(self):
        """Verifies unknown tool name returns ToolResult(success=False) with actionable error."""
        call = ToolCall(
            tool_name="NON_EXISTENT_TOOL",  # type: ignore
            tool_args={},
            call_id="call-unk",
        )
        res = self.engine.execute(call)
        self.assertIsInstance(res, ToolResult)
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("Unknown or unsupported tool", res.error)
        self.assertIn("read_file", res.error)

    def test_missing_required_arguments(self):
        """Verifies missing required arguments for tools return clear error messages."""
        # READ_FILE missing path
        res = self.engine.execute(ToolCall(tool_name=ToolName.READ_FILE, tool_args={}, call_id="m1"))
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'path'", res.error)

        # EDIT_FILE missing search_block
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.EDIT_FILE,
            tool_args={"path": "a.txt"},
            call_id="m2",
        ))
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'search_block'", res.error)

        # WRITE_FILE missing content
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.WRITE_FILE,
            tool_args={"path": "a.txt"},
            call_id="m3",
        ))
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'content'", res.error)

        # RUN_BASH missing command
        res = self.engine.execute(ToolCall(tool_name=ToolName.RUN_BASH, tool_args={}, call_id="m4"))
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'command'", res.error)

        # GREP_SEARCH missing query
        res = self.engine.execute(ToolCall(tool_name=ToolName.GREP_SEARCH, tool_args={}, call_id="m5"))
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'query'", res.error)

    def test_wrong_argument_types(self):
        """Verifies non-convertible argument types are caught cleanly with useful messages."""
        # start_line not an integer
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": "init.txt", "start_line": "not_an_int"},
            call_id="w1",
        ))
        self.assertFalse(res.success)
        self.assertIn("Invalid argument type: 'start_line'", res.error)

        # path is integer instead of string
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": 12345},
            call_id="w2",
        ))
        self.assertFalse(res.success)
        self.assertIn("must be a string", res.error)

        # timeout not an integer
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "echo ok", "timeout": "not_a_num"},
            call_id="w3",
        ))
        self.assertFalse(res.success)
        self.assertIn("Invalid argument type: 'timeout'", res.error)

        # timeout negative integer
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "echo ok", "timeout": -5},
            call_id="w4",
        ))
        self.assertFalse(res.success)
        self.assertIn("Invalid timeout", res.error)

        # empty command
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "   "},
            call_id="w5",
        ))
        self.assertFalse(res.success)
        self.assertIn("cannot be empty", res.error)

    def test_malformed_tool_call_invocation(self):
        """Verifies invocation with non-ToolCall or bad tool_args structure."""
        # Non-ToolCall object
        res = self.engine.execute("not_a_tool_call")  # type: ignore
        self.assertIsInstance(res, ToolResult)
        self.assertFalse(res.success)
        self.assertIn("expected a ToolCall instance", res.error)

        # tool_args is a string instead of dict
        call_bad_args = ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args="not_a_dict",  # type: ignore
            call_id="bad_args",
        )
        res = self.engine.execute(call_bad_args)
        self.assertFalse(res.success)
        self.assertIn("expected a dictionary", res.error)

        # tool_args is None (should be handled safely as empty dict)
        call_none_args = ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args=None,  # type: ignore
            call_id="none_args",
        )
        res = self.engine.execute(call_none_args)
        self.assertFalse(res.success)
        self.assertIn("Missing required argument: 'path'", res.error)

    def test_handler_exception_isolation(self):
        """Verifies that an unhandled exception inside a handler never crashes execute()."""
        with patch.object(self.engine, "read_file", side_effect=RuntimeError("Simulated filesystem driver crash")):
            call = ToolCall(
                tool_name=ToolName.READ_FILE,
                tool_args={"path": "init.txt"},
                call_id="c_err",
            )
            res = self.engine.execute(call)
            self.assertIsInstance(res, ToolResult)
            self.assertFalse(res.success)
            self.assertEqual(res.exit_code, 1)
            self.assertIn("Tool execution failed: RuntimeError", res.error)

    def test_tool_result_contract_shape(self):
        """Verifies that ToolResult fields conform strictly to PRD contract."""
        call = ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": "init.txt"},
            call_id="shape_check",
        )
        res = self.engine.execute(call)
        self.assertIsInstance(res.tool_name, ToolName)
        self.assertIsInstance(res.success, bool)
        self.assertIsInstance(res.output, str)
        self.assertIsInstance(res.exit_code, int)
        self.assertIsInstance(res.duration_sec, float)
        self.assertGreaterEqual(res.duration_sec, 0.0)

    def test_timeout_representation_via_execute(self):
        """Verifies that timeouts executed via execute() preserve exit_code 124 and timing."""
        call = ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "python3 -c 'import time; time.sleep(5)'", "timeout": 1},
            call_id="timeout_call",
        )
        res = self.engine.execute(call)
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 124)
        self.assertIn("timed out", res.error)
        self.assertGreaterEqual(res.duration_sec, 0.9)

    def test_run_tests_empty_command_fallback(self):
        """Verifies registry execution with empty test_command falls back to pytest."""
        call = ToolCall(
            tool_name=ToolName.RUN_TESTS,
            tool_args={"test_command": ""},
            call_id="c_empty_test",
        )
        res = self.engine.execute(call)
        self.assertIsInstance(res, ToolResult)
        self.assertEqual(res.tool_name, ToolName.RUN_TESTS)
        # Must not be rejected with an argument-validation error
        self.assertNotIn("cannot be empty", res.error or "")
        self.assertNotIn("Invalid argument", res.error or "")

    def test_run_tests_omitted_command_fallback(self):
        """Verifies registry execution with omitted test_command falls back to pytest."""
        call = ToolCall(
            tool_name=ToolName.RUN_TESTS,
            tool_args={},
            call_id="c_omitted_test",
        )
        res = self.engine.execute(call)
        self.assertIsInstance(res, ToolResult)
        self.assertEqual(res.tool_name, ToolName.RUN_TESTS)
        # Must not be rejected with an argument-validation error
        self.assertNotIn("Missing required argument", res.error or "")


if __name__ == "__main__":
    unittest.main()
