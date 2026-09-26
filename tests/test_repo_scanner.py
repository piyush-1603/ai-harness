"""Unit tests for deterministic repository scanner and indexer."""

from pathlib import Path
import pytest

from src.context.scanner import (
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


def test_ignored_directories(tmp_path: Path) -> None:
    """Verify common build/cache/dependency directories are ignored."""
    repo = tmp_path / "my_repo"
    repo.mkdir()

    # Ignored directories
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("git config")

    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("console.log('vendor');")

    (repo / ".next").mkdir()
    (repo / ".next" / "cache.js").write_text("cache")

    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("bundle")

    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("out")

    (repo / "coverage").mkdir()
    (repo / "coverage" / "lcov.info").write_text("coverage")

    (repo / "venv" / "lib").mkdir(parents=True)
    (repo / "venv" / "lib" / "site.py").write_text("# venv")

    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "activate").write_text("# activate")

    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.cpython-314.pyc").write_bytes(b"pyc")

    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "v").write_text("cache")

    # Legitimate source files
    (repo / "src").mkdir()
    (repo / "src" / "index.js").write_text("console.log('app');")
    (repo / "src" / "utils.js").write_text("module.exports = {};")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert "src/index.js" in index.discovered_files
    assert "src/utils.js" in index.discovered_files
    assert index.file_count == 2

    # None of the ignored files should appear
    for f in index.discovered_files:
        assert not f.startswith(".git")
        assert not f.startswith("node_modules")
        assert not f.startswith(".next")
        assert not f.startswith("dist")
        assert not f.startswith("build")
        assert not f.startswith("coverage")
        assert not f.startswith("venv")
        assert not f.startswith(".venv")
        assert not f.startswith("__pycache__")
        assert not f.startswith(".pytest_cache")


def test_language_detection(tmp_path: Path) -> None:
    """Verify accurate language and extension detection."""
    repo = tmp_path / "lang_repo"
    repo.mkdir()

    (repo / "main.py").write_text("print('hello')")
    (repo / "worker.py").write_text("import os")
    (repo / "types.pyi").write_text("x: int")
    (repo / "app.ts").write_text("const x: number = 1;")
    (repo / "component.tsx").write_text("export const C = () => <div/>;")
    (repo / "index.js").write_text("console.log(1);")
    (repo / "server.go").write_text("package main")
    (repo / "lib.rs").write_text("fn test() {}")
    (repo / "README.md").write_text("# Title")
    (repo / "Dockerfile").write_text("FROM alpine")
    (repo / "Makefile").write_text("all:\n\techo 1")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    langs = index.detected_languages
    assert langs["Python"] == 3
    assert langs["TypeScript"] == 2
    assert langs["JavaScript"] == 1
    assert langs["Go"] == 1
    assert langs["Rust"] == 1
    assert langs["Markdown"] == 1
    assert langs["Dockerfile"] == 1
    assert langs["Makefile"] == 1

    exts = index.detected_extensions
    assert exts[".py"] == 2
    assert exts[".pyi"] == 1
    assert exts[".ts"] == 1
    assert exts[".tsx"] == 1


def test_important_file_detection(tmp_path: Path) -> None:
    """Verify common project configuration files and documentation are flagged."""
    repo = tmp_path / "cfg_repo"
    repo.mkdir()

    config_files = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "setup.py",
        "Dockerfile",
        "docker-compose.yml",
        "tsconfig.json",
        "README.md",
        "pytest.ini",
        "LICENSE",
    ]
    for filename in config_files:
        (repo / filename).write_text("content")

    # Regular file
    (repo / "script.py").write_text("print('run')")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    for cf in config_files:
        assert cf in index.important_files

    assert "script.py" not in index.important_files


