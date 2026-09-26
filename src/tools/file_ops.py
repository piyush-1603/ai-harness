"""File operations for Track C tools: read_file, edit_file, write_file.

Enforces workspace sandboxing, path traversal protection, exact block matching,
and bounded output.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from typing import Optional, Union

from src.common.types import ToolName, ToolResult

MAX_OUTPUT_CHARS = 10000


def truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Safely bounds output to prevent context window explosion."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    omitted = len(text) - max_chars
    return (
        f"{text[:half]}\n\n"
        f"[... Output truncated: {omitted} characters omitted ...]\n\n"
        f"{text[-half:]}"
    )


def resolve_workspace_path(path: Union[str, Path], workspace_dir: Optional[str] = None) -> Path:
    """Resolves path relative to workspace_dir and ensures it cannot escape."""
    ws_str = workspace_dir or os.environ.get("HARNESS_WORKSPACE_DIR", ".")
    ws = Path(ws_str).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = (ws / target).resolve()
    else:
        target = target.resolve()

    try:
        target.relative_to(ws)
    except ValueError:
        raise PermissionError(f"Path traversal detected: '{path}' escapes workspace '{ws}'")

    return target


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int = -1,
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Reads a file within a 1-based line range [start_line, end_line].

    If end_line is -1, reads to the end of the file.
    Rejects directories, missing files, and invalid line ranges.
    """
    start_time = time.perf_counter()
    try:
        target_path = resolve_workspace_path(path, workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    if not target_path.exists():
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=f"File not found: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    if target_path.is_dir():
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=f"Path is a directory, not a file: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    if start_line < 1:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=f"Invalid start_line: {start_line}. Line numbers are 1-based and must be >= 1.",
            duration_sec=time.perf_counter() - start_time,
        )

    if end_line != -1 and end_line < start_line:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=f"Invalid line range: start_line ({start_line}) cannot be greater than end_line ({end_line}).",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=False,
            output="",
            error=f"Failed to read file '{path}': {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=True,
            output="<empty file>",
            duration_sec=time.perf_counter() - start_time,
        )

    if start_line > total_lines:
        return ToolResult(
            tool_name=ToolName.READ_FILE,
            success=True,
            output=f"<start_line {start_line} is beyond end of file ({total_lines} total lines)>",
            duration_sec=time.perf_counter() - start_time,
        )

    actual_end = total_lines if (end_line == -1 or end_line > total_lines) else end_line
    actual_start = start_line

    selected_lines = lines[actual_start - 1 : actual_end]
    formatted = "\n".join(
        f"{actual_start + idx:6d} | {line}" for idx, line in enumerate(selected_lines)
    )

    bounded_output = truncate_output(formatted)
    return ToolResult(
        tool_name=ToolName.READ_FILE,
        success=True,
        output=bounded_output,
        duration_sec=time.perf_counter() - start_time,
    )


def edit_file(
    path: str,
    search_block: str,
    replace_block: str,
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Edits a file by replacing exactly one occurrence of search_block with replace_block.

    Fails if search_block is empty, not found, or matches more than once.
    """
    start_time = time.perf_counter()
    if not search_block:
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error="EDIT_FAILED: search_block cannot be empty.",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        target_path = resolve_workspace_path(path, workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    if not target_path.exists():
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: File not found: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    if target_path.is_dir():
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: Path is a directory: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: Error reading '{path}': {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    match_count = content.count(search_block)
    if match_count == 0:
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: search_block not found in '{path}'.",
            duration_sec=time.perf_counter() - start_time,
        )

    if match_count > 1:
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=(
                f"EDIT_FAILED: search_block matched {match_count} locations in '{path}'. "
                f"It must match exactly once. Add more surrounding context to disambiguate."
            ),
            duration_sec=time.perf_counter() - start_time,
        )

    new_content = content.replace(search_block, replace_block, 1)

    temp_file: Optional[Path] = None
    try:
        # Create temporary file in target_path.parent to ensure same filesystem for atomic os.replace
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".{target_path.name}.tmp_",
            delete=False,
        ) as tf:
            temp_file = Path(tf.name)
            tf.write(new_content)
            tf.flush()
            os.fsync(tf.fileno())

        os.replace(temp_file, target_path)
    except Exception as e:
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass
        return ToolResult(
            tool_name=ToolName.EDIT_FILE,
            success=False,
            output="",
            error=f"EDIT_FAILED: Error writing updated file '{path}': {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    return ToolResult(
        tool_name=ToolName.EDIT_FILE,
        success=True,
        output=f"Successfully edited '{path}' (1 replacement applied).",
        duration_sec=time.perf_counter() - start_time,
    )


def write_file(
    path: str,
    content: str,
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Writes content to a file, creating parent directories if necessary."""
    start_time = time.perf_counter()
    try:
        target_path = resolve_workspace_path(path, workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.WRITE_FILE,
            success=False,
            output="",
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    if target_path.exists() and target_path.is_dir():
        return ToolResult(
            tool_name=ToolName.WRITE_FILE,
            success=False,
            output="",
            error=f"Cannot write: '{path}' is an existing directory.",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return ToolResult(
            tool_name=ToolName.WRITE_FILE,
            success=False,
            output="",
            error=f"Failed to write file '{path}': {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    return ToolResult(
        tool_name=ToolName.WRITE_FILE,
        success=True,
        output=f"Successfully wrote {len(content)} characters to '{path}'.",
        duration_sec=time.perf_counter() - start_time,
    )
