# AI Coding Harness

A Python coding-agent harness with a Textual terminal UI, a model-tool interaction loop, independent workspace verification, and structured failure recovery. Use the TUI to submit tasks against an external repository, or run the CLI and monitor its saved task state.

---

## Prerequisites

- **Python**: 3.10 or higher
- **Git**: Installed and available on `PATH`
- **Make**: Optional; equivalent Python commands are shown below
- For real tasks: a model API key and an existing target Git repository with its test dependencies installed

Commands below use macOS/Linux shell syntax. Run harness commands from the cloned `ai-harness` directory.

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/piyush-1603/ai-harness.git
cd ai-harness
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Alternatively, run `make setup` after cloning. Dependencies include `pydantic`, `jinja2`, `pytest`, and `textual`.

### 2. Preview the TUI without an API key

```bash
.venv/bin/python -m src.tui.app --demo
```

Demo mode displays sample data; it does not run an agent or modify a target repository. Press `q` to quit.

### 3. Configure a real model

Read the API key without displaying it or putting its literal value in shell history:

```bash
export AI_API_KEY="$(.venv/bin/python -c 'import getpass; print(getpass.getpass("API key: "))')"
```

Paste the key at the prompt and press Enter; the pasted characters are hidden. Configure the endpoint and a model available to that key. For Gemini, replace the model placeholder before running:

```bash
export AI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export AI_MODEL="<available-gemini-model-id>"
```

See Google's [Gemini compatibility documentation](https://ai.google.dev/gemini-api/docs/openai) for provider setup. The adapter appends `/chat/completions` to `AI_BASE_URL`; do not include that suffix yourself. Other compatible providers require their own key, base URL, and model ID.

The entrypoints read exported environment variables; they do not automatically load a `.env` file. Keep this terminal open for the following commands.

### 4. Launch against an external repository

Clone the target repository separately and install its dependencies using that project's instructions. Replace both paths below. For a Python repository with its own virtual environment:

```bash
export HARNESS_TEST_COMMAND="/absolute/path/to/target-repo/.venv/bin/python -m pytest -q"

.venv/bin/python -m src.tui.app \
  --workspace "/absolute/path/to/target-repo"
```

The test command runs with the target repository as its working directory. Use its interpreter, not the harness interpreter, when the two environments have different dependencies. Other projects can supply their own command, such as `npm test`.

The TUI opens in **READY** with a visible input. Type a concrete coding task and press Enter, for example: “Fix the incorrect result in `calculator.add`; preserve the existing tests and verify the change.” The existing Orchestrator runs in a background Textual worker. Input is enabled again when the run ends, allowing another task to be submitted.

**READY means the worker is idle, not that the task passed.** Inspect Events and verification observations for the result. Each submitted prompt creates a new `interactive-...` task; it does not continue the previous conversation. Repository displays the supplied workspace.

| Key | Action |
|---|---|
| `Enter` in input | Submit a task |
| `Esc` | Leave input focus |
| `Tab` / `Shift+Tab` | Move focus between controls, including the input |
| `1`–`5` outside input | Overview, Context, Memory, Repository, Events |
| `r` outside input | Refresh |
| `?` outside input | Help |
| `q` outside input | Quit |

While input is focused, letters and digits are task text. Press `Esc` before using navigation shortcuts.

### 5. CLI with a read-only monitor

For a CLI run, use the same provider configuration and target test command:

```bash
.venv/bin/python -m src.main \
  --issue "Fix the incorrect result in calculator.add without modifying tests" \
  --workspace "/absolute/path/to/target-repo" \
  --test-command "$HARNESS_TEST_COMMAND" \
  --max-attempts 30
```

In another terminal, change to the **same `ai-harness` directory** and run:

```bash
.venv/bin/python -m src.tui.app \
  --task task-1 \
  --workspace "/absolute/path/to/target-repo"
```

CLI runs use `task-1`. The monitor reads `.harness/` relative to the launch directory, has no coding prompt, and does not start an agent. Start the CLI first so the task exists. An interactive task can also be monitored by passing its `interactive-...` ID. Use the same target workspace in both terminals.

---

## Configuration & Environment Variables

