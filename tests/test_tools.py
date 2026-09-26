"""Comprehensive unit and integration tests for Track C tools subsystem."""

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
        # Create an external directory and link to it inside workspace
        with tempfile.TemporaryDirectory() as ext_dir:
            secret_file = Path(ext_dir) / "secret.txt"
            secret_file.write_text("sensitive_data", encoding="utf-8")

            leak_link = self.ws / "leak_link"
            leak_link.symlink_to(secret_file)

            res = read_file("leak_link", workspace_dir=str(self.ws))
            self.assertFalse(res.success)
            self.assertIn("Path traversal detected", res.error)


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
        # Spawns a background sleep, writes its PID, and waits
        pid_file = self.ws / "child.pid"
        cmd = f'sleep 60 & echo $! > {pid_file.name} && wait'
        res = run_bash(cmd, timeout=1, workspace_dir=str(self.ws))
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 124)

        time.sleep(0.2)
        if pid_file.exists():
            child_pid = int(pid_file.read_text().strip())
            # Verify child process was killed and is no longer running
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


class TestSearchOps(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

        # Create files for searching
        (self.ws / "src").mkdir()
        (self.ws / "src" / "alpha.py").write_text("def calculate_tax():\n    return 0.1\n")
        (self.ws / "src" / "beta.py").write_text("def calculate_discount():\n    return 0.2\n")

        # Noise dir that should be ignored
        (self.ws / ".venv").mkdir()
        (self.ws / ".venv" / "calculate_ignored.py").write_text("def calculate_tax(): pass\n")
        (self.ws / ".git").mkdir()
        (self.ws / ".git" / "git_search.py").write_text("def calculate_tax(): pass\n")
        (self.ws / "__pycache__").mkdir()
        (self.ws / "__pycache__" / "cached.py").write_text("def calculate_tax(): pass\n")

        # Binary file that should be ignored
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
        # Noise directories should be excluded
        self.assertNotIn(".venv", res.output)
        self.assertNotIn(".git", res.output)


class TestGitTools(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()

        # Initialize real git repo
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
        # Modify file
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


class TestToolEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()
        self.engine = ToolEngine(workspace_dir=str(self.ws))

        # Setup git repo in workspace for git tools
        subprocess.run(["git", "init"], cwd=str(self.ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "EngineTester"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "config", "user.email", "engine@test.com"], cwd=str(self.ws), check=True)
        (self.ws / "init.txt").write_text("initial\n")
        subprocess.run(["git", "add", "."], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(self.ws), check=True)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_all_canonical_tool_dispatches(self):
        # 1. WRITE_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.WRITE_FILE,
            tool_args={"path": "mod.py", "content": "x = 10\n"},
            call_id="c1",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.WRITE_FILE)

        # 2. READ_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": "mod.py", "start_line": 1, "end_line": 1},
            call_id="c2",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.READ_FILE)
        self.assertIn("x = 10", res.output)

        # 3. EDIT_FILE
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.EDIT_FILE,
            tool_args={"path": "mod.py", "search_block": "x = 10", "replace_block": "x = 20"},
            call_id="c3",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.EDIT_FILE)

        # 4. RUN_BASH
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_BASH,
            tool_args={"command": "python3 mod.py"},
            call_id="c4",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.RUN_BASH)

        # 5. RUN_TESTS
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.RUN_TESTS,
            tool_args={"test_command": "python3 -c 'assert 1 == 1'"},
            call_id="c5",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.RUN_TESTS)

        # 6. GIT_DIFF
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GIT_DIFF,
            tool_args={},
            call_id="c6",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_DIFF)

        # 7. GIT_STATUS
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GIT_STATUS,
            tool_args={},
            call_id="c7",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GIT_STATUS)

        # 8. GREP_SEARCH
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.GREP_SEARCH,
            tool_args={"query": "x = 20"},
            call_id="c8",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.GREP_SEARCH)
        self.assertIn("mod.py", res.output)

        # 9. LIST_DIRECTORY
        res = self.engine.execute(ToolCall(
            tool_name=ToolName.LIST_DIRECTORY,
            tool_args={"path": "."},
            call_id="c9",
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.tool_name, ToolName.LIST_DIRECTORY)
        self.assertIn("mod.py", res.output)

    def test_missing_argument_handling(self):
        call = ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={},  # Missing 'path'
            call_id="call-err",
        )
        res = self.engine.execute(call)
        self.assertFalse(res.success)
        self.assertIn("Missing required argument 'path'", res.error)

    def test_unhandled_crash_protection(self):
        call = ToolCall(
            tool_name=ToolName.READ_FILE,
            tool_args={"path": "something.txt", "start_line": "not_an_int"},
            call_id="call-type-err",
        )
        res = self.engine.execute(call)
        self.assertIsInstance(res, ToolResult)
        self.assertFalse(res.success)


if __name__ == "__main__":
    unittest.main()
