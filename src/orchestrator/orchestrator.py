"""
Orchestrator — Person A's subsystem.

Owns: the state machine, retry/escalation logic, failure detection,
and the main entry point that wires Context (Person B) and Tools (Person C)
together. Ships with STUB implementations of both so this file runs and
is testable standalone, before their real code exists.

Swap StubContext -> real ContextManager and StubTools -> real Tools
once those land. Nothing else in this file should need to change if
everyone codes to the same contract below.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Shared interface contract (Section 7 of the PRD). Agree on this with
# B and C before anyone writes their real implementation.
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None


@dataclass
class ScratchpadState:
    hypothesis: str = ""
    files_touched: list = field(default_factory=list)
    attempt_history: list = field(default_factory=list)


@dataclass
class VerificationResult:
    passed: bool
    failing_tests: list = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class State(Enum):
    EXPLORING = auto()
    EDITING = auto()
    TESTING = auto()
    DONE = auto()
    RETRY = auto()
    ESCALATE = auto()   # blocked — report and stop, don't loop forever


class Orchestrator:
    def __init__(self, context, tools, max_attempts: int = 8):
        """
        context: object implementing get_scratchpad() / update_scratchpad()
        tools:   object implementing explore(), edit(), verify()
        max_attempts: hard step cap before we give up and escalate
        """
        self.context = context
        self.tools = tools
        self.max_attempts = max_attempts
        self.state = State.EXPLORING
        self.attempts = 0
        self.last_error: Optional[str] = None
        self.consecutive_same_error = 0

    def run(self, issue: str) -> dict:
        """Drive the loop for one issue. Returns a final report dict."""
        self.context.update_scratchpad(hypothesis=f"Investigating: {issue}")

        while self.attempts < self.max_attempts:
            self.attempts += 1

            if self.state == State.EXPLORING:
                self._explore(issue)

            elif self.state == State.EDITING:
                self._edit()

            elif self.state == State.TESTING:
                self._test()

            elif self.state == State.DONE:
                return self._report("resolved")

            elif self.state == State.ESCALATE:
                return self._report("blocked")

        # Ran out of attempts without resolving or explicitly escalating
        self.state = State.ESCALATE
        return self._report("blocked_step_cap")

    # -- state handlers ----------------------------------------------------

    def _explore(self, issue: str):
        result: ToolResult = self.tools.explore(issue, self.context.get_scratchpad())
        self.context.update_scratchpad(attempt=f"explore: {result.output[:200]}")
        # Once exploration finds a target, move to editing.
        # Real logic: ask the model if it has enough info yet.
        self.state = State.EDITING

    def _edit(self):
        result: ToolResult = self.tools.edit(self.context.get_scratchpad())
        self.context.update_scratchpad(attempt=f"edit: {result.output[:200]}")
        self.state = State.TESTING

    def _test(self):
        verification: VerificationResult = self.tools.verify()

        if verification.passed:
            self.state = State.DONE
            self.last_error = None
            self.consecutive_same_error = 0
            return

        # Failed — this is the recovery decision point.
        error_signature = ",".join(sorted(verification.failing_tests)) or verification.stderr[:100]

        if error_signature == self.last_error:
            self.consecutive_same_error += 1
        else:
            self.consecutive_same_error = 1
        self.last_error = error_signature

        self.context.update_scratchpad(
            attempt=f"test failed: {verification.failing_tests or verification.stderr[:200]}"
        )

        if self.consecutive_same_error >= 2:
            # Same failure twice in a row -> don't retry blindly, force a
            # different strategy on the next pass.
            self.context.update_scratchpad(
                hypothesis="Previous approach repeated the same failure. "
                           "Try a different file/approach next attempt."
            )
            self.state = State.EXPLORING
        else:
            # New information (a different failure) -> just try again with it.
            self.state = State.EDITING

    def _report(self, status: str) -> dict:
        return {
            "status": status,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "scratchpad": self.context.get_scratchpad(),
        }


# ---------------------------------------------------------------------------
# Stubs — replace these with Person B's and Person C's real classes.
# Kept deliberately dumb so you can unit test the state machine logic above
# without needing their code to exist yet.
# ---------------------------------------------------------------------------

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


class StubTools:
    """Simulates: fails once with error A, then succeeds — to test recovery."""
    def __init__(self):
        self._call_count = 0

    def explore(self, issue, scratchpad) -> ToolResult:
        return ToolResult(success=True, output=f"found relevant file for: {issue}")

    def edit(self, scratchpad) -> ToolResult:
        self._call_count += 1
        return ToolResult(success=True, output=f"applied edit attempt #{self._call_count}")

    def verify(self) -> VerificationResult:
        if self._call_count < 2:
            return VerificationResult(passed=False, failing_tests=["test_discounts.py::test_zero_qty"])
        return VerificationResult(passed=True)


if __name__ == "__main__":
    orch = Orchestrator(context=StubContext(), tools=StubTools())
    report = orch.run("calculate_discount() returns wrong values when quantity is 0")
    print(report)
