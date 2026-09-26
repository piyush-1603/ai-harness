"""Data models and schemas for task state and memory subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Optional, Union


def now_iso() -> str:
    """Return the current UTC timestamp formatted as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(ts: Any) -> str:
    """Format any timestamp representation into an ISO 8601 string."""
    if ts is None:
        return now_iso()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


class Phase(str, Enum):
    """Lifecycle phases for task execution."""
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    RECOVER = "RECOVER"


class TaskStatus(str, Enum):
    """Status flags for the task state."""
    RUNNING = "RUNNING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class EventType(str, Enum):
    """Event types recorded in the append-only event log."""
    TASK_INITIALIZED = "TASK_INITIALIZED"
    PHASE_CHANGED = "PHASE_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PLAN_UPDATED = "PLAN_UPDATED"
    HYPOTHESIS_UPDATED = "HYPOTHESIS_UPDATED"
    RELEVANT_FILE_ADDED = "RELEVANT_FILE_ADDED"
    FILE_TOUCHED = "FILE_TOUCHED"
    DISCOVERY_ADDED = "DISCOVERY_ADDED"
    OBSERVATION_ADDED = "OBSERVATION_ADDED"
    ATTEMPT_RECORDED = "ATTEMPT_RECORDED"
    FAILURE_RECORDED = "FAILURE_RECORDED"
    FAILURE_RESOLVED = "FAILURE_RESOLVED"
    ERRORS_UPDATED = "ERRORS_UPDATED"
    VERIFICATION_UPDATED = "VERIFICATION_UPDATED"
    ITERATION_INCREMENTED = "ITERATION_INCREMENTED"
    MODEL_CALL_RECORDED = "MODEL_CALL_RECORDED"
    TOKEN_USAGE_RECORDED = "TOKEN_USAGE_RECORDED"
    TOOL_CALL_RECORDED = "TOOL_CALL_RECORDED"


