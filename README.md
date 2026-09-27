# AI Coding Harness

An autonomous software engineering harness that solves programming issues through a model-tool interaction loop, independent workspace verification, and structured failure recovery.

---

## Prerequisites

- **Python**: 3.10 or higher
- **Git**: Installed and available on `PATH`
- **Make**: Standard build tools

---

## Quickstart

```bash
# 1. Clone the repository
git clone <repo-url>
cd ai-harness

# 2. Set up virtual environment and dependencies
make setup

# 3. Configure credentials
export AI_API_KEY="your-api-key"

# Optional: configure model provider (defaults to gpt-4o-mini)
export AI_MODEL="gemini-2.5-flash"
export AI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"

# Optional: specify workspace directory and test verification command
export HARNESS_WORKSPACE_DIR="/path/to/workspace"
export HARNESS_TEST_COMMAND="python3 -B test_calculator.py"

# 4. Run orchestration for an issue
make run ISSUE="fix the bug in the calculator implementation"

# 5. Run the test suite
make test
```

---

## Configuration & Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `AI_API_KEY` | `str` | *None* | Required in production mode. API key for foundation model provider. |
| `AI_MODEL` | `str` | `gpt-4o-mini` | Target model identifier. Compatible with OpenAI, OpenRouter, Gemini, and other OpenAI-compatible endpoints. |
| `AI_BASE_URL` | `str` | OpenAI endpoint | Base URL for OpenAI-compatible chat completion endpoint. |
| `HARNESS_WORKSPACE_DIR` | `str` | `.` | Target repository/workspace directory to scan, inspect, and modify. |
| `HARNESS_TEST_COMMAND` | `str` | *None* | Command executed by VerificationEngine to verify code correctness (e.g., `python3 -B test.py` or `pytest`). |
| `HARNESS_MAX_STEPS` | `int` | `30` | Maximum model/tool interaction steps before escalating. |
| `HARNESS_MAX_RECOVERIES` | `int` | `5` | Maximum autonomous recovery cycles following verification failures. |
| `HARNESS_TOOL_TIMEOUT` | `int` | `60` | Execution timeout in seconds for individual tool calls. |

---

## Execution Commands

### Setup
```bash
make setup
```
Initializes a virtual environment in `.venv/` and installs dependencies from `requirements.txt` (`pydantic`, `jinja2`, `pytest`).

### Run
```bash
make run ISSUE="description of the issue"
```
Or with custom CLI options:
```bash
.venv/bin/python -m src.main \
  --issue "fix the bug in the calculator implementation" \
  --workspace "/path/to/workspace" \
  --test-command "python3 -B test_calculator.py" \
  --max-attempts 30
```

### Test
```bash
make test
```
Executes the full test suite (350+ tests) across all subsystems using `pytest`. Tests run hermetically with deterministic mock modes; no external API key or network access is required.

---

## Exit-Code Semantics

The harness entrypoint enforces strict exit-code contracts:

- **`0` (Success)**: Task completed and independently verified. Requires:
  1. Orchestrator status is `completed` or `resolved`.
  2. `VerificationEngine` independently verifies the workspace (`verified is True`).
  3. Non-empty workspace diff detected.
  4. Valid Python syntax across modified files.
  5. Configured test command exits with code `0`.
- **`1` (Failure / Non-Zero)**: Any unverified outcome, including:
  - Verification failure (failing tests, syntax errors, regressions).
  - Step limit exhausted without resolution (`blocked_step_cap`).
  - Provider rate limiting (`RATE_LIMIT`) — terminates immediately without burning recovery retries.
  - Authentication or connection errors (`ModelAPIError`).
  - Missing API key in production mode.

---

## Verification Evidence Reported

Upon run completion, the harness emits a structured final report to stdout:

```
--- Final Report ---
Status: completed
Verified: True
Verification: PASSED
Test Command: python3 -B test_calculator.py
Verification Summary: Verification passed: Tests exited with code 0 across 1 modified file(s).
Test Output:
Tests passed successfully.
Files Modified: calculator.py
Git Diff:
diff --git a/calculator.py b/calculator.py
...
Attempts: 4
Recovery Attempts: 1
Cost: $0.00
Final Hypothesis: ...

+----------------------------------------------------+
| TELEMETRY REPORT                                   |
+----------------------------------------------------+
| Model Calls:       4                               |
| Prompt Tokens:     3200                            |
| Completion Tokens: 240                             |
| Total Tokens:      3440                            |
| Tool Calls:        3                               |
| Recovery Attempts: 1                               |
| Wall Time (s):     4.12                            |
+----------------------------------------------------+
```

### Evidence Fields
- **Status**: Terminal state (`completed`, `failed`, `error`, `blocked_step_cap`).
- **Verified**: Hard-gate boolean from `VerificationEngine`.
- **Verification Status**: `PASSED`, `FAILED`, `NO_CHANGES`, or `REGRESSION_DETECTED`.
- **Failure Classification**: Categorized cause on failure (`SYNTAX_ERROR`, `ASSERTION_FAILED`, `IMPORT_MISSING`, `TIMEOUT`, `TEST_EXECUTION_ERROR`, `RATE_LIMIT`, `UNKNOWN_ERROR`).
- **Test Command & Output**: Exact verification command executed and its output.
- **Files Modified & Git Diff**: Explicit file paths and unified diff of workspace modifications.
- **Attempts**: Total model interaction turns.
- **Recovery Attempts**: Number of autonomous recovery cycles triggered by verification failures.
- **Telemetry**: Token consumption, tool call counts, recovery counts, and elapsed wall-clock time.

---

## Security & Secrets

- **No Secret Leakage**: `AI_API_KEY` is loaded from the environment and is never logged, printed, embedded into exception messages, or stored in telemetry reports.
- **Git Discipline**: Secrets, environment files (`.env*`), and execution artifacts (`.harness/`, `runs/`, `*.zip`) are excluded via `.gitignore`. Never commit API keys or credentials.
