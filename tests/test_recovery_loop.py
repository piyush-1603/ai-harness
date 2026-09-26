"""Tests for Phase 8: Minimal Autonomous Recovery Loop.

Verifies the recovery workflow:
- Test A: First attempt fails verification -> recovery edit -> second verification passes (status="completed", verified=True)
- Test B: Verification fails repeatedly until HARNESS_MAX_RECOVERIES (status="failed", verified=False)
- Test C: RecoveryManager returns ESCALATE (status="failed", verified=False)
- Test D: Clean pass on first attempt requires zero recoveries (status="completed", verified=True, recovery_attempts=0)
- Test E: Failure evidence correctly injected into recovery turn prompt
"""

import json
from pathlib import Path
import subprocess

import pytest

from src.common.types import (
    FailureClassification,
    TaskSpec,
    VerificationReport,
    VerificationStatus,
)
from src.memory.manager import MemoryManager
from src.orchestrator.model_adapter import ModelAdapter
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.orchestrator.recovery import RecoveryDecision, RecoveryManager
from src.tools.registry import ToolEngine
from src.verification.verifier import VerificationEngine


def _init_git_repo(ws: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(ws), check=True, capture_output=True)


def test_recovery_first_attempt_fails_then_edit_passes(tmp_path: Path) -> None:
    """Test A: First attempt fails verification -> recovery edit -> second verification passes."""
    _init_git_repo(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    mock_responses = [
        # Turn 1: Premature completion (code is still buggy)
        json.dumps({"action": "complete", "message": "Initial implementation done"}),
        # Turn 2: Recovery edit after seeing verification failure
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a - b\n",
                "replace_block": "return a + b\n",
            },
        }),
        # Turn 3: Second completion after fixing the bug
        json.dumps({"action": "complete", "message": "Fixed addition operator"}),
    ]

    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = ToolEngine(workspace_dir=str(tmp_path))
    verifier = VerificationEngine()
    memory_mgr = MemoryManager()
    orch = Orchestrator(
        model_adapter=adapter,
        tool_engine=engine,
        verifier=verifier,
        memory_manager=memory_mgr,
    )

    task = TaskSpec(
        issue_id="recovery-test-a",
        issue_description="Fix addition in calc.py",
        workspace_dir=str(tmp_path),
        test_command="python3 -B test_calc.py",
    )
    report = orch.run(task)

    assert report["status"] == "completed"
    assert report["verified"] is True
    assert report["recovery_attempts"] == 1
    assert report["telemetry"].recovery_attempts == 1
    assert isinstance(report["verification_report"], VerificationReport)
    assert report["verification_report"].is_verified is True
    assert report["verification_report"].tests_passed is True
    assert report["verification_status"] == VerificationStatus.PASSED.value
    assert "calc.py" in report["files_modified"]
    assert "calc.py" in report["telemetry"].files_modified
    assert "calc.py" in report["git_diff"]

    attempts = memory_mgr.get_state().attempts
    assert any("recovery_1" in att.id for att in attempts)


