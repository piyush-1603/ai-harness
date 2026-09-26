"""Independent verification engine for Track C.

Validates candidate fixes against real evidence:
1. Non-empty git diff / status (including untracked new files)
2. AST syntax validity on all modified or newly created Python files
3. Test suite execution and exit codes
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shlex
from typing import List, Optional

from src.common.types import (
    FailureClassification,
    VerificationReport,
    VerificationStatus,
)
from src.tools.file_ops import resolve_workspace_path, truncate_output
from src.tools.search import NOISE_DIRS
from src.tools.shell_ops import run_bash


class VerificationEngine:
    """Independently verifies candidate code changes without trusting model assertions."""

    def __init__(self, default_timeout: int = 60):
        self.default_timeout = default_timeout

    def verify(
        self,
        workspace_dir: str,
        test_command: Optional[str] = None,
    ) -> VerificationReport:
        """Runs the 3-point independent verification protocol.

        1. Inspects git working tree for non-empty diff / new files.
        2. Validates AST syntax on all touched/untracked Python files.
        3. Executes test_command (if supplied) and checks for exit code 0.
        """
        try:
            ws_path = resolve_workspace_path(".", workspace_dir)
        except PermissionError as e:
            return VerificationReport(
                status=VerificationStatus.FAILED,
                is_verified=False,
                tests_passed=False,
                test_command=test_command or "",
                test_output="",
                files_modified=[],
                git_diff="",
                failure_classification=FailureClassification.UNKNOWN_ERROR,
                syntax_valid=False,
                summary=f"Verification failed: {str(e)}",
            )

        ws_str = str(ws_path)

        # ---------------------------------------------------------------------
        # 1. Inspect Git Diff & Status
        # ---------------------------------------------------------------------
        # Use -uall so all untracked files inside new directories are listed individually
        status_res = run_bash("git status --porcelain -uall", timeout=30, workspace_dir=ws_str)
        if not status_res.success:
            err_msg = status_res.error or status_res.output.strip() or "Git status command failed"
            return VerificationReport(
                status=VerificationStatus.FAILED,
                is_verified=False,
                tests_passed=False,
                test_command=test_command or "",
                test_output="",
                files_modified=[],
                git_diff="",
                failure_classification=FailureClassification.UNKNOWN_ERROR,
                syntax_valid=False,
                summary=f"Verification failed: Git status command failed ({err_msg}).",
            )

        diff_res = run_bash("git diff HEAD", timeout=30, workspace_dir=ws_str)

        # Fallback if repository HEAD does not exist yet
        if not diff_res.success and "ambiguous argument 'HEAD'" in diff_res.output:
            diff_res = run_bash("git diff", timeout=30, workspace_dir=ws_str)

        raw_status = status_res.output.strip()
        raw_diff = diff_res.output.strip()

        # Identify all modified or untracked files
        modified_files: List[str] = []
        if raw_status:
            for line in raw_status.splitlines():
                line_clean = line.strip()
                if not line_clean:
                    continue
                # Status prefix is first 2 chars plus space
                file_part = line_clean[2:].strip()
                # Strip git quote escaping for paths with spaces e.g. "path with space/file.py"
                if file_part.startswith('"') and file_part.endswith('"'):
                    file_part = file_part[1:-1]
                # Handle renames e.g. "R  old -> new"
                if "->" in file_part:
                    file_part = file_part.split("->")[1].strip()
                if file_part and file_part not in modified_files:
                    modified_files.append(file_part)

        # If git diff has files not captured in status, include them
        diff_names_res = run_bash("git diff --name-only HEAD", timeout=30, workspace_dir=ws_str)
        if diff_names_res.success and diff_names_res.output.strip():
            for fname in diff_names_res.output.strip().splitlines():
                fname = fname.strip()
                if fname.startswith('"') and fname.endswith('"'):
                    fname = fname[1:-1]
                if fname and fname not in modified_files:
                    modified_files.append(fname)

        # Ensure deterministic and deduplicated files_modified
        modified_files = sorted(list(dict.fromkeys(modified_files)))

        # Check for NO_CHANGES
        if not raw_status and not raw_diff:
            return VerificationReport(
                status=VerificationStatus.NO_CHANGES,
                is_verified=False,
                tests_passed=False,
                test_command=test_command or "",
                test_output="",
                files_modified=[],
                git_diff="",
                failure_classification=None,
                syntax_valid=True,
                summary="Verification failed: No changes detected in workspace.",
            )

        # Build comprehensive diff including untracked files
        full_diff = raw_diff
        untracked_diff_chunks: List[str] = []
        if raw_status:
            for line in raw_status.splitlines():
                line_clean = line.strip()
                if line_clean.startswith("??"):
                    untracked_fname = line_clean[2:].strip()
                    if untracked_fname.startswith('"') and untracked_fname.endswith('"'):
                        untracked_fname = untracked_fname[1:-1]
                    untracked_path = ws_path / untracked_fname
                    if untracked_path.is_file():
                        res = run_bash(f"git diff --no-index /dev/null {shlex.quote(untracked_fname)}", workspace_dir=ws_str)
                        if res.output.strip():
                            untracked_diff_chunks.append(res.output.strip())

        if untracked_diff_chunks:
            combined_untracked = "\n\n".join(untracked_diff_chunks)
            if full_diff:
                full_diff = f"{full_diff}\n\n{combined_untracked}"
            else:
                full_diff = combined_untracked

        # ---------------------------------------------------------------------
        # 2. Syntax Validation on Modified / Untracked Python Files
        # ---------------------------------------------------------------------
        syntax_valid = True
        syntax_err_msg = ""

        # Collect all Python files to validate, expanding any directories
        py_files_to_check: List[Path] = []
        for rel_file in modified_files:
            abs_file = ws_path / rel_file
            if abs_file.is_file() and rel_file.endswith(".py"):
                if abs_file not in py_files_to_check:
                    py_files_to_check.append(abs_file)
            elif abs_file.is_dir():
                for root, dirs, files in os.walk(abs_file):
                    dirs[:] = [d for d in dirs if d not in NOISE_DIRS]
                    dirs.sort()
                    for f in sorted(files):
                        if f.endswith(".py"):
                            p = Path(root) / f
                            if p not in py_files_to_check:
                                py_files_to_check.append(p)

        py_files_to_check = sorted(list(dict.fromkeys(py_files_to_check)), key=lambda p: str(p))

        for abs_file in py_files_to_check:
            try:
                rel_disp = str(abs_file.relative_to(ws_path))
            except ValueError:
                rel_disp = str(abs_file)

            try:
                content = abs_file.read_text(encoding="utf-8", errors="replace")
                ast.parse(content, filename=str(abs_file))
            except SyntaxError as e:
                syntax_valid = False
                syntax_err_msg = f"Syntax error in '{rel_disp}' at line {e.lineno}: {e.msg}"
                break
            except Exception as e:
                syntax_valid = False
                syntax_err_msg = f"Failed to parse '{rel_disp}': {type(e).__name__}: {str(e)}"
                break

        if not syntax_valid:
            return VerificationReport(
                status=VerificationStatus.FAILED,
                is_verified=False,
                tests_passed=False,
                test_command=test_command or "",
                test_output="",
                files_modified=modified_files,
                git_diff=truncate_output(full_diff),
                failure_classification=FailureClassification.SYNTAX_ERROR,
                syntax_valid=False,
                summary=f"Verification failed: {syntax_err_msg}",
            )

        # ---------------------------------------------------------------------
        # 3. Test Execution Verification
        # ---------------------------------------------------------------------
        effective_cmd = test_command.strip() if (test_command and test_command.strip()) else ""

        if not effective_cmd:
            # No test command specified -> verified on non-empty diff + valid syntax
            return VerificationReport(
                status=VerificationStatus.PASSED,
                is_verified=True,
                tests_passed=True,
                test_command="",
                test_output="No test command specified.",
                files_modified=modified_files,
                git_diff=truncate_output(full_diff),
                failure_classification=None,
                syntax_valid=True,
                summary=f"Verification passed: {len(modified_files)} file(s) modified with valid syntax (no tests specified).",
            )

        test_result = run_bash(
            command=effective_cmd,
            timeout=self.default_timeout,
            workspace_dir=ws_str,
        )

        test_output = truncate_output(test_result.output)

        if test_result.exit_code == 0:
            return VerificationReport(
                status=VerificationStatus.PASSED,
                is_verified=True,
                tests_passed=True,
                test_command=effective_cmd,
                test_output=test_output,
                files_modified=modified_files,
                git_diff=truncate_output(full_diff),
                failure_classification=None,
                syntax_valid=True,
                summary=f"Verification passed: Tests exited with code 0 across {len(modified_files)} modified file(s).",
            )

        # Test failure classification heuristics
        failure_class = FailureClassification.TEST_EXECUTION_ERROR
        if test_result.exit_code == 124:
            failure_class = FailureClassification.TIMEOUT
        elif "ModuleNotFoundError" in test_output or "ImportError" in test_output:
            failure_class = FailureClassification.IMPORT_MISSING
        elif "AssertionError" in test_output or "assert " in test_output or "FAILED" in test_output:
            failure_class = FailureClassification.ASSERTION_FAILED

        return VerificationReport(
            status=VerificationStatus.FAILED,
            is_verified=False,
            tests_passed=False,
            test_command=effective_cmd,
            test_output=test_output,
            files_modified=modified_files,
            git_diff=truncate_output(full_diff),
            failure_classification=failure_class,
            syntax_valid=True,
            summary=f"Verification failed: Test command '{effective_cmd}' failed with exit code {test_result.exit_code}.",
        )