def test_test_file_and_directory_detection(tmp_path: Path) -> None:
    """Verify test files, test directories, and non-test helpers are properly classified."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    (repo / "src" / "api").mkdir(parents=True)
    (repo / "src" / "api" / "routes.py").write_text("# route")
    (repo / "src" / "api" / "routes.test.ts").write_text("// test")
    (repo / "src" / "utils.py").write_text("# utils")

    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_routes.py").write_text("# test")
    (repo / "tests" / "conftest.py").write_text("# conf")
    (repo / "tests" / "helper.py").write_text("# helper")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert "tests" in index.test_directories
    assert "src" in index.source_directories

    # Only files matching test naming conventions should be in test_files
    assert "tests/test_routes.py" in index.test_files
    assert "src/api/routes.test.ts" in index.test_files
    assert "tests/__init__.py" not in index.test_files
    assert "tests/conftest.py" not in index.test_files
    assert "tests/helper.py" not in index.test_files

    # Role checks
    assert index.file_roles["tests/test_routes.py"] == FileRole.TEST
    assert index.file_roles["src/api/routes.test.ts"] == FileRole.TEST
    assert index.file_roles["tests/__init__.py"] == FileRole.OTHER
    assert index.file_roles["tests/helper.py"] == FileRole.OTHER
    assert index.file_roles["tests/conftest.py"] == FileRole.CONFIG

    # Source files
    assert "src/api/routes.py" in index.source_files
    assert "src/utils.py" in index.source_files


def test_binary_and_large_file_exclusion(tmp_path: Path) -> None:
    """Verify binary files and oversized files are excluded from index."""
    repo = tmp_path / "binary_repo"
    repo.mkdir()

    # Binary by extension
    (repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (repo / "archive.zip").write_bytes(b"PK\x03\x04")

    # Binary by content (null bytes in first 1024 bytes)
    (repo / "unknown.dat").write_bytes(b"header\x00binary\x00data")

    # Large text file (600 KB > 500 KB default limit)
    (repo / "huge.txt").write_text("x" * (600 * 1024))

    # Normal code file
    (repo / "app.py").write_text("print('ok')")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert index.discovered_files == ["app.py"]
    assert index.file_count == 1


def test_deterministic_output(tmp_path: Path) -> None:
    """Verify repeated scans of the same repo produce identical indices and text."""
    repo = tmp_path / "determ_repo"
    repo.mkdir()

    (repo / "src").mkdir()
    (repo / "src" / "b.py").write_text("# b")
    (repo / "src" / "a.py").write_text("# a")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_b.py").write_text("# tb")
    (repo / "tests" / "test_a.py").write_text("# ta")
    (repo / "pyproject.toml").write_text("[tool]")
    (repo / "README.md").write_text("# Readme")

    scanner1 = RepositoryScanner()
    scanner2 = RepositoryScanner()

    idx1 = scanner1.scan(repo)
    idx2 = scanner2.scan(repo)

    assert idx1.to_dict() == idx2.to_dict()
    assert idx1.render_text() == idx2.render_text()
    assert str(idx1) == str(idx2)


def test_custom_ignore_rules(tmp_path: Path) -> None:
    """Verify custom ignore directories and file patterns are respected."""
    repo = tmp_path / "custom_repo"
    repo.mkdir()

    (repo / "generated").mkdir()
    (repo / "generated" / "code.py").write_text("# gen")

    (repo / "legacy").mkdir()
    (repo / "legacy" / "old.py").write_text("# legacy")

    (repo / "app.py").write_text("# app")
    (repo / "app.bak").write_text("# backup")

    config = ScannerConfig(
        ignore_directories={"generated"},
        ignore_patterns=["*.bak"],
    )
    scanner = RepositoryScanner(config=config)
    index = scanner.scan(repo)

    assert "generated/code.py" not in index.discovered_files
    assert "app.bak" not in index.discovered_files
    assert "legacy/old.py" in index.discovered_files
    assert "app.py" in index.discovered_files


def test_max_scanned_files_cap(tmp_path: Path) -> None:
    """Verify max_scanned_files cap halts file discovery."""
    repo = tmp_path / "cap_repo"
    repo.mkdir()

    for i in range(25):
        (repo / f"file_{i:02d}.py").write_text(f"# {i}")

    scanner = RepositoryScanner(ScannerConfig(max_scanned_files=10))
    index = scanner.scan(repo)

    assert index.file_count == 10
    assert len(index.discovered_files) == 10


def test_invalid_repository_root_handling(tmp_path: Path) -> None:
    """Verify clear exceptions are raised for invalid directory paths."""
    scanner = RepositoryScanner()

    with pytest.raises(FileNotFoundError):
        scanner.scan(tmp_path / "non_existent_directory")

    file_path = tmp_path / "some_file.txt"
    file_path.write_text("not a dir")
    with pytest.raises(NotADirectoryError):
        scanner.scan(file_path)


def test_documentation_files_classification(tmp_path: Path) -> None:
    """Verify documentation files (README, PRD, docs/*.md) are classified as DOCUMENTATION, not SOURCE."""
    repo = tmp_path / "doc_repo"
    repo.mkdir()

    (repo / "README.md").write_text("# Readme")
    (repo / "README.rst").write_text("Readme RST")
    (repo / "PRD.md").write_text("# Product Requirements")
    (repo / "docs").mkdir()
    (repo / "docs" / "architecture.md").write_text("# Architecture")
    (repo / "docs" / "guide.rst").write_text("Guide")
    (repo / "LICENSE").write_text("MIT License")
    (repo / "main.py").write_text("print('app')")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert "README.md" in index.documentation_files
    assert "README.rst" in index.documentation_files
    assert "PRD.md" in index.documentation_files
    assert "docs/architecture.md" in index.documentation_files
    assert "docs/guide.rst" in index.documentation_files
    assert "LICENSE" in index.documentation_files

    # Crucial rule: documentation files must NOT be in source_files
    assert "README.md" not in index.source_files
    assert "README.rst" not in index.source_files
    assert "PRD.md" not in index.source_files
    assert "docs/architecture.md" not in index.source_files
    assert "docs/guide.rst" not in index.source_files
    assert "LICENSE" not in index.source_files

    assert index.file_roles["README.md"] == FileRole.DOCUMENTATION
    assert index.file_roles["PRD.md"] == FileRole.DOCUMENTATION
    assert index.file_roles["main.py"] == FileRole.SOURCE
    assert "main.py" in index.source_files


def test_tightened_test_file_detection(tmp_path: Path) -> None:
    """Verify tests/__init__.py is not a test file and test naming conventions are enforced."""
    repo = tmp_path / "test_conventions_repo"
    repo.mkdir()

    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_example.py").write_text("def test_one(): pass")
    (repo / "tests" / "example_test.py").write_text("def test_two(): pass")
    (repo / "tests" / "conftest.py").write_text("# fixtures")
    (repo / "tests" / "helper.py").write_text("def setup_data(): pass")
    (repo / "src").mkdir()
    (repo / "src" / "component.test.js").write_text("// test js")
    (repo / "src" / "button.spec.ts").write_text("// spec ts")
    (repo / "src" / "widget.test.tsx").write_text("// test tsx")
    (repo / "src" / "modal.spec.jsx").write_text("// spec jsx")
    (repo / "src" / "app.py").write_text("print('app')")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    # Valid test files
    assert "tests/test_example.py" in index.test_files
    assert "tests/example_test.py" in index.test_files
    assert "src/component.test.js" in index.test_files
    assert "src/button.spec.ts" in index.test_files
    assert "src/widget.test.tsx" in index.test_files
    assert "src/modal.spec.jsx" in index.test_files

    # Excluded from test_files
    assert "tests/__init__.py" not in index.test_files
    assert "tests/conftest.py" not in index.test_files
    assert "tests/helper.py" not in index.test_files

    assert index.file_roles["tests/__init__.py"] == FileRole.OTHER
    assert index.file_roles["tests/test_example.py"] == FileRole.TEST
    assert index.file_roles["tests/conftest.py"] == FileRole.CONFIG
    assert index.file_roles["tests/helper.py"] == FileRole.OTHER


def test_config_files_not_classified_as_source(tmp_path: Path) -> None:
    """Verify config and manifest files are classified as CONFIG, not SOURCE."""
    repo = tmp_path / "config_repo"
    repo.mkdir()

    configs = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "pytest.ini",
        "tsconfig.json",
        "Dockerfile",
        "docker-compose.yml",
        ".gitignore",
        "setup.py",
        "Makefile",
    ]
    for c in configs:
        (repo / c).write_text("config")

    (repo / "main.py").write_text("print('main')")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    for c in configs:
        assert index.file_roles[c] == FileRole.CONFIG
        assert c not in index.source_files
        assert c in index.important_files

    assert index.file_roles["main.py"] == FileRole.SOURCE
    assert "main.py" in index.source_files


def test_single_primary_role_per_file(tmp_path: Path) -> None:
    """Verify each file has exactly one primary role and collections are mutually exclusive."""
    repo = tmp_path / "roles_repo"
    repo.mkdir()

    (repo / "README.md").write_text("# Readme")
    (repo / "PRD.md").write_text("# PRD")
    (repo / "pyproject.toml").write_text("[tool]")
    (repo / ".gitignore").write_text("*.pyc")
    (repo / "main.py").write_text("print('main')")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_main.py").write_text("def test_ok(): pass")
    (repo / "tests" / "fixture.json").write_text("{}")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert index.file_count == 8
    assert len(index.file_roles) == 8

    # Check each file has a recognized FileRole
    valid_roles = {FileRole.SOURCE, FileRole.TEST, FileRole.CONFIG, FileRole.DOCUMENTATION, FileRole.OTHER}
    for file, role in index.file_roles.items():
        assert role in valid_roles

    # Check mutual exclusivity
    source_set = set(index.source_files)
    test_set = set(index.test_files)
    doc_set = set(index.documentation_files)
    config_set = set(index.config_files)
    other_set = set(index.other_files)

    assert source_set.isdisjoint(test_set)
    assert source_set.isdisjoint(doc_set)
    assert source_set.isdisjoint(config_set)
    assert test_set.isdisjoint(doc_set)
    assert test_set.isdisjoint(config_set)
    assert doc_set.isdisjoint(config_set)

    # Union must equal all discovered files
    assert source_set | test_set | doc_set | config_set | other_set == set(index.discovered_files)


def test_text_renderer_documentation_section(tmp_path: Path) -> None:
    """Verify render_text() includes ## DOCUMENTATION and separates categories."""
    repo = tmp_path / "render_repo"
    repo.mkdir()

    (repo / "README.md").write_text("# Readme")
    (repo / "PRD.md").write_text("# PRD")
    (repo / "pyproject.toml").write_text("[tool]")
    (repo / "main.py").write_text("print('hello')")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text("def test(): pass")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)
    rendered = index.render_text()

    assert "## DOCUMENTATION" in rendered
    assert "- PRD.md" in rendered
    assert "- README.md" in rendered

    assert "## IMPORTANT & CONFIGURATION FILES" in rendered
    assert "- pyproject.toml" in rendered

    assert "## SOURCE CODE" in rendered
    assert "main.py" in rendered

    assert "## TEST SUITE" in rendered
    assert "test_main.py" in rendered


def test_scanning_realistic_repository(tmp_path: Path) -> None:
    """End-to-end test scanning a realistic repository layout."""
    repo = tmp_path / "realistic_repo"
    repo.mkdir()

    # Root config
    (repo / "pyproject.toml").write_text('[project]\nname = "my-service"')
    (repo / "README.md").write_text("# My Service Documentation")
    (repo / "Dockerfile").write_text("FROM python:3.14-slim")
    (repo / "Makefile").write_text("test:\n\tpytest")

    # Ignored trees
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"pyc")

    # Source code
    (repo / "src" / "api").mkdir(parents=True)
    (repo / "src" / "api" / "routes.py").write_text("def get_users(): pass")
    (repo / "src" / "core").mkdir(parents=True)
    (repo / "src" / "core" / "engine.py").write_text("class Engine: pass")
    (repo / "src" / "models").mkdir(parents=True)
    (repo / "src" / "models" / "user.py").write_text("class User: pass")

    # Tests
    (repo / "tests").mkdir()
    (repo / "tests" / "test_routes.py").write_text("def test_get_users(): assert True")
    (repo / "tests" / "conftest.py").write_text("# fixtures")

    # Assets
    (repo / "assets").mkdir()
    (repo / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    # Discovered files check (image and .git ignored)
    assert index.file_count == 9
    assert "assets/logo.png" not in index.discovered_files

    # Config files check
    assert "pyproject.toml" in index.important_files
    assert "README.md" in index.important_files
    assert "Dockerfile" in index.important_files
    assert "Makefile" in index.important_files

    # Documentation files check
    assert "README.md" in index.documentation_files
    assert "README.md" not in index.source_files

    # Languages check
    assert index.detected_languages["Python"] == 5
    assert index.detected_languages["TOML"] == 1
    assert index.detected_languages["Markdown"] == 1

    # Source files check (configs and docs excluded)
    assert "src/api/routes.py" in index.source_files
    assert "src/core/engine.py" in index.source_files
    assert "src/models/user.py" in index.source_files
    assert "pyproject.toml" not in index.source_files
    assert "Dockerfile" not in index.source_files
    assert "Makefile" not in index.source_files

    # Test files check (conftest is config, not test file)
    assert "tests/test_routes.py" in index.test_files
    assert "tests/conftest.py" not in index.test_files

    # Text rendering verification
    rendered = index.render_text()
    assert "## REPOSITORY OVERVIEW" in rendered
    assert "Total Files: 9" in rendered
    assert "## DETECTED LANGUAGES" in rendered
    assert "Python: 5 files" in rendered
    assert "## IMPORTANT & CONFIGURATION FILES" in rendered
    assert "pyproject.toml" in rendered
    assert "## DOCUMENTATION" in rendered
    assert "README.md" in rendered
    assert "## SOURCE CODE" in rendered
    assert "src/api/routes.py" in rendered
    assert "## TEST SUITE" in rendered
    assert "tests/test_routes.py" in rendered
