"""
Tests for Orchestrator — verifies the state machine and recovery logic
independently of any real model, tools, or context implementation.

Each test uses a purpose-built fake Tools class that simulates a specific
scenario (instant success, one failure then success, repeated identical
failure, permanent failure) so you can prove the ORCHESTRATION logic is
correct on its own, before Person B/C's real code exists.

Run: python3 -m pytest test_orchestrator.py -v
"""

from src.orchestrator.orchestrator import Orchestrator, ToolResult, VerificationResult, State, OrchestratorConfig
from src.common.types import ToolCall, ToolName, ScratchpadState
from src.orchestrator.model_adapter import ModelCompletion

class StubContext:
    def __init__(self):
        self._state = ScratchpadState()
    def get_scratchpad(self) -> ScratchpadState:
        return self._state
    def update_scratchpad(self, hypothesis: str = None, attempt: str = None):
        if hypothesis: self._state.hypothesis = hypothesis
        if attempt: self._state.attempt_history.append(attempt)




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
        elif self.fails_times == -1: # Infinite
            return VerificationResult(passed=False, failing_tests=[self.fail_sig])
        return VerificationResult(passed=True)

class StubModel:
    def __init__(self, tools_per_phase=1):
        self.calls = 0
        self.tools_per_phase = tools_per_phase
    def decide(self, prompt):
        self.calls += 1
        if self.calls % (self.tools_per_phase + 1) == 0:
            return ModelCompletion(message="Done")
        return ToolCall(tool_name=ToolName.RUN_BASH, tool_args={}, call_id="abc")

def test_resolves_immediately_when_verification_passes():
    orch = Orchestrator(StubContext(), StubToolEngine(), StubVerifier(0), StubModel(), config=OrchestratorConfig(step_limit=10))
    report = orch.run("issue")
    assert report["status"] == "resolved"

def test_recovers_from_a_single_failure():
    orch = Orchestrator(StubContext(), StubToolEngine(), StubVerifier(1), StubModel(), config=OrchestratorConfig(step_limit=10))
    report = orch.run("issue")
    assert report["status"] == "resolved"

def test_repeated_identical_failure_triggers_re_exploration():
    orch = Orchestrator(StubContext(), StubToolEngine(), StubVerifier(-1, "same"), StubModel(), config=OrchestratorConfig(step_limit=14))
    report = orch.run("issue")
    assert report["status"] == "blocked_step_cap"

def test_step_cap_prevents_infinite_loop_on_varying_failures():
    orch = Orchestrator(StubContext(), StubToolEngine(), StubVerifier(10), StubModel(), config=OrchestratorConfig(step_limit=5))
    report = orch.run("issue")
    assert report["status"] == "blocked_step_cap"

def test_report_always_includes_scratchpad_history():
    orch = Orchestrator(StubContext(), StubToolEngine(), StubVerifier(1), StubModel(), config=OrchestratorConfig(step_limit=10))
    report = orch.run("issue")
    assert any("tool" in h.lower() for h in report["scratchpad"].attempt_history)

if __name__ == "__main__":
    # Allow running without pytest installed, as a quick sanity check
    import sys
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL: {t.__name__} — {e}")
    sys.exit(1 if failures else 0)
