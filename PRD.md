# Product Requirements Document (PRD)
# System: Autonomous Coding Harness (`ai-harness-2026`)
**Document Status:** LOCKED / RATIFIED  
**Version:** 1.0.0  
**Target:** LCC × DevClub AI Coding Harness Hackathon 2026  

---

## 1. Product Vision & Principles

### 1.1 Objective
Build an autonomous, competition-grade software engineering coding harness around a standardized, text-only foundation model. The harness accepts an unseen GitHub issue, inspects the codebase, plans changes, executes edits, executes tests, autonomously recovers from failures, and produces an independently verified patch with transparent resource telemetry.

### 1.2 Core Tenets
1. **Correctness First**: A task is never marked complete based solely on model claims. Independent verification of tests and clean git diffs is mandatory.
2. **Evidence Over Claims**: Every step, error, and verification status is grounded in real tool execution results and git status.
3. **Resource Efficiency**: Active context pruning, token compaction, and AST repository mapping prevent context bloat and redundant model calls.
4. **Hermetic Reproducibility**: The evaluation workflow (`make setup` → `make run` → `make test`) must execute flawlessly in a clean environment without manual configuration or source code modifications.

---

## 2. Canonical Environment & Configuration Registry

To avoid configuration collisions, all subsystems must strictly use these exact environment variable names and fallback defaults.

| Environment Variable | Type | Default | Description |
|---|---|---|---|
| `AI_API_KEY` | `str` | *None (Required)* | The foundation model API key supplied by the evaluator. |
| `AI_MODEL` | `str` | `gpt-4o-mini` | Model identifier (overridable by evaluator for prescribed model). |
| `AI_BASE_URL` | `str` | `""` | Optional base URL for OpenAI/Anthropic/Gemini-compatible proxies. |
| `HARNESS_WORKSPACE_DIR` | `str` | `.` | Absolute or relative path to the target repository being modified. |
| `HARNESS_MAX_STEPS` | `int` | `30` | Hard cap on total agent turns per issue to prevent infinite loops. |
| `HARNESS_MAX_RECOVERIES` | `int` | `5` | Maximum retry attempts after test failures before escalation. |
| `HARNESS_TOOL_TIMEOUT` | `int` | `60` | Hard timeout (in seconds) for shell / test command execution. |
| `HARNESS_CONTEXT_MAX_TOKENS` | `int` | `24000` | Maximum token ceiling for prompt context before pruning triggers. |
| `HARNESS_VERBOSE` | `bool` | `false` | Enable detailed debug logs in terminal. |

---

## 3. Canonical Enums & State Constants

All team tracks must import and use these exact enums from `src.common.types`.

```python
from enum import Enum

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
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

class VerificationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_CHANGES = "NO_CHANGES"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
```

---

## 4. Standardized Data Schemas & Contracts

All three tracks communicate via these exact dataclasses defined in `src/common/types.py`. **Do not invent ad-hoc dictionaries.**

### 4.1 Task Specification
```python
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class TaskSpec:
    issue_id: str
    issue_description: str
    workspace_dir: str = "."
    test_command: Optional[str] = None
    prescribed_model: Optional[str] = None
```

### 4.2 Tool Invocation & Results
```python
@dataclass
class ToolCall:
    tool_name: ToolName
    tool_args: Dict[str, Any]
    call_id: str

@dataclass
class ToolResult:
    tool_name: ToolName
    success: bool
    output: str                   # Safely truncated if exceeding bounds
    exit_code: int = 0
    error: Optional[str] = None
    duration_sec: float = 0.0
```

### 4.3 Recovery & Memory Scratchpad
```python
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
```

### 4.4 Verification Report
```python
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
```

### 4.5 Resource Telemetry Report
```python
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
        ...
```

---

## 5. Team Track Ownership & Subsystem APIs

```
                           ┌────────────────────────┐
                           │      ENTRY POINT       │
                           │      src/main.py       │
                           │   (Owned by Track A)   │
                           └───────────┬────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│        TRACK A         │   │        TRACK B         │   │        TRACK C         │
│ Orchestration/Recovery │   │    Context & Memory    │   │  Tools & Verification  │
│                        │   │                        │   │                        │
│ • loop.py              │   │ • engine.py            │   │ • registry.py          │
│ • state.py             │   │ • repo_map.py          │   │ • file_ops.py          │
│ • recovery_engine.py   │   │ • compactor.py         │   │ • shell_ops.py         │
│ • model_adapter.py     │   │ • scratchpad.py        │   │ • search.py            │
│ • Makefile             │   │ • tracker.py           │   │ • verifier.py          │
└────────────────────────┘   └────────────────────────┘   └────────────────────────┘
```

### Track A: Person A — Orchestration + Recovery + Model Adapter
- **Directory**: `src/orchestrator/`, `src/recovery/`, `src/model/`, `src/main.py`, `Makefile`
- **Class**: `HarnessOrchestrator`
  - `def run(task: TaskSpec) -> VerificationReport`
