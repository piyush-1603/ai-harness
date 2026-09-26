"""Typed adapters for converting Person C objects to Person B memory models."""

from typing import Optional

from src.common.types import ToolResult, VerificationReport
from src.memory.models import Observation, VerificationResult, Failure, Attempt

def observation_from_tool_result(
    result: ToolResult,
    files: Optional[list[str]] = None
) -> Observation:
    """Converts a ToolResult into an Observation."""
    if result.success:
        summary = f"{result.tool_name.value} succeeded (exit_code={result.exit_code})"
    else:
        if result.error:
            summary = f"{result.tool_name.value} failed (exit_code={result.exit_code}): {result.error}"
        else:
            summary = f"{result.tool_name.value} failed (exit_code={result.exit_code})"

    return Observation(
        type="tool_result",
        source=result.tool_name.value,
        summary=summary,
        raw_output=result.output,
        files=files if files is not None else [],
    )


def verification_from_report(report: VerificationReport) -> VerificationResult:
    """Converts a VerificationReport into a VerificationResult."""
    success = bool(report.is_verified and report.tests_passed)
    
    details = {
        "status": report.status.value,
        "test_command": report.test_command,
        "test_output": report.test_output,
        "files_modified": report.files_modified,
        "git_diff": report.git_diff,
        "failure_classification": report.failure_classification.value if report.failure_classification else None,
        "syntax_valid": report.syntax_valid
    }

    return VerificationResult(
        success=success,
        summary=report.summary,
        tests_passed=1 if report.tests_passed else 0,
        tests_failed=0 if report.tests_passed else 1,
        details=details,
    )


def failure_from_verification_report(report: VerificationReport) -> Optional[Failure]:
    """
    Produces a Failure if verification was unsuccessful.
    Returns None if verification was successful.
    """
    success = bool(report.is_verified and report.tests_passed)
    if success:
        return None

    error_signature = (
        report.failure_classification.value 
        if report.failure_classification 
        else report.status.value
    )

    return Failure(
        error_signature=error_signature,
        summary=report.summary,
        action="verification",
        files=report.files_modified,
    )


def attempt_from_tool_result(
    result: ToolResult,
    attempt_id: str,
    action: str,
    hypothesis: Optional[str] = None,
    files_touched: Optional[list[str]] = None,
    iteration: int = 0
) -> Attempt:
    """Converts a ToolResult and action-level details into an Attempt."""
    if result.success:
        res_summary = f"{result.tool_name.value} succeeded (exit_code={result.exit_code})"
    else:
        if result.error:
            res_summary = f"{result.tool_name.value} failed (exit_code={result.exit_code}): {result.error}"
        else:
            res_summary = f"{result.tool_name.value} failed (exit_code={result.exit_code})"

    return Attempt(
        id=attempt_id,
        hypothesis=hypothesis,
        action=action,
        files_touched=files_touched if files_touched is not None else [],
        result=res_summary,
        success=result.success,
        iteration=iteration,
    )
