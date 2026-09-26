"""Unit and integration tests for Phase B3.2 symbol and import extraction."""

from pathlib import Path
import pytest

from src.context.scanner import (
    FileRole,
    RepositoryIndex,
    RepositoryScanner,
    ScannerConfig,
)
from src.context.symbols import (
    FileSymbols,
    SymbolRecord,
    extract_file_symbols,
    extract_javascript_symbols,
    extract_python_symbols,
)


def test_python_function_extraction() -> None:
    """Verify synchronous top-level Python function extraction."""
    code = """
def calculate_metrics(values: list[float]) -> dict:
    total = sum(values)
    return {"total": total}

def _internal_helper():
    pass
"""
    fs = extract_python_symbols(code, "src/metrics.py")
    assert fs.language == "Python"
    assert len(fs.symbols) == 2

    s1 = fs.symbols[0]
    assert s1.name == "calculate_metrics"
    assert s1.kind == "function"
    assert s1.parent is None
    assert s1.exported is True
    assert s1.line_start == 2
    assert s1.line_end == 4

    s2 = fs.symbols[1]
    assert s2.name == "_internal_helper"
    assert s2.kind == "function"
    assert s2.exported is False
    assert s2.line_start == 6


def test_python_async_function_extraction() -> None:
    """Verify asynchronous top-level Python function extraction."""
    code = """
async def fetch_record(record_id: str) -> dict:
    return {"id": record_id}

async def _async_cleanup():
    pass
"""
    fs = extract_python_symbols(code, "src/fetcher.py")
    assert len(fs.symbols) == 2

    s1 = fs.symbols[0]
    assert s1.name == "fetch_record"
    assert s1.kind == "async_function"
    assert s1.parent is None
    assert s1.exported is True
    assert s1.line_start == 2

    s2 = fs.symbols[1]
    assert s2.name == "_async_cleanup"
    assert s2.kind == "async_function"
    assert s2.exported is False


def test_python_class_and_method_extraction() -> None:
    """Verify Python classes, sync methods, and async methods with parent relationships."""
    code = """
class TaskWorker:
    def __init__(self, name: str):
        self.name = name

    def execute(self) -> bool:
        return True

    async def run_async(self) -> None:
        pass

    def _private_step(self):
        pass
"""
    fs = extract_python_symbols(code, "src/worker.py")
    assert len(fs.symbols) == 5

    cls_sym = fs.symbols[0]
    assert cls_sym.name == "TaskWorker"
    assert cls_sym.kind == "class"
    assert cls_sym.parent is None
    assert cls_sym.exported is True

    init_sym = fs.symbols[1]
    assert init_sym.name == "__init__"
    assert init_sym.kind == "method"
    assert init_sym.parent == "TaskWorker"
    assert init_sym.exported is False  # starts with _

    exec_sym = fs.symbols[2]
    assert exec_sym.name == "execute"
    assert exec_sym.kind == "method"
    assert exec_sym.parent == "TaskWorker"
    assert exec_sym.exported is True

    async_sym = fs.symbols[3]
    assert async_sym.name == "run_async"
    assert async_sym.kind == "async_method"
    assert async_sym.parent == "TaskWorker"
    assert async_sym.exported is True

    priv_sym = fs.symbols[4]
    assert priv_sym.name == "_private_step"
    assert priv_sym.kind == "method"
    assert priv_sym.parent == "TaskWorker"
    assert priv_sym.exported is False


def test_python_imports_extraction() -> None:
    """Verify Python import statements across ast.Import and ast.ImportFrom."""
    code = """
import os
import sys as system
from pathlib import Path
from src.memory.models import TaskState, Attempt
from .bundle import ContextBundle
from ..core import engine
"""
    fs = extract_python_symbols(code, "src/builder.py")
    expected = [
        "..core",
        ".bundle",
        "os",
        "pathlib",
        "src.memory.models",
        "sys",
    ]
    assert fs.imports == expected


