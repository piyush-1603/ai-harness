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

from pydantic import BaseModel

class OrchestratorConfig(BaseModel):
    step_limit: int = 8
    cost_limit: float = 3.0
    system_template: str = ""
    instance_template: str = ""

try:
    from orchestrator.recovery import RecoveryManager, RecoveryDecision
    from orchestrator.prompting import PromptBuilder
except ImportError:
    from recovery import RecoveryManager, RecoveryDecision  # type: ignore
    from prompting import PromptBuilder  # type: ignore


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
    ESCALATE = auto()   # blocked — report and stop, don't loop forever


class Orchestrator:
    def __init__(self, context, tools, config: OrchestratorConfig = None):
        """
        context: object implementing get_scratchpad() / update_scratchpad()
        tools:   object implementing explore(), edit(), verify()
        config:  OrchestratorConfig holding step_limit, cost_limit, templates
        """
        self.context = context
        self.tools = tools
        self.config = config or OrchestratorConfig()
        self.state = State.EXPLORING
        
        # Track limits
        self.n_calls = 0
        self.cost = 0.0
        
        self.recovery_manager = RecoveryManager()
        kwargs = {}
        if self.config.system_template:
            kwargs['system_template'] = self.config.system_template
        if self.config.instance_template:
            kwargs['instance_template'] = self.config.instance_template
        self.prompt_builder = PromptBuilder(**kwargs)

    def run(self, issue: str) -> dict:
        """Drive the loop for one issue. Returns a final report dict."""
        self.context.update_scratchpad(hypothesis=f"Investigating: {issue}")

        while self.n_calls < self.config.step_limit and self.cost < self.config.cost_limit:
            self.n_calls += 1
            # In a real setup, self.cost would increment based on tool/model usage
            self.cost += 0.05  # Plausible cost estimate for demo until real model is wired

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
            self.recovery_manager.reset()
            return

        # Failed — delegate to distinct recovery layer
        advice = self.recovery_manager.analyze_failure(
            verification.failing_tests, verification.stderr
        )

        last_observation = verification.failing_tests or verification.stderr[:100]
        self.context.update_scratchpad(
            attempt=f"test failed: {last_observation}"
        )

        # Build prompt using PromptBuilder to inform Foundation Model
        next_prompt = self.prompt_builder.build_turn_prompt(
            issue=self.context.get_scratchpad().hypothesis,
            scratchpad_state=self.context.get_scratchpad(),
            last_observation=str(last_observation),
            recovery_hint=advice.hint
        )

        if advice.decision == RecoveryDecision.FORCE_EXPLORE:
            self.context.update_scratchpad(hypothesis=advice.hint)
            self.state = State.EXPLORING
        elif advice.decision == RecoveryDecision.RETRY_EDIT:
            self.state = State.EDITING
        else:
            self.state = State.ESCALATE

    def _report(self, status: str) -> dict:
        return {
            "status": status,
            "n_calls": self.n_calls, "cost": self.cost,
            "last_error": self.recovery_manager.last_error_signature,
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
