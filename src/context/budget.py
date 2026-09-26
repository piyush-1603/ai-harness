"""
Deterministic context budget enforcement for Person B context management subsystem.
Enforces hard maximum token size on model-facing context without tokenizer dependencies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import math
import os
from typing import Any, Optional, Union

from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import ContextProfile, RepositoryScope
from src.context.scanner import RepositoryIndex
from src.memory.manager import MemoryManager
from src.memory.models import TaskState

DEFAULT_MAX_CONTEXT_TOKENS: int = 24000
DEFAULT_CHARS_PER_TOKEN: int = 4
ENV_MAX_CONTEXT_TOKENS: str = "HARNESS_CONTEXT_MAX_TOKENS"
TRUNCATION_MARKER: str = "[CONTEXT TRUNCATED TO BUDGET]"


def get_default_max_context_tokens() -> int:
    """
    Retrieve default max context tokens from environment HARNESS_CONTEXT_MAX_TOKENS.
    Safely falls back to 24000 if unset, unparseable, or non-positive.
    """
    val = os.environ.get(ENV_MAX_CONTEXT_TOKENS)
    if val is not None:
        try:
            parsed = int(val.strip())
            if parsed > 0:
                return parsed
        except (ValueError, TypeError):
            pass
    return DEFAULT_MAX_CONTEXT_TOKENS


def estimate_tokens(text: str, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """
    Deterministically estimate token count from character length.
    Empty text returns 0.
    Invalid or non-positive chars_per_token is rejected with ValueError.
    """
    if not isinstance(chars_per_token, (int, float)) or chars_per_token <= 0:
        raise ValueError(f"chars_per_token must be a positive number, got {chars_per_token}")

    if not text:
        return 0

    return math.ceil(len(text) / chars_per_token)


@dataclass
class ContextBudgetConfig:
    """Configuration for deterministic context budgeting."""
    max_context_tokens: Optional[int] = None
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN

    def __post_init__(self) -> None:
        if self.max_context_tokens is None:
            self.max_context_tokens = get_default_max_context_tokens()
        else:
            try:
                self.max_context_tokens = int(self.max_context_tokens)
                if self.max_context_tokens <= 0:
                    self.max_context_tokens = get_default_max_context_tokens()
            except (ValueError, TypeError):
                self.max_context_tokens = get_default_max_context_tokens()

        if not isinstance(self.chars_per_token, (int, float)) or self.chars_per_token <= 0:
            self.chars_per_token = DEFAULT_CHARS_PER_TOKEN
        else:
            self.chars_per_token = int(self.chars_per_token)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_context_tokens": self.max_context_tokens,
            "chars_per_token": self.chars_per_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBudgetConfig:
        return cls(
            max_context_tokens=data.get("max_context_tokens"),
            chars_per_token=int(data.get("chars_per_token", DEFAULT_CHARS_PER_TOKEN)),
        )


@dataclass
class ContextBudgetResult:
    """Structured result returned by ContextBudgeter."""
    text: str
    estimated_tokens_before: int
    estimated_tokens_after: int
    max_tokens: int
    was_reduced: bool
    hard_truncated: bool
    reductions: list[str] = field(default_factory=list)
    final_config: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "max_tokens": self.max_tokens,
            "was_reduced": self.was_reduced,
            "hard_truncated": self.hard_truncated,
            "reductions": list(self.reductions),
            "final_config": self.final_config,
        }


class ContextBudgeter:
    """
    Enforces deterministic token budget limits on final model-facing context.
    Reduces lower-priority context in deterministic stages when over budget,
    and falls back to hard truncation if the core context exceeds the budget.
    """

    def __init__(
        self,
        max_context_tokens: Optional[int] = None,
        chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
        config: Optional[ContextBudgetConfig] = None,
    ) -> None:
        if config is not None:
            self.budget_config = ContextBudgetConfig(
                max_context_tokens=config.max_context_tokens,
                chars_per_token=config.chars_per_token,
            )
        else:
            self.budget_config = ContextBudgetConfig(
                max_context_tokens=max_context_tokens,
                chars_per_token=chars_per_token,
            )

    def fit(
        self,
        state: Union[TaskState, MemoryManager],
        config: Optional[Union[ContextConfig, ContextProfile]] = None,
        repository_index: Optional[RepositoryIndex] = None,
        max_context_tokens: Optional[int] = None,
    ) -> ContextBudgetResult:
        """
        Produce model-ready text guaranteed to fit within the configured token budget.
        Never mutates state, config, or repository_index.
        """
        # Extract TaskState without mutating input
        if isinstance(state, MemoryManager):
            task_state = state.get_state()
        elif isinstance(state, TaskState):
            task_state = state
        else:
            raise TypeError(f"state must be TaskState or MemoryManager, got {type(state)}")

        # Determine effective base ContextConfig without mutating input
        if isinstance(config, ContextProfile):
            base_cfg = config.to_context_config()
        elif isinstance(config, ContextConfig):
            base_cfg = config
        elif config is None:
            base_cfg = ContextConfig()
        else:
            raise TypeError(f"config must be ContextConfig, ContextProfile, or None, got {type(config)}")

        # Determine target max tokens
        if max_context_tokens is not None:
            try:
                target_max = int(max_context_tokens)
                if target_max <= 0:
                    target_max = self.budget_config.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS
            except (ValueError, TypeError):
                target_max = self.budget_config.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS
        elif getattr(base_cfg, "max_context_tokens", None) is not None:
            target_max = int(base_cfg.max_context_tokens)
        else:
            target_max = self.budget_config.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS

        chars_per_token = getattr(base_cfg, "chars_per_token", self.budget_config.chars_per_token)

        # Work strictly from a deepcopy of configuration to guarantee immutability
        effective_cfg = copy.deepcopy(base_cfg)

        def _render(cfg: ContextConfig) -> tuple[str, int]:
            builder = ContextBuilder(config=cfg, repository_index=repository_index)
            bundle = builder.build(task_state)
            rendered = bundle.render_text()
            tokens = estimate_tokens(rendered, chars_per_token=chars_per_token)
            return rendered, tokens

        # Step 0: Render initial context
        text, current_tokens = _render(effective_cfg)
        tokens_before = current_tokens

        # Check if already within budget (return byte-for-byte unchanged)
        if current_tokens <= target_max:
            return ContextBudgetResult(
                text=text,
                estimated_tokens_before=tokens_before,
                estimated_tokens_after=current_tokens,
                max_tokens=target_max,
                was_reduced=False,
                hard_truncated=False,
                reductions=[],
                final_config=effective_cfg.to_dict(),
            )

        reductions: list[str] = []

        # Reduction Stage 1: Disable telemetry
        if effective_cfg.include_telemetry:
            effective_cfg.include_telemetry = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_telemetry")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 2: Disable successful attempt history
        if effective_cfg.include_successful_attempts:
            effective_cfg.include_successful_attempts = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_successful_attempts")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 3: BROAD repository scope -> FOCUSED
        scope_val = effective_cfg.repository_scope
        if scope_val in (RepositoryScope.BROAD, "BROAD"):
            effective_cfg.repository_scope = RepositoryScope.FOCUSED
            text, current_tokens = _render(effective_cfg)
            reductions.append("contract_repository_scope_to_focused")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 4: Disable repository overview
        if effective_cfg.include_repository_overview:
            effective_cfg.include_repository_overview = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_repository_overview")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 5: Reduce max_discoveries (halve window)
        if effective_cfg.max_discoveries > 0:
            half_disc = effective_cfg.max_discoveries // 2
            effective_cfg.max_discoveries = half_disc
            text, current_tokens = _render(effective_cfg)
            reductions.append(f"reduce_max_discoveries_to_{half_disc}")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 6: Reduce max_observations (halve window)
        if effective_cfg.max_observations > 0:
            half_obs = effective_cfg.max_observations // 2
            effective_cfg.max_observations = half_obs
            text, current_tokens = _render(effective_cfg)
            reductions.append(f"reduce_max_observations_to_{half_obs}")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 7: Reduce successful/failed attempt windows (halve window)
        if effective_cfg.max_attempts > 0:
            half_att = effective_cfg.max_attempts // 2
            effective_cfg.max_attempts = half_att
            text, current_tokens = _render(effective_cfg)
            reductions.append(f"reduce_max_attempts_to_{half_att}")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 8: FOCUSED repository scope -> MINIMAL
        scope_val = effective_cfg.repository_scope
        if scope_val in (RepositoryScope.FOCUSED, "FOCUSED", RepositoryScope.BROAD, "BROAD"):
            effective_cfg.repository_scope = RepositoryScope.MINIMAL
            text, current_tokens = _render(effective_cfg)
            reductions.append("contract_repository_scope_to_minimal")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 9: Disable broader symbols
        if effective_cfg.include_symbols:
            effective_cfg.include_symbols = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_symbols")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Reduction Stage 10: Disable local imports if still required
        if effective_cfg.include_local_imports:
            effective_cfg.include_local_imports = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_local_imports")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Further progressive reductions before hard truncation:
        # Clear discoveries completely
        if effective_cfg.max_discoveries > 0:
            effective_cfg.max_discoveries = 0
            text, current_tokens = _render(effective_cfg)
            reductions.append("clear_discoveries")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Clear observations completely
        if effective_cfg.max_observations > 0:
            effective_cfg.max_observations = 0
            text, current_tokens = _render(effective_cfg)
            reductions.append("clear_observations")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Clear attempt window completely
        if effective_cfg.max_attempts > 0:
            effective_cfg.max_attempts = 0
            text, current_tokens = _render(effective_cfg)
            reductions.append("clear_attempts")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Disable failed attempts
        if effective_cfg.include_failed_attempts:
            effective_cfg.include_failed_attempts = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_failed_attempts")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Disable repeated failures
        if effective_cfg.include_repeated_failures:
            effective_cfg.include_repeated_failures = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_repeated_failures")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Disable verification
        if effective_cfg.include_verification:
            effective_cfg.include_verification = False
            text, current_tokens = _render(effective_cfg)
            reductions.append("disable_verification")
            if current_tokens <= target_max:
                return ContextBudgetResult(
                    text=text,
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=current_tokens,
                    max_tokens=target_max,
                    was_reduced=True,
                    hard_truncated=False,
                    reductions=reductions,
                    final_config=effective_cfg.to_dict(),
                )

        # Final Hard Fallback: deterministic character truncation to budget limit
        reductions.append("hard_truncation")
        max_chars = target_max * chars_per_token
        marker_suffix = f"\n\n{TRUNCATION_MARKER}"
        marker_only = TRUNCATION_MARKER

        if max_chars <= 0:
            final_text = ""
        elif len(marker_suffix) >= max_chars:
            final_text = marker_only[:max_chars]
        else:
            avail = max_chars - len(marker_suffix)
            prefix = text[:avail].rstrip()
            if prefix:
                final_text = f"{prefix}{marker_suffix}"
            else:
                final_text = marker_only[:max_chars]

        final_tokens = estimate_tokens(final_text, chars_per_token=chars_per_token)
        return ContextBudgetResult(
            text=final_text,
            estimated_tokens_before=tokens_before,
            estimated_tokens_after=final_tokens,
            max_tokens=target_max,
            was_reduced=True,
            hard_truncated=True,
            reductions=reductions,
            final_config=effective_cfg.to_dict(),
        )
