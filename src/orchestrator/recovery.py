from dataclasses import dataclass
from enum import Enum, auto
import hashlib
import re

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
    def __init__(self, explore_threshold: int = 2, escalate_threshold: int = 4):
        self.explore_threshold = explore_threshold
        self.escalate_threshold = escalate_threshold
        
        self.signature_history: list[str] = []
        self.stuck_counter = 0

    def _normalize_error(self, error_text: str) -> str:
        text = re.sub(r'0x[0-9a-fA-F]+', '0x...', error_text)
        text = re.sub(r'line \d+', 'line X', text)
        text = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', '<timestamp>', text)
        text = re.sub(r'\d+\.\d+s?', '<time>', text)
        return hashlib.md5(text.encode()).hexdigest()

    def analyze_failure(self, failing_tests: list, stderr: str) -> RecoveryAdvice:
        raw_sig = ",".join(sorted(failing_tests)) if failing_tests else stderr[:200]
        
        # Empty signature edge case guard
        if not raw_sig.strip():
            error_signature = f"empty_{hashlib.md5(str(len(self.signature_history)).encode()).hexdigest()}"
        else:
            error_signature = self._normalize_error(raw_sig)

        # Check for repeat within last 4 signatures
        if error_signature in self.signature_history[-4:]:
            self.stuck_counter += 1
        else:
            self.stuck_counter = 1
            
        self.signature_history.append(error_signature)

        if self.stuck_counter >= self.escalate_threshold:
            return RecoveryAdvice(
                decision=RecoveryDecision.ESCALATE,
                hint="Repeated failures detected despite forced exploration. Escalating."
            )
        elif self.stuck_counter >= self.explore_threshold:
            return RecoveryAdvice(
                decision=RecoveryDecision.FORCE_EXPLORE,
                hint="Previous approach repeated a failure recently. Try a different file/approach next attempt."
            )
        else:
            return RecoveryAdvice(
                decision=RecoveryDecision.RETRY_EDIT,
                hint="New test failure encountered. Adjust the edit based on this new information."
            )
            
    def reset(self):
        self.signature_history.clear()
        self.stuck_counter = 0
    
    @property
    def last_error_signature(self) -> str:
        return self.signature_history[-1] if self.signature_history else None
