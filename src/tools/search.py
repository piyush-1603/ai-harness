"""Search and exploration operations for Track C tools: grep_search, list_directory.

Provides structured repository navigation while skipping noise directories
and preventing path traversal outside the workspace.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import time
from typing import List, Optional, Set

from src.common.types import ToolName, ToolResult
from src.tools.file_ops import resolve_workspace_path, truncate_output

NOISE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".eggs",
    ".harness_state",
}

NOISE_FILES: Set[str] = {
    ".DS_Store",
}

MAX_GREP_MATCHES = 100


def _is_binary_file(path: Path) -> bool:
    """Checks if a file appears to be binary by scanning the first 1024 bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
            return b"\0" in chunk
    except Exception:
        return True


def grep_search(
    query: str,
    path: str = ".",
    workspace_dir: Optional[str] = None,
    max_matches: int = MAX_GREP_MATCHES,
) -> ToolResult:
    """Recursively searches for query (regex or literal) within path.

    Returns matching file paths, line numbers, and line contents.
    Skips noise directories and binary files.
    """
    start_time = time.perf_counter()
    if not query:
        return ToolResult(
            tool_name=ToolName.GREP_SEARCH,
            success=False,
            output="",
            error="Query string cannot be empty.",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        target_path = resolve_workspace_path(path, workspace_dir)
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.GREP_SEARCH,
            success=False,
            output="",
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    if not target_path.exists():
        return ToolResult(
            tool_name=ToolName.GREP_SEARCH,
            success=False,
            output="",
            error=f"Search path does not exist: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        pattern = re.compile(query, re.MULTILINE)
    except re.error:
        # Fall back to literal string search if query is not valid regex
        pattern = re.compile(re.escape(query), re.MULTILINE)

    matches: List[str] = []
    total_found = 0

    def _search_file(file_path: Path):
        nonlocal total_found
        if _is_binary_file(file_path):
            return

        try:
            rel_path = file_path.relative_to(ws_path)
        except ValueError:
            rel_path = file_path

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        for line_idx, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                total_found += 1
                if len(matches) < max_matches:
                    clean_line = line.strip()
                    matches.append(f"{rel_path}:{line_idx}: {clean_line}")
                if len(matches) >= max_matches:
                    break

    if target_path.is_file():
        if target_path.name not in NOISE_FILES:
            _search_file(target_path)
    else:
        for root, dirs, files in os.walk(target_path):
            # Prune noise directories in-place before traversing
            dirs[:] = [d for d in dirs if d not in NOISE_DIRS]
            # Deterministically order subdirectories in-place
            dirs.sort()

            # Deterministically process sorted filenames
            for file_name in sorted(files):
                if file_name in NOISE_FILES:
                    continue
                file_path = Path(root) / file_name
                _search_file(file_path)
                if len(matches) >= max_matches:
                    break

            if len(matches) >= max_matches:
                break

    duration = time.perf_counter() - start_time
    if not matches:
        return ToolResult(
            tool_name=ToolName.GREP_SEARCH,
            success=True,
            output=f"No matches found for query '{query}' in '{path}'.",
            duration_sec=duration,
        )

    output = "\n".join(matches)
    if total_found > len(matches):
        output += f"\n\n[Output truncated: found {total_found} matches, showing first {len(matches)}]"

    return ToolResult(
        tool_name=ToolName.GREP_SEARCH,
        success=True,
        output=truncate_output(output),
        duration_sec=duration,
    )


def list_directory(
    path: str = ".",
    workspace_dir: Optional[str] = None,
) -> ToolResult:
    """Lists files and directories in path deterministically with metadata."""
    start_time = time.perf_counter()
    try:
        target_path = resolve_workspace_path(path, workspace_dir)
        ws_path = resolve_workspace_path(".", workspace_dir)
    except PermissionError as e:
        return ToolResult(
            tool_name=ToolName.LIST_DIRECTORY,
            success=False,
            output="",
            error=str(e),
            duration_sec=time.perf_counter() - start_time,
        )

    if not target_path.exists():
        return ToolResult(
            tool_name=ToolName.LIST_DIRECTORY,
            success=False,
            output="",
            error=f"Directory not found: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    if target_path.is_file():
        return ToolResult(
            tool_name=ToolName.LIST_DIRECTORY,
            success=False,
            output="",
            error=f"Path is a file, not a directory: '{path}'",
            duration_sec=time.perf_counter() - start_time,
        )

    try:
        entries = sorted(target_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as e:
        return ToolResult(
            tool_name=ToolName.LIST_DIRECTORY,
            success=False,
            output="",
            error=f"Failed to read directory '{path}': {str(e)}",
            duration_sec=time.perf_counter() - start_time,
        )

    lines: List[str] = []
    try:
        rel_display = str(target_path.relative_to(ws_path))
        if rel_display == ".":
            rel_display = "./"
        else:
            rel_display = f"./{rel_display}/"
    except ValueError:
        rel_display = str(target_path)

    lines.append(f"Directory listing of {rel_display}:")

    for entry in entries:
        if entry.name in NOISE_DIRS or entry.name in NOISE_FILES:
            continue
        if entry.is_dir():
            lines.append(f"  [DIR]  {entry.name}/")
        elif entry.is_file():
            try:
                size_bytes = entry.stat().st_size
                lines.append(f"  [FILE] {entry.name:<30} ({size_bytes:,} bytes)")
            except Exception:
                lines.append(f"  [FILE] {entry.name}")
        elif entry.is_symlink():
            lines.append(f"  [LINK] {entry.name}")

    if len(lines) == 1:
        lines.append("  <empty directory>")

    output = "\n".join(lines)
    return ToolResult(
        tool_name=ToolName.LIST_DIRECTORY,
        success=True,
        output=truncate_output(output),
        duration_sec=time.perf_counter() - start_time,
    )
