from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


class AgentStatus(str, Enum):
    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    EXPLORING = "EXPLORING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    TESTING = "TESTING"
    RECOVERING = "RECOVERING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class ToolName(str, Enum):
    READ_FILE = "read_file"
    EDIT_FILE = "edit_file"
    WRITE_FILE = "write_file"
    RUN_BASH = "run_bash"
    RUN_TESTS = "run_tests"
    GIT_DIFF = "git_diff"
    GIT_STATUS = "git_status"
    GREP_SEARCH = "grep_search"
    LIST_DIRECTORY = "list_directory"


class FailureClassification(str, Enum):
    SYNTAX_ERROR = "SYNTAX_ERROR"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    IMPORT_MISSING = "IMPORT_MISSING"
    TIMEOUT = "TIMEOUT"
    TEST_EXECUTION_ERROR = "TEST_EXECUTION_ERROR"
    EDIT_FAILED = "EDIT_FAILED"
    LOOP_DETECTED = "LOOP_DETECTED"
    RATE_LIMIT = "RATE_LIMIT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class VerificationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_CHANGES = "NO_CHANGES"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"


@dataclass
class TaskSpec:
    issue_id: str
    issue_description: str
    workspace_dir: str = "."
    test_command: Optional[str] = None
    prescribed_model: Optional[str] = None


@dataclass
class ToolCall:
    tool_name: ToolName
    tool_args: Dict[str, Any]
    call_id: str


@dataclass
class ToolResult:
    tool_name: ToolName = ToolName.RUN_BASH
    success: bool = True
    output: str = ""                   # Safely truncated if exceeding bounds
    exit_code: int = 0
    error: Optional[str] = None
    duration_sec: float = 0.0


@dataclass
class RecoveryAttempt:
    attempt_number: int
    failed_action: str
    failure_type: FailureClassification
    error_snippet: str
    hypothesis: str
    diff_at_failure: str
    timestamp: float


@dataclass
class ScratchpadState:
    task_summary: str = ""
    identified_files: List[str] = field(default_factory=list)
    active_hypothesis: str = ""
    history_attempts: List[RecoveryAttempt] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    last_known_diff: str = ""
    # Compatibility fields for Track A orchestrator
    hypothesis: str = ""
    files_touched: List[str] = field(default_factory=list)
    attempt_history: List[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    passed: bool
    failing_tests: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass
class VerificationReport:
    status: VerificationStatus
    is_verified: bool
    tests_passed: bool
    test_command: str
    test_output: str
    files_modified: List[str]
    git_diff: str
    failure_classification: Optional[FailureClassification] = None
    syntax_valid: bool = True
    summary: str = ""


@dataclass
class TelemetryReport:
    total_model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_tool_calls: int = 0
    tool_call_counts: Dict[str, int] = field(default_factory=dict)
    files_inspected: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    recovery_attempts: int = 0
    total_wall_time_sec: float = 0.0

    def to_table(self) -> str:
        """Renders the terminal-ready ASCII telemetry box for evaluation."""
        return (
            f"+----------------------------------------------------+\n"
            f"| TELEMETRY REPORT                                   |\n"
            f"+----------------------------------------------------+\n"
            f"| Model Calls:       {self.total_model_calls:<31} |\n"
            f"| Prompt Tokens:     {self.prompt_tokens:<31} |\n"
            f"| Completion Tokens: {self.completion_tokens:<31} |\n"
            f"| Total Tokens:      {self.total_tokens:<31} |\n"
            f"| Tool Calls:        {self.total_tool_calls:<31} |\n"
            f"| Recovery Attempts: {self.recovery_attempts:<31} |\n"
            f"| Wall Time (s):     {self.total_wall_time_sec:<31.2f} |\n"
            f"+----------------------------------------------------+"
        )
