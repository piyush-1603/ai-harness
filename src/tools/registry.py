"""Central ToolEngine registry for Track C.

Dispatches strongly-typed ToolCall invocations to specialized tool modules
(file_ops, shell_ops, search). Enforces safety, validates arguments, and ensures
no tool exception ever crashes the orchestrator.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional, Tuple

from src.common.types import ToolCall, ToolName, ToolResult
from src.tools.file_ops import edit_file, read_file, write_file
from src.tools.search import grep_search, list_directory
from src.tools.shell_ops import git_diff, git_status, run_bash, run_tests


# -----------------------------------------------------------------------------
# Argument Validation Helpers
# -----------------------------------------------------------------------------

def _validate_str(
    args: Dict[str, Any],
    key: str,
    tool_name: str,
    required: bool = True,
    default: Optional[str] = None,
    allow_empty: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Validates that an argument is a string (or converts safely if required)."""
    if key not in args or args[key] is None:
        if required:
            return None, f"Missing required argument: '{key}' for tool '{tool_name}'."
        return default, None

    val = args[key]
    if not isinstance(val, str):
        return None, f"Invalid argument type: '{key}' for tool '{tool_name}' must be a string, got {type(val).__name__}."

    if not allow_empty and not val.strip():
        return None, f"Invalid argument: '{key}' for tool '{tool_name}' cannot be empty."

    return val, None


