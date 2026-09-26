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
    local_imports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "symbols": [s.to_dict() for s in self.symbols],
            "imports": list(self.imports),
            "local_imports": list(self.local_imports),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileSymbols:
        return cls(
            path=data["path"],
            language=data.get("language", ""),
            symbols=[SymbolRecord.from_dict(s) for s in data.get("symbols", [])],
            imports=list(data.get("imports", [])),
            local_imports=list(data.get("local_imports", [])),
        )


# ---------------------------------------------------------------------------
# Python Extraction via built-in `ast`
# ---------------------------------------------------------------------------

def extract_python_symbols(
    content: str,
    rel_path: str,
    max_symbols: int = 100,
    repo_files: Optional[set[str]] = None,
) -> FileSymbols:
    """
    Extract functions, classes, methods, and imports from Python source using `ast`.
    Malformed source must never raise an exception.
    """
    try:
        tree = ast.parse(content, filename=rel_path)
    except (SyntaxError, IndentationError, ValueError, MemoryError):
        return FileSymbols(path=rel_path, language="Python", symbols=[], imports=[], local_imports=[])

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
    raw_imports = sorted(list(imports_set))

    # Resolve local imports if repo_files is provided
    local_imports: list[str] = []
    if repo_files is not None:
        local_imports = resolve_file_local_imports(
            file_path=Path(rel_path),
            rel_path=rel_path,
            language="Python",
            imports=raw_imports,
            repo_files=repo_files,
            content=content,
        )

    return FileSymbols(
        path=rel_path,
        language="Python",
        symbols=symbols,
        imports=raw_imports,
        local_imports=local_imports,
    )


# ---------------------------------------------------------------------------
# JavaScript / TypeScript Extraction (Deterministic Regex / Line-based)
# ---------------------------------------------------------------------------

RE_JS_FUNCTION = re.compile(
    r"^(?P<export>export\s+)?(?P<default>default\s+)?(?P<async>async\s+)?function\s+(?P<name>[a-zA-Z0-9_$]+)\s*\("
)

RE_JS_CLASS = re.compile(
    r"^(?P<export>export\s+)?(?P<default>default\s+)?class\s+(?P<name>[a-zA-Z0-9_$]+)"
)

RE_JS_VAR_FUNCTION = re.compile(
    r"^(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[a-zA-Z0-9_$]+)\s*(?::\s*[^=]+)?=\s*(?P<async>async\s+)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
)
RE_JS_VAR_FUNC_EXPR = re.compile(
    r"^(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[a-zA-Z0-9_$]+)\s*(?::\s*[^=]+)?=\s*(?P<async>async\s+)?function"
)

RE_JS_METHOD = re.compile(
    r"^\s+(?:(?:public|private|protected|static|readonly|override)\s+)*(?P<async>async\s+)?(?P<name>[a-zA-Z0-9_$]+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{"
)

