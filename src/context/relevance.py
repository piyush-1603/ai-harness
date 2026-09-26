import os
from dataclasses import dataclass, field
from typing import Optional

from src.memory.models import TaskState

# Define priority tiers
TIER_CRITICAL = 5
TIER_VERY_HIGH = 4
TIER_HIGH = 3
TIER_MEDIUM = 2
TIER_LOW = 1
TIER_VERY_LOW = 0

@dataclass
class ContextCandidate:
    id: str
    category: str
    text: str
    estimated_tokens: int
    score: float = 0.0
    priority_tier: int = 0
    files: list[str] = field(default_factory=list)
    recency_index: Optional[int] = None
    mandatory: bool = False
    scoring_reasons: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

class RelevanceScorer:
    def _normalize_path(self, path: str) -> str:
        return os.path.normpath(path)

    def score_candidates(self, state: TaskState, candidates: list[ContextCandidate]) -> list[ContextCandidate]:
        touched_files = {self._normalize_path(p) for p in state.touched_files}
        relevant_files = {self._normalize_path(p) for p in state.relevant_files}

        for c in candidates:
            tier = 0
            score = 0.0
            reasons = []

            # Determine base tier based on category and metadata
            if c.category == "current_error":
                tier = TIER_CRITICAL
                reasons.append("base tier: CRITICAL (current error)")
            elif c.category == "verification_result":
                if c.metadata.get("success") is False:
                    tier = TIER_CRITICAL
                    reasons.append("base tier: CRITICAL (failed verification)")
                else:
                    tier = TIER_LOW
                    reasons.append("base tier: LOW (successful verification)")
            elif c.category == "repeated_failure":
                if not c.metadata.get("resolved"):
                    tier = TIER_CRITICAL
                    reasons.append("base tier: CRITICAL (active repeated failure)")
                    score += 10.0
                    reasons.append("active failure")
                else:
                    tier = TIER_LOW
                    reasons.append("base tier: LOW (resolved repeated failure)")
            elif c.category == "failed_attempt":
                tier = TIER_VERY_HIGH
                reasons.append("base tier: VERY_HIGH (failed attempt)")
                score += 10.0
                reasons.append("active failure")
            elif c.category == "symbols":
                c_files = {self._normalize_path(p) for p in c.files}
                if c_files.intersection(touched_files) or c_files.intersection(relevant_files):
                    tier = TIER_VERY_HIGH
                    reasons.append("base tier: VERY_HIGH (symbols from focus files)")
                else:
                    tier = TIER_MEDIUM
                    reasons.append("base tier: MEDIUM (other symbols)")
            elif c.category == "local_imports":
                c_files = {self._normalize_path(p) for p in c.files}
                if c_files.intersection(touched_files) or c_files.intersection(relevant_files):
                    tier = TIER_HIGH
                    reasons.append("base tier: HIGH (local imports from focus files)")
                else:
                    tier = TIER_MEDIUM
                    reasons.append("base tier: MEDIUM (other local imports)")
            elif c.category == "observation":
                c_files = {self._normalize_path(p) for p in c.files}
                if c_files.intersection(touched_files) or c_files.intersection(relevant_files):
                    tier = TIER_HIGH
                    reasons.append("base tier: HIGH (observation involving focus files)")
                else:
                    tier = TIER_MEDIUM
                    reasons.append("base tier: MEDIUM (other recent observations)")
            elif c.category == "discovery":
                c_files = {self._normalize_path(p) for p in c.files}
                if c_files.intersection(touched_files) or c_files.intersection(relevant_files):
                    tier = TIER_HIGH
                    reasons.append("base tier: HIGH (discovery involving focus files)")
                else:
                    tier = TIER_MEDIUM
                    reasons.append("base tier: MEDIUM (other discoveries)")
            elif c.category == "successful_attempt":
                tier = TIER_LOW
                reasons.append("base tier: LOW (successful attempt)")
            elif c.category == "repository_overview":
                tier = TIER_LOW
                reasons.append("base tier: LOW (repository overview)")
            elif c.category == "telemetry":
                tier = TIER_VERY_LOW
                reasons.append("base tier: VERY_LOW (telemetry)")
            else:
                tier = TIER_VERY_LOW
                reasons.append(f"base tier: VERY_LOW (unknown category {c.category})")

            # Apply bonuses
            c_files_norm = {self._normalize_path(p) for p in c.files}
            if c_files_norm.intersection(touched_files):
                score += 15.0
                reasons.append("touched-file overlap")
            elif c_files_norm.intersection(relevant_files):
                score += 5.0
                reasons.append("relevant-file overlap")

            if "confidence" in c.metadata:
                conf = c.metadata["confidence"]
                if conf is not None:
                    score += float(conf)
                    reasons.append(f"discovery confidence: {conf}")

            c.priority_tier = tier
            c.score = score
            c.scoring_reasons = reasons

        return candidates