def test_js_function_extraction() -> None:
    """Verify JavaScript regular, async, arrow, and expression function extractions."""
    code = """
function calculateTotal(items) {
    return 100;
}

async function loadAsyncData(url) {
    return null;
}

const formatTitle = (title) => {
    return title.trim();
};

const parsePayload = function(data) {
    return JSON.parse(data);
};
"""
    fs = extract_javascript_symbols(code, "src/utils.js", "JavaScript")
    names = [s.name for s in fs.symbols]
    kinds = [s.kind for s in fs.symbols]

    assert "calculateTotal" in names
    assert "loadAsyncData" in names
    assert "formatTitle" in names
    assert "parsePayload" in names

    assert kinds[names.index("calculateTotal")] == "function"
    assert kinds[names.index("loadAsyncData")] == "async_function"
    assert kinds[names.index("formatTitle")] == "function"
    assert kinds[names.index("parsePayload")] == "function"


def test_js_class_and_method_extraction() -> None:
    """Verify JavaScript class and method extractions."""
    code = """
class StorageService {
    constructor() {}

    getItem(key) {
        return null;
    }

    async setItem(key, val) {
        return true;
    }
}
"""
    fs = extract_javascript_symbols(code, "src/storage.js", "JavaScript")
    names = [s.name for s in fs.symbols]
    assert "StorageService" in names
    assert "getItem" in names
    assert "setItem" in names

    s_cls = fs.symbols[names.index("StorageService")]
    assert s_cls.kind == "class"
    assert s_cls.parent is None

    s_m1 = fs.symbols[names.index("getItem")]
    assert s_m1.kind == "method"
    assert s_m1.parent == "StorageService"

    s_m2 = fs.symbols[names.index("setItem")]
    assert s_m2.kind == "async_method"
    assert s_m2.parent == "StorageService"


def test_ts_exported_symbols_extraction() -> None:
    """Verify TypeScript exported function, class, and arrow expressions."""
    code = """
export function createSession(id: string): Session {
    return { id };
}

export async function authenticate(): Promise<boolean> {
    return true;
}

export class AuthManager {
    public check(): boolean {
        return true;
    }
}

export const renderButton = (label: string) => {
    return label;
};

function internalUtil() {
    return 1;
}
"""
    fs = extract_javascript_symbols(code, "src/auth.ts", "TypeScript")
    sym_map = {s.name: s for s in fs.symbols}

    assert sym_map["createSession"].exported is True
    assert sym_map["createSession"].kind == "function"

    assert sym_map["authenticate"].exported is True
    assert sym_map["authenticate"].kind == "async_function"

    assert sym_map["AuthManager"].exported is True
    assert sym_map["AuthManager"].kind == "class"

    assert sym_map["renderButton"].exported is True
    assert sym_map["renderButton"].kind == "function"

    assert sym_map["internalUtil"].exported is False


def test_js_ts_imports_extraction() -> None:
    """Verify ES module imports extraction."""
    code = """
import React, { useState } from 'react';
import { Button } from './components/Button';
import './styles.css';
import type { UserProfile } from './types';
"""
    fs = extract_javascript_symbols(code, "src/App.tsx", "TypeScript")
    expected = [
        "./components/Button",
        "./styles.css",
        "./types",
        "react",
    ]
    assert fs.imports == expected


def test_commonjs_require_extraction() -> None:
    """Verify CommonJS require statements extraction."""
    code = """
const fs = require('fs');
const path = require("path");
const { helper } = require('./helper');
"""
    fs = extract_javascript_symbols(code, "src/server.js", "JavaScript")
    expected = [
        "./helper",
        "fs",
        "path",
    ]
    assert fs.imports == expected


def test_malformed_source_handling(tmp_path: Path) -> None:
    """Verify malformed code never crashes the scanner."""
    repo = tmp_path / "broken_repo"
    repo.mkdir()

    # Broken Python file with syntax error
    (repo / "broken.py").write_text("def missing_colon(\n    broken = 1\n")
    # Valid Python file
    (repo / "valid.py").write_text("def valid_fn(): pass\n")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert "broken.py" in index.discovered_files
    assert "valid.py" in index.discovered_files

    # broken.py should not crash and may have empty symbols
    if "broken.py" in index.file_symbols:
        assert index.file_symbols["broken.py"].symbols == []

    assert "valid.py" in index.file_symbols
    assert len(index.file_symbols["valid.py"].symbols) == 1
    assert index.file_symbols["valid.py"].symbols[0].name == "valid_fn"


