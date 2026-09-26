"""Comprehensive unit and integration tests for Track C verification subsystem."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from src.common.types import (
    FailureClassification,
    VerificationReport,
    VerificationStatus,
)
from src.verification.verifier import VerificationEngine


class TestVerificationEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.test_dir.name).resolve()
        self.verifier = VerificationEngine(default_timeout=30)

        # Initialize clean git repository
        subprocess.run(["git", "init"], cwd=str(self.ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "VerifierTest"], cwd=str(self.ws), check=True)
        subprocess.run(["git", "config", "user.email", "verify@test.com"], cwd=str(self.ws), check=True)

        # Initial commit
        self.code_file = self.ws / "calculator.py"
        self.code_file.write_text(
            "def calculate(a, b):\n    return a - b\n",
            encoding="utf-8",
        )
        self.test_file = self.ws / "test_calc.py"
        self.test_file.write_text(
            "from calculator import calculate\n\ndef test_calc():\n    assert calculate(2, 3) == 5\n\nif __name__ == '__main__':\n    test_calc()\n",
            encoding="utf-8",
        )

        subprocess.run(["git", "add", "."], cwd=str(self.ws), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.ws), check=True)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_no_changes(self):
        # Without touching any file, verify must return NO_CHANGES
        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command="python3 test_calc.py",
        )
        self.assertIsInstance(report, VerificationReport)
        self.assertEqual(report.status, VerificationStatus.NO_CHANGES)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertEqual(len(report.files_modified), 0)
        self.assertEqual(report.git_diff, "")

    def test_valid_diff_passing_tests_valid_syntax(self):
        # Fix the bug: calculate should return a + b
        self.code_file.write_text(
            "def calculate(a, b):\n    return a + b\n",
            encoding="utf-8",
        )

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command="python3 test_calc.py",
        )

        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertTrue(report.is_verified)
        self.assertTrue(report.tests_passed)
        self.assertTrue(report.syntax_valid)
        self.assertIn("calculator.py", report.files_modified)
        self.assertIn("+    return a + b", report.git_diff)
        self.assertIsNone(report.failure_classification)

    def test_test_failure_classification(self):
        # Modify file, but bug remains (calculate returns a * b, fails 2 + 3 == 5)
        self.code_file.write_text(
            "def calculate(a, b):\n    return a * b\n",
            encoding="utf-8",
        )

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command="python3 test_calc.py",
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertTrue(report.syntax_valid)
        self.assertIn("calculator.py", report.files_modified)
        self.assertEqual(report.failure_classification, FailureClassification.ASSERTION_FAILED)

    def test_syntax_failure_detection(self):
        # Introduce a broken syntax error in Python
        self.code_file.write_text(
            "def calculate(a, b\n    return a + b\n",  # Missing closing paren
            encoding="utf-8",
        )

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command="python3 test_calc.py",
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.syntax_valid)
        self.assertEqual(report.failure_classification, FailureClassification.SYNTAX_ERROR)
        self.assertIn("Syntax error", report.summary)

    def test_syntax_failure_in_newly_created_untracked_directory(self):
        # Create an entirely new directory with a broken Python file inside
        new_dir = self.ws / "nested_pkg"
        new_dir.mkdir()
        broken_file = new_dir / "broken.py"
        broken_file.write_text("class Foo(\n    pass\n", encoding="utf-8")  # Syntax error

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command=None,
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.syntax_valid)
        self.assertEqual(report.failure_classification, FailureClassification.SYNTAX_ERROR)
        self.assertIn("broken.py", report.summary)

    def test_untracked_file_with_spaces_in_path(self):
        # New file inside a folder with spaces
        spaced_dir = self.ws / "new folder with spaces"
        spaced_dir.mkdir()
        valid_file = spaced_dir / "valid helper.py"
        valid_file.write_text("def helper():\n    return 42\n", encoding="utf-8")

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command=None,
        )

        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertTrue(report.is_verified)
        self.assertTrue(report.syntax_valid)
        self.assertTrue(any("valid helper.py" in f for f in report.files_modified))
        # Ensure git diff contains the new untracked file content
        self.assertIn("def helper():", report.git_diff)

    def test_git_diff_output_populated_on_modified_file(self):
        self.code_file.write_text(
            "def calculate(a, b):\n    # added comment\n    return a + b\n",
            encoding="utf-8",
        )

        report = self.verifier.verify(workspace_dir=str(self.ws))
        self.assertIn("# added comment", report.git_diff)
        self.assertIn("+    return a + b", report.git_diff)

    def test_non_git_workspace_verification_failure(self):
        with tempfile.TemporaryDirectory() as non_git_dir:
            file_path = Path(non_git_dir) / "some_file.py"
            file_path.write_text("x = 1\n", encoding="utf-8")

            report = self.verifier.verify(
                workspace_dir=non_git_dir,
                test_command=None,
            )

            self.assertEqual(report.status, VerificationStatus.FAILED)
            self.assertFalse(report.is_verified)
            self.assertFalse(report.tests_passed)
            self.assertEqual(report.failure_classification, FailureClassification.UNKNOWN_ERROR)
            self.assertEqual(report.files_modified, [])
            self.assertIn("Git status", report.summary)

    def test_untracked_file_with_single_quote(self):
        quoted_file = self.ws / "untracked_test's.py"
        quoted_file.write_text("def helper():\n    return 'quoted'\n", encoding="utf-8")

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command=None,
        )

        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertTrue(report.is_verified)
        self.assertTrue(report.syntax_valid)
        self.assertIn("untracked_test's.py", report.files_modified)
        self.assertIn("def helper():", report.git_diff)

    def test_module_not_found_classification_precedence(self):
        fail_test = self.ws / "test_import_fail.py"
        fail_test.write_text(
            "import sys\n"
            "sys.stderr.write('FAILED tests/test_foo.py - ModuleNotFoundError: No module named foo\\n')\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )

        self.code_file.write_text("def calculate(a, b):\n    return a + b + 1\n", encoding="utf-8")

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command="python3 test_import_fail.py",
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertEqual(report.failure_classification, FailureClassification.IMPORT_MISSING)

    def test_deterministic_syntax_traversal_and_noise_pruning(self):
        subrepo = self.ws / "subrepo"
        subrepo.mkdir()
        subprocess.run(["git", "init"], cwd=str(subrepo), check=True, capture_output=True)

        pycache_dir = subrepo / "__pycache__"
        pycache_dir.mkdir(parents=True)
        (pycache_dir / "bad.py").write_text("def broken(", encoding="utf-8")

        venv_dir = subrepo / ".venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / "bad_venv.py").write_text("def broken_venv(", encoding="utf-8")

        (subrepo / "z_mod.py").write_text("x = 1\n", encoding="utf-8")
        (subrepo / "a_mod.py").write_text("y = 2\n", encoding="utf-8")

        report = self.verifier.verify(workspace_dir=str(self.ws))

        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertTrue(report.is_verified)
        self.assertTrue(report.syntax_valid)

        # Introduce two broken files to verify deterministic traversal ordering
        (subrepo / "z_broken.py").write_text("class ZBroken(", encoding="utf-8")
        (subrepo / "a_broken.py").write_text("class ABroken(", encoding="utf-8")

        report2 = self.verifier.verify(workspace_dir=str(self.ws))
        self.assertEqual(report2.status, VerificationStatus.FAILED)
        self.assertFalse(report2.syntax_valid)
        self.assertEqual(report2.failure_classification, FailureClassification.SYNTAX_ERROR)
        self.assertIn("a_broken.py", report2.summary)

    def test_deterministic_and_deduplicated_files_modified(self):
        (self.ws / "z_file.py").write_text("z = 1\n", encoding="utf-8")
        (self.ws / "a_file.py").write_text("a = 1\n", encoding="utf-8")
        (self.ws / "m_file.py").write_text("m = 1\n", encoding="utf-8")
        self.code_file.write_text("def calculate(a, b):\n    return a + b\n", encoding="utf-8")

        report = self.verifier.verify(workspace_dir=str(self.ws))

        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertEqual(len(report.files_modified), len(set(report.files_modified)))
        self.assertEqual(report.files_modified, sorted(report.files_modified))
        for f in ["a_file.py", "calculator.py", "m_file.py", "z_file.py"]:
            self.assertIn(f, report.files_modified)

    def test_verification_engine_timeout_classification(self):
        # GAP-1: VerificationEngine timeout classification
        self.code_file.write_text("def calculate(a, b):\n    return a + b\n", encoding="utf-8")

        fast_verifier = VerificationEngine(default_timeout=1)
        report = fast_verifier.verify(
            workspace_dir=str(self.ws),
            test_command='python3 -c "import time; time.sleep(3)"',
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertEqual(report.failure_classification, FailureClassification.TIMEOUT)
        self.assertIn("failed with exit code 124", report.summary)

    def test_verification_engine_command_execution_failure(self):
        # GAP-2: VerificationEngine command execution failure
        self.code_file.write_text("def calculate(a, b):\n    return a + b\n", encoding="utf-8")

        nonexistent_cmd = "nonexistent_test_runner_command_xyz123"
        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command=nonexistent_cmd,
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertEqual(report.failure_classification, FailureClassification.TEST_EXECUTION_ERROR)
        self.assertIn(nonexistent_cmd, report.summary)
        self.assertIn("failed with exit code", report.summary)

    def test_verification_engine_silent_non_zero_failure(self):
        # GAP-3: VerificationEngine silent non-zero failure
        self.code_file.write_text("def calculate(a, b):\n    return a + b\n", encoding="utf-8")

        report = self.verifier.verify(
            workspace_dir=str(self.ws),
            test_command='python3 -c "import sys; sys.exit(42)"',
        )

        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertFalse(report.is_verified)
        self.assertFalse(report.tests_passed)
        self.assertEqual(report.failure_classification, FailureClassification.TEST_EXECUTION_ERROR)
        self.assertEqual(report.test_output, "")
        self.assertIn("exit code 42", report.summary)


if __name__ == "__main__":
    unittest.main()
