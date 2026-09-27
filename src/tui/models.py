from dataclasses import dataclass, field
from typing import Optional

@dataclass
class RelevantFileData:
    path: str
    status: str

@dataclass
class ActivityItem:
    timestamp: str
    kind: str
    label: str
    description: str

@dataclass
class OverviewData:
    task_id: str
    task: str
    phase: str
    status: str
    iteration: int
    model_calls: int
    tool_calls: int
    pressure_level: str
    pressure_score: int
    repository_scope: str
    context_tokens: int
    context_limit: int
    active_failure: Optional[str]
    active_failure_count: int
    relevant_files: list[RelevantFileData]
    recent_activity: list[ActivityItem]

@dataclass
class ContextCandidateData:
    id: str
    category: str
    priority: str
    score: float
    estimated_tokens: int
    files: list[str]
    decision: str
    reasons: list[str]
    selected: bool

@dataclass
class ContextData:
    pressure_level: str
    repository_scope: str
    max_tokens: int
    final_tokens: int
    reductions: list[str]
    selected_candidates: list[ContextCandidateData]
    skipped_candidates: list[ContextCandidateData]

@dataclass
class MemoryItemData:
    kind: str
    label: str
    summary: str
    count: int = 1
    ref: Optional[str] = None
    original_chars: Optional[int] = None
    preview: Optional[str] = None
    exists: Optional[bool] = None

@dataclass
class MemoryData:
    observations: list[MemoryItemData]
    attempts: list[MemoryItemData]
    failures: list[MemoryItemData]
    discoveries: list[MemoryItemData]
    artifacts: list[MemoryItemData]

@dataclass
class RepositoryFileData:
    path: str
    role: str
    touched: bool
    relevant: bool
    symbols: list[str]
    imports: list[str]

@dataclass
class RepositoryData:
    files: list[RepositoryFileData]

@dataclass
class EventItemData:
    timestamp: str
    kind: str
    summary: str
