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
from src.common.types import ToolResult, ScratchpadState, VerificationResult
from src.common.types import ToolCall, ToolName
from src.orchestrator.model_adapter import ModelCompletion

from enum import Enum, auto
from typing import Optional

from pydantic import BaseModel

class OrchestratorConfig(BaseModel):
    step_limit: int = 8
    cost_limit: float = 3.0
    system_template: str = ""
    instance_template: str = ""

from src.orchestrator.recovery import RecoveryManager, RecoveryDecision
from src.orchestrator.prompting import PromptBuilder


# ---------------------------------------------------------------------------
# Shared interface contract (Section 7 of the PRD). Agree on this with
# B and C before anyone writes their real implementation.
# ---------------------------------------------------------------------------



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
    def __init__(self, context, tool_engine, verifier, model, config: OrchestratorConfig = None):
        """
        context: object implementing get_scratchpad() / update_scratchpad()
        tool_engine: object implementing execute(ToolCall)
        verifier: object implementing verify() -> VerificationResult
        model: object implementing decide(prompt) -> Union[ToolCall, ModelCompletion]
        config: OrchestratorConfig holding step_limit, cost_limit, templates
        """
        self.context = context
        self.tool_engine = tool_engine
        self.verifier = verifier
        self.model = model
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

            if self.state in (State.EXPLORING, State.EDITING):
                self._run_tool_loop(issue)

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

    def _run_tool_loop(self, issue: str):
        last_obs = self.context.get_scratchpad().attempt_history[-1] if self.context.get_scratchpad().attempt_history else "No observation yet."
        hint = self.recovery_manager.last_error_signature if self.state == State.EDITING else None
        
        prompt = self.prompt_builder.build_turn_prompt(
            issue=issue,
            scratchpad_state=self.context.get_scratchpad(),
            last_observation=last_obs,
            recovery_hint=hint
        )
        decision = self.model.decide(prompt)
        
        if isinstance(decision, ModelCompletion):
            if self.state == State.EXPLORING:
                self.state = State.EDITING
            else:
                self.state = State.TESTING
        else:
            result: ToolResult = self.tool_engine.execute(decision)
            self.context.update_scratchpad(attempt=f"{self.state.name} tool {decision.tool_name.value} output: {result.output[:200]}")

    def _test(self):
        verification: VerificationResult = self.verifier.verify()

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

