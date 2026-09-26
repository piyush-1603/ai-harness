"""Deterministic symbol and import extraction for Python and JavaScript/TypeScript."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Optional


@dataclass
class SymbolRecord:
    """
    Structured record representing a declared symbol (function, class, method).
    """
    name: str
    kind: str
    file: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    parent: Optional[str] = None
    exported: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "parent": self.parent,
            "exported": self.exported,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SymbolRecord:
        return cls(
            name=data["name"],
            kind=data["kind"],
            file=data["file"],
            line_start=data.get("line_start"),
            line_end=data.get("line_end"),
            parent=data.get("parent"),
            exported=bool(data.get("exported", True)),
        )


@dataclass
class FileSymbols:
    """
    Structured collection of symbols and imports discovered in a source file.
    """
    path: str
    language: str
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "symbols": [s.to_dict() for s in self.symbols],
            "imports": list(self.imports),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileSymbols:
        return cls(
            path=data["path"],
            language=data.get("language", ""),
            symbols=[SymbolRecord.from_dict(s) for s in data.get("symbols", [])],
            imports=list(data.get("imports", [])),
        )


# ---------------------------------------------------------------------------
# Python Extraction via built-in `ast`
# ---------------------------------------------------------------------------

def extract_python_symbols(
    content: str,
    rel_path: str,
    max_symbols: int = 100,
) -> FileSymbols:
    """
    Extract functions, classes, methods, and imports from Python source using `ast`.
    Malformed source must never raise an exception.
    """
    try:
        tree = ast.parse(content, filename=rel_path)
    except (SyntaxError, IndentationError, ValueError, MemoryError):
        return FileSymbols(path=rel_path, language="Python", symbols=[], imports=[])

    symbols: list[SymbolRecord] = []
    imports_set: set[str] = set()

    # Detect __all__ if explicitly defined at module level
    all_exports: Optional[set[str]] = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(stmt.value, (ast.List, ast.Tuple, ast.Set)):
                        all_exports = {
                            elt.value
                            for elt in stmt.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }

    # Extract imports across the AST
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports_set.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            level_dots = "." * node.level if node.level else ""
            if node.module:
                imports_set.add(f"{level_dots}{node.module}")
            elif level_dots:
                imports_set.add(level_dots)

    # Extract top-level symbols and class methods
    for node in tree.body:
        if len(symbols) >= max_symbols:
            break

        if isinstance(node, ast.FunctionDef):
            is_exported = (node.name in all_exports) if all_exports is not None else not node.name.startswith("_")
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind="function",
                    file=rel_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    parent=None,
                    exported=is_exported,
                )
            )
        elif isinstance(node, ast.AsyncFunctionDef):
            is_exported = (node.name in all_exports) if all_exports is not None else not node.name.startswith("_")
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind="async_function",
                    file=rel_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    parent=None,
                    exported=is_exported,
                )
            )
        elif isinstance(node, ast.ClassDef):
            is_exported = (node.name in all_exports) if all_exports is not None else not node.name.startswith("_")
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind="class",
                    file=rel_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    parent=None,
                    exported=is_exported,
                )
            )

            # Class methods
            for item in node.body:
                if len(symbols) >= max_symbols:
                    break
                if isinstance(item, ast.FunctionDef):
                    symbols.append(
                        SymbolRecord(
                            name=item.name,
                            kind="method",
                            file=rel_path,
                            line_start=item.lineno,
                            line_end=getattr(item, "end_lineno", item.lineno),
                            parent=node.name,
                            exported=not item.name.startswith("_"),
                        )
                    )
                elif isinstance(item, ast.AsyncFunctionDef):
                    symbols.append(
                        SymbolRecord(
                            name=item.name,
                            kind="async_method",
                            file=rel_path,
                            line_start=item.lineno,
                            line_end=getattr(item, "end_lineno", item.lineno),
                            parent=node.name,
                            exported=not item.name.startswith("_"),
                        )
                    )

    # Sort symbols deterministically by start line, then name
    symbols.sort(key=lambda s: (s.line_start or 0, s.name))

    return FileSymbols(
        path=rel_path,
        language="Python",
        symbols=symbols,
        imports=sorted(list(imports_set)),
    )


# ---------------------------------------------------------------------------
# JavaScript / TypeScript Extraction (Deterministic Regex / Line-based)
# ---------------------------------------------------------------------------

# Function declaration: function foo(...) or async function foo(...)
RE_JS_FUNCTION = re.compile(
    r"^(?P<export>export\s+)?(?P<default>default\s+)?(?P<async>async\s+)?function\s+(?P<name>[a-zA-Z0-9_$]+)\s*\("
)

# Class declaration: class Foo or export class Foo
RE_JS_CLASS = re.compile(
    r"^(?P<export>export\s+)?(?P<default>default\s+)?class\s+(?P<name>[a-zA-Z0-9_$]+)"
)

# Arrow function or assigned function: const foo = (...) => or export const foo = function(...)
RE_JS_VAR_FUNCTION = re.compile(
    r"^(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[a-zA-Z0-9_$]+)\s*(?::\s*[^=]+)?=\s*(?P<async>async\s+)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
)
RE_JS_VAR_FUNC_EXPR = re.compile(
    r"^(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[a-zA-Z0-9_$]+)\s*(?::\s*[^=]+)?=\s*(?P<async>async\s+)?function"
)

# Class method: methodName(...) { or async methodName(...) {
RE_JS_METHOD = re.compile(
    r"^\s+(?:(?:public|private|protected|static|readonly|override)\s+)*(?P<async>async\s+)?(?P<name>[a-zA-Z0-9_$]+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{"
)

# ES Module imports: import ... from '...' or import '...'
RE_ES_IMPORT_FROM = re.compile(r"""(?:^|\s)import\s+.*?\s+from\s+['"]([^'"]+)['"]""")
RE_ES_IMPORT_BARE = re.compile(r"""(?:^|\s)import\s+['"]([^'"]+)['"]""")

# CommonJS require: require('...')
RE_CJS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def extract_javascript_symbols(
    content: str,
    rel_path: str,
    language: str,
    max_symbols: int = 100,
) -> FileSymbols:
    """
    Extract functions, classes, methods, and imports from JS/TS source code deterministically.
    Conservative regex parsing to avoid false positives.
    """
    symbols: list[SymbolRecord] = []
    imports_set: set[str] = set()

    lines = content.splitlines()

    current_class: Optional[str] = None
    class_brace_depth = 0

    control_keywords = {"if", "for", "while", "switch", "catch", "with"}

    for line_idx, raw_line in enumerate(lines, start=1):
        if len(symbols) >= max_symbols:
            break

        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            continue

        # Check ES Module imports
        for match in RE_ES_IMPORT_FROM.finditer(raw_line):
            imports_set.add(match.group(1))
        for match in RE_ES_IMPORT_BARE.finditer(raw_line):
            imports_set.add(match.group(1))

        # Check CommonJS require
        for match in RE_CJS_REQUIRE.finditer(raw_line):
            imports_set.add(match.group(1))

        # Track class brace depth
        if current_class is not None:
            open_braces = raw_line.count("{")
            close_braces = raw_line.count("}")
            class_brace_depth += (open_braces - close_braces)

            if class_brace_depth <= 0:
                current_class = None
                class_brace_depth = 0
            else:
                # Inside class body, check for methods
                method_match = RE_JS_METHOD.match(raw_line)
                if method_match:
                    name = method_match.group("name")
                    if name not in control_keywords and name != "constructor":
                        is_async = bool(method_match.group("async"))
                        symbols.append(
                            SymbolRecord(
                                name=name,
                                kind="async_method" if is_async else "method",
                                file=rel_path,
                                line_start=line_idx,
                                line_end=None,
                                parent=current_class,
                                exported=False,
                            )
                        )
                continue

        # Check top-level function declaration
        fn_match = RE_JS_FUNCTION.match(line)
        if fn_match:
            is_exported = bool(fn_match.group("export") or fn_match.group("default"))
            is_async = bool(fn_match.group("async"))
            symbols.append(
                SymbolRecord(
                    name=fn_match.group("name"),
                    kind="async_function" if is_async else "function",
                    file=rel_path,
                    line_start=line_idx,
                    line_end=None,
                    parent=None,
                    exported=is_exported,
                )
            )
            continue

        # Check class declaration
        class_match = RE_JS_CLASS.match(line)
        if class_match:
            is_exported = bool(class_match.group("export") or class_match.group("default"))
            class_name = class_match.group("name")
            symbols.append(
                SymbolRecord(
                    name=class_name,
                    kind="class",
                    file=rel_path,
                    line_start=line_idx,
                    line_end=None,
                    parent=None,
                    exported=is_exported,
                )
            )
            current_class = class_name
            class_brace_depth = raw_line.count("{") - raw_line.count("}")
            if class_brace_depth <= 0:
                current_class = None
                class_brace_depth = 0
            continue

        # Check arrow function
        var_match = RE_JS_VAR_FUNCTION.match(line)
        if var_match:
            is_exported = bool(var_match.group("export"))
            is_async = bool(var_match.group("async"))
            symbols.append(
                SymbolRecord(
                    name=var_match.group("name"),
                    kind="async_function" if is_async else "function",
                    file=rel_path,
                    line_start=line_idx,
                    line_end=None,
                    parent=None,
                    exported=is_exported,
                )
            )
            continue

        # Check function expression
        expr_match = RE_JS_VAR_FUNC_EXPR.match(line)
        if expr_match:
            is_exported = bool(expr_match.group("export"))
            is_async = bool(expr_match.group("async"))
            symbols.append(
                SymbolRecord(
                    name=expr_match.group("name"),
                    kind="async_function" if is_async else "function",
                    file=rel_path,
                    line_start=line_idx,
                    line_end=None,
                    parent=None,
                    exported=is_exported,
                )
            )
            continue

    symbols.sort(key=lambda s: (s.line_start or 0, s.name))

    return FileSymbols(
        path=rel_path,
        language=language,
        symbols=symbols,
        imports=sorted(list(imports_set)),
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def extract_file_symbols(
    file_path: Path,
    rel_path: str,
    language: str,
    max_symbols: int = 100,
) -> Optional[FileSymbols]:
    """
    Extract symbols and imports from supported source code files.
    Returns None if language is not supported or file cannot be read.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    if language == "Python":
        return extract_python_symbols(content, rel_path, max_symbols=max_symbols)
    elif language in ("JavaScript", "TypeScript"):
        return extract_javascript_symbols(content, rel_path, language=language, max_symbols=max_symbols)

    return None


