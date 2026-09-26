"""Deterministic repository scanner and indexer for the coding-agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from src.context.symbols import (
    FileSymbols,
    SymbolRecord,
    extract_file_symbols,
    format_file_symbols,
    resolve_file_local_imports,
)


# ---------------------------------------------------------------------------
# File Roles
# ---------------------------------------------------------------------------

class FileRole(str, Enum):
    """
    Deterministic primary role classification for a repository file.
    Each file has exactly one primary role.
    """
    SOURCE = "SOURCE"
    TEST = "TEST"
    CONFIG = "CONFIG"
    DOCUMENTATION = "DOCUMENTATION"
    OTHER = "OTHER"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value.upper())


# ---------------------------------------------------------------------------
# Default Constants
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS: set[str] = {
    # Version control
    ".git",
    ".svn",
    ".hg",
    # Dependencies & Package Managers
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "ENV",
    "vendor",
    # Build & Distribution artifacts
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".output",
    "target",
    "bin",
    "obj",
    # Cache & Test coverage
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage",
    "htmlcov",
    ".cache",
    ".turbo",
    ".parcel-cache",
    ".sass-cache",
    # Python packaging
    ".tox",
    ".nox",
    ".eggs",
    # IDE & Harness Internal
    ".idea",
    ".vscode",
    ".harness",
    ".harness_state",
}

DEFAULT_IGNORE_PATTERNS: list[str] = [
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.class",
    "*.jar",
    "*.min.js",
    "*.min.css",
    "*.map",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
    "*.swo",
    "*~",
    "*.tmp",
]

DEFAULT_BINARY_EXTENSIONS: set[str] = {
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".bmp",
    ".tiff",
    ".psd",
    # Audio / Video
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".flac",
    ".webm",
    ".mkv",
    # Archives / Executables
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".exe",
    ".bin",
    ".iso",
    ".dmg",
    ".apk",
    # Fonts
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    # Data / Documents
    ".pdf",
    ".wasm",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".parquet",
}

KNOWN_TEXT_EXTENSIONS: set[str] = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".json",
    ".jsonc",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".sh",
    ".bash",
    ".zsh",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".cs",
    ".php",
    ".rb",
    ".sql",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
    "dockerfile",
    "makefile",
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".hpp": "C++ Header",
    ".cc": "C++",
    ".cxx": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".sql": "SQL",
}

DOC_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".rst",
    ".adoc",
    ".asciidoc",
}

DOC_DIR_NAMES: set[str] = {
    "doc",
    "docs",
    "documentation",
}

DOC_FILE_PREFIXES: tuple[str, ...] = (
    "readme",
    "prd",
    "license",
    "licence",
    "contributing",
    "changelog",
    "history",
    "roadmap",
    "code_of_conduct",
    "authors",
    "architecture",
)

CONFIG_FILENAMES: set[str] = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "cargo.toml",
    "cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "tsconfig.json",
    "jsconfig.json",
    "makefile",
    "cmakelists.txt",
    "pytest.ini",
    "tox.ini",
    "conftest.py",
    ".coveragerc",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".env.example",
    ".env.template",
}

CONFIG_PATTERNS: list[str] = [
    "requirements*.txt",
    "dockerfile*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose*.yml",
    "compose*.yaml",
    "jest.config.*",
    "vitest.config.*",
    "webpack.config.*",
    "vite.config.*",
    "rollup.config.*",
    "babel.config.*",
    ".eslintrc*",
    ".prettierrc*",
    "*.ini",
    "*.cfg",
]

TEST_FILE_PATTERNS: list[str] = [
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.mjs",
    "*.test.cjs",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.mjs",
    "*.spec.cjs",
    "*.spec.ts",
    "*.spec.tsx",
    "*_test.go",
    "*test.java",
    "*tests.java",
    "*testcase.java",
    "*test.kt",
    "*tests.kt",
    "*test.scala",
    "*tests.scala",
    "*test.cs",
    "*tests.cs",
    "*_test.cpp",
    "*_test.cc",
]

SOURCE_CODE_EXTENSIONS: set[str] = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".sql",
    ".graphql",
    ".gql",
}

TEST_DIR_NAMES: set[str] = {
    "tests",
    "test",
    "testing",
    "spec",
    "specs",
    "__tests__",
}

SOURCE_DIR_NAMES: set[str] = {
    "src",
    "lib",
    "app",
    "pkg",
    "core",
    "packages",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_binary_file(
    file_path: Path,
    binary_extensions: set[str] = DEFAULT_BINARY_EXTENSIONS,
    check_bytes: int = 1024,
) -> bool:
    """
    Determine if a file is binary without reading the entire content.
    Uses extension heuristics first, followed by reading at most check_bytes.
    """
    ext = file_path.suffix.lower()
    if ext in binary_extensions:
        return True
    if ext in KNOWN_TEXT_EXTENSIONS:
        return False
    name_lower = file_path.name.lower()
    if name_lower in CONFIG_FILENAMES or name_lower.startswith("readme") or name_lower.startswith("license"):
        return False

    try:
        with open(file_path, "rb") as f:
            chunk = f.read(check_bytes)
            if not chunk:
                return False
            if b"\x00" in chunk:
                return True
            try:
                chunk.decode("utf-8")
                return False
            except UnicodeDecodeError:
                return True
    except (OSError, PermissionError):
        return True


def is_config_file(rel_path_or_name: str) -> bool:
    """Check if file matches common project configuration files."""
    name_lower = Path(rel_path_or_name).name.lower()
    if name_lower in CONFIG_FILENAMES:
        return True
    return any(fnmatch.fnmatch(name_lower, pat) for pat in CONFIG_PATTERNS)


def is_documentation_file(rel_path: str) -> bool:
    """
    Check if a relative path corresponds to a documentation file.
    Based on extension, directory, or documentation filename conventions.
    """
    path = Path(rel_path)
    ext = path.suffix.lower()
    if ext in DOC_EXTENSIONS:
        return True

    # Inside a docs/ directory
    for part in path.parts[:-1]:
        if part.lower() in DOC_DIR_NAMES:
            return True

    name_lower = path.name.lower()
    if any(name_lower.startswith(prefix) for prefix in DOC_FILE_PREFIXES):
        return True

    return False


def is_important_file(rel_path_or_name: str) -> bool:
    """
    Check if filename matches common important root/configuration files.
    Preserves backward compatibility for important_files collection.
    """
    name_lower = Path(rel_path_or_name).name.lower()
    if is_config_file(name_lower):
        return True
    if name_lower.startswith("readme"):
        return True
    if name_lower.startswith("license") or name_lower.startswith("licence"):
        return True
    return False


def is_test_file(rel_path: str) -> bool:
    """
    Determine if a relative path points to a likely test file.
    Only returns True if the filename matches deterministic test naming conventions.
    Files merely residing inside a test directory (e.g. __init__.py) do not match.
    """
    filename = Path(rel_path).name.lower()
    return any(fnmatch.fnmatch(filename, pat) for pat in TEST_FILE_PATTERNS)


def is_test_directory(rel_path: str) -> bool:
    """Check if directory is a test directory."""
    parts = Path(rel_path).parts
    return any(p.lower() in TEST_DIR_NAMES for p in parts)


def is_source_directory(rel_path: str) -> bool:
    """Check if directory is a primary source directory."""
    parts = Path(rel_path).parts
    if is_test_directory(rel_path):
        return False
    return any(p.lower() in SOURCE_DIR_NAMES for p in parts)


def classify_file_role(rel_path: str) -> FileRole:
    """
    Deterministically classify a discovered file into exactly one FileRole.
    Classification is based on path, filename, and extension only.
    """
    # 1. Explicit test file naming conventions
    if is_test_file(rel_path):
        return FileRole.TEST

    # 2. Documentation files (e.g. README.md, PRD.md, docs/*.md)
    if is_documentation_file(rel_path):
        return FileRole.DOCUMENTATION

    # 3. Project configuration & build manifests
    if is_config_file(rel_path):
        return FileRole.CONFIG

    # 4. Files inside test directories that are not test cases (e.g. __init__.py, helpers)
    if is_test_directory(rel_path):
        return FileRole.OTHER

    # 5. Source code files
    ext = Path(rel_path).suffix.lower()
    if ext in SOURCE_CODE_EXTENSIONS:
        return FileRole.SOURCE

    return FileRole.OTHER


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ScannerConfig:
    """
    Configuration options for RepositoryScanner.
    """
    ignore_directories: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORE_DIRS))
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    max_file_size: int = 500 * 1024  # 500 KB default
    max_scanned_files: int = 2000
    binary_extensions: set[str] = field(default_factory=lambda: set(DEFAULT_BINARY_EXTENSIONS))
    max_symbols_per_file: int = 100
    max_total_symbols: int = 2000
    extract_symbols: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ignore_directories": sorted(list(self.ignore_directories)),
            "ignore_patterns": list(self.ignore_patterns),
            "max_file_size": self.max_file_size,
            "max_scanned_files": self.max_scanned_files,
            "binary_extensions": sorted(list(self.binary_extensions)),
            "max_symbols_per_file": self.max_symbols_per_file,
            "max_total_symbols": self.max_total_symbols,
            "extract_symbols": self.extract_symbols,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScannerConfig:
        return cls(
            ignore_directories=set(data.get("ignore_directories", DEFAULT_IGNORE_DIRS)),
            ignore_patterns=list(data.get("ignore_patterns", DEFAULT_IGNORE_PATTERNS)),
            max_file_size=int(data.get("max_file_size", 500 * 1024)),
            max_scanned_files=int(data.get("max_scanned_files", 2000)),
            binary_extensions=set(data.get("binary_extensions", DEFAULT_BINARY_EXTENSIONS)),
            max_symbols_per_file=int(data.get("max_symbols_per_file", 100)),
            max_total_symbols=int(data.get("max_total_symbols", 2000)),
            extract_symbols=bool(data.get("extract_symbols", True)),
        )


@dataclass
class RepositoryIndex:
    """
    Structured, lightweight representation of a scanned repository.
    Deterministic, serializable, and enriched with lightweight symbol intelligence.
    """
    root_dir: str
    discovered_files: list[str] = field(default_factory=list)
    discovered_directories: list[str] = field(default_factory=list)
    file_count: int = 0
    detected_languages: dict[str, int] = field(default_factory=dict)
    detected_extensions: dict[str, int] = field(default_factory=dict)
    important_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    test_directories: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    source_directories: list[str] = field(default_factory=list)
    documentation_files: list[str] = field(default_factory=list)
    file_roles: dict[str, str] = field(default_factory=dict)
    file_symbols: dict[str, FileSymbols] = field(default_factory=dict)

    # -------------------------------------------------------------------------
    # Convenience Property Aliases
    # -------------------------------------------------------------------------

    @property
    def repository_root(self) -> str:
        return self.root_dir

    @property
    def important_root_files(self) -> list[str]:
        return self.important_files

    @property
    def config_files(self) -> list[str]:
        return [f for f in self.discovered_files if self.file_roles.get(f) == FileRole.CONFIG]

    @property
    def other_files(self) -> list[str]:
        return [f for f in self.discovered_files if self.file_roles.get(f) == FileRole.OTHER]

    @property
    def languages(self) -> dict[str, int]:
        return self.detected_languages

    @property
    def extensions(self) -> dict[str, int]:
        return self.detected_extensions

    @property
    def symbols_by_file(self) -> dict[str, FileSymbols]:
        return self.file_symbols

    @property
    def total_symbols(self) -> int:
        return sum(len(fs.symbols) for fs in self.file_symbols.values())

    def get_file_role(self, rel_path: str) -> FileRole:
        """Get the primary FileRole for a relative path."""
        val = self.file_roles.get(rel_path)
        if val:
            return FileRole(val)
        return classify_file_role(rel_path)

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert RepositoryIndex to a JSON-serializable dictionary."""
        return {
            "root_dir": self.root_dir,
            "discovered_files": list(self.discovered_files),
            "discovered_directories": list(self.discovered_directories),
            "file_count": self.file_count,
            "detected_languages": dict(self.detected_languages),
            "detected_extensions": dict(self.detected_extensions),
            "important_files": list(self.important_files),
            "test_files": list(self.test_files),
            "test_directories": list(self.test_directories),
            "source_files": list(self.source_files),
            "source_directories": list(self.source_directories),
            "documentation_files": list(self.documentation_files),
            "file_roles": dict(self.file_roles),
            "file_symbols": {k: v.to_dict() for k, v in self.file_symbols.items()},
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryIndex:
        raw_symbols = data.get("file_symbols", {})
        parsed_symbols = {k: FileSymbols.from_dict(v) for k, v in raw_symbols.items()}

        return cls(
            root_dir=data.get("root_dir", data.get("repository_root", "")),
            discovered_files=list(data.get("discovered_files", [])),
            discovered_directories=list(data.get("discovered_directories", [])),
            file_count=int(data.get("file_count", len(data.get("discovered_files", [])))),
            detected_languages=dict(data.get("detected_languages", {})),
            detected_extensions=dict(data.get("detected_extensions", {})),
            important_files=list(
                data.get("important_files", data.get("important_root_files", []))
            ),
            test_files=list(data.get("test_files", [])),
            test_directories=list(data.get("test_directories", [])),
            source_files=list(data.get("source_files", [])),
            source_directories=list(data.get("source_directories", [])),
            documentation_files=list(data.get("documentation_files", [])),
            file_roles=dict(data.get("file_roles", {})),
            file_symbols=parsed_symbols,
        )

    # -------------------------------------------------------------------------
    # Text Rendering
    # -------------------------------------------------------------------------

    def render_symbols(self, max_files: int = 15) -> str:
        """
        Produce a compact text rendering of discovered symbols and imports.
        """
        if not self.file_symbols:
            return "## SYMBOLS & IMPORTS\n(None)"

        lines: list[str] = ["## SYMBOLS & IMPORTS"]
        sorted_files = sorted(self.file_symbols.keys())
        sample = sorted_files[:max_files]
        for f in sample:
            fs = self.file_symbols[f]
            lines.extend(format_file_symbols(fs))

        if len(sorted_files) > max_files:
            lines.append(f"... and {len(sorted_files) - max_files} more files with symbols")

        return "\n".join(lines)

    def render_text(
        self,
        max_sample_files: int = 15,
        include_symbols: bool = False,
        max_symbol_files: int = 10,
    ) -> str:
        """
        Produce a compact, deterministic text overview of the repository.
        Keeps source, test, documentation, and config output clearly separated.
        Optionally appends a compact symbol and import breakdown.
        """
        sections: list[str] = []

        # 1. Overview
        sections.append(
            f"## REPOSITORY OVERVIEW\n"
            f"Root: {self.root_dir}\n"
            f"Total Files: {self.file_count} | Total Directories: {len(self.discovered_directories)}"
        )

        # 2. Languages / Extensions
        if self.detected_languages:
            lang_lines = [
                f"- {lang}: {count} files"
                for lang, count in sorted(self.detected_languages.items(), key=lambda x: (-x[1], x[0]))
            ]
            sections.append("## DETECTED LANGUAGES\n" + "\n".join(lang_lines))
        elif self.detected_extensions:
            ext_lines = [
                f"- {ext}: {count} files"
                for ext, count in sorted(self.detected_extensions.items(), key=lambda x: (-x[1], x[0]))
            ]
            sections.append("## DETECTED EXTENSIONS\n" + "\n".join(ext_lines))
        else:
            sections.append("## DETECTED LANGUAGES\n(None)")

        # 3. Important / Configuration Files (excluding documentation files)
        config_items = [
            f for f in self.important_files
            if f not in self.documentation_files
        ]
        if config_items:
            cfg_lines = [f"- {f}" for f in sorted(config_items)]
            sections.append("## IMPORTANT & CONFIGURATION FILES\n" + "\n".join(cfg_lines))
        else:
            sections.append("## IMPORTANT & CONFIGURATION FILES\n(None)")

        # 4. Documentation
        if self.documentation_files:
            doc_lines = [f"- {f}" for f in sorted(self.documentation_files)]
            sections.append("## DOCUMENTATION\n" + "\n".join(doc_lines))
        else:
            sections.append("## DOCUMENTATION\n(None)")

        # 5. Source Structure
        src_lines: list[str] = []
        if self.source_directories:
            src_lines.append(f"Source Directories: {', '.join(sorted(self.source_directories))}")
        if self.source_files:
            src_sample = sorted(self.source_files)[:max_sample_files]
            src_lines.append(f"Source Files ({len(self.source_files)}):")
            for sf in src_sample:
                src_lines.append(f"  - {sf}")
            if len(self.source_files) > max_sample_files:
                src_lines.append(f"  ... and {len(self.source_files) - max_sample_files} more")
        if src_lines:
            sections.append("## SOURCE CODE\n" + "\n".join(src_lines))
        else:
            sections.append("## SOURCE CODE\n(None)")

        # 6. Test Suite
        test_lines: list[str] = []
        if self.test_directories:
            test_lines.append(f"Test Directories: {', '.join(sorted(self.test_directories))}")
        if self.test_files:
            test_sample = sorted(self.test_files)[:max_sample_files]
            test_lines.append(f"Test Files ({len(self.test_files)}):")
            for tf in test_sample:
                test_lines.append(f"  - {tf}")
            if len(self.test_files) > max_sample_files:
                test_lines.append(f"  ... and {len(self.test_files) - max_sample_files} more")
        if test_lines:
            sections.append("## TEST SUITE\n" + "\n".join(test_lines))
        else:
            sections.append("## TEST SUITE\n(None)")

        # 7. Optional Symbols Section
        if include_symbols:
            sections.append(self.render_symbols(max_files=max_symbol_files))

        return "\n\n".join(sections)

    def to_text(self) -> str:
        """Alias for render_text()."""
        return self.render_text()

    def __str__(self) -> str:
        return self.render_text()


# ---------------------------------------------------------------------------
# Scanner Engine
# ---------------------------------------------------------------------------

class RepositoryScanner:
    """
    Recursively scans a repository filesystem and produces a structured RepositoryIndex.
    Guarantees deterministic ordering, strict filtering, explicit file-role classification,
    and deterministic symbol and import extraction.
    """

    def __init__(self, config: Optional[ScannerConfig] = None) -> None:
        self.config = config or ScannerConfig()

    def scan(
        self,
        root_dir: Union[Path, str],
        config: Optional[ScannerConfig] = None,
    ) -> RepositoryIndex:
        """
        Scan the repository root and return a populated RepositoryIndex.
        """
        root_path = Path(root_dir).resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Repository root directory does not exist: {root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Repository root is not a directory: {root_path}")

        cfg = config or self.config

        discovered_files: list[str] = []
        discovered_directories: list[str] = []
        detected_languages: dict[str, int] = {}
        detected_extensions: dict[str, int] = {}
        important_files: list[str] = []
        test_files: list[str] = []
        test_directories: list[str] = []
        source_files: list[str] = []
        source_directories: list[str] = []
        documentation_files: list[str] = []
        file_roles: dict[str, str] = {}
        file_symbols: dict[str, FileSymbols] = {}

        total_symbols_extracted = 0

        # Recursively traverse directory tree deterministically
        for dirpath, dirnames, filenames in os.walk(str(root_path)):
            current_path = Path(dirpath)
            rel_dir = current_path.relative_to(root_path).as_posix()

            # Record relative directory (except root '.')
            if rel_dir != ".":
                discovered_directories.append(rel_dir)
                if is_test_directory(rel_dir):
                    test_directories.append(rel_dir)
                elif is_source_directory(rel_dir):
                    source_directories.append(rel_dir)

            # In-place filtering of subdirectories to prevent descending into ignored trees
            filtered_dirnames: list[str] = []
            for d in dirnames:
                if d in cfg.ignore_directories:
                    continue
                # Check wildcard pattern ignores
                if any(fnmatch.fnmatch(d, pat) for pat in cfg.ignore_patterns):
                    continue
                filtered_dirnames.append(d)

            # Sort subdirectories in-place for deterministic traversal order
            filtered_dirnames.sort()
            dirnames[:] = filtered_dirnames

            # Sort filenames deterministically
            filenames.sort()
            for filename in filenames:
                if len(discovered_files) >= cfg.max_scanned_files:
                    break

                # Ignore patterns
                if any(fnmatch.fnmatch(filename, pat) for pat in cfg.ignore_patterns):
                    continue

                full_file_path = current_path / filename

                # Check if symlink is broken or not a file
                try:
                    if not full_file_path.is_file():
                        continue
                    file_size = full_file_path.stat().st_size
                except (OSError, PermissionError):
                    continue

                # File size cap
                if file_size > cfg.max_file_size:
                    continue

                # Binary file exclusion
                if is_binary_file(full_file_path, binary_extensions=cfg.binary_extensions):
                    continue

                rel_file = full_file_path.relative_to(root_path).as_posix()
                discovered_files.append(rel_file)

                # Extension & Language Detection
                file_lang = ""
                ext = full_file_path.suffix.lower()
                if ext:
                    detected_extensions[ext] = detected_extensions.get(ext, 0) + 1
                    file_lang = EXTENSION_TO_LANGUAGE.get(ext, "")
                    if file_lang:
                        detected_languages[file_lang] = detected_languages.get(file_lang, 0) + 1
                elif filename.lower() == "dockerfile":
                    file_lang = "Dockerfile"
                    detected_languages["Dockerfile"] = detected_languages.get("Dockerfile", 0) + 1
                elif filename.lower() == "makefile":
                    file_lang = "Makefile"
                    detected_languages["Makefile"] = detected_languages.get("Makefile", 0) + 1

                # Important file detection (root config / manifests / docs)
                if is_important_file(filename):
                    important_files.append(rel_file)

                # Explicit deterministic file-role classification
                role = classify_file_role(rel_file)
                file_roles[rel_file] = role.value

                if role == FileRole.TEST:
                    test_files.append(rel_file)
                elif role == FileRole.DOCUMENTATION:
                    documentation_files.append(rel_file)
                elif role == FileRole.SOURCE:
                    source_files.append(rel_file)

                # Symbol and Import Intelligence extraction (Phase B3.2)
                # Only extract for SOURCE and TEST files
                if (
                    cfg.extract_symbols
                    and role in (FileRole.SOURCE, FileRole.TEST)
                    and total_symbols_extracted < cfg.max_total_symbols
                ):
                    remaining_budget = cfg.max_total_symbols - total_symbols_extracted
                    file_symbol_limit = min(cfg.max_symbols_per_file, remaining_budget)
                    try:
                        fs = extract_file_symbols(
                            file_path=full_file_path,
                            rel_path=rel_file,
                            language=file_lang,
                            max_symbols=file_symbol_limit,
                        )
                        if fs is not None and (fs.symbols or fs.imports):
                            file_symbols[rel_file] = fs
                            total_symbols_extracted += len(fs.symbols)
                    except Exception:
                        # Malformed or unsupported file must never abort indexing
                        pass

            if len(discovered_files) >= cfg.max_scanned_files:
                break

        # Ensure all collections are deterministically sorted
        discovered_files.sort()
        discovered_directories.sort()
        important_files.sort()
        test_files.sort()
        test_directories.sort()
        source_files.sort()
        source_directories.sort()
        documentation_files.sort()

        # Pass 2: Resolve local repository imports
        repo_files_set = set(discovered_files)
        for rel_file, fs in file_symbols.items():
            full_path = root_path / rel_file
            fs.local_imports = resolve_file_local_imports(
                file_path=full_path,
                rel_path=rel_file,
                language=fs.language,
                imports=fs.imports,
                repo_files=repo_files_set,
            )

        return RepositoryIndex(
            root_dir=str(root_path),
            discovered_files=discovered_files,
            discovered_directories=discovered_directories,
            file_count=len(discovered_files),
            detected_languages=dict(sorted(detected_languages.items())),
            detected_extensions=dict(sorted(detected_extensions.items())),
            important_files=important_files,
            test_files=test_files,
            test_directories=test_directories,
            source_files=source_files,
            source_directories=source_directories,
            documentation_files=documentation_files,
            file_roles=dict(sorted(file_roles.items())),
            file_symbols=dict(sorted(file_symbols.items())),
        )