def test_max_symbols_per_file(tmp_path: Path) -> None:
    """Verify max_symbols_per_file limits extraction per file."""
    repo = tmp_path / "per_file_repo"
    repo.mkdir()

    funcs = "\n".join(f"def func_{i}(): pass" for i in range(25))
    (repo / "large.py").write_text(funcs)

    cfg = ScannerConfig(max_symbols_per_file=7)
    scanner = RepositoryScanner(config=cfg)
    index = scanner.scan(repo)

    assert len(index.file_symbols["large.py"].symbols) == 7


def test_max_total_symbols_cap(tmp_path: Path) -> None:
    """Verify max_total_symbols cleanly caps total symbols extracted across repository."""
    repo = tmp_path / "total_cap_repo"
    repo.mkdir()

    for file_idx in range(5):
        funcs = "\n".join(f"def func_{file_idx}_{i}(): pass" for i in range(10))
        (repo / f"mod_{file_idx}.py").write_text(funcs)

    cfg = ScannerConfig(max_total_symbols=15, max_symbols_per_file=10)
    scanner = RepositoryScanner(config=cfg)
    index = scanner.scan(repo)

    total_extracted = sum(len(fs.symbols) for fs in index.file_symbols.values())
    assert total_extracted <= 15
    assert index.total_symbols == total_extracted


def test_documentation_and_config_files_never_parsed_for_symbols(tmp_path: Path) -> None:
    """Verify DOCUMENTATION, CONFIG, and OTHER files are strictly excluded from symbol extraction."""
    repo = tmp_path / "skip_repo"
    repo.mkdir()

    (repo / "README.md").write_text("# Readme\n```python\ndef fake(): pass\n```")
    (repo / "PRD.md").write_text("# PRD\nfunction js_in_doc() {}")
    (repo / "package.json").write_text('{"name": "test"}')
    (repo / "pyproject.toml").write_text('[project]\nname = "test"')
    (repo / ".gitignore").write_text("*.pyc")
    (repo / "main.py").write_text("def app_main(): pass")

    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_main.py").write_text("def test_it(): pass")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    # Only SOURCE and TEST files should have entries in file_symbols
    assert "main.py" in index.file_symbols
    assert "tests/test_main.py" in index.file_symbols

    assert "README.md" not in index.file_symbols
    assert "PRD.md" not in index.file_symbols
    assert "package.json" not in index.file_symbols
    assert "pyproject.toml" not in index.file_symbols
    assert ".gitignore" not in index.file_symbols
    assert "tests/__init__.py" not in index.file_symbols


def test_deterministic_symbol_output(tmp_path: Path) -> None:
    """Verify repeated scans produce identical symbol information."""
    repo = tmp_path / "determ_repo"
    repo.mkdir()

    (repo / "src").mkdir()
    (repo / "src" / "worker.py").write_text("import sys\ndef run(): pass\nclass Engine: pass")
    (repo / "src" / "api.ts").write_text("import { x } from './x';\nexport function get(): void {}")

    scanner1 = RepositoryScanner()
    scanner2 = RepositoryScanner()

    idx1 = scanner1.scan(repo)
    idx2 = scanner2.scan(repo)

    assert idx1.to_dict() == idx2.to_dict()
    assert idx1.render_symbols() == idx2.render_symbols()
    assert idx1.render_text(include_symbols=True) == idx2.render_text(include_symbols=True)


def test_serialization_deserialization_of_symbols(tmp_path: Path) -> None:
    """Verify RepositoryIndex JSON round-trip preserves all SymbolRecord and FileSymbols data."""
    repo = tmp_path / "serde_repo"
    repo.mkdir()

    (repo / "service.py").write_text(
        "import os\n"
        "class Service:\n"
        "    def execute(self): pass\n"
    )

    scanner = RepositoryScanner()
    original_index = scanner.scan(repo)

    data = original_index.to_dict()
    reloaded_index = RepositoryIndex.from_dict(data)

    assert original_index.total_symbols == reloaded_index.total_symbols
    assert len(original_index.file_symbols) == len(reloaded_index.file_symbols)

    fs_orig = original_index.file_symbols["service.py"]
    fs_reload = reloaded_index.file_symbols["service.py"]

    assert fs_orig.path == fs_reload.path
    assert fs_orig.language == fs_reload.language
    assert fs_orig.imports == fs_reload.imports
    assert len(fs_orig.symbols) == len(fs_reload.symbols)

    for s1, s2 in zip(fs_orig.symbols, fs_reload.symbols):
        assert s1.name == s2.name
        assert s1.kind == s2.kind
        assert s1.parent == s2.parent
        assert s1.line_start == s2.line_start
        assert s1.exported == s2.exported


