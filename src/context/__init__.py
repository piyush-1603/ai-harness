"""Context building and repository scanning subsystem."""

from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
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
from src.context.symbols import (
    FileSymbols,
    SymbolRecord,
    resolve_file_local_imports,
)

__all__ = [
    "ContextBuilder",
    "ContextBundle",
    "ContextConfig",
    "RepositoryScanner",
    "RepositoryIndex",
    "ScannerConfig",
    "FileRole",
    "FileSymbols",
    "SymbolRecord",
    "resolve_file_local_imports",
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