# ---------------------------------------------------------------------------
# Rendering Helpers
# ---------------------------------------------------------------------------

def format_file_symbols(fs: FileSymbols) -> list[str]:
    """
    Format a FileSymbols object into compact, deterministic lines.
    """
    lines: list[str] = [fs.path]

    # Group methods by parent class
    classes: dict[str, list[str]] = {}
    top_level_fns: list[str] = []
    other_syms: list[str] = []

    for s in fs.symbols:
        if s.kind == "class":
            if s.name not in classes:
                classes[s.name] = []
        elif s.kind in ("method", "async_method"):
            if s.parent:
                classes.setdefault(s.parent, []).append(s.name)
            else:
                top_level_fns.append(s.name)
        elif s.kind in ("function", "async_function"):
            top_level_fns.append(s.name)
        else:
            other_syms.append(s.name)

    for cls_name in sorted(classes.keys()):
        lines.append(f"  class: {cls_name}")
        methods = classes[cls_name]
        if methods:
            lines.append(f"  methods: {', '.join(methods)}")

    if top_level_fns:
        lines.append(f"  functions: {', '.join(top_level_fns)}")

    if other_syms:
        lines.append(f"  symbols: {', '.join(other_syms)}")

    if fs.imports:
        lines.append(f"  imports: {', '.join(fs.imports)}")

    return lines