def _validate_int(
    args: Dict[str, Any],
    key: str,
    tool_name: str,
    required: bool = False,
    default: Optional[int] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """Validates that an argument is an integer or can be converted to one."""
    if key not in args or args[key] is None:
        if required:
            return None, f"Missing required argument: '{key}' for tool '{tool_name}'."
        return default, None

    val = args[key]
    if isinstance(val, bool):
        return None, f"Invalid argument type: '{key}' for tool '{tool_name}' must be an integer, got bool."

    if isinstance(val, int):
        return val, None

    if isinstance(val, str):
        try:
            return int(val.strip()), None
        except ValueError:
            pass

    return None, f"Invalid argument type: '{key}' for tool '{tool_name}' must be an integer, got {repr(val)}."


# -----------------------------------------------------------------------------
# ToolEngine
# -----------------------------------------------------------------------------

class ToolEngine:
    """Core tool execution engine implementing dispatch across all canonical ToolName values."""

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        default_timeout: int = 60,
    ):
        self.workspace_dir = workspace_dir or os.environ.get("HARNESS_WORKSPACE_DIR", ".")
        self.default_timeout = default_timeout

        # Explicit canonical ToolName -> handler mapping for clear inspection
        self._handlers: Dict[ToolName, Callable[[Dict[str, Any]], ToolResult]] = {
            ToolName.READ_FILE: self._dispatch_read_file,
            ToolName.EDIT_FILE: self._dispatch_edit_file,
            ToolName.WRITE_FILE: self._dispatch_write_file,
            ToolName.RUN_BASH: self._dispatch_run_bash,
            ToolName.RUN_TESTS: self._dispatch_run_tests,
            ToolName.GIT_DIFF: self._dispatch_git_diff,
            ToolName.GIT_STATUS: self._dispatch_git_status,
            ToolName.GREP_SEARCH: self._dispatch_grep_search,
            ToolName.LIST_DIRECTORY: self._dispatch_list_directory,
        }

    # -------------------------------------------------------------------------
    # Public Tool Operations (direct callers)
    # -------------------------------------------------------------------------

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = -1,
    ) -> ToolResult:
        """Reads a file within a 1-based line range."""
        return read_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
            workspace_dir=self.workspace_dir,
        )

    def edit_file(
        self,
        path: str,
        search_block: str,
        replace_block: str,
    ) -> ToolResult:
        """Edits a file by replacing an exact, unique search_block."""
        return edit_file(
            path=path,
            search_block=search_block,
            replace_block=replace_block,
            workspace_dir=self.workspace_dir,
        )

    def write_file(
        self,
        path: str,
        content: str,
    ) -> ToolResult:
        """Writes content to a file, auto-creating parent directories."""
        return write_file(
            path=path,
            content=content,
            workspace_dir=self.workspace_dir,
        )

    def run_bash(
        self,
        command: str,
        timeout: Optional[int] = None,
    ) -> ToolResult:
        """Executes a bash command with timeout protection in the workspace."""
        effective_timeout = timeout if (timeout is not None and timeout > 0) else self.default_timeout
        return run_bash(
            command=command,
            timeout=effective_timeout,
            workspace_dir=self.workspace_dir,
        )

    def run_tests(
        self,
        test_command: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> ToolResult:
        """Executes a test command in the workspace."""
        effective_timeout = timeout if (timeout is not None and timeout > 0) else self.default_timeout
        return run_tests(
            test_command=test_command,
            timeout=effective_timeout,
            workspace_dir=self.workspace_dir,
        )

    def git_diff(
        self,
        path: Optional[str] = None,
    ) -> ToolResult:
        """Retrieves git diff of working tree changes."""
        return git_diff(
            path=path,
            workspace_dir=self.workspace_dir,
        )

    def git_status(self) -> ToolResult:
        """Retrieves git status of working tree."""
        return git_status(
            workspace_dir=self.workspace_dir,
        )

    def grep_search(
        self,
        query: str,
        path: str = ".",
    ) -> ToolResult:
        """Recursively searches for matching patterns in code."""
        return grep_search(
            query=query,
            path=path,
            workspace_dir=self.workspace_dir,
        )

    def list_directory(
        self,
        path: str = ".",
    ) -> ToolResult:
        """Lists files and subdirectories."""
        return list_directory(
            path=path,
            workspace_dir=self.workspace_dir,
        )

    # -------------------------------------------------------------------------
    # Central Execution Boundary
    # -------------------------------------------------------------------------

    def execute(self, call: ToolCall) -> ToolResult:
        """Dispatches a ToolCall to the appropriate tool method.

        Guarantees:
        1. Every execution path returns a canonical ToolResult.
        2. Malformed calls, missing args, and wrong types return ToolResult(success=False).
        3. No exception escapes execute() to crash the orchestrator.
        4. duration_sec is always measured and non-negative.
        5. exit_code is 0 on success and non-zero on failure.
        """
        start_time = time.perf_counter()

        # 1. Guard against non-ToolCall instances
        if not isinstance(call, ToolCall):
            return ToolResult(
                tool_name=ToolName.RUN_BASH,
                success=False,
                output="",
                exit_code=1,
                error=f"Invalid invocation: expected a ToolCall instance, got {type(call).__name__}.",
                duration_sec=time.perf_counter() - start_time,
            )

        # 2. Normalize and validate ToolName
        tool_name = call.tool_name
        if isinstance(tool_name, str):
            try:
                tool_name = ToolName(tool_name)
            except ValueError:
                pass

        if not isinstance(tool_name, ToolName):
            supported = ", ".join(t.value for t in ToolName)
            return ToolResult(
                tool_name=ToolName.RUN_BASH,
                success=False,
                output="",
                exit_code=1,
                error=f"Unknown or unsupported tool: {repr(call.tool_name)}. Supported tools: {supported}.",
                duration_sec=time.perf_counter() - start_time,
            )

        # 3. Validate tool_args is a dictionary
        raw_args = call.tool_args
        if raw_args is None:
            args: Dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Invalid tool_args: expected a dictionary, got {type(raw_args).__name__}.",
                duration_sec=time.perf_counter() - start_time,
            )

        # 4. Lookup and invoke handler
        handler = self._handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                exit_code=1,
                error=f"No handler registered for tool: {tool_name.value}.",
                duration_sec=time.perf_counter() - start_time,
            )

        try:
            result = handler(args)
            # Ensure duration is populated if handler did not compute it
            if result.duration_sec <= 0.0:
                result.duration_sec = time.perf_counter() - start_time
            # Ensure exit_code aligns with success flag
            if not result.success and result.exit_code == 0:
                result.exit_code = 1
            return result
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Tool execution failed: {type(e).__name__}: {str(e)}",
                duration_sec=time.perf_counter() - start_time,
            )

    # -------------------------------------------------------------------------
    # Canonical Dispatch Handlers with Strict Argument Validation
    # -------------------------------------------------------------------------

    def _dispatch_read_file(self, args: Dict[str, Any]) -> ToolResult:
        path, err = _validate_str(args, "path", ToolName.READ_FILE.value, required=True)
        if err:
            return self._arg_error(ToolName.READ_FILE, err)

        start_line, err = _validate_int(args, "start_line", ToolName.READ_FILE.value, required=False, default=1)
        if err:
            return self._arg_error(ToolName.READ_FILE, err)

        end_line, err = _validate_int(args, "end_line", ToolName.READ_FILE.value, required=False, default=-1)
        if err:
            return self._arg_error(ToolName.READ_FILE, err)

        return self.read_file(path=path, start_line=start_line, end_line=end_line)

    def _dispatch_edit_file(self, args: Dict[str, Any]) -> ToolResult:
        path, err = _validate_str(args, "path", ToolName.EDIT_FILE.value, required=True)
        if err:
            return self._arg_error(ToolName.EDIT_FILE, err)

        search_block, err = _validate_str(args, "search_block", ToolName.EDIT_FILE.value, required=True, allow_empty=False)
        if err:
            return self._arg_error(ToolName.EDIT_FILE, err)

        replace_block, err = _validate_str(args, "replace_block", ToolName.EDIT_FILE.value, required=True, allow_empty=True)
        if err:
            return self._arg_error(ToolName.EDIT_FILE, err)

        return self.edit_file(path=path, search_block=search_block, replace_block=replace_block)

    def _dispatch_write_file(self, args: Dict[str, Any]) -> ToolResult:
        path, err = _validate_str(args, "path", ToolName.WRITE_FILE.value, required=True)
        if err:
            return self._arg_error(ToolName.WRITE_FILE, err)

        content, err = _validate_str(args, "content", ToolName.WRITE_FILE.value, required=True, allow_empty=True)
        if err:
            return self._arg_error(ToolName.WRITE_FILE, err)

        return self.write_file(path=path, content=content)

    def _dispatch_run_bash(self, args: Dict[str, Any]) -> ToolResult:
        command, err = _validate_str(args, "command", ToolName.RUN_BASH.value, required=True)
        if err:
            return self._arg_error(ToolName.RUN_BASH, err)

        timeout, err = _validate_int(args, "timeout", ToolName.RUN_BASH.value, required=False, default=None)
        if err:
            return self._arg_error(ToolName.RUN_BASH, err)

        if timeout is not None and timeout <= 0:
            return self._arg_error(ToolName.RUN_BASH, f"Invalid timeout: {timeout}. Must be a positive integer.")

        return self.run_bash(command=command, timeout=timeout)

    def _dispatch_run_tests(self, args: Dict[str, Any]) -> ToolResult:
        test_command, err = _validate_str(args, "test_command", ToolName.RUN_TESTS.value, required=False, default=None, allow_empty=True)
        if err:
            return self._arg_error(ToolName.RUN_TESTS, err)

        timeout, err = _validate_int(args, "timeout", ToolName.RUN_TESTS.value, required=False, default=None)
        if err:
            return self._arg_error(ToolName.RUN_TESTS, err)

        if timeout is not None and timeout <= 0:
            return self._arg_error(ToolName.RUN_TESTS, f"Invalid timeout: {timeout}. Must be a positive integer.")

        return self.run_tests(test_command=test_command, timeout=timeout)

    def _dispatch_git_diff(self, args: Dict[str, Any]) -> ToolResult:
        path, err = _validate_str(args, "path", ToolName.GIT_DIFF.value, required=False, default=None, allow_empty=False)
        if err:
            return self._arg_error(ToolName.GIT_DIFF, err)

        return self.git_diff(path=path)

    def _dispatch_git_status(self, args: Dict[str, Any]) -> ToolResult:
        return self.git_status()

    def _dispatch_grep_search(self, args: Dict[str, Any]) -> ToolResult:
        query, err = _validate_str(args, "query", ToolName.GREP_SEARCH.value, required=True)
        if err:
            return self._arg_error(ToolName.GREP_SEARCH, err)

        path, err = _validate_str(args, "path", ToolName.GREP_SEARCH.value, required=False, default=".", allow_empty=False)
        if err:
            return self._arg_error(ToolName.GREP_SEARCH, err)

        return self.grep_search(query=query, path=path)

    def _dispatch_list_directory(self, args: Dict[str, Any]) -> ToolResult:
        path, err = _validate_str(args, "path", ToolName.LIST_DIRECTORY.value, required=False, default=".", allow_empty=False)
        if err:
            return self._arg_error(ToolName.LIST_DIRECTORY, err)

        return self.list_directory(path=path)

    @staticmethod
    def _arg_error(tool_name: ToolName, message: str) -> ToolResult:
        """Returns a standardized argument error result."""
        return ToolResult(
            tool_name=tool_name,
            success=False,
            output="",
            exit_code=1,
            error=message,
            duration_sec=0.0,
        )
