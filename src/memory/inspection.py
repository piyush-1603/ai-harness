from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class ObservationInspection:
    index: int
    type: str
    source: str
    summary: str
    files: list[str]
    timestamp: str
    raw_output_preview: Optional[str]
    output_ref: Optional[str]
    raw_output_chars: int
    has_external_artifact: bool
    full_output: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.type,
            "source": self.source,
            "summary": self.summary,
            "files": list(self.files),
            "timestamp": self.timestamp,
            "raw_output_preview": self.raw_output_preview,
            "output_ref": self.output_ref,
            "raw_output_chars": self.raw_output_chars,
            "has_external_artifact": self.has_external_artifact,
            "full_output": self.full_output,
        }


@dataclass
class FailureInspection:
    index: int
    error_signature: str
    summary: str
    action: str
    files: list[str]
    occurrence_count: int
    resolved: bool
    first_seen: str
    last_seen: str
    resolved_at: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "error_signature": self.error_signature,
            "summary": self.summary,
            "action": self.action,
            "files": list(self.files),
            "occurrence_count": self.occurrence_count,
            "resolved": self.resolved,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "resolved_at": self.resolved_at,
        }


@dataclass
class ArtifactInspection:
    ref: str
    content: str
    chars: int
    exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "content": self.content,
            "chars": self.chars,
            "exists": self.exists,
        }
