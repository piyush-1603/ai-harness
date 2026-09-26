import copy

from src.common.types import (
    ToolResult,
    ToolName,
    VerificationReport,
    VerificationStatus,
    FailureClassification,
)
from src.memory.adapters import (
    observation_from_tool_result,
    verification_from_report,
    failure_from_verification_report,
    attempt_from_tool_result,
)
from src.memory.models import Observation, VerificationResult, Failure, Attempt
from src.memory.manager import MemoryManager

def test_observation_from_tool_result_success():
    res = ToolResult(
        tool_name=ToolName.RUN_BASH,
        success=True,
        output="hello",
        exit_code=0,
    )
    orig_res = copy.deepcopy(res)
    
    obs = observation_from_tool_result(res, files=["src/test.py"])
    
    assert isinstance(obs, Observation)
    assert obs.type == "tool_result"
    assert obs.source == "run_bash"
    assert obs.summary == "run_bash succeeded (exit_code=0)"
    assert obs.raw_output == "hello"
    assert obs.files == ["src/test.py"]
    
    assert res == orig_res # not mutated


def test_observation_from_tool_result_failure_with_error():
    res = ToolResult(
        tool_name=ToolName.READ_FILE,
        success=False,
        output="stack trace...",
        exit_code=1,
        error="File not found"
    )
    
    obs = observation_from_tool_result(res)
    assert obs.summary == "read_file failed (exit_code=1): File not found"
    assert obs.raw_output == "stack trace..."
    assert obs.files == []


def test_observation_from_tool_result_failure_without_error():
    res = ToolResult(
        tool_name=ToolName.WRITE_FILE,
        success=False,
        output="permission denied",
        exit_code=13,
    )
    
    obs = observation_from_tool_result(res)
    assert obs.summary == "write_file failed (exit_code=13)"
    assert obs.raw_output == "permission denied"
    assert obs.files == []


def test_verification_from_report_success():
    rep = VerificationReport(
        status=VerificationStatus.PASSED,
        is_verified=True,
        tests_passed=True,
        test_command="pytest",
        test_output="1 passed",
        files_modified=["a.py"],
        git_diff="+a",
        summary="All good"
    )
    orig_rep = copy.deepcopy(rep)
    
    ver = verification_from_report(rep)
    assert isinstance(ver, VerificationResult)
    assert ver.success is True
    assert ver.summary == "All good"
    assert ver.details["status"] == "PASSED"
    assert ver.details["test_command"] == "pytest"
    assert ver.details["test_output"] == "1 passed"
    assert ver.details["files_modified"] == ["a.py"]
    assert ver.details["git_diff"] == "+a"
    assert ver.details["failure_classification"] is None
    assert ver.details["syntax_valid"] is True
    
    assert rep == orig_rep


def test_verification_from_report_failed():
    rep = VerificationReport(
        status=VerificationStatus.FAILED,
        is_verified=True,
        tests_passed=False,
        test_command="pytest",
        test_output="1 failed",
        files_modified=["a.py"],
        git_diff="+a",
        failure_classification=FailureClassification.ASSERTION_FAILED,
        syntax_valid=True,
        summary="Tests failed"
    )
    
    ver = verification_from_report(rep)
    assert ver.success is False
    assert ver.summary == "Tests failed"
    assert ver.details["status"] == "FAILED"
    assert ver.details["failure_classification"] == "ASSERTION_FAILED"


def test_failure_from_verification_report_success():
    rep = VerificationReport(
        status=VerificationStatus.PASSED,
        is_verified=True,
        tests_passed=True,
        test_command="pytest",
        test_output="1 passed",
        files_modified=["a.py"],
        git_diff="+a",
        summary="All good"
    )
    fail = failure_from_verification_report(rep)
    assert fail is None


def test_failure_from_verification_report_failed_with_classification():
    rep = VerificationReport(
        status=VerificationStatus.FAILED,
        is_verified=True,
        tests_passed=False,
        test_command="pytest",
        test_output="1 failed",
        files_modified=["a.py"],
        git_diff="+a",
        failure_classification=FailureClassification.ASSERTION_FAILED,
        summary="Tests failed"
    )
    
    fail = failure_from_verification_report(rep)
    assert isinstance(fail, Failure)
    assert fail.error_signature == "ASSERTION_FAILED"
    assert fail.summary == "Tests failed"
    assert fail.action == "verification"
    assert fail.files == ["a.py"]
    assert fail.occurrence_count == 1


def test_failure_from_verification_report_failed_without_classification():
    rep = VerificationReport(
        status=VerificationStatus.FAILED,
        is_verified=True,
        tests_passed=False,
        test_command="pytest",
        test_output="timeout",
        files_modified=["a.py"],
        git_diff="+a",
        failure_classification=None,
        summary="Timeout"
    )
    
    fail = failure_from_verification_report(rep)
    assert fail.error_signature == "FAILED"
    assert fail.files == ["a.py"]


def test_failure_from_verification_report_no_changes():
    rep = VerificationReport(
        status=VerificationStatus.NO_CHANGES,
        is_verified=False, # the adapter explicitly uses is_verified and tests_passed
        tests_passed=False,
        test_command="",
        test_output="",
        files_modified=[],
        git_diff="",
        failure_classification=None,
        summary="No changes to test"
    )
    
    fail = failure_from_verification_report(rep)
    assert isinstance(fail, Failure)
    assert fail.error_signature == "NO_CHANGES"


def test_attempt_from_tool_result():
    res = ToolResult(
        tool_name=ToolName.RUN_TESTS,
        success=False,
        output="failed",
        exit_code=2,
    )
    
    att = attempt_from_tool_result(
        res,
        attempt_id="att_1",
        action="running tests",
        hypothesis="test this",
        files_touched=["b.py"],
        iteration=3
    )
    assert isinstance(att, Attempt)
    assert att.id == "att_1"
    assert att.action == "running tests"
    assert att.hypothesis == "test this"
    assert att.files_touched == ["b.py"]
    assert att.success is False
    assert att.result == "run_tests failed (exit_code=2)"
    assert att.iteration == 3


def test_integration_memory_manager():
    mm = MemoryManager()
    mm.initialize_task(task_id="t1", task="test")
    
    rep = VerificationReport(
        status=VerificationStatus.FAILED,
        is_verified=True,
        tests_passed=False,
        test_command="pytest",
        test_output="1 failed",
        files_modified=["c.py"],
        git_diff="+c",
        failure_classification=FailureClassification.SYNTAX_ERROR,
        summary="Syntax error"
    )
    
    # 1. Update verification
    ver = verification_from_report(rep)
    mm.set_verification(ver)
    
    # 2. Add failure
    fail = failure_from_verification_report(rep)
    if fail:
        mm.add_failure(fail)
        
    state = mm.get_state()
    
    assert state.verification is not None
    assert state.verification.success is False
    assert state.verification.details["status"] == "FAILED"
    assert state.verification.details["failure_classification"] == "SYNTAX_ERROR"
    
    assert len(state.failures) == 1
    assert state.failures[0].error_signature == "SYNTAX_ERROR"
    assert state.failures[0].files == ["c.py"]
    assert state.failures[0].occurrence_count == 1
    
    # Send same failure again to verify occurrence increment via MM works natively with adapter object
    fail2 = failure_from_verification_report(rep)
    if fail2:
        mm.add_failure(fail2)
        
    state2 = mm.get_state()
    assert len(state2.failures) == 1
    assert state2.failures[0].occurrence_count == 2
