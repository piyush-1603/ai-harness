"""Alias module re-exporting RepositoryScanner, RepositoryIndex, ScannerConfig, and FileRole."""

from src.context.scanner import (
    DEFAULT_BINARY_EXTENSIONS,
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_PATTERNS,
    FileRole,
    RepositoryIndex,
    RepositoryScanner,
    ScannerConfig,
    classify_file_role,
    is_binary_file,
    is_config_file,
    is_documentation_file,
    is_important_file,
    is_test_file,
)

__all__ = [
    "RepositoryScanner",
    "RepositoryIndex",
    "ScannerConfig",
    "FileRole",
    "classify_file_role",
    "is_documentation_file",
    "is_config_file",
    "DEFAULT_IGNORE_DIRS",
    "DEFAULT_IGNORE_PATTERNS",
    "DEFAULT_BINARY_EXTENSIONS",
    "is_binary_file",
    "is_important_file",
    "is_test_file",
]