def test_realistic_integration_python_and_javascript(tmp_path: Path) -> None:
    """Integration test: realistic multi-language repository with Python and TypeScript."""
    repo = tmp_path / "full_stack_repo"
    repo.mkdir()

    # Documentation and configs
    (repo / "README.md").write_text("# Full Stack Repo Documentation")
    (repo / "pyproject.toml").write_text('[project]\nname = "backend"')
    (repo / "package.json").write_text('{"name": "frontend"}')
    (repo / ".gitignore").write_text("node_modules\n__pycache__\n")

    # Python backend
    (repo / "src").mkdir()
    (repo / "src" / "builder.py").write_text(
        "from src.memory.manager import MemoryManager\n"
        "from src.context.bundle import ContextBundle\n\n"
        "class ContextBuilder:\n"
        "    def build(self):\n"
        "        pass\n"
        "    def render_text(self):\n"
        "        pass\n"
    )

    # TypeScript frontend
    (repo / "src" / "client.ts").write_text(
        "import { Config } from './config';\n"
        "import axios from 'axios';\n\n"
        "export class ApiClient {\n"
        "    public async request(endpoint: string): Promise<any> {\n"
        "        return null;\n"
        "    }\n"
        "}\n\n"
        "export const defaultClient = () => new ApiClient();\n"
    )

    # Test file
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_builder.py").write_text(
        "import pytest\n"
        "from src.builder import ContextBuilder\n\n"
        "def test_build():\n"
        "    assert True\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    # Verify counts
    assert "src/builder.py" in index.file_symbols
    assert "src/client.ts" in index.file_symbols
    assert "tests/test_builder.py" in index.file_symbols

    # Verify docs & configs are not in file_symbols
    assert "README.md" not in index.file_symbols
    assert "pyproject.toml" not in index.file_symbols
    assert "package.json" not in index.file_symbols
    assert "tests/__init__.py" not in index.file_symbols

    # Check Python builder symbols
    builder_fs = index.file_symbols["src/builder.py"]
    assert "src.context.bundle" in builder_fs.imports
    assert "src.memory.manager" in builder_fs.imports
    builder_names = [s.name for s in builder_fs.symbols]
    assert "ContextBuilder" in builder_names
    assert "build" in builder_names
    assert "render_text" in builder_names

    # Check TypeScript client symbols
    client_fs = index.file_symbols["src/client.ts"]
    assert "./config" in client_fs.imports
    assert "axios" in client_fs.imports
    client_names = [s.name for s in client_fs.symbols]
    assert "ApiClient" in client_names
    assert "defaultClient" in client_names

    # Check compact rendering
    rendered_symbols = index.render_symbols()
    assert "## SYMBOLS & IMPORTS" in rendered_symbols
    assert "src/builder.py" in rendered_symbols
    assert "class: ContextBuilder" in rendered_symbols
    assert "methods: build, render_text" in rendered_symbols
    assert "src/client.ts" in rendered_symbols

    # Check render_text with include_symbols=True
    full_text = index.render_text(include_symbols=True)
    assert "## REPOSITORY OVERVIEW" in full_text
    assert "## DOCUMENTATION" in full_text
    assert "## SOURCE CODE" in full_text
    assert "## TEST SUITE" in full_text
    assert "## SYMBOLS & IMPORTS" in full_text
    assert "src/builder.py" in full_text