@dataclass
class Observation:
    """Record of an environmental observation or tool execution output."""
    type: str
    source: str
    summary: str
    raw_output: Optional[str] = None
    files: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.timestamp = _format_timestamp(self.timestamp)
        if not isinstance(self.files, list):
            self.files = list(self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "source": self.source,
            "summary": self.summary,
            "raw_output": self.raw_output,
            "files": list(self.files),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        return cls(
            type=data["type"],
            source=data["source"],
            summary=data["summary"],
            raw_output=data.get("raw_output"),
            files=list(data.get("files", [])),
            timestamp=data.get("timestamp", now_iso()),
        )


@dataclass
class Attempt:
    """Record of an action or solution attempt made during execution."""
    id: str
    hypothesis: Optional[str] = None
    action: str = ""
    files_touched: list[str] = field(default_factory=list)
    result: str = ""
    success: bool = False
    iteration: int = 0
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.timestamp = _format_timestamp(self.timestamp)
        if not isinstance(self.files_touched, list):
            self.files_touched = list(self.files_touched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hypothesis": self.hypothesis,
            "action": self.action,
            "files_touched": list(self.files_touched),
            "result": self.result,
            "success": self.success,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attempt:
        return cls(
            id=data["id"],
            hypothesis=data.get("hypothesis"),
            action=data.get("action", ""),
            files_touched=list(data.get("files_touched", [])),
            result=data.get("result", ""),
            success=bool(data.get("success", False)),
            iteration=int(data.get("iteration", 0)),
            timestamp=data.get("timestamp", now_iso()),
        )


@dataclass
class Failure:
    """Record of an error or failure encountered, with occurrence deduplication."""
    error_signature: str
    summary: str
    action: str
    files: list[str] = field(default_factory=list)
    occurrence_count: int = 1
    first_seen: str = field(default_factory=now_iso)
    last_seen: str = field(default_factory=now_iso)
    resolved: bool = False
    resolved_at: Optional[str] = None

    def __post_init__(self) -> None:
        self.first_seen = _format_timestamp(self.first_seen)
        self.last_seen = _format_timestamp(self.last_seen)
        if self.resolved_at:
            self.resolved_at = _format_timestamp(self.resolved_at)
        if not isinstance(self.files, list):
            self.files = list(self.files)
        self.resolved = bool(self.resolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_signature": self.error_signature,
            "summary": self.summary,
            "action": self.action,
            "files": list(self.files),
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Failure:
        first = data.get("first_seen", now_iso())
        return cls(
            error_signature=data["error_signature"],
            summary=data.get("summary", ""),
            action=data.get("action", ""),
            files=list(data.get("files", [])),
            occurrence_count=int(data.get("occurrence_count", 1)),
            first_seen=first,
            last_seen=data.get("last_seen", first),
            resolved=bool(data.get("resolved", False)),
            resolved_at=data.get("resolved_at"),
        )


@dataclass
class Discovery:
    """Record of a learned fact, insight, or evidence found in the codebase."""
    statement: str
    evidence: str
    files: list[str] = field(default_factory=list)
    confidence: float = 1.0
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.timestamp = _format_timestamp(self.timestamp)
        if not isinstance(self.files, list):
            self.files = list(self.files)
        self.confidence = float(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence": self.evidence,
            "files": list(self.files),
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Discovery:
        return cls(
            statement=data["statement"],
            evidence=data.get("evidence", ""),
            files=list(data.get("files", [])),
            confidence=float(data.get("confidence", 1.0)),
            timestamp=data.get("timestamp", now_iso()),
        )


@dataclass
class TokenUsage:
    """Accumulated token usage metrics."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        self.input_tokens = int(self.input_tokens)
        self.output_tokens = int(self.output_tokens)
        if self.total_tokens == 0 and (self.input_tokens != 0 or self.output_tokens != 0):
            self.total_tokens = self.input_tokens + self.output_tokens
        else:
            self.total_tokens = int(self.total_tokens)

    def add(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens += int(input_tokens)
        self.output_tokens += int(output_tokens)
        self.total_tokens = self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsage:
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
        )


@dataclass
class VerificationResult:
    """Structured report of test or verification outcome."""
    success: bool
    summary: str
    tests_passed: Union[list[str], int] = field(default_factory=list)
    tests_failed: Union[list[str], int] = field(default_factory=list)
    details: Any = None
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.timestamp = _format_timestamp(self.timestamp)
        self.success = bool(self.success)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "summary": self.summary,
            "tests_passed": list(self.tests_passed) if isinstance(self.tests_passed, list) else self.tests_passed,
            "tests_failed": list(self.tests_failed) if isinstance(self.tests_failed, list) else self.tests_failed,
            "details": self.details,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        return cls(
            success=bool(data["success"]),
            summary=data.get("summary", ""),
            tests_passed=data.get("tests_passed", []),
            tests_failed=data.get("tests_failed", []),
            details=data.get("details"),
            timestamp=data.get("timestamp", now_iso()),
        )


@dataclass
class Event:
    """Lightweight append-only event record for state mutations."""
    type: str
    timestamp: str = field(default_factory=now_iso)
    iteration: int = 0
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timestamp = _format_timestamp(self.timestamp)
        self.iteration = int(self.iteration)
        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "iteration": self.iteration,
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            type=data["type"],
            timestamp=data.get("timestamp", now_iso()),
            iteration=int(data.get("iteration", 0)),
            summary=data.get("summary", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TaskState:
    """
    Structured task state acting as single source of truth for a single task.
    """
    task_id: str
    task: str
    phase: Phase = Phase.PLAN
    status: TaskStatus = TaskStatus.RUNNING
    plan: list[str] = field(default_factory=list)
    current_hypothesis: Optional[str] = None
    relevant_files: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    discoveries: list[Discovery] = field(default_factory=list)
    recent_observations: list[Observation] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    current_errors: list[str] = field(default_factory=list)
    verification: Optional[VerificationResult] = None
    iteration: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.created_at = _format_timestamp(self.created_at)
        self.updated_at = _format_timestamp(self.updated_at)
        if isinstance(self.phase, str):
            try:
                self.phase = Phase(self.phase.upper())
            except (ValueError, KeyError):
                self.phase = Phase[self.phase]
        if isinstance(self.status, str):
            try:
                self.status = TaskStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TaskStatus[self.status]
        if not isinstance(self.plan, list):
            self.plan = list(self.plan)
        if not isinstance(self.relevant_files, list):
            self.relevant_files = list(self.relevant_files)
        if not isinstance(self.touched_files, list):
            self.touched_files = list(self.touched_files)
        if not isinstance(self.current_errors, list):
            self.current_errors = list(self.current_errors)
        if isinstance(self.token_usage, dict):
            self.token_usage = TokenUsage.from_dict(self.token_usage)
        self.iteration = int(self.iteration)
        self.model_calls = int(self.model_calls)
        self.tool_calls = int(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "phase": self.phase.value if isinstance(self.phase, Enum) else str(self.phase),
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "plan": list(self.plan),
            "current_hypothesis": self.current_hypothesis,
            "relevant_files": list(self.relevant_files),
            "touched_files": list(self.touched_files),
            "discoveries": [d.to_dict() for d in self.discoveries],
            "recent_observations": [o.to_dict() for o in self.recent_observations],
            "attempts": [a.to_dict() for a in self.attempts],
            "failures": [f.to_dict() for f in self.failures],
            "current_errors": list(self.current_errors),
            "verification": self.verification.to_dict() if self.verification else None,
            "iteration": self.iteration,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "token_usage": self.token_usage.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskState:
        raw_phase = data.get("phase", Phase.PLAN)
        if isinstance(raw_phase, str):
            try:
                phase = Phase(raw_phase.upper())
            except (ValueError, KeyError):
                phase = Phase[raw_phase]
        else:
            phase = raw_phase

        raw_status = data.get("status", TaskStatus.RUNNING)
        if isinstance(raw_status, str):
            try:
                status = TaskStatus(raw_status.upper())
            except (ValueError, KeyError):
                status = TaskStatus[raw_status]
        else:
            status = raw_status

        verification_data = data.get("verification")
        verification = (
            VerificationResult.from_dict(verification_data)
            if verification_data is not None
            else None
        )

        raw_token_usage = data.get("token_usage", {})
        token_usage = (
            TokenUsage.from_dict(raw_token_usage)
            if isinstance(raw_token_usage, dict)
            else (raw_token_usage if isinstance(raw_token_usage, TokenUsage) else TokenUsage())
        )

        return cls(
            task_id=data["task_id"],
            task=data["task"],
            phase=phase,
            status=status,
            plan=list(data.get("plan", [])),
            current_hypothesis=data.get("current_hypothesis"),
            relevant_files=list(data.get("relevant_files", [])),
            touched_files=list(data.get("touched_files", [])),
            discoveries=[
                Discovery.from_dict(d) if isinstance(d, dict) else d
                for d in data.get("discoveries", [])
            ],
            recent_observations=[
                Observation.from_dict(o) if isinstance(o, dict) else o
                for o in data.get("recent_observations", [])
            ],
            attempts=[
                Attempt.from_dict(a) if isinstance(a, dict) else a
                for a in data.get("attempts", [])
            ],
            failures=[
                Failure.from_dict(f) if isinstance(f, dict) else f
                for f in data.get("failures", [])
            ],
            current_errors=list(data.get("current_errors", [])),
            verification=verification,
            iteration=int(data.get("iteration", 0)),
            model_calls=int(data.get("model_calls", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            token_usage=token_usage,
            created_at=data.get("created_at", now_iso()),
            updated_at=data.get("updated_at", now_iso()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> TaskState:
        return cls.from_dict(json.loads(json_str))
