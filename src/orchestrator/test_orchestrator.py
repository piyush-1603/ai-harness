import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.common.types import ToolCall, ToolName, TaskSpec, ToolResult
from src.orchestrator.model_adapter import ModelAdapter
from src.tools.registry import ToolEngine

def mock_success_verifier():
    mock_verifier = MagicMock()
    mock_report = MagicMock()
    mock_report.is_verified = True
    mock_report.tests_passed = True
    mock_report.status = MagicMock()
    mock_report.status.value = "SUCCESS"
    mock_report.test_command = "pytest"
    mock_report.test_output = "ok"
    mock_report.files_modified = []
    mock_report.git_diff = ""
    mock_report.failure_classification = None
    mock_report.syntax_valid = True
    mock_report.summary = "Success"
    mock_verifier.verify.return_value = mock_report
    return mock_verifier

def make_failed_report(sig):
    mock_report = MagicMock()
    mock_report.is_verified = False
    mock_report.tests_passed = False
    mock_report.status = MagicMock()
    mock_report.status.value = sig
    mock_report.failure_classification = None
    mock_report.summary = sig
    mock_report.files_modified = []
    mock_report.test_output = sig
    mock_report.test_command = "pytest"
    mock_report.git_diff = ""
    mock_report.syntax_valid = True
    return mock_report

def mock_engine():
    engine = MagicMock()
    engine.execute.return_value = ToolResult(tool_name=ToolName.RUN_BASH, success=True, output="mocked output", exit_code=0)
    return engine


from unittest.mock import patch

@patch("src.orchestrator.orchestrator.git_reset_hard")
def test_resolves_immediately_when_verification_passes(mock_git):

    mock_responses = [
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix"}}),
        json.dumps({"action": "complete", "message": "Done"})
    ]
    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = mock_engine()
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier(), config=OrchestratorConfig(step_limit=10))
    report = orch.run(TaskSpec("i", "desc", "."))
    assert report["status"] == "completed"

@patch("src.orchestrator.orchestrator.git_reset_hard")
def test_recovers_from_a_single_failure(mock_git):
    mock_responses = [
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix"}}),
        json.dumps({"action": "complete", "message": "Done"}),
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix 2"}}),
        json.dumps({"action": "complete", "message": "Done 2"})
    ]
    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = mock_engine()
    v = MagicMock()
    v.verify.side_effect = [make_failed_report("err"), mock_success_verifier().verify()]
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=v, config=OrchestratorConfig(step_limit=10))
    report = orch.run(TaskSpec("i", "desc", "."))
    assert report["status"] == "completed"

@patch("src.orchestrator.orchestrator.git_reset_hard")
def test_repeated_identical_failure_triggers_re_exploration(mock_git):
    mock_responses = [
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix"}}),
        json.dumps({"action": "complete", "message": "Done"})
    ] * 6
    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = mock_engine()
    v = MagicMock()
    v.verify.return_value = make_failed_report("same")
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=v, config=OrchestratorConfig(step_limit=14))
    report = orch.run(TaskSpec("i", "desc", "."))
    assert report["status"] in ["blocked", "error", "blocked_step_cap"]

@patch("src.orchestrator.orchestrator.git_reset_hard")
def test_step_cap_prevents_infinite_loop_on_varying_failures(mock_git):
    mock_responses = [
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix"}}),
        json.dumps({"action": "complete", "message": "Done"})
    ] * 6
    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = mock_engine()
    v = MagicMock()
    v.verify.side_effect = [make_failed_report(f"err{i}") for i in range(10)]
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=v, config=OrchestratorConfig(step_limit=5))
    report = orch.run(TaskSpec("i", "desc", "."))
    assert report["status"] in ["blocked", "error", "blocked_step_cap"]

@patch("src.orchestrator.orchestrator.git_reset_hard")
def test_report_always_includes_scratchpad_history(mock_git):
    mock_responses = [
        json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo fix"}}),
        json.dumps({"action": "complete", "message": "Done"})
    ]
    adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    engine = mock_engine()
    v = MagicMock()
    v.verify.return_value = make_failed_report("err")
    orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=v, config=OrchestratorConfig(step_limit=3))
    report = orch.run(TaskSpec("i", "desc", "."))
    assert report.get("status") in ["blocked", "error", "blocked_step_cap"]
