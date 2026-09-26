from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None

@dataclass
class ScratchpadState:
    hypothesis: str = ""
    files_touched: list = field(default_factory=list)
    attempt_history: list = field(default_factory=list)

@dataclass
class VerificationResult:
    passed: bool
    failing_tests: list = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
