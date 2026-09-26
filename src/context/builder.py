"""Deterministic context builder deriving model-ready context bundles from TaskState."""

from __future__ import annotations

from typing import Optional, Union

from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    TaskState,
    TokenUsage,
    VerificationResult,
)


class ContextBuilder:
    """
    Constructs a deterministic, compact ContextBundle from TaskState or MemoryManager.
    Never mutates the underlying TaskState.
    """

    def __init__(self, config: Optional[ContextConfig] = None) -> None:
        self.config = config or ContextConfig()

    def build(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
    ) -> ContextBundle:
        """
        Derives a model-ready ContextBundle from the given state.
        Guarantees that state_or_manager is not mutated.
        """
        if hasattr(state_or_manager, "get_state"):
            state = state_or_manager.get_state()
        elif isinstance(state_or_manager, TaskState):
            state = state_or_manager
        else:
            raise TypeError(
                f"Expected TaskState or MemoryManager, got {type(state_or_manager).__name__}"
            )

        cfg = config or self.config

        # 1. Base Task & Lifecycle (copies of primitives/collections)
        task = state.task
        phase = state.phase
        status = state.status
        plan = list(state.plan)
        current_hypothesis = state.current_hypothesis
        relevant_files = list(state.relevant_files)
        touched_files = list(state.touched_files)

        # 2. Discoveries (stable learned facts, bounded by max_discoveries)
        disc_slice = (
            state.discoveries[-cfg.max_discoveries :]
            if cfg.max_discoveries > 0
            else []
        )
        important_discoveries = [
            Discovery(
                statement=d.statement,
                evidence=d.evidence,
                files=list(d.files),
                confidence=d.confidence,
                timestamp=d.timestamp,
            )
            for d in disc_slice
        ]

        # 3. Recent Observations (recency limited, raw_output omitted by default)
        obs_slice = (
            state.recent_observations[-cfg.max_observations :]
            if cfg.max_observations > 0
            else []
        )
        recent_observations = [
            Observation(
                type=o.type,
                source=o.source,
                summary=o.summary,
                raw_output=o.raw_output if cfg.include_raw_output else None,
                files=list(o.files),
                timestamp=o.timestamp,
            )
            for o in obs_slice
        ]

        # 4. Attempts (recency limited, mutually exclusive failed vs successful)
        attempts_slice = (
            state.attempts[-cfg.max_attempts :]
            if cfg.max_attempts > 0
            else []
        )

        failed_attempts = [
            Attempt(
                id=a.id,
                hypothesis=a.hypothesis,
                action=a.action,
                files_touched=list(a.files_touched),
                result=a.result,
                success=a.success,
                iteration=a.iteration,
                timestamp=a.timestamp,
            )
            for a in attempts_slice
            if not a.success
        ]

        if cfg.include_successful_attempts:
            recent_attempts = [
                Attempt(
                    id=a.id,
                    hypothesis=a.hypothesis,
                    action=a.action,
                    files_touched=list(a.files_touched),
                    result=a.result,
                    success=a.success,
                    iteration=a.iteration,
                    timestamp=a.timestamp,
                )
                for a in attempts_slice
                if a.success
            ]
        else:
            recent_attempts = []

        # 5. Repeated Failures (occurrence_count >= min_failure_occurrences)
        repeated_failures = [
            Failure(
                error_signature=f.error_signature,
                summary=f.summary,
                action=f.action,
                files=list(f.files),
                occurrence_count=f.occurrence_count,
                first_seen=f.first_seen,
                last_seen=f.last_seen,
            )
            for f in state.failures
            if f.occurrence_count >= cfg.min_failure_occurrences
        ]

        # 6. Current Active Errors
        current_errors = list(state.current_errors)

        # 7. Latest Verification Result
        latest_verification = None
        if state.verification is not None:
            latest_verification = VerificationResult(
                success=state.verification.success,
                summary=state.verification.summary,
                tests_passed=state.verification.tests_passed,
                tests_failed=state.verification.tests_failed,
                details=state.verification.details,
                timestamp=state.verification.timestamp,
            )

        # 8. Telemetry & Budget
        token_usage = TokenUsage(
            input_tokens=state.token_usage.input_tokens,
            output_tokens=state.token_usage.output_tokens,
            total_tokens=state.token_usage.total_tokens,
        )

        return ContextBundle(
            task=task,
            phase=phase,
            status=status,
            plan=plan,
            current_hypothesis=current_hypothesis,
            relevant_files=relevant_files,
            touched_files=touched_files,
            important_discoveries=important_discoveries,
            recent_observations=recent_observations,
            recent_attempts=recent_attempts,
            failed_attempts=failed_attempts,
            repeated_failures=repeated_failures,
            current_errors=current_errors,
            latest_verification_result=latest_verification,
            iteration_count=state.iteration,
            model_call_count=state.model_calls,
            tool_call_count=state.tool_calls,
            token_usage=token_usage,
        )

    def render_text(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        config: Optional[ContextConfig] = None,
    ) -> str:
        """
        Builds a bundle and returns its deterministic text representation.
        """
        bundle = self.build(state_or_manager, config=config)
        return bundle.render_text()
