"""Alias module re-exporting RepositoryScanner, RepositoryIndex, and ScannerConfig."""

from src.context.scanner import (
    DEFAULT_BINARY_EXTENSIONS,
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_PATTERNS,
    RepositoryIndex,
    RepositoryScanner,
    ScannerConfig,
    is_binary_file,
    is_important_file,
    is_test_file,
)

__all__ = [
    "RepositoryScanner",
    "RepositoryIndex",
    "ScannerConfig",
    "DEFAULT_IGNORE_DIRS",
    "DEFAULT_IGNORE_PATTERNS",
    "DEFAULT_BINARY_EXTENSIONS",
    "is_binary_file",
    "is_important_file",
    "is_test_file",
]
