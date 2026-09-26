import subprocess
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Adjusting import to allow main execution testing
from src.main import main

def test_step1_require_auth_missing_repo(capsys):
    # If --require-auth is set but no --repo is provided, fail.
    test_args = ["main.py", "--issue", "test", "--require-auth"]
    with patch.object(sys, 'argv', test_args):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        captured = capsys.readouterr()
        assert "Error: --require-auth is set but --repo is missing" in captured.err

@patch("src.main.check_repo_access")
@patch("src.main.clone_authenticated")
@patch("src.main.Orchestrator")
@patch("src.main.RepositoryScanner")
def test_step1_require_auth_present_repo(mock_scanner, mock_orch, mock_clone, mock_check, capsys, tmp_path):
    # Setup mock auth result
    mock_auth = MagicMock()
    mock_auth.owner = "testowner"
    mock_auth.name = "testname"
    mock_auth.authorized_at = 12345.0
    mock_check.return_value = mock_auth

    mock_orch_inst = MagicMock()
    mock_orch_inst.run.return_value = {"status": "completed", "verified": True}
    mock_orch.return_value = mock_orch_inst

    workspace = tmp_path / "new_workspace"
    test_args = ["main.py", "--issue", "test", "--require-auth", "--repo", "testowner/testname", "--workspace", str(workspace)]
    
    with patch.object(sys, 'argv', test_args):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0
            
    mock_check.assert_called_once_with("testowner/testname", "")
    mock_clone.assert_called_once_with("testowner/testname", str(workspace), "")

@patch("src.main.check_repo_access")
@patch("src.main.clone_authenticated")
@patch("src.main.Orchestrator")
@patch("src.main.RepositoryScanner")
def test_step2_workspace_mismatch(mock_scanner, mock_orch, mock_clone, mock_check, capsys, tmp_path):
    # Setup mock auth result
    mock_auth = MagicMock()
    mock_auth.owner = "testowner"
    mock_auth.name = "testname"
    mock_check.return_value = mock_auth

    # Create workspace with unrelated git remote
    workspace = tmp_path / "existing_workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("hello")

    # We patch subprocess.run in main specifically for the git remote get-url call
    with patch("subprocess.run") as mock_run:
        mock_run_result = MagicMock()
        mock_run_result.stdout = "https://github.com/otherowner/othername.git\\n"
        mock_run.return_value = mock_run_result
        
        test_args = ["main.py", "--issue", "test", "--repo", "testowner/testname", "--workspace", str(workspace)]
        with patch.object(sys, 'argv', test_args):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 1
            captured = capsys.readouterr()
            assert "Error: Existing workspace" in captured.err
            assert "does not match authorized repo" in captured.err

@patch("src.main.check_repo_access")
@patch("src.main.clone_authenticated")
@patch("src.main.Orchestrator")
@patch("src.main.RepositoryScanner")
def test_step2_workspace_no_remote(mock_scanner, mock_orch, mock_clone, mock_check, capsys, tmp_path):
    mock_auth = MagicMock()
    mock_auth.owner = "testowner"
    mock_auth.name = "testname"
    mock_check.return_value = mock_auth

    workspace = tmp_path / "existing_workspace2"
    workspace.mkdir()
    (workspace / "file.txt").write_text("hello")

    with patch("subprocess.run") as mock_run:
        # Simulate git failing
        mock_run.side_effect = subprocess.CalledProcessError(128, ["git", "remote"])
        
        test_args = ["main.py", "--issue", "test", "--repo", "testowner/testname", "--workspace", str(workspace)]
        with patch.object(sys, 'argv', test_args):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 1
            captured = capsys.readouterr()
            assert "is not a valid git repository or missing origin remote" in captured.err
