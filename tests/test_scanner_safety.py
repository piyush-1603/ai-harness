import os
from pathlib import Path
import pytest
from src.context.scanner import RepositoryScanner, ScannerConfig, RepositoryIndex

def test_symlink_escape_not_indexed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    
    # external symlink
    link = repo / "external.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("Symlinks not supported")
        
    scanner = RepositoryScanner()
    index = scanner.scan(repo)
    
    assert "external.txt" not in index.discovered_files

def test_broken_symlink_does_not_crash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    
    link = repo / "broken.txt"
    try:
        link.symlink_to(tmp_path / "does_not_exist.txt")
    except OSError:
        pytest.skip("Symlinks not supported")
        
    scanner = RepositoryScanner()
    index = scanner.scan(repo)
    
    assert "broken.txt" not in index.discovered_files

def test_unreadable_file_safety(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    f = repo / "unreadable.txt"
    f.write_text("hello")
    
    # Mock stat to raise PermissionError for this file
    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        if self.name == "unreadable.txt":
            raise PermissionError("Mock unreadable")
        return orig_stat(self, *args, **kwargs)
        
    monkeypatch.setattr(Path, "stat", mock_stat)
    
    scanner = RepositoryScanner()
    index = scanner.scan(repo)
    
    assert "unreadable.txt" not in index.discovered_files

def test_max_files_truncation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    
    for i in range(5):
        (repo / f"file_{i}.txt").write_text("hello")
        
    cfg = ScannerConfig(max_scanned_files=3)
    scanner = RepositoryScanner(config=cfg)
    index = scanner.scan(repo)
    
    assert len(index.discovered_files) == 3
    assert index.scan_truncated is True
    assert "max_files_reached" in index.truncation_reasons

def test_oversized_file_truncation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    
    f1 = repo / "small.txt"
    f1.write_text("small")
    
    f2 = repo / "large.txt"
    f2.write_text("a" * 1024)
    
    cfg = ScannerConfig(max_file_size=500)
    scanner = RepositoryScanner(config=cfg)
    index = scanner.scan(repo)
    
    assert "small.txt" in index.discovered_files
    assert "large.txt" not in index.discovered_files
    assert index.scan_truncated is True
    assert "file_too_large:large.txt" in index.truncation_reasons

def test_deterministic_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    
    # Create files in random order
    for name in ["z.txt", "a.txt", "m.txt", "b.py", "c.ts"]:
        (repo / name).write_text("content")
        
    scanner = RepositoryScanner()
    idx1 = scanner.scan(repo)
    idx2 = scanner.scan(repo)
    
    assert idx1.to_dict() == idx2.to_dict()
    assert idx1.discovered_files == ["a.txt", "b.py", "c.ts", "m.txt", "z.txt"]

def test_normal_scan_works(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_foo.py").write_text("def test_bar(): pass")
    (repo / "README.md").write_text("doc")
    
    scanner = RepositoryScanner()
    index = scanner.scan(repo)
    
    assert "test_foo.py" in index.discovered_files
    assert "test_foo.py" in index.test_files
    assert "README.md" in index.documentation_files
    assert index.scan_truncated is False
    assert not index.truncation_reasons