def test_local_python_module_resolution(tmp_path: Path) -> None:
    """Verify absolute project Python imports resolve to repository-relative files."""
    repo = tmp_path / "py_local_repo"
    repo.mkdir()

    (repo / "src" / "memory").mkdir(parents=True)
    (repo / "src" / "memory" / "__init__.py").write_text("")
    (repo / "src" / "memory" / "models.py").write_text("class TaskState: pass\nclass Attempt: pass\n")
    (repo / "src" / "memory" / "manager.py").write_text("class MemoryManager: pass\n")

    (repo / "src" / "builder.py").write_text(
        "from src.memory.models import TaskState\n"
        "from src.memory.manager import MemoryManager\n"
        "class ContextBuilder: pass\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    builder_fs = index.file_symbols["src/builder.py"]
    assert "src.memory.models" in builder_fs.imports
    assert "src.memory.manager" in builder_fs.imports

    # Check local_imports resolved to repository paths
    assert "src/memory/models.py" in builder_fs.local_imports
    assert "src/memory/manager.py" in builder_fs.local_imports


def test_relative_python_import_resolution(tmp_path: Path) -> None:
    """Verify relative Python imports (. and ..) resolve to local files."""
    repo = tmp_path / "py_rel_repo"
    repo.mkdir()

    (repo / "src" / "context").mkdir(parents=True)
    (repo / "src" / "context" / "bundle.py").write_text("class Bundle: pass\n")
    (repo / "src" / "context" / "config.py").write_text("class Config: pass\n")

    (repo / "src" / "memory").mkdir(parents=True)
    (repo / "src" / "memory" / "models.py").write_text("class TaskState: pass\n")

    (repo / "src" / "context" / "builder.py").write_text(
        "from .bundle import Bundle\n"
        "from .config import Config\n"
        "from ..memory.models import TaskState\n"
        "class Builder: pass\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    builder_fs = index.file_symbols["src/context/builder.py"]
    assert ".bundle" in builder_fs.imports
    assert ".config" in builder_fs.imports
    assert "..memory.models" in builder_fs.imports

    assert "src/context/bundle.py" in builder_fs.local_imports
    assert "src/context/config.py" in builder_fs.local_imports
    assert "src/memory/models.py" in builder_fs.local_imports


def test_standard_library_imports_excluded_from_local_imports(tmp_path: Path) -> None:
    """Verify stdlib imports (os, sys, typing, etc.) are excluded from local_imports."""
    repo = tmp_path / "py_stdlib_repo"
    repo.mkdir()

    (repo / "worker.py").write_text(
        "import os\n"
        "import sys\n"
        "import json\n"
        "from pathlib import Path\n"
        "from typing import Any, Optional\n"
        "def work(): pass\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    fs = index.file_symbols["worker.py"]
    # All must remain in imports
    assert "os" in fs.imports
    assert "sys" in fs.imports
    assert "json" in fs.imports
    assert "pathlib" in fs.imports
    assert "typing" in fs.imports

    # None must be in local_imports
    assert fs.local_imports == []


def test_unresolved_third_party_imports_excluded_from_local_imports(tmp_path: Path) -> None:
    """Verify third-party package imports (pytest, requests, etc.) are excluded from local_imports."""
    repo = tmp_path / "py_pkg_repo"
    repo.mkdir()

    (repo / "app.py").write_text(
        "import pytest\n"
        "import requests\n"
        "import fastapi\n"
        "def run(): pass\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    fs = index.file_symbols["app.py"]
    assert "pytest" in fs.imports
    assert "requests" in fs.imports
    assert "fastapi" in fs.imports

    assert fs.local_imports == []


def test_relative_js_ts_import_resolution(tmp_path: Path) -> None:
    """Verify relative JS/TS imports (with and without extension, and index) resolve."""
    repo = tmp_path / "ts_local_repo"
    repo.mkdir()

    (repo / "src" / "components").mkdir(parents=True)
    (repo / "src" / "components" / "Button.tsx").write_text("export const Button = () => null;\n")
    (repo / "src" / "components" / "index.ts").write_text("export * from './Button';\n")
    (repo / "src" / "styles.css").write_text("/* styles */")
    (repo / "src" / "utils.ts").write_text("export function helper(): void {}\n")

    (repo / "src" / "app.tsx").write_text(
        "import { helper } from './utils';\n"
        "import { Button } from './components';\n"
        "import './styles.css';\n"
        "export const App = () => null;\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    app_fs = index.file_symbols["src/app.tsx"]
    assert "./utils" in app_fs.imports
    assert "./components" in app_fs.imports
    assert "./styles.css" in app_fs.imports

    assert "src/utils.ts" in app_fs.local_imports
    assert "src/components/index.ts" in app_fs.local_imports
    assert "src/styles.css" in app_fs.local_imports


def test_unresolved_package_imports_excluded_in_js_ts(tmp_path: Path) -> None:
    """Verify external npm packages (react, axios, lodash) are not in local_imports."""
    repo = tmp_path / "ts_pkg_repo"
    repo.mkdir()

    (repo / "index.ts").write_text(
        "import React from 'react';\n"
        "import axios from 'axios';\n"
        "import { get } from 'lodash';\n"
        "export function main() {}\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    fs = index.file_symbols["index.ts"]
    assert "react" in fs.imports
    assert "axios" in fs.imports
    assert "lodash" in fs.imports

    assert fs.local_imports == []


def test_deterministic_local_import_resolution(tmp_path: Path) -> None:
    """Verify repeated scans produce identical local_imports ordering."""
    repo = tmp_path / "determ_local_repo"
    repo.mkdir()

    (repo / "a.py").write_text("def a(): pass\n")
    (repo / "b.py").write_text("def b(): pass\n")
    (repo / "c.py").write_text("import b\nimport a\ndef c(): pass\n")

    scanner1 = RepositoryScanner()
    scanner2 = RepositoryScanner()

    idx1 = scanner1.scan(repo)
    idx2 = scanner2.scan(repo)

    assert idx1.file_symbols["c.py"].local_imports == idx2.file_symbols["c.py"].local_imports
    assert idx1.file_symbols["c.py"].local_imports == ["a.py", "b.py"]


def test_serialization_of_local_imports(tmp_path: Path) -> None:
    """Verify local_imports survive RepositoryIndex JSON serialization round-trip."""
    repo = tmp_path / "serde_local_repo"
    repo.mkdir()

    (repo / "helper.py").write_text("def help(): pass\n")
    (repo / "main.py").write_text("import helper\ndef run(): pass\n")

    scanner = RepositoryScanner()
    original_index = scanner.scan(repo)

    raw_json = original_index.to_json()
    reloaded_index = RepositoryIndex.from_dict(original_index.to_dict())

    fs_orig = original_index.file_symbols["main.py"]
    fs_reload = reloaded_index.file_symbols["main.py"]

    assert fs_orig.local_imports == fs_reload.local_imports
    assert fs_reload.local_imports == ["helper.py"]
    assert '"local_imports": [' in raw_json


def test_integration_local_imports_rendering(tmp_path: Path) -> None:
    """Integration test: verify compact rendering shows 'local imports:' clearly."""
    repo = tmp_path / "render_local_repo"
    repo.mkdir()

    (repo / "src" / "memory").mkdir(parents=True)
    (repo / "src" / "memory" / "models.py").write_text("class TaskState: pass\n")
    (repo / "src" / "memory" / "manager.py").write_text("class MemoryManager: pass\n")

    (repo / "src" / "context").mkdir(parents=True)
    (repo / "src" / "context" / "bundle.py").write_text("class ContextBundle: pass\n")
    (repo / "src" / "context" / "config.py").write_text("class ContextConfig: pass\n")

    (repo / "src" / "context" / "builder.py").write_text(
        "import os\n"
        "from typing import Optional\n"
        "from src.context.bundle import ContextBundle\n"
        "from src.context.config import ContextConfig\n"
        "from src.memory.manager import MemoryManager\n"
        "from src.memory.models import TaskState\n\n"
        "class ContextBuilder:\n"
        "    def build(self):\n"
        "        pass\n"
        "    def render_text(self):\n"
        "        pass\n"
    )

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    builder_fs = index.file_symbols["src/context/builder.py"]
    assert "src/context/bundle.py" in builder_fs.local_imports
    assert "src/context/config.py" in builder_fs.local_imports
    assert "src/memory/manager.py" in builder_fs.local_imports
    assert "src/memory/models.py" in builder_fs.local_imports

    # Check that rendering prefers local imports
    rendered = index.render_symbols()
    assert "src/context/builder.py" in rendered
    assert "class: ContextBuilder" in rendered
    assert "methods: build, render_text" in rendered
    assert "local imports: src/context/bundle.py, src/context/config.py, src/memory/manager.py, src/memory/models.py" in rendered