| Variable | Default | Scope and behavior |
|---|---|---|
| `AI_API_KEY` | Unset | Required for real model calls; not needed for demo or monitoring. |
| `AI_MODEL` | `gpt-4o-mini` | Adapter default. Set a model ID supported by the configured provider and key. |
| `AI_BASE_URL` | `https://api.openai.com/v1` | Base URL for compatible chat completions; `/chat/completions` is appended. |
| `HARNESS_WORKSPACE_DIR` | `.` | CLI workspace; when nonempty it overrides CLI `--workspace`. TUI uses `--workspace` directly. |
| `HARNESS_TEST_COMMAND` | Unset | Verification command for CLI and interactive TUI. CLI `--test-command` takes precedence when nonempty. |
| `HARNESS_MAX_STEPS` | `30` | CLI step limit; when nonempty it overrides `--max-attempts`. Interactive TUI currently uses a fixed 30-step limit. |
| `HARNESS_MAX_RECOVERIES` | `5` | Maximum verification recovery cycles in either execution mode. |
| `HARNESS_TOOL_TIMEOUT` | `60` | CLI tool engine default timeout in seconds. Interactive TUI uses the tool engine default of 60 seconds; this does not configure the independent verifier's timeout. |
| `HARNESS_CONTEXT_MAX_TOKENS` | `24000` | Estimated context budget in CLI and TUI. Invalid or nonpositive values fall back to the default. |

Unset `HARNESS_WORKSPACE_DIR` and `HARNESS_MAX_STEPS` if you want the corresponding CLI flags to control those values.

The TUI context counter estimates tokens as roughly one token per four characters. It measures the selected context, not total API usage. Instructions and the latest tool result are added afterward, so the full request can exceed this context budget. For example, set `export HARNESS_CONTEXT_MAX_TOKENS=8000` before launching to lower the context cap; this only trims context when it exceeds the cap.

---

## Execution Commands

### Setup
```bash
make setup
```
Initializes a virtual environment in `.venv/` and installs dependencies from `requirements.txt` (`pydantic`, `jinja2`, `pytest`, `textual`).

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
Or run `.venv/bin/python -m pytest -q`. The suite includes deterministic model tests and headless Textual interaction tests; a live provider key is not required. It does not establish that your provider credentials work.

For focused interactive TUI checks:

```bash
.venv/bin/python -m pytest tests/test_tui_m9.py -q
```

---

## Exit-Code Semantics

The CLI entrypoint (`python -m src.main`) uses the following exit codes. Exiting the TUI is not a task-success signal:

- **`0` (Success)**: Task completed and independently verified. Requires:
  1. Orchestrator status is `completed` or `resolved`.
  2. `VerificationEngine` independently verifies the workspace (`verified is True`).
  3. Workspace changes detected, including new untracked files.
  4. Valid Python syntax across modified files.
  5. If a test command is configured, it exits with code `0`. Without one, verification only checks changes and Python syntax; it does not run tests.
- **`1` (Failure / Non-Zero)**: Any unverified outcome, including:
  - Verification failure (failing tests, syntax errors, regressions).
  - Step limit exhausted without resolution (`blocked_step_cap`).
  - Provider rate limiting (`RATE_LIMIT`) — terminates immediately without burning recovery retries.
  - Authentication or connection errors (`ModelAPIError`).
  - Missing API key in production mode.

A task that makes no changes is reported as `NO_CHANGES`, even if the repository is already correct. Use a real bug-fix task to test successful end-to-end verification; “run tests and change nothing” is not a successful-fix benchmark under the current verifier.

---

## Verification Evidence Reported

Upon run completion, the CLI prints a final report to stdout. Example values below are illustrative, not benchmark results:

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
- **Telemetry**: Accumulated provider-reported input/output tokens for recorded model calls, tool call counts, recovery counts, and elapsed time. The displayed cost uses fixed rates in the code, not provider-specific billing. Calls that fail before usage is recorded may be absent from these totals.

---

## Troubleshooting

- **`No module named textual`**: Install `requirements.txt` with `.venv/bin/python -m pip install -r requirements.txt`, then launch with that same interpreter. Changing directories does not switch an activated virtual environment.
- **Navigation keys appear in the prompt**: Press `Esc`, then the shortcut. Use `Tab` to return to input.
- **Could not load `task-1`**: Run the CLI first and launch the monitor from the same directory so both use the same `.harness/` storage.
- **Missing key, HTTP 401/403, or HTTP 404**: Check the exported key, provider base URL, and model ID in the launching terminal. A 404 can indicate a wrong endpoint or unavailable model. A rendered TUI and a return to READY do not prove model access worked.
- **Missing test dependencies**: Install the target project's dependencies in its environment and use that environment's interpreter in `HARNESS_TEST_COMMAND`.
- **`NO_CHANGES`**: The verifier requires a changed workspace. Inspect the result rather than treating the READY label as success.

## Security & Secrets

- Credentials are read from the environment. Keep API keys out of prompts and committed files. `.env`, `.env.*` (except `.env.example`), and `.harness/` artifacts are ignored by Git.
- The agent can edit files and execute local shell commands. Use a dedicated checkout with a clean starting state so its changes can be reviewed; this is not an isolated sandbox.
- **Current limitation:** `src/main.py` disables default HTTPS certificate verification, and the TUI imports that module. This must be corrected before treating the harness as suitable for security-sensitive use.
