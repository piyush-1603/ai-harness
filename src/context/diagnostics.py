from dataclasses import dataclass
from typing import Optional, Any, Union

from src.context.budget import ContextBudgeter
from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import AdaptiveContextPolicy
from src.context.scanner import RepositoryIndex
from src.memory.manager import MemoryManager
from src.memory.models import TaskState

def get_priority_label(tier: int) -> str:
    mapping = {
        5: "CRITICAL",
        4: "VERY_HIGH",
        3: "HIGH",
        2: "MEDIUM",
        1: "LOW",
        0: "VERY_LOW"
    }
    return mapping.get(tier, f"UNKNOWN_{tier}")

@dataclass
class CandidateDiagnostic:
    id: str
    category: str
    selected: bool
    priority_tier: int
    priority_label: str
    score: float
    estimated_tokens: int
    files: list[str]
    scoring_reasons: list[str]
    decision_reason: str
    recency_index: Optional[int] = None
    output_ref: Optional[str] = None
    raw_output_chars: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "selected": self.selected,
            "priority_tier": self.priority_tier,
            "priority_label": self.priority_label,
            "score": self.score,
            "estimated_tokens": self.estimated_tokens,
            "files": list(self.files),
            "scoring_reasons": list(self.scoring_reasons),
            "decision_reason": self.decision_reason,
            "recency_index": self.recency_index,
            "output_ref": self.output_ref,
            "raw_output_chars": self.raw_output_chars,
        }

@dataclass
class PolicyDiagnostic:
    phase: str
    pressure_level: str
    pressure_score: int
    pressure_reasons: list[str]
    repository_scope: str
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "pressure_level": self.pressure_level,
            "pressure_score": self.pressure_score,
            "pressure_reasons": list(self.pressure_reasons),
            "repository_scope": self.repository_scope,
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
        }

@dataclass
class BudgetDiagnostic:
    max_tokens: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    was_reduced: bool
    hard_truncated: bool
    hard_truncation_note: Optional[str]
    reductions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "was_reduced": self.was_reduced,
            "hard_truncated": self.hard_truncated,
            "hard_truncation_note": self.hard_truncation_note,
            "reductions": list(self.reductions),
        }

@dataclass
class ContextDiagnosticsReport:
    policy: PolicyDiagnostic
    budget: BudgetDiagnostic
    core_tokens: int
    optional_token_budget: int
    optional_tokens_used: int
    candidate_count: int
    selected_count: int
    skipped_count: int
    candidates: list[CandidateDiagnostic]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "budget": self.budget.to_dict(),
            "core_tokens": self.core_tokens,
            "optional_token_budget": self.optional_token_budget,
            "optional_tokens_used": self.optional_tokens_used,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "skipped_count": self.skipped_count,
            "candidates": [c.to_dict() for c in self.candidates],
        }

    def render_text(self) -> str:
        lines = ["Context Diagnostics", "===================", "", "Policy", "------"]
        lines.append(f"Phase: {self.policy.phase}")
        lines.append(f"Pressure: {self.policy.pressure_level} ({self.policy.pressure_score})")
        lines.append(f"Repository Scope: {self.policy.repository_scope}")
        if self.policy.pressure_reasons:
            lines.append("\nPressure Reasons:")
            for r in self.policy.pressure_reasons:
                lines.append(f"- {r}")

        lines.append("\nBudget")
        lines.append("------")
        lines.append(f"Max Tokens: {self.budget.max_tokens}")
        lines.append(f"Before Budgeting: {self.budget.estimated_tokens_before}")
        lines.append(f"After Budgeting: {self.budget.estimated_tokens_after}")
        lines.append(f"Reduced: {'yes' if self.budget.was_reduced else 'no'}")
        lines.append(f"Hard Truncated: {'yes' if self.budget.hard_truncated else 'no'}")
        if self.budget.hard_truncation_note:
            lines.append(f"Note: {self.budget.hard_truncation_note}")

        if self.budget.reductions:
            lines.append("\nReductions:")
            for r in self.budget.reductions:
                lines.append(f"- {r}")

        lines.append("\nPacking")
        lines.append("-------")
        lines.append(f"Core Tokens: {self.core_tokens}")
        lines.append(f"Optional Budget: {self.optional_token_budget}")
        lines.append(f"Optional Used: {self.optional_tokens_used}")
        lines.append(f"\nCandidates: {self.candidate_count}")
        lines.append(f"Selected: {self.selected_count}")
        lines.append(f"Skipped: {self.skipped_count}")

        selected = [c for c in self.candidates if c.selected]
        if selected:
            lines.append("\nSelected Candidates")
            lines.append("-------------------")
            for c in selected:
                lines.append(f"[{c.priority_label}] {c.id}")
                lines.append(f"Score: {c.score}")
                lines.append(f"Tokens: {c.estimated_tokens}")
                if c.output_ref:
                    lines.append(f"Artifact: {c.output_ref}")
                    lines.append(f"Original chars: {c.raw_output_chars}")
                    lines.append("Context uses bounded preview only")
                if c.scoring_reasons:
                    lines.append("Reasons:")
                    for r in c.scoring_reasons:
                        lines.append(f"  - {r}")
                lines.append("")

        skipped = [c for c in self.candidates if not c.selected]
        if skipped:
            lines.append("Skipped Candidates")
            lines.append("------------------")
            for c in skipped:
                lines.append(f"[{c.priority_label}] {c.id}")
                lines.append(f"Reason: {c.decision_reason}")
                lines.append("")

        return "\n".join(lines).strip()

