"""Central ToolEngine registry for Track C.

Dispatches strongly-typed ToolCall invocations to specialized tool modules
(file_ops, shell_ops, search). Enforces safety, validates arguments, and ensures
no tool exception ever crashes the orchestrator.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from src.common.types import ToolCall, ToolName, ToolResult
from src.tools.file_ops import edit_file, read_file, write_file
from src.tools.search import grep_search, list_directory
from src.tools.shell_ops import git_diff, git_status, run_bash, run_tests


class ToolEngine:
    """Core tool execution engine implementing dispatch across all canonical ToolName values."""

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        default_timeout: int = 60,
    ):
        self.workspace_dir = workspace_dir or os.environ.get("HARNESS_WORKSPACE_DIR", ".")
        self.default_timeout = default_timeout

    # -------------------------------------------------------------------------
    # Public Tool Operations
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
    # Generic Dispatcher
    # -------------------------------------------------------------------------

    def execute(self, call: ToolCall) -> ToolResult:
        """Dispatches a ToolCall to the appropriate tool method.

        Guarantees that no exception crashes the orchestrator: every failure
        is caught and formatted into a valid ToolResult.
        """
        start_time = time.perf_counter()
        tool_name = call.tool_name
        args: Dict[str, Any] = call.tool_args or {}

        try:
            if tool_name == ToolName.READ_FILE:
                if "path" not in args:
                    return self._missing_arg_result(tool_name, "path", start_time)
                return self.read_file(
                    path=str(args["path"]),
                    start_line=int(args.get("start_line", 1)),
                    end_line=int(args.get("end_line", -1)),
                )

            elif tool_name == ToolName.EDIT_FILE:
                for req in ("path", "search_block", "replace_block"):
                    if req not in args:
                        return self._missing_arg_result(tool_name, req, start_time)
                return self.edit_file(
                    path=str(args["path"]),
                    search_block=str(args["search_block"]),
                    replace_block=str(args["replace_block"]),
                )

            elif tool_name == ToolName.WRITE_FILE:
                for req in ("path", "content"):
                    if req not in args:
                        return self._missing_arg_result(tool_name, req, start_time)
                return self.write_file(
                    path=str(args["path"]),
                    content=str(args["content"]),
                )

            elif tool_name == ToolName.RUN_BASH:
                if "command" not in args:
                    return self._missing_arg_result(tool_name, "command", start_time)
                timeout_val = int(args["timeout"]) if "timeout" in args else None
                return self.run_bash(
                    command=str(args["command"]),
                    timeout=timeout_val,
                )

            elif tool_name == ToolName.RUN_TESTS:
                test_cmd = str(args["test_command"]) if "test_command" in args else None
                timeout_val = int(args["timeout"]) if "timeout" in args else None
                return self.run_tests(
                    test_command=test_cmd,
                    timeout=timeout_val,
                )

            elif tool_name == ToolName.GIT_DIFF:
                path_arg = str(args["path"]) if "path" in args else None
                return self.git_diff(path=path_arg)

            elif tool_name == ToolName.GIT_STATUS:
                return self.git_status()

            elif tool_name == ToolName.GREP_SEARCH:
                if "query" not in args:
                    return self._missing_arg_result(tool_name, "query", start_time)
                return self.grep_search(
                    query=str(args["query"]),
                    path=str(args.get("path", ".")),
                )

            elif tool_name == ToolName.LIST_DIRECTORY:
                return self.list_directory(
                    path=str(args.get("path", ".")),
                )

            else:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output="",
                    exit_code=1,
                    error=f"Unsupported tool: {tool_name}",
                    duration_sec=time.perf_counter() - start_time,
                )

        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                exit_code=1,
                error=f"Tool execution encountered an unhandled error: {type(e).__name__}: {str(e)}",
                duration_sec=time.perf_counter() - start_time,
            )

    @staticmethod
    def _missing_arg_result(tool_name: ToolName, arg_name: str, start_time: float) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            output="",
            exit_code=1,
            error=f"Missing required argument '{arg_name}' for tool '{tool_name.value}'.",
            duration_sec=time.perf_counter() - start_time,
        )
