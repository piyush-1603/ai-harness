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


if __name__ == "__main__":
    unittest.main()
