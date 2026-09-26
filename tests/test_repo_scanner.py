"""Unit tests for deterministic repository scanner and indexer."""

from pathlib import Path
import pytest

from src.context.scanner import (
    RepositoryIndex,
    RepositoryScanner,
    ScannerConfig,
    is_binary_file,
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
    """Verify test files, test directories, and source files are identified."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    (repo / "src" / "api").mkdir(parents=True)
    (repo / "src" / "api" / "routes.py").write_text("# route")
    (repo / "src" / "api" / "routes.test.ts").write_text("// test")
    (repo / "src" / "utils.py").write_text("# utils")

    (repo / "tests").mkdir()
    (repo / "tests" / "test_routes.py").write_text("# test")
    (repo / "tests" / "conftest.py").write_text("# conf")
    (repo / "tests" / "helper.py").write_text("# helper")

    scanner = RepositoryScanner()
    index = scanner.scan(repo)

    assert "tests" in index.test_directories
    assert "src" in index.source_directories

    assert "tests/test_routes.py" in index.test_files
    assert "tests/conftest.py" in index.test_files
    assert "tests/helper.py" in index.test_files
    assert "src/api/routes.test.ts" in index.test_files

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

    # Languages check
    assert index.detected_languages["Python"] == 5
    assert index.detected_languages["TOML"] == 1
    assert index.detected_languages["Markdown"] == 1

    # Source files check
    assert "src/api/routes.py" in index.source_files
    assert "src/core/engine.py" in index.source_files
    assert "src/models/user.py" in index.source_files

    # Test files check
    assert "tests/test_routes.py" in index.test_files
    assert "tests/conftest.py" in index.test_files

    # Text rendering verification
    rendered = index.render_text()
    assert "## REPOSITORY OVERVIEW" in rendered
    assert "Total Files: 9" in rendered
    assert "## DETECTED LANGUAGES" in rendered
    assert "Python: 5 files" in rendered
    assert "## IMPORTANT & CONFIGURATION FILES" in rendered
    assert "pyproject.toml" in rendered
    assert "## SOURCE CODE" in rendered
    assert "src/api/routes.py" in rendered
    assert "## TEST SUITE" in rendered
    assert "tests/test_routes.py" in rendered
