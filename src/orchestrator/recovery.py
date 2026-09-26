from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

class RecoveryDecision(Enum):
    RETRY_EDIT = auto()
    FORCE_EXPLORE = auto()
    ESCALATE = auto()

@dataclass
class RecoveryAdvice:
    decision: RecoveryDecision
    hint: str

class RecoveryManager:
    """
    Tracks error history, classifies failure, and picks next strategy.
    """
    def __init__(self, max_consecutive_same_error: int = 2):
        self.max_consecutive = max_consecutive_same_error
        self.last_error_signature: Optional[str] = None
        self.consecutive_same_error = 0

    def analyze_failure(self, failing_tests: list, stderr: str) -> RecoveryAdvice:
        error_signature = ",".join(sorted(failing_tests)) if failing_tests else stderr[:100]

        if error_signature == self.last_error_signature:
            self.consecutive_same_error += 1
        else:
            self.consecutive_same_error = 1
            
        self.last_error_signature = error_signature

        if self.consecutive_same_error >= self.max_consecutive:
            return RecoveryAdvice(
                decision=RecoveryDecision.FORCE_EXPLORE,
                hint="Previous approach repeated the same failure. Try a different file/approach next attempt."
            )
        else:
            return RecoveryAdvice(
                decision=RecoveryDecision.RETRY_EDIT,
                hint="New test failure encountered. Adjust the edit based on this new information."
            )
            
    def reset(self):
        self.last_error_signature = None
        self.consecutive_same_error = 0
