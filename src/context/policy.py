"""Deterministic adaptive context policy engine for the coding-agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Optional, Union

from src.context.config import ContextConfig
from src.context.scanner import RepositoryIndex
from src.memory.models import Phase, TaskState


class PressureLevel(str, Enum):
    """Recovery pressure severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value.upper())


class RepositoryScope(str, Enum):
    """Scope of repository context eligibility."""
    MINIMAL = "MINIMAL"
    FOCUSED = "FOCUSED"
    BROAD = "BROAD"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value.upper())


@dataclass
class AdaptivePolicyConfig:
    """
    Configurable thresholds and weights for recovery pressure calculation
    and phase-aware context limit adjustments.
    """
    # Pressure thresholds
    medium_pressure_threshold: int = 3
    high_pressure_threshold: int = 6

    # Scoring weights
    verification_failure_weight: int = 2
    consecutive_failed_attempt_weight: int = 1
    active_error_weight: int = 1
    repeated_failure_weight: int = 1
    strongly_repeated_threshold: int = 4
    strongly_repeated_bonus: int = 1

    # Monotonic limits: (Phase, PressureLevel) -> limits
    limits: dict[str, dict[str, dict[str, int]]] = field(default_factory=lambda: {
        "PLAN": {
            "LOW": {"max_observations": 4, "max_attempts": 2, "max_discoveries": 6},
            "MEDIUM": {"max_observations": 6, "max_attempts": 3, "max_discoveries": 8},
            "HIGH": {"max_observations": 8, "max_attempts": 5, "max_discoveries": 10},
        },
        "EXECUTE": {
            "LOW": {"max_observations": 6, "max_attempts": 4, "max_discoveries": 8},
            "MEDIUM": {"max_observations": 10, "max_attempts": 6, "max_discoveries": 12},
            "HIGH": {"max_observations": 14, "max_attempts": 8, "max_discoveries": 16},
        },
        "VERIFY": {
            "LOW": {"max_observations": 5, "max_attempts": 3, "max_discoveries": 6},
            "MEDIUM": {"max_observations": 8, "max_attempts": 5, "max_discoveries": 8},
            "HIGH": {"max_observations": 12, "max_attempts": 7, "max_discoveries": 12},
        },
        "RECOVER": {
            "LOW": {"max_observations": 8, "max_attempts": 5, "max_discoveries": 10},
            "MEDIUM": {"max_observations": 12, "max_attempts": 8, "max_discoveries": 16},
            "HIGH": {"max_observations": 16, "max_attempts": 10, "max_discoveries": 20},
        },
    })

    def to_dict(self) -> dict[str, Any]:
        return {
            "medium_pressure_threshold": self.medium_pressure_threshold,
            "high_pressure_threshold": self.high_pressure_threshold,
            "verification_failure_weight": self.verification_failure_weight,
            "consecutive_failed_attempt_weight": self.consecutive_failed_attempt_weight,
            "active_error_weight": self.active_error_weight,
            "repeated_failure_weight": self.repeated_failure_weight,
            "strongly_repeated_threshold": self.strongly_repeated_threshold,
            "strongly_repeated_bonus": self.strongly_repeated_bonus,
            "limits": self.limits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptivePolicyConfig:
        return cls(
            medium_pressure_threshold=int(data.get("medium_pressure_threshold", 3)),
            high_pressure_threshold=int(data.get("high_pressure_threshold", 6)),
            verification_failure_weight=int(data.get("verification_failure_weight", 2)),
            consecutive_failed_attempt_weight=int(data.get("consecutive_failed_attempt_weight", 1)),
            active_error_weight=int(data.get("active_error_weight", 1)),
            repeated_failure_weight=int(data.get("repeated_failure_weight", 1)),
            strongly_repeated_threshold=int(data.get("strongly_repeated_threshold", 4)),
            strongly_repeated_bonus=int(data.get("strongly_repeated_bonus", 1)),
            limits=data.get("limits", cls().limits),
        )


@dataclass
class ContextProfile:
    """
    Deterministic specification of what context should be included and how much
    based on task lifecycle phase and recovery pressure.
    """
    phase: str
    pressure_level: PressureLevel
    pressure_score: int
    repository_scope: RepositoryScope
    max_observations: int
    max_attempts: int
    max_discoveries: int
    include_repository_overview: bool
    include_symbols: bool
    include_local_imports: bool
    include_successful_attempts: bool
    include_failed_attempts: bool
    include_repeated_failures: bool
    include_verification: bool
    include_telemetry: bool
    reasons: list[str] = field(default_factory=list)

    def to_context_config(self) -> ContextConfig:
        """
        Map profile values into the existing B2 ContextConfig.
        Preserves B2 ContextBuilder compatibility without reimplementing it.
        """
        return ContextConfig(
            max_observations=self.max_observations,
            max_attempts=self.max_attempts,
            max_discoveries=self.max_discoveries,
            include_successful_attempts=self.include_successful_attempts,
            include_failed_attempts=self.include_failed_attempts,
            include_repeated_failures=self.include_repeated_failures,
            include_verification=self.include_verification,
            include_telemetry=self.include_telemetry,
            include_repository_overview=self.include_repository_overview,
            include_symbols=self.include_symbols,
            include_local_imports=self.include_local_imports,
            repository_scope=self.repository_scope,
            include_raw_output=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "pressure_level": self.pressure_level.value,
            "pressure_score": self.pressure_score,
            "repository_scope": self.repository_scope.value,
            "max_observations": self.max_observations,
            "max_attempts": self.max_attempts,
            "max_discoveries": self.max_discoveries,
            "include_repository_overview": self.include_repository_overview,
            "include_symbols": self.include_symbols,
            "include_local_imports": self.include_local_imports,
            "include_successful_attempts": self.include_successful_attempts,
            "include_failed_attempts": self.include_failed_attempts,
            "include_repeated_failures": self.include_repeated_failures,
            "include_verification": self.include_verification,
            "include_telemetry": self.include_telemetry,
            "reasons": list(self.reasons),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextProfile:
        return cls(
            phase=str(data.get("phase", "PLAN")),
            pressure_level=PressureLevel(data.get("pressure_level", "LOW")),
            pressure_score=int(data.get("pressure_score", 0)),
            repository_scope=RepositoryScope(data.get("repository_scope", "FOCUSED")),
            max_observations=int(data.get("max_observations", 8)),
            max_attempts=int(data.get("max_attempts", 5)),
            max_discoveries=int(data.get("max_discoveries", 12)),
            include_repository_overview=bool(data.get("include_repository_overview", False)),
            include_symbols=bool(data.get("include_symbols", False)),
            include_local_imports=bool(data.get("include_local_imports", False)),
            include_successful_attempts=bool(data.get("include_successful_attempts", True)),
            include_failed_attempts=bool(data.get("include_failed_attempts", False)),
            include_repeated_failures=bool(data.get("include_repeated_failures", False)),
            include_verification=bool(data.get("include_verification", False)),
            include_telemetry=bool(data.get("include_telemetry", False)),
            reasons=list(data.get("reasons", [])),
        )

    def render_text(self) -> str:
        """
        Deterministic, human-readable text representation for debugging and observability.
        """
        lines = [
            f"Phase: {self.phase}",
            f"Pressure: {self.pressure_level.value} ({self.pressure_score})",
            f"Repository Scope: {self.repository_scope.value}",
            "",
            "Reasons:",
        ]
        for r in self.reasons:
            lines.append(f"- {r}")

        lines.append("")
        lines.append("Context Policy:")
        lines.append(f"- max observations: {self.max_observations}")
        lines.append(f"- max attempts: {self.max_attempts}")
        lines.append(f"- max discoveries: {self.max_discoveries}")
        lines.append(f"- repository overview: {'enabled' if self.include_repository_overview else 'disabled'}")
        lines.append(f"- symbols: {'enabled' if self.include_symbols else 'disabled'}")
        lines.append(f"- local import neighbors: {'enabled' if self.include_local_imports else 'disabled'}")
        lines.append(f"- successful attempts: {'enabled' if self.include_successful_attempts else 'disabled'}")
        lines.append(f"- failed attempts: {'enabled' if self.include_failed_attempts else 'disabled'}")
        lines.append(f"- repeated failures: {'enabled' if self.include_repeated_failures else 'disabled'}")
        lines.append(f"- verification: {'enabled' if self.include_verification else 'disabled'}")
        lines.append(f"- telemetry: {'enabled' if self.include_telemetry else 'disabled'}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render_text()


class AdaptiveContextPolicy:
    """
    Deterministic policy engine that calculates recovery pressure and derives
    phase-aware, pressure-adapted context profiles from TaskState.
    """

    def __init__(self, config: Optional[AdaptivePolicyConfig] = None) -> None:
        self.config = config or AdaptivePolicyConfig()

    def calculate_pressure(
        self,
        state_or_manager: Any,
    ) -> tuple[int, PressureLevel, list[str]]:
        """
        Calculate numeric pressure score, PressureLevel, and explainable reasons from TaskState.
        Never mutates the underlying TaskState.
        """
        state = self._resolve_state(state_or_manager)
        cfg = self.config

        score = 0
        reasons: list[str] = []

        # 1. Latest verification failure
        if state.verification is not None and not state.verification.success:
            score += cfg.verification_failure_weight
            reasons.append(f"verification failed (+{cfg.verification_failure_weight})")

        # 2. Consecutive failed attempts from the end of attempt history
        consecutive_failures = 0
        for attempt in reversed(state.attempts):
            if not attempt.success:
                consecutive_failures += 1
            else:
                # Successful recent attempt resets the failure streak
                break

        if consecutive_failures > 0:
            att_points = consecutive_failures * cfg.consecutive_failed_attempt_weight
            score += att_points
            plural = "s" if consecutive_failures > 1 else ""
            reasons.append(f"{consecutive_failures} consecutive failed attempt{plural} (+{att_points})")

        # 3. Active current errors
        if state.current_errors:
            score += cfg.active_error_weight
            reasons.append(f"active errors present (+{cfg.active_error_weight})")

        # 4. Repeated failures and strongly repeated occurrences
        for failure in state.failures:
            if failure.occurrence_count >= 2:
                points = cfg.repeated_failure_weight
                if failure.occurrence_count >= cfg.strongly_repeated_threshold:
                    points += cfg.strongly_repeated_bonus
                score += points
                reasons.append(
                    f"repeated failure {failure.error_signature} x{failure.occurrence_count} (+{points})"
                )

        if score == 0:
            reasons.append("normal execution, no pressure indicators")

        # Categorize pressure level
        if score >= cfg.high_pressure_threshold:
            level = PressureLevel.HIGH
        elif score >= cfg.medium_pressure_threshold:
            level = PressureLevel.MEDIUM
        else:
            level = PressureLevel.LOW

        return score, level, reasons

    def evaluate(
        self,
        state_or_manager: Any,
        repo_index: Optional[RepositoryIndex] = None,
    ) -> ContextProfile:
        """
        Evaluate task state and produce an adaptive ContextProfile.
        Never mutates the underlying TaskState.
        """
        state = self._resolve_state(state_or_manager)
        cfg = self.config

        # 1. Calculate pressure
        score, level, reasons = self.calculate_pressure(state)

        # 2. Extract phase
        raw_phase = state.phase.value if isinstance(state.phase, Enum) else str(state.phase)
        phase_str = raw_phase.upper()
        if phase_str not in cfg.limits:
            phase_str = "PLAN"

        # 3. Resolve limits for (phase, pressure_level)
        limits_map = cfg.limits[phase_str][level.value]
        max_obs = limits_map["max_observations"]
        max_att = limits_map["max_attempts"]
        max_disc = limits_map["max_discoveries"]

        # 4. Check repository availability
        has_repo = repo_index is not None and repo_index.file_count > 0
        has_symbols = has_repo and bool(repo_index.file_symbols)
        has_local_imports = has_repo and any(bool(fs.local_imports) for fs in repo_index.file_symbols.values())

        # 5. Determine repository scope and content eligibility
        if phase_str == "PLAN":
            scope = RepositoryScope.BROAD
            include_repo_overview = has_repo
            include_symbols = has_symbols
            include_local_imports = has_local_imports
            include_success_att = (level != PressureLevel.LOW)
            include_failed_att = False
            include_repeated_failures = False
            include_verification = False
            include_telemetry = False

        elif phase_str == "EXECUTE":
            scope = RepositoryScope.BROAD if level == PressureLevel.HIGH else RepositoryScope.FOCUSED
            include_repo_overview = (level == PressureLevel.HIGH and has_repo)
            include_symbols = has_symbols
            include_local_imports = (level != PressureLevel.LOW and has_local_imports)
            include_success_att = True
            include_failed_att = True
            include_repeated_failures = (level != PressureLevel.LOW)
            include_verification = (level != PressureLevel.LOW and state.verification is not None)
            include_telemetry = True

        elif phase_str == "VERIFY":
            scope = RepositoryScope.FOCUSED if level == PressureLevel.HIGH else RepositoryScope.MINIMAL
            include_repo_overview = False
            include_symbols = (level == PressureLevel.HIGH and has_symbols)
            include_local_imports = False
            include_success_att = True
            include_failed_att = True
            include_repeated_failures = (level != PressureLevel.LOW)
            include_verification = True
            include_telemetry = True

        else:  # RECOVER
            scope = RepositoryScope.FOCUSED if level == PressureLevel.LOW else RepositoryScope.BROAD
            include_repo_overview = (level != PressureLevel.LOW and has_repo)
            include_symbols = has_symbols
            include_local_imports = has_local_imports
            include_success_att = True
            include_failed_att = True
            include_repeated_failures = True
            include_verification = True
            include_telemetry = True

        return ContextProfile(
            phase=phase_str,
            pressure_level=level,
            pressure_score=score,
            repository_scope=scope,
            max_observations=max_obs,
            max_attempts=max_att,
            max_discoveries=max_disc,
            include_repository_overview=include_repo_overview,
            include_symbols=include_symbols,
            include_local_imports=include_local_imports,
            include_successful_attempts=include_success_att,
            include_failed_attempts=include_failed_att,
            include_repeated_failures=include_repeated_failures,
            include_verification=include_verification,
            include_telemetry=include_telemetry,
            reasons=reasons,
        )

    @staticmethod
    def _resolve_state(state_or_manager: Any) -> TaskState:
        """Extract TaskState without mutating it."""
        if hasattr(state_or_manager, "get_state"):
            return state_or_manager.get_state()
        if hasattr(state_or_manager, "state"):
            return state_or_manager.state
        return state_or_manager