def test_recovery_fails_repeatedly_until_max_recoveries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test B: Verification fails repeatedly until HARNESS_MAX_RECOVERIES is exhausted."""
    monkeypatch.setenv("HARNESS_MAX_RECOVERIES", "2")
    _init_git_repo(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    mock_responses = [
        # Turn 1: Attempt 1 completes -> fails verification (recovery 1)
        json.dumps({"action": "complete", "message": "First attempt"}),
        # Turn 2: Tries wrong edit
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a - b\n",
                "replace_block": "return a * b\n",
            },
        }),
        # Turn 3: Attempt 2 completes -> fails verification (recovery 2)
        json.dumps({"action": "complete", "message": "Second attempt"}),
        # Turn 4: Tries another wrong edit
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a * b\n",
                "replace_block": "return a / b\n",
            },
        }),
        # Turn 5: Attempt 3 completes -> fails verification -> max_recoveries exhausted (2 >= 2)
        json.dumps({"action": "complete", "message": "Third attempt"}),
    ]

    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = ToolEngine(workspace_dir=str(tmp_path))
    verifier = VerificationEngine()
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

    task = TaskSpec(
        issue_id="recovery-test-b",
        issue_description="Repeated failures",
        workspace_dir=str(tmp_path),
        test_command="python3 -B test_calc.py",
    )
    report = orch.run(task)

    assert report["status"] == "failed"
    assert report["verified"] is False
    assert report["recovery_attempts"] == 2
    assert report["telemetry"].recovery_attempts == 2
    assert "Exhausted maximum recovery attempts (2)" in report["last_error"]
    assert report["verification_status"] == VerificationStatus.FAILED.value
    assert report["failure_classification"] == FailureClassification.ASSERTION_FAILED.value
    assert report["test_output"] != ""
    assert isinstance(report["verification_report"], VerificationReport)
    assert report["verification_report"].is_verified is False


def test_recovery_escalates_on_repeated_failure(tmp_path: Path) -> None:
    """Test C: RecoveryManager returns ESCALATE -> immediate stop with status='failed', verified=False."""
    _init_git_repo(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    mock_responses = [
        # Turn 1: Edit that produces failing tests
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a - b\n",
                "replace_block": "return a * b\n",
            },
        }),
        # Turn 2: Attempt 1 completes -> fails verification -> escalate immediately
        json.dumps({"action": "complete", "message": "Initial try"}),
    ]

    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = ToolEngine(workspace_dir=str(tmp_path))
    verifier = VerificationEngine()
    # escalate_threshold=1 forces ESCALATE on the very first analyzed failure
    recovery_mgr = RecoveryManager(escalate_threshold=1)
    orch = Orchestrator(
        model_adapter=adapter,
        tool_engine=engine,
        verifier=verifier,
        recovery_manager=recovery_mgr,
    )

    task = TaskSpec(
        issue_id="recovery-test-c",
        issue_description="Escalation test",
        workspace_dir=str(tmp_path),
        test_command="python3 -B test_calc.py",
    )
    report = orch.run(task)

    assert report["status"] == "failed"
    assert report["verified"] is False
    assert report["recovery_attempts"] == 0
    assert "Recovery escalated:" in report["last_error"]
    assert report["verification_status"] == VerificationStatus.FAILED.value
    assert report["failure_classification"] == FailureClassification.ASSERTION_FAILED.value
    assert isinstance(report["verification_report"], VerificationReport)
    assert report["verification_report"].is_verified is False


def test_recovery_clean_pass_requires_zero_recoveries(tmp_path: Path) -> None:
    """Test D: Existing successful path passes cleanly with zero recovery attempts."""
    _init_git_repo(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    mock_responses = [
        # Turn 1: Edit correctly before completion
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a - b\n",
                "replace_block": "return a + b\n",
            },
        }),
        # Turn 2: Complete
        json.dumps({"action": "complete", "message": "Fixed addition on first pass"}),
    ]

    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = ToolEngine(workspace_dir=str(tmp_path))
    verifier = VerificationEngine()
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

    task = TaskSpec(
        issue_id="recovery-test-d",
        issue_description="Clean single-pass success",
        workspace_dir=str(tmp_path),
        test_command="python3 -B test_calc.py",
    )
    report = orch.run(task)

    assert report["status"] == "completed"
    assert report["verified"] is True
    assert report["recovery_attempts"] == 0
    assert report["telemetry"].recovery_attempts == 0
    assert report["last_error"] is None
    assert report["verification_report"].is_verified is True


def test_recovery_prompt_contains_failure_evidence(tmp_path: Path) -> None:
    """Test E: Verification failure injects status, classification, command, output, and summary into prompt."""
    _init_git_repo(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    prompts_received: list[str] = []

    class InspectingAdapter(ModelAdapter):
        def decide(self, prompt: str):
            prompts_received.append(prompt)
            return super().decide(prompt)

    mock_responses = [
        # Turn 1: First edit (wrong)
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a - b\n",
                "replace_block": "return a * b\n",
            },
        }),
        # Turn 2: Premature completion (tests still fail)
        json.dumps({"action": "complete", "message": "Done multiplication"}),
        # Turn 3: Recovery edit fixing the bug
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "search_block": "return a * b\n",
                "replace_block": "return a + b\n",
            },
        }),
        # Turn 4: Final completion
        json.dumps({"action": "complete", "message": "Fixed addition"}),
    ]

    adapter = InspectingAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = ToolEngine(workspace_dir=str(tmp_path))
    verifier = VerificationEngine()
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

    task = TaskSpec(
        issue_id="recovery-test-e",
        issue_description="Prompt verification test",
        workspace_dir=str(tmp_path),
        test_command="python3 -B test_calc.py",
    )
    report = orch.run(task)

    assert report["status"] == "completed"
    assert report["verified"] is True
    assert len(prompts_received) >= 3

    # Turn 3 prompt was generated right after verification failure on Turn 2
    recovery_prompt = prompts_received[2]
    assert "## VERIFICATION FAILURE" in recovery_prompt
    assert "Verification Status: FAILED" in recovery_prompt
    assert "Failure Classification: ASSERTION_FAILED" in recovery_prompt
    assert "Test Command: python3 -B test_calc.py" in recovery_prompt
    assert "Verification Summary: Verification failed:" in recovery_prompt
    assert "Test Output:" in recovery_prompt
    assert "AssertionError" in recovery_prompt
    assert "## RECOVERY HINT" in recovery_prompt