RE_ES_IMPORT_FROM = re.compile(r"""(?:^|\s)import\s+.*?\s+from\s+['"]([^'"]+)['"]""")
RE_ES_IMPORT_BARE = re.compile(r"""(?:^|\s)import\s+['"]([^'"]+)['"]""")
RE_CJS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def extract_javascript_symbols(
    content: str,
    rel_path: str,
    language: str,
    max_symbols: int = 100,
    repo_files: Optional[set[str]] = None,
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
    raw_imports = sorted(list(imports_set))

    local_imports: list[str] = []
    if repo_files is not None:
        local_imports = resolve_file_local_imports(
            file_path=Path(rel_path),
            rel_path=rel_path,
            language=language,
            imports=raw_imports,
            repo_files=repo_files,
            content=content,
        )

    return FileSymbols(
        path=rel_path,
        language=language,
        symbols=symbols,
        imports=raw_imports,
        local_imports=local_imports,
    )


# ---------------------------------------------------------------------------
# Local Import Resolution Helpers (Phase B3.2.1)
# ---------------------------------------------------------------------------

def resolve_python_import_string(
    import_str: str,
    importer_rel_path: str,
    repo_files: set[str],
) -> Optional[str]:
    """
    Resolve a Python import string to a repository-relative file path if local.
    Returns None if the import cannot be resolved to a local file.
    """
    # Relative import (starts with one or more dots)
    if import_str.startswith("."):
        leading_dots = len(import_str) - len(import_str.lstrip("."))
        module_part = import_str.lstrip(".")
        importer_dir = Path(importer_rel_path).parent

        target_dir = importer_dir
        for _ in range(leading_dots - 1):
            target_dir = target_dir.parent

        if module_part:
            subpath = module_part.replace(".", "/")
            base = os.path.normpath((target_dir / subpath).as_posix()).replace("\\", "/")
        else:
            base = os.path.normpath(target_dir.as_posix()).replace("\\", "/")

        for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi", base):
            if candidate in repo_files:
                return candidate
        return None

    # Absolute project import (relative to repository root)
    path_base = import_str.replace(".", "/")
    for candidate in (f"{path_base}.py", f"{path_base}/__init__.py", f"{path_base}.pyi", path_base):
        if candidate in repo_files:
            return candidate

    return None


def resolve_javascript_import_string(
    import_str: str,
    importer_rel_path: str,
    repo_files: set[str],
) -> Optional[str]:
    """
    Resolve a JavaScript/TypeScript relative import string to a repository-relative file.
    Does not attempt node_modules or package resolution.
    """
    if not import_str.startswith("."):
        return None

    importer_dir = Path(importer_rel_path).parent
    combined = os.path.normpath((importer_dir / import_str).as_posix()).replace("\\", "/")

    # 1. Exact match (e.g. ./styles.css)
    if combined in repo_files:
        return combined

    # 2. Common extensions
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        cand = f"{combined}{ext}"
        if cand in repo_files:
            return cand

    # 3. Directory index files
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        cand = f"{combined}/index{ext}"
        if cand in repo_files:
            return cand

    return None


def resolve_file_local_imports(
    file_path: Path,
    rel_path: str,
    language: str,
    imports: list[str],
    repo_files: set[str],
    content: Optional[str] = None,
) -> list[str]:
    """
    Determine which imports resolve to files/modules inside the scanned repository.
    Deterministic, conservative, and never guesses.
    """
    local_resolved: set[str] = set()

    if language == "Python":
        # Resolve from raw imports list first
        for imp in imports:
            resolved = resolve_python_import_string(imp, rel_path, repo_files)
            if resolved:
                local_resolved.add(resolved)

        # Also inspect AST to resolve `from package import submodule`
        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                content = ""

        if content:
            try:
                tree = ast.parse(content, filename=rel_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        level_dots = "." * node.level if node.level else ""
                        base_mod = f"{level_dots}{node.module}" if node.module else level_dots
                        for alias in node.names:
                            candidate_imp = f"{base_mod}.{alias.name}" if base_mod and not base_mod.endswith(".") else f"{base_mod}{alias.name}"
                            resolved = resolve_python_import_string(candidate_imp, rel_path, repo_files)
                            if resolved:
                                local_resolved.add(resolved)
            except Exception:
                pass

    elif language in ("JavaScript", "TypeScript"):
        for imp in imports:
            resolved = resolve_javascript_import_string(imp, rel_path, repo_files)
            if resolved:
                local_resolved.add(resolved)

    return sorted(list(local_resolved))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def extract_file_symbols(
    file_path: Path,
    rel_path: str,
    language: str,
    max_symbols: int = 100,
    repo_files: Optional[set[str]] = None,
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
        return extract_python_symbols(content, rel_path, max_symbols=max_symbols, repo_files=repo_files)
    elif language in ("JavaScript", "TypeScript"):
        return extract_javascript_symbols(content, rel_path, language=language, max_symbols=max_symbols, repo_files=repo_files)

    return None


# ---------------------------------------------------------------------------
# Rendering Helpers
# ---------------------------------------------------------------------------

def format_file_symbols(fs: FileSymbols) -> list[str]:
    """
    Format a FileSymbols object into compact, deterministic lines.
    Prefers local imports over raw imports when available.
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

    # Prefer local imports in compact rendering
    if fs.local_imports:
        lines.append(f"  local imports: {', '.join(fs.local_imports)}")
    elif fs.imports:
        lines.append(f"  imports: {', '.join(fs.imports)}")

    return lines
