"""Configuration for deterministic context selection and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from src.context.policy import RepositoryScope


@dataclass
class ContextConfig:
    """
    Configurable parameters for extracting model-ready context from TaskState and RepositoryIndex.
    """
    max_observations: int = 8
    max_attempts: int = 5
    max_discoveries: int = 12
    include_raw_output: bool = False
    include_successful_attempts: bool = True
    include_failed_attempts: bool = True
    include_repeated_failures: bool = True
    include_verification: bool = True
    include_telemetry: bool = True
    include_repository_overview: bool = False
    include_symbols: bool = False
    include_local_imports: bool = False
    repository_scope: Optional[Union[RepositoryScope, str]] = None
    min_failure_occurrences: int = 2
    max_context_tokens: Optional[int] = None
    chars_per_token: int = 4

    def to_dict(self) -> dict[str, Any]:
        scope_val = None
        if self.repository_scope is not None:
            scope_val = (
                self.repository_scope.value
                if hasattr(self.repository_scope, "value")
                else str(self.repository_scope)
            )
        return {
            "max_observations": self.max_observations,
            "max_attempts": self.max_attempts,
            "max_discoveries": self.max_discoveries,
            "include_raw_output": self.include_raw_output,
            "include_successful_attempts": self.include_successful_attempts,
            "include_failed_attempts": self.include_failed_attempts,
            "include_repeated_failures": self.include_repeated_failures,
            "include_verification": self.include_verification,
            "include_telemetry": self.include_telemetry,
            "include_repository_overview": self.include_repository_overview,
            "include_symbols": self.include_symbols,
            "include_local_imports": self.include_local_imports,
            "repository_scope": scope_val,
            "min_failure_occurrences": self.min_failure_occurrences,
            "max_context_tokens": self.max_context_tokens,
            "chars_per_token": self.chars_per_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextConfig:
        return cls(
            max_observations=int(data.get("max_observations", 8)),
            max_attempts=int(data.get("max_attempts", 5)),
            max_discoveries=int(data.get("max_discoveries", 12)),
            include_raw_output=bool(data.get("include_raw_output", False)),
            include_successful_attempts=bool(data.get("include_successful_attempts", True)),
            include_failed_attempts=bool(data.get("include_failed_attempts", True)),
            include_repeated_failures=bool(data.get("include_repeated_failures", True)),
            include_verification=bool(data.get("include_verification", True)),
            include_telemetry=bool(data.get("include_telemetry", True)),
            include_repository_overview=bool(data.get("include_repository_overview", False)),
            include_symbols=bool(data.get("include_symbols", False)),
            include_local_imports=bool(data.get("include_local_imports", False)),
            repository_scope=data.get("repository_scope"),
            min_failure_occurrences=int(data.get("min_failure_occurrences", 2)),
            max_context_tokens=data.get("max_context_tokens"),
            chars_per_token=int(data.get("chars_per_token", 4)),
        )
