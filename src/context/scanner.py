"""Deterministic repository scanner and indexer for the coding-agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Optional, Union


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

IMPORTANT_FILE_NAMES: set[str] = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "tsconfig.json",
    "jsconfig.json",
    "makefile",
    "cmakelists.txt",
    "pytest.ini",
    "tox.ini",
    "jest.config.js",
    "jest.config.ts",
    "vitest.config.ts",
    "vitest.config.js",
    ".coveragerc",
    "conftest.py",
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
    if file_path.name.lower() in IMPORTANT_FILE_NAMES:
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


def is_important_file(filename: str) -> bool:
    """Check if filename matches common important root/configuration files."""
    name_lower = filename.lower()
    if name_lower in IMPORTANT_FILE_NAMES:
        return True
    if name_lower.startswith("readme"):
        return True
    if name_lower.startswith("license"):
        return True
    if fnmatch.fnmatch(name_lower, "dockerfile*"):
        return True
    if fnmatch.fnmatch(name_lower, "requirements*.txt"):
        return True
    return False


def is_test_file(rel_path: str) -> bool:
    """Determine if a relative path points to a likely test file."""
    parts = Path(rel_path).parts
    # Check if in a test directory
    for part in parts[:-1]:
        if part.lower() in TEST_DIR_NAMES:
            return True

    filename = parts[-1].lower()
    if filename.startswith("test_") or filename.endswith("_test.py") or filename == "conftest.py":
        return True
    if fnmatch.fnmatch(filename, "*.test.*") or fnmatch.fnmatch(filename, "*.spec.*"):
        return True
    if filename.endswith("_test.go"):
        return True
    if fnmatch.fnmatch(filename, "*test.java") or fnmatch.fnmatch(filename, "*tests.java"):
        return True
    return False


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ignore_directories": sorted(list(self.ignore_directories)),
            "ignore_patterns": list(self.ignore_patterns),
            "max_file_size": self.max_file_size,
            "max_scanned_files": self.max_scanned_files,
            "binary_extensions": sorted(list(self.binary_extensions)),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScannerConfig:
        return cls(
            ignore_directories=set(data.get("ignore_directories", DEFAULT_IGNORE_DIRS)),
            ignore_patterns=list(data.get("ignore_patterns", DEFAULT_IGNORE_PATTERNS)),
            max_file_size=int(data.get("max_file_size", 500 * 1024)),
            max_scanned_files=int(data.get("max_scanned_files", 2000)),
            binary_extensions=set(data.get("binary_extensions", DEFAULT_BINARY_EXTENSIONS)),
        )


@dataclass
class RepositoryIndex:
    """
    Structured, lightweight representation of a scanned repository.
    Deterministic and serializable.
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
        return self.important_files

    @property
    def languages(self) -> dict[str, int]:
        return self.detected_languages

    @property
    def extensions(self) -> dict[str, int]:
        return self.detected_extensions

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
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryIndex:
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
        )

    # -------------------------------------------------------------------------
    # Text Rendering
    # -------------------------------------------------------------------------

    def render_text(self, max_sample_files: int = 15) -> str:
        """
        Produce a compact, deterministic text overview of the repository.
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

        # 3. Important / Configuration Files
        if self.important_files:
            cfg_lines = [f"- {f}" for f in sorted(self.important_files)]
            sections.append("## IMPORTANT & CONFIGURATION FILES\n" + "\n".join(cfg_lines))
        else:
            sections.append("## IMPORTANT & CONFIGURATION FILES\n(None)")

        # 4. Source Structure
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

        # 5. Test Suite
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
    Guarantees deterministic ordering and strict filtering.
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
                ext = full_file_path.suffix.lower()
                if ext:
                    detected_extensions[ext] = detected_extensions.get(ext, 0) + 1
                    lang = EXTENSION_TO_LANGUAGE.get(ext)
                    if lang:
                        detected_languages[lang] = detected_languages.get(lang, 0) + 1
                elif filename.lower() == "dockerfile":
                    detected_languages["Dockerfile"] = detected_languages.get("Dockerfile", 0) + 1
                elif filename.lower() == "makefile":
                    detected_languages["Makefile"] = detected_languages.get("Makefile", 0) + 1

                # Important file detection
                if is_important_file(filename):
                    important_files.append(rel_file)

                # Test file vs Source file classification
                if is_test_file(rel_file):
                    test_files.append(rel_file)
                else:
                    # Check if file has a code extension or is a known source file
                    if ext in EXTENSION_TO_LANGUAGE or filename.lower() in {"dockerfile", "makefile"}:
                        source_files.append(rel_file)

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
        )