class ContextDiagnosticsEngine:
    def explain(
        self,
        state_or_manager: Union[TaskState, MemoryManager],
        repository_index: Optional[RepositoryIndex] = None,
        policy: Optional[AdaptiveContextPolicy] = None,
        budgeter: Optional[ContextBudgeter] = None,
        max_context_tokens: Optional[int] = None,
    ) -> ContextDiagnosticsReport:
        if isinstance(state_or_manager, MemoryManager):
            state = state_or_manager.get_state()
        else:
            state = state_or_manager

        if policy is None:
            policy = AdaptiveContextPolicy()
        if budgeter is None:
            budgeter = ContextBudgeter()

        # Evaluate policy
        profile = policy.evaluate(state, repository_index)

        # Run budgeter
        budget_result = budgeter.fit(
            state,
            config=profile,
            repository_index=repository_index,
            max_context_tokens=max_context_tokens
        )

        final_config_dict = budget_result.final_config or profile.to_context_config().to_dict()
        final_config = ContextConfig.from_dict(final_config_dict)

        # Build with the final configuration to get exact candidate scoring and packing report
        builder = ContextBuilder(config=final_config, repository_index=repository_index)
        bundle, packing_report = builder.build_with_report(state, max_tokens=budget_result.max_tokens)

        # Create PolicyDiagnostic
        pd = PolicyDiagnostic(
            phase=profile.phase.value if hasattr(profile.phase, "value") else str(profile.phase),
            pressure_level=profile.pressure_level.value if hasattr(profile.pressure_level, "value") else str(profile.pressure_level),
            pressure_score=profile.pressure_score,
            pressure_reasons=list(profile.reasons),
            repository_scope=profile.repository_scope.value if hasattr(profile.repository_scope, "value") else str(profile.repository_scope),
            max_observations=profile.max_observations,
            max_attempts=profile.max_attempts,
            max_discoveries=profile.max_discoveries,
            include_repository_overview=profile.include_repository_overview,
            include_symbols=profile.include_symbols,
            include_local_imports=profile.include_local_imports,
            include_successful_attempts=profile.include_successful_attempts,
            include_failed_attempts=profile.include_failed_attempts,
            include_repeated_failures=profile.include_repeated_failures,
            include_verification=profile.include_verification,
            include_telemetry=profile.include_telemetry,
        )

        hard_truncation_note = None
        if budget_result.hard_truncated:
            hard_truncation_note = "Final rendered text was character-truncated after structured reductions; candidate decisions describe the pre-truncation structured bundle."

        bd = BudgetDiagnostic(
            max_tokens=budget_result.max_tokens,
            estimated_tokens_before=budget_result.estimated_tokens_before,
            estimated_tokens_after=budget_result.estimated_tokens_after,
            was_reduced=budget_result.was_reduced,
            hard_truncated=budget_result.hard_truncated,
            hard_truncation_note=hard_truncation_note,
            reductions=list(budget_result.reductions),
        )

        candidates = []
        
        # Build lookup for artifacts using exact slice that ContextBuilder used
        if final_config.max_observations > 0:
            diagnostic_obs_slice = state.recent_observations[-final_config.max_observations:]
        else:
            diagnostic_obs_slice = []

        obs_map = {f"obs_{i}": o for i, o in enumerate(diagnostic_obs_slice)}

        def extract_candidate(c_dict, selected: bool):
            cid = c_dict["id"]
            output_ref = None
            raw_output_chars = None
            if cid in obs_map:
                o = obs_map[cid]
                if getattr(o, "output_ref", None):
                    output_ref = o.output_ref
                    raw_output_chars = o.raw_output_chars

            return CandidateDiagnostic(
                id=cid,
                category=c_dict["category"],
                selected=selected,
                priority_tier=c_dict["priority_tier"],
                priority_label=get_priority_label(c_dict["priority_tier"]),
                score=c_dict["score"],
                estimated_tokens=c_dict["estimated_tokens"],
                files=c_dict.get("files", []),
                scoring_reasons=c_dict.get("scoring_reasons", []),
                decision_reason=c_dict.get("decision_reason", ""),
                recency_index=c_dict.get("recency_index"),
                output_ref=output_ref,
                raw_output_chars=raw_output_chars,
            )

        for c_dict in packing_report.selected:
            candidates.append(extract_candidate(c_dict, selected=True))
            
        for c_dict in packing_report.skipped:
            candidates.append(extract_candidate(c_dict, selected=False))

        # Maintain deterministic order
        def sort_key(c: CandidateDiagnostic):
            return (
                not c.selected,
                -c.priority_tier,
                -c.score,
                -(c.recency_index if c.recency_index is not None else -1),
                c.category,
                c.id
            )
        candidates.sort(key=sort_key)

        return ContextDiagnosticsReport(
            policy=pd,
            budget=bd,
            core_tokens=packing_report.core_tokens,
            optional_token_budget=budget_result.max_tokens - packing_report.core_tokens,
            optional_tokens_used=packing_report.optional_tokens_used,
            candidate_count=packing_report.candidate_count,
            selected_count=packing_report.selected_count,
            skipped_count=packing_report.skipped_count,
            candidates=candidates,
        )
