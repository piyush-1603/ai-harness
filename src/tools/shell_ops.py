"""Shell operations for Track C tools: run_bash, run_tests, git_diff, git_status.

Reuses the proven process-group timeout and termination pattern from mini-SWE-agent,
while wrapping execution safely in the PRD ToolResult contract.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import signal
import subprocess
import time
from typing import Dict, Optional, Tuple

from src.common.types import ToolName, ToolResult
from src.tools.file_ops import resolve_workspace_path, truncate_output

DEFAULT_TIMEOUT_SEC = 60


def _run_subprocess(
    command: str,
    cwd: str,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, Optional[str], float]:
    """Runs a shell command in cwd with timeout and process-group cleanup.

    Returns (exit_code, combined_output, error_message, duration_sec).
    """
    start_time = time.perf_counter()
    full_env = {**os.environ, **(env or {})}

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            text=True,
            cwd=cwd,
            env=full_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except Exception as e:
        duration = time.perf_counter() - start_time
        return -1, "", f"Failed to spawn process: {type(e).__name__}: {str(e)}", duration

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        duration = time.perf_counter() - start_time
        exit_code = process.returncode
        
        # Merge stdout and stderr while preserving distinction if both exist
        if stdout and stderr:
            combined = f"{stdout}\n--- stderr ---\n{stderr}".strip()
        elif stderr:
            combined = stderr.strip()
        else:
            combined = stdout.strip()

        return exit_code, combined, None, duration

    except subprocess.TimeoutExpired:
        # Terminate the entire process group to prevent orphaned background processes
        if os.name == "posix":
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass

        try:
            stdout, stderr = process.communicate(timeout=2)
            combined = (stdout or "") + (stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            combined = ""
            try:
                process.kill()
                process.wait(timeout=1)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                pass

        duration = time.perf_counter() - start_time
        return 124, combined.strip(), f"Command timed out after {timeout} seconds.", duration

    except Exception as e:
        duration = time.perf_counter() - start_time
        return -1, "", f"Subprocess error: {type(e).__name__}: {str(e)}", duration


def run_bash(
    command: str,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    workspace_dir: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> ToolResult:
    """Executes a bash command in the workspace directory with timeout protection."""
    start_time = time.perf_counter()
    try:
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.RUN_BASH,
            success=False,
            output="",
            exit_code=1,
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    effective_timeout = timeout if timeout > 0 else DEFAULT_TIMEOUT_SEC
    exit_code, output, err_msg, duration = _run_subprocess(
        command=command,
        cwd=str(ws_path),
        timeout=effective_timeout,
        env=env,
    )

    bounded_output = truncate_output(output)
    success = (exit_code == 0)

    if err_msg:
        final_error: Optional[str] = err_msg
    elif not success and not output and exit_code != 0:
        final_error = f"Command exited with non-zero exit code {exit_code}"
    else:
        final_error = None

    return ToolResult(
        tool_name=ToolName.RUN_BASH,
        success=success,
        output=bounded_output,
        exit_code=exit_code,
        error=final_error,
        duration_sec=duration,
    )


def run_tests(
    test_command: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Executes a test command using the safe subprocess mechanism."""
    cmd = test_command.strip() if (test_command and test_command.strip()) else "pytest"
    result = run_bash(command=cmd, timeout=timeout, workspace_dir=workspace_dir)
    return ToolResult(
        tool_name=ToolName.RUN_TESTS,
        success=result.success,
        output=result.output,
        exit_code=result.exit_code,
        error=result.error,
        duration_sec=result.duration_sec,
    )


def git_diff(
    path: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Retrieves git diff of working tree and staged changes against HEAD."""
    start_time = time.perf_counter()
    try:
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.GIT_DIFF,
            success=False,
            output="",
            exit_code=1,
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    # If path is provided, diff specific path; otherwise diff all (unstaged + staged)
    rel_path_str: Optional[str] = None
    if path:
        try:
            target_path = resolve_workspace_path(path, workspace_dir)
            rel_path = target_path.relative_to(ws_path)
            rel_path_str = str(rel_path)
            cmd = f"git diff HEAD -- {shlex.quote(rel_path_str)}"
        except PermissionError as e:
            return ToolResult(
                tool_name=ToolName.GIT_DIFF,
                success=False,
                output="",
                exit_code=1,
                error=str(e),
                duration_sec=time.perf_counter() - start_time,
            )
    else:
        cmd = "git diff HEAD"

    exit_code, output, err_msg, duration = _run_subprocess(command=cmd, cwd=str(ws_path), timeout=30)
    
    # If HEAD is unborn (fresh repo), fallback to git diff
    if exit_code != 0 and "ambiguous argument 'HEAD'" in (output or ""):
        fallback_cmd = f"git diff -- {shlex.quote(rel_path_str)}" if rel_path_str else "git diff"
        exit_code, output, err_msg, duration = _run_subprocess(command=fallback_cmd, cwd=str(ws_path), timeout=30)

    return ToolResult(
        tool_name=ToolName.GIT_DIFF,
        success=(exit_code == 0),
        output=truncate_output(output),
        exit_code=exit_code,
        error=err_msg if exit_code != 0 else None,
        duration_sec=duration,
    )


def git_status(workspace_dir: Optional[str] = None) -> ToolResult:
    """Retrieves git status in short porcelain format."""
    start_time = time.perf_counter()
    try:
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.GIT_STATUS,
            success=False,
            output="",
            exit_code=1,
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    exit_code, output, err_msg, duration = _run_subprocess(
        command="git status --short",
        cwd=str(ws_path),
        timeout=30,
    )

    return ToolResult(
        tool_name=ToolName.GIT_STATUS,
        success=(exit_code == 0),
        output=truncate_output(output),
        exit_code=exit_code,
        error=err_msg if exit_code != 0 else None,
        duration_sec=duration,
    )

def git_reset_hard(workspace_dir: Optional[str] = None) -> ToolResult:
    """Wipes all uncommitted changes and new files, restoring a clean working tree."""
    start_time = time.perf_counter()
    try:
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.RUN_BASH,
            success=False,
            output="",
            exit_code=1,
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    cmd = "git reset --hard HEAD && git clean -fd"
    exit_code, output, err_msg, duration = _run_subprocess(command=cmd, cwd=str(ws_path), timeout=30)
    
    return ToolResult(
        tool_name=ToolName.RUN_BASH,
        success=(exit_code == 0),
        output=truncate_output(output),
        exit_code=exit_code,
        error=err_msg if exit_code != 0 else None,
        duration_sec=duration,
    )