- **Class**: `RecoveryEngine`
  - `def classify_failure(raw_output: str, exit_code: int) -> FailureClassification`
  - `def generate_recovery_plan(scratchpad: ScratchpadState, last_failure: RecoveryAttempt) -> str`
  - `def detect_loop(scratchpad: ScratchpadState, proposed_tool: ToolCall) -> bool`
- **Class**: `ModelAdapter`
  - `def query(messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None) -> ModelResponse`
  - Enforces text-only constraints. Handles exponential backoff on HTTP 429/503.

### Track B: Person B — Context, Memory & Resource Telemetry
- **Directory**: `src/context/`, `src/memory/`, `src/telemetry/`
- **Class**: `ContextEngine`
  - `def build_prompt(task: TaskSpec, scratchpad: ScratchpadState, recent_results: List[ToolResult]) -> List[Dict[str, str]]`
  - Enforces `HARNESS_CONTEXT_MAX_TOKENS` budget.
- **Class**: `RepoMapGenerator`
  - `def generate_map(workspace_dir: str, max_depth: int = 4) -> str`
  - Extracts classes, functions, and file paths using AST/ripgrep without loading raw code bodies into context.
- **Class**: `ContextCompactor`
  - `def compact_tool_output(output: str, max_lines: int = 60) -> str`
  - Truncates repetitive logs, keeping head and tail lines with omission markers.
- **Class**: `TelemetryTracker`
  - `def log_tokens(prompt_tok: int, comp_tok: int)`
  - `def log_tool(tool_name: ToolName)`
  - `def print_summary() -> TelemetryReport`

### Track C: Person C — Tools, Repository Exploration & Verification
- **Directory**: `src/tools/`, `src/verification/`
- **Class**: `ToolEngine`
  - `def execute(call: ToolCall) -> ToolResult`
  - Implements:
    - `read_file(path: str, start_line: int, end_line: int) -> ToolResult`
    - `edit_file(path: str, search_block: str, replace_block: str) -> ToolResult`
    - `write_file(path: str, content: str) -> ToolResult`
    - `run_bash(command: str, timeout: int = 60) -> ToolResult`
    - `grep_search(query: str, path: str = ".") -> ToolResult`
    - `list_directory(path: str = ".") -> ToolResult`
- **Class**: `VerificationEngine`
  - `def verify(workspace_dir: str, test_command: Optional[str] = None) -> VerificationReport`
  - Independently checks:
    1. Is `git diff` non-empty?
    2. Does `test_command` exit with `0`?
    3. Are modified files free of syntax errors (`py_compile` or language AST parser)?

---

## 6. The Evaluator Interface (`Makefile`) Specification

The root `Makefile` must adhere strictly to the following behavior:

```makefile
SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup run test clean

setup:
	@echo "=== Setting up environment ==="
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "=== Setup complete ==="

run:
	@echo "=== Launching AI Coding Harness ==="
	@if [ -z "$$AI_API_KEY" ]; then \
		echo "ERROR: AI_API_KEY environment variable is not set."; \
		echo "Please run: export AI_API_KEY=\"your_key_here\""; \
		exit 1; \
	fi
	$(PYTHON) -m src.main $(ARGS)

test:
	@echo "=== Running internal verification suite ==="
	$(PYTHON) -m pytest tests/ -v

clean:
	@echo "=== Cleaning generated artifacts ==="
	rm -rf $(VENV)
	rm -rf .pytest_cache
	rm -rf __pycache__ src/**/__pycache__ tests/**/__pycache__
	rm -rf .harness_state/
	@echo "=== Clean complete ==="
```

### Evaluator Runtime Experience
When the evaluator launches `make run`:
1. If no issue arguments are passed (`make run`), `src/main.py` opens an interactive prompt asking for the issue text or path to an issue file.
2. If passed via CLI (`make run ARGS="--issue 'fix bug in auth.py'"`), it immediately proceeds into the autonomous loop.
3. Upon completion, the harness prints:
   - Verification status: `[VERIFIED SUCCESS]` or `[FAILED]`
   - Summary of modified files & diff
   - Telemetry execution table (tokens, calls, runtime)
   - Exits with `0` if verified, `1` if failed.

---

## 7. Development Rules to Prevent Git Conflicts

1. **Lock `src/common/types.py` First**:
   - `src/common/types.py` is the single source of truth for all schemas and enums.
   - Once committed, no one alters field names without unanimous team agreement.
2. **Strict File Isolation**:
   - Person A touches ONLY `src/orchestrator/`, `src/recovery/`, `src/model/`, `src/main.py`, `Makefile`.
   - Person B touches ONLY `src/context/`, `src/memory/`, `src/telemetry/`.
   - Person C touches ONLY `src/tools/`, `src/verification/`.
3. **No Hardcoded Secrets**:
   - `.env` is gitignored. `.env.example` contains only empty template keys (`AI_API_KEY=`).
4. **Independent Unit Testability**:
   - Person C writes tests in `tests/test_tools.py` and `tests/test_verification.py`.
   - Person B writes tests in `tests/test_context.py` and `tests/test_repo_map.py`.
   - Person A writes tests in `tests/test_orchestrator.py` and `tests/test_recovery.py`.
