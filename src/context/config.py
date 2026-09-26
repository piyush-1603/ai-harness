"""Configuration for deterministic context selection and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ContextConfig:
    """
    Configurable parameters for extracting model-ready context from TaskState.
    """
    max_observations: int = 8
    max_attempts: int = 5
    include_raw_output: bool = False
    include_successful_attempts: bool = True
    min_failure_occurrences: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_observations": self.max_observations,
            "max_attempts": self.max_attempts,
            "include_raw_output": self.include_raw_output,
            "include_successful_attempts": self.include_successful_attempts,
            "min_failure_occurrences": self.min_failure_occurrences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextConfig:
        return cls(
            max_observations=int(data.get("max_observations", 8)),
            max_attempts=int(data.get("max_attempts", 5)),
            include_raw_output=bool(data.get("include_raw_output", False)),
            include_successful_attempts=bool(data.get("include_successful_attempts", True)),
            min_failure_occurrences=int(data.get("min_failure_occurrences", 2)),
        )
