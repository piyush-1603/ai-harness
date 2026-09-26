"""
Tests for Orchestrator — verifies the state machine and recovery logic
independently of any real model, tools, or context implementation.

Each test uses a purpose-built fake Tools class that simulates a specific
scenario (instant success, one failure then success, repeated identical
failure, permanent failure) so you can prove the ORCHESTRATION logic is
correct on its own, before Person B/C's real code exists.

Run: python3 -m pytest test_orchestrator.py -v
"""

from orchestrator import Orchestrator, StubContext, ToolResult, VerificationResult, State


# ---------------------------------------------------------------------------
# Fake tool implementations, one per scenario
# ---------------------------------------------------------------------------

class AlwaysPassTools:
    """Every edit passes verification immediately -> should resolve in 3 steps."""
    def explore(self, issue, scratchpad):
        return ToolResult(success=True, output="found it")

    def edit(self, scratchpad):
        return ToolResult(success=True, output="applied edit")

    def verify(self):
        return VerificationResult(passed=True)


class FailOnceThenPassTools:
    """Fails with error A once, then passes -> tests basic retry."""
    def __init__(self):
        self.edits = 0

    def explore(self, issue, scratchpad):
        return ToolResult(success=True, output="found it")

    def edit(self, scratchpad):
        self.edits += 1
        return ToolResult(success=True, output=f"edit #{self.edits}")

    def verify(self):
        if self.edits < 2:
            return VerificationResult(passed=False, failing_tests=["test_a"])
        return VerificationResult(passed=True)


class RepeatedSameFailureTools:
    """Always fails with the SAME error -> should trigger re-exploration
    after 2 identical failures, not infinite blind retries."""
    def explore(self, issue, scratchpad):
        return ToolResult(success=True, output="found it")

    def edit(self, scratchpad):
        return ToolResult(success=True, output="applied edit")

    def verify(self):
        return VerificationResult(passed=False, failing_tests=["test_a"])  # never changes


class DifferentFailureEachTimeTools:
    """Fails but with a DIFFERENT error each time -> should keep retrying
    via EDITING (new info each time) rather than re-exploring, until step cap."""
    def __init__(self):
        self.n = 0

    def explore(self, issue, scratchpad):
        return ToolResult(success=True, output="found it")

    def edit(self, scratchpad):
        return ToolResult(success=True, output="applied edit")

    def verify(self):
        self.n += 1
        return VerificationResult(passed=False, failing_tests=[f"test_{self.n}"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_resolves_immediately_when_verification_passes():
    orch = Orchestrator(context=StubContext(), tools=AlwaysPassTools())
    report = orch.run("some issue")
    assert report["status"] == "resolved"
    assert report["last_error"] is None


def test_recovers_from_a_single_failure():
    orch = Orchestrator(context=StubContext(), tools=FailOnceThenPassTools())
    report = orch.run("some issue")
    assert report["status"] == "resolved"
    # explore, edit, test(fail), edit, test(pass), detect-DONE = 6 attempts
    assert report["attempts"] == 6


def test_repeated_identical_failure_triggers_re_exploration():
    tools = RepeatedSameFailureTools()
    orch = Orchestrator(context=StubContext(), tools=tools, max_attempts=6)
    report = orch.run("some issue")
    # Never passes -> should hit the step cap and escalate, not loop forever
    assert report["status"] == "blocked_step_cap"
    # Scratchpad should show the hypothesis was updated to force a new strategy
    sp = report["scratchpad"]
    assert "different" in sp.hypothesis.lower() or "approach" in sp.hypothesis.lower()


def test_step_cap_prevents_infinite_loop_on_varying_failures():
    tools = DifferentFailureEachTimeTools()
    orch = Orchestrator(context=StubContext(), tools=tools, max_attempts=5)
    report = orch.run("some issue")
    assert report["status"] == "blocked_step_cap"
    assert report["attempts"] == 5  # hit the cap exactly, didn't overrun


def test_report_always_includes_scratchpad_history():
    orch = Orchestrator(context=StubContext(), tools=FailOnceThenPassTools())
    report = orch.run("some issue")
    history = report["scratchpad"].attempt_history
    assert any("explore" in h for h in history)
    assert any("edit" in h for h in history)
    assert any("test failed" in h for h in history)


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
