"""ContextBundle data model and text rendering for coding-agent context."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    Phase,
    TaskStatus,
    TokenUsage,
    VerificationResult,
)


@dataclass
class ContextBundle:
    """
    Compact, structured context package derived deterministically from TaskState.
    Acts as the immutable presentation layer for model input and telemetry.
    """
    task: str
    phase: Phase
    status: TaskStatus
    plan: list[str] = field(default_factory=list)
    current_hypothesis: Optional[str] = None
    relevant_files: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    important_discoveries: list[Discovery] = field(default_factory=list)
    recent_observations: list[Observation] = field(default_factory=list)
    recent_attempts: list[Attempt] = field(default_factory=list)
    failed_attempts: list[Attempt] = field(default_factory=list)
    repeated_failures: list[Failure] = field(default_factory=list)
    current_errors: list[str] = field(default_factory=list)
    latest_verification_result: Optional[VerificationResult] = None
    iteration_count: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    # -------------------------------------------------------------------------
    # Convenience Property Aliases
    # -------------------------------------------------------------------------

    @property
    def discoveries(self) -> list[Discovery]:
        return self.important_discoveries

    @property
    def observations(self) -> list[Observation]:
        return self.recent_observations

    @property
    def attempts(self) -> list[Attempt]:
        return self.recent_attempts

    @property
    def successful_attempts(self) -> list[Attempt]:
        return [a for a in self.recent_attempts if a.success]

    @property
    def verification(self) -> Optional[VerificationResult]:
        return self.latest_verification_result

    @property
    def iteration(self) -> int:
        return self.iteration_count

    @property
    def model_calls(self) -> int:
        return self.model_call_count

    @property
    def tool_calls(self) -> int:
        return self.tool_call_count

    # -------------------------------------------------------------------------
    # Programmatic Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert ContextBundle into a JSON-serializable dictionary."""
        return {
            "task": self.task,
            "phase": self.phase.value if isinstance(self.phase, Enum) else str(self.phase),
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "plan": list(self.plan),
            "current_hypothesis": self.current_hypothesis,
            "relevant_files": list(self.relevant_files),
            "touched_files": list(self.touched_files),
            "important_discoveries": [d.to_dict() for d in self.important_discoveries],
            "recent_observations": [o.to_dict() for o in self.recent_observations],
            "recent_attempts": [a.to_dict() for a in self.recent_attempts],
            "failed_attempts": [a.to_dict() for a in self.failed_attempts],
            "repeated_failures": [f.to_dict() for f in self.repeated_failures],
            "current_errors": list(self.current_errors),
            "latest_verification_result": (
                self.latest_verification_result.to_dict()
                if self.latest_verification_result
                else None
            ),
            "iteration_count": self.iteration_count,
            "model_call_count": self.model_call_count,
            "tool_call_count": self.tool_call_count,
            "token_usage": self.token_usage.to_dict(),
        }

    # -------------------------------------------------------------------------
    # Human & Model Readable Text Rendering
    # -------------------------------------------------------------------------

    def render_text(self) -> str:
        """
        Renders the bundle as deterministic, structured text organized in clear sections.
        Does not inject prescriptive model instructions.
        """
        sections: list[str] = []

        # 1. TASK
        sections.append(f"## TASK\n{self.task}")

        # 2. PHASE / STATUS
        phase_str = self.phase.value if isinstance(self.phase, Enum) else str(self.phase)
        status_str = self.status.value if isinstance(self.status, Enum) else str(self.status)
        sections.append(f"## PHASE / STATUS\nPhase: {phase_str}\nStatus: {status_str}")

        # 3. PLAN
        if self.plan:
            plan_lines = [f"{i}. {step}" for i, step in enumerate(self.plan, start=1)]
            sections.append("## PLAN\n" + "\n".join(plan_lines))
        else:
            sections.append("## PLAN\n(No plan defined)")

        # 4. CURRENT HYPOTHESIS
        if self.current_hypothesis:
            sections.append(f"## CURRENT HYPOTHESIS\n{self.current_hypothesis}")
        else:
            sections.append("## CURRENT HYPOTHESIS\n(None)")

        # 5. RELEVANT FILES
        rel_lines: list[str] = []
        if self.relevant_files:
            rel_lines.append(f"Relevant: {', '.join(self.relevant_files)}")
        else:
            rel_lines.append("Relevant: (None)")
        if self.touched_files:
            rel_lines.append(f"Touched: {', '.join(self.touched_files)}")
        else:
            rel_lines.append("Touched: (None)")
        sections.append("## RELEVANT FILES\n" + "\n".join(rel_lines))

        # 6. DISCOVERIES
        if self.important_discoveries:
            disc_lines: list[str] = []
            for d in self.important_discoveries:
                evidence_suffix = f" (evidence: {d.evidence})" if d.evidence else ""
                disc_lines.append(f"- [{d.confidence:.2f}] {d.statement}{evidence_suffix}")
            sections.append("## DISCOVERIES\n" + "\n".join(disc_lines))
        else:
            sections.append("## DISCOVERIES\n(None)")

        # 7. RECENT OBSERVATIONS
        if self.recent_observations:
            obs_lines: list[str] = []
            for o in self.recent_observations:
                line = f"- [{o.type} | {o.source}] {o.summary}"
                if o.raw_output is not None:
                    line += f"\n  Output:\n{o.raw_output}"
                obs_lines.append(line)
            sections.append("## RECENT OBSERVATIONS\n" + "\n".join(obs_lines))
        else:
            sections.append("## RECENT OBSERVATIONS\n(None)")

        # 8. FAILED ATTEMPTS
        if self.failed_attempts:
            att_lines: list[str] = []
            for a in self.failed_attempts:
                res_suffix = f" -> {a.result}" if a.result else ""
                att_lines.append(f"- Attempt {a.id} (iter {a.iteration}): {a.action}{res_suffix}")
            sections.append("## FAILED ATTEMPTS\n" + "\n".join(att_lines))
        else:
            sections.append("## FAILED ATTEMPTS\n(None)")

        # 9. REPEATED FAILURES
        if self.repeated_failures:
            fail_lines: list[str] = []
            for f in self.repeated_failures:
                fail_lines.append(f"- [x{f.occurrence_count}] {f.error_signature}: {f.summary}")
            sections.append("## REPEATED FAILURES\n" + "\n".join(fail_lines))
        else:
            sections.append("## REPEATED FAILURES\n(None)")

        # 10. CURRENT ERRORS
        if self.current_errors:
            err_lines = [f"- {err}" for err in self.current_errors]
            sections.append("## CURRENT ERRORS\n" + "\n".join(err_lines))
        else:
            sections.append("## CURRENT ERRORS\n(None)")

        # 11. VERIFICATION
        if self.latest_verification_result:
            v = self.latest_verification_result
            v_lines = [
                f"Success: {v.success}",
                f"Summary: {v.summary}",
            ]
            if v.tests_passed:
                v_lines.append(f"Passed: {v.tests_passed}")
            if v.tests_failed:
                v_lines.append(f"Failed: {v.tests_failed}")
            sections.append("## VERIFICATION\n" + "\n".join(v_lines))
        else:
            sections.append("## VERIFICATION\n(None)")

        # 12. EFFICIENCY
        eff_lines = [
            f"Iteration: {self.iteration_count}",
            f"Model Calls: {self.model_call_count}",
            f"Tool Calls: {self.tool_call_count}",
            f"Token Usage: {self.token_usage.total_tokens} total ({self.token_usage.input_tokens} input, {self.token_usage.output_tokens} output)",
        ]
        sections.append("## EFFICIENCY\n" + "\n".join(eff_lines))

        return "\n\n".join(sections)

    def to_text(self) -> str:
        """Alias for render_text()."""
        return self.render_text()

    def __str__(self) -> str:
        return self.render_text()
