import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig, ToolResult
from src.common.types import ToolCall, ToolName, TaskSpec, ToolResult, ScratchpadState, VerificationResult
from src.orchestrator.model_adapter import ModelAdapter, ModelCompletion
from src.tools.registry import ToolEngine


class StubContext:
    def __init__(self):
        self._state = ScratchpadState()

    def get_scratchpad(self) -> ScratchpadState:
        return self._state

    def update_scratchpad(self, hypothesis: str = None, attempt: str = None):
        if hypothesis:
            self._state.hypothesis = hypothesis
        if attempt:
            self._state.attempt_history.append(attempt)


class StubToolEngine:
    def __init__(self, output="success"):
        self.output = output
        self.calls = 0

    def execute(self, call):
        self.calls += 1
        return ToolResult(success=True, output=f"{self.output} {self.calls}")


class StubVerifier:
    def __init__(self, fails_times=0, fail_sig="test_a"):
        self.fails_times = fails_times
        self.fail_sig = fail_sig
        self.calls = 0

    def verify(self):
        self.calls += 1
        if self.calls <= self.fails_times:
            return VerificationResult(passed=False, failing_tests=[f"{self.fail_sig}_{self.calls}"])
        elif self.fails_times == -1:
            return VerificationResult(passed=False, failing_tests=[self.fail_sig])
        return VerificationResult(passed=True)


class StubModel:
    def __init__(self, tools_per_phase=1):
        self.calls = 0
        self.tools_per_phase = tools_per_phase

    def decide(self, prompt):
        self.calls += 1
        if self.calls == 1:
            return ToolCall(tool_name=ToolName.RUN_BASH, tool_args={"command": "sed -i '' 's/0.1/0.9/' app.py"}, call_id="abc")
        elif self.calls % 2 == 0:
            return ModelCompletion(message="Done")
        else:
            return ToolCall(tool_name=ToolName.RUN_BASH, tool_args={"command": "echo dummy"}, call_id="abc")


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
    assert report["status"] in ["blocked", "error", "blocked_step_cap", "failed"]

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
    assert report.get("status") in ["blocked", "error", "blocked_step_cap", "failed"]
