"""
Tests for Person B Hardening P5.1: Canonical Path Normalization for Task Memory.

Verifies:
- Normalized path inserted first, unnormalized second.
- Unnormalized path inserted first, normalized second.
- Redundant variations (./src/a.py, src/./a.py, src/foo/../a.py) resolve to single entry ["src/a.py"].
- Duplicate prevention in relevant_files and touched_files.
- Return values correctly indicate whether a new logical path was inserted.
- Storage and disk reload preserve only canonical, repository-relative paths.
- No conversion to machine-specific absolute paths.
- Failure, Attempt, Discovery, and Observation path canonicalization.
"""

import json
import os
from pathlib import Path
import pytest

from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    TaskState,
)
from src.memory.storage import TaskStorage


@pytest.fixture
def tmp_manager(tmp_path: Path) -> MemoryManager:
    """Provide a MemoryManager backed by an isolated temporary directory."""
    storage = TaskStorage(base_dir=tmp_path / ".harness")
    return MemoryManager(storage=storage)


def test_normalized_inserted_first_unnormalized_second(tmp_manager: MemoryManager) -> None:
    """When a normalized path is added first, later unnormalized variants must be rejected."""
    tmp_manager.initialize_task("task-norm-first", "Test normalized first")

    assert tmp_manager.add_relevant_file("src/auth.py") is True
    assert tmp_manager.add_relevant_file("./src/auth.py") is False
    assert tmp_manager.add_relevant_file("src/./auth.py") is False
    assert tmp_manager.add_relevant_file("src/foo/../auth.py") is False
    assert tmp_manager.add_relevant_file("src/../src/auth.py") is False

    state = tmp_manager.get_state()
    assert state.relevant_files == ["src/auth.py"]


def test_unnormalized_inserted_first_normalized_second(tmp_manager: MemoryManager) -> None:
    """When an unnormalized path is added first, it must be stored normalized, and later normalized calls must be rejected."""
    tmp_manager.initialize_task("task-unnorm-first", "Test unnormalized first")

    # Insert unnormalized path first
    assert tmp_manager.add_relevant_file("./src/auth.py") is True

    # State must store canonical form immediately
    state = tmp_manager.get_state()
    assert state.relevant_files == ["src/auth.py"]

    # Subsequent normalized and equivalent unnormalized paths must be rejected as duplicates
    assert tmp_manager.add_relevant_file("src/auth.py") is False
    assert tmp_manager.add_relevant_file("src/./auth.py") is False
    assert tmp_manager.add_relevant_file("src/foo/../auth.py") is False

    assert state.relevant_files == ["src/auth.py"]


def test_various_redundant_paths_resolve_to_single_entry(tmp_manager: MemoryManager) -> None:
    """
    Verify the exact requirement from P5.1 prompt:
    src/auth.py, ./src/auth.py, src/./auth.py, src/foo/../auth.py
    must all resolve to one stored entry: ["src/auth.py"].
    """
    tmp_manager.initialize_task("task-all-variants", "Test all path variants")

    paths = [
        "src/auth.py",
        "./src/auth.py",
        "src/./auth.py",
        "src/foo/../auth.py",
    ]

    # Insert in order
    results = [tmp_manager.add_relevant_file(p) for p in paths]
    assert results == [True, False, False, False]
    assert tmp_manager.get_state().relevant_files == ["src/auth.py"]

    # Now verify in touched_files starting with a complex relative path
    tmp_manager.initialize_task("task-touched-variants", "Test touched variants")
    assert tmp_manager.mark_file_touched("src/foo/../auth.py") is True
    assert tmp_manager.mark_file_touched("./src/auth.py") is False
    assert tmp_manager.mark_file_touched("src/./auth.py") is False
    assert tmp_manager.mark_file_touched("src/auth.py") is False

    assert tmp_manager.get_state().touched_files == ["src/auth.py"]


def test_duplicate_prevention_in_relevant_files(tmp_manager: MemoryManager) -> None:
    """Verify duplicate prevention and alias methods in relevant_files."""
    tmp_manager.initialize_task("task-rel-dups", "Test duplicate prevention in relevant_files")

    assert tmp_manager.add_relevant_file("src/models.py") is True
    assert tmp_manager.mark_relevant_file("./src/models.py") is False
    assert tmp_manager.add_relevant_file("src/core/engine.py") is True
    assert tmp_manager.mark_relevant_file("src/core/./engine.py") is False
    assert tmp_manager.add_relevant_file("./src/core/sub/../engine.py") is False

    state = tmp_manager.get_state()
    assert state.relevant_files == ["src/models.py", "src/core/engine.py"]


def test_duplicate_prevention_in_touched_files(tmp_manager: MemoryManager) -> None:
    """Verify duplicate prevention in touched_files via direct calls and add_attempt."""
    tmp_manager.initialize_task("task-touch-dups", "Test duplicate prevention in touched_files")

    assert tmp_manager.mark_file_touched("./src/service.py") is True
    assert tmp_manager.add_touched_file("src/service.py") is False
    assert tmp_manager.mark_file_touched("src/../src/service.py") is False

    # Add attempt touching existing file in unnormalized form and new file in unnormalized form
    attempt = Attempt(
        id="att-1",
        action="modify service and helper",
        files_touched=["./src/service.py", "src/utils/../utils/helper.py", "src/utils/./helper.py"],
    )
    tmp_manager.add_attempt(attempt)

    state = tmp_manager.get_state()
    assert state.touched_files == ["src/service.py", "src/utils/helper.py"]
    # Attempt record itself should also store canonical paths
    assert state.attempts[0].files_touched == ["src/service.py", "src/utils/helper.py"]


def test_return_values_indicate_logical_insertion(tmp_manager: MemoryManager) -> None:
    """Verify boolean returns accurately indicate new logical path insertion."""
    tmp_manager.initialize_task("task-returns", "Test boolean return values")

    # add_relevant_file
    assert tmp_manager.add_relevant_file("src/a.py") is True
    assert tmp_manager.add_relevant_file("./src/a.py") is False
    assert tmp_manager.add_relevant_file("src/b.py") is True

    # mark_relevant_file
    assert tmp_manager.mark_relevant_file("./src/b.py") is False
    assert tmp_manager.mark_relevant_file("src/c.py") is True

    # mark_file_touched
    assert tmp_manager.mark_file_touched("src/x.py") is True
    assert tmp_manager.mark_file_touched("./src/x.py") is False

    # add_touched_file
    assert tmp_manager.add_touched_file("src/./x.py") is False
    assert tmp_manager.add_touched_file("src/y.py") is True


def test_persisted_and_reloaded_state_contains_only_canonical_paths(tmp_path: Path) -> None:
    """Verify saved JSON and reloaded memory contain only canonical, deduplicated paths."""
    storage = TaskStorage(base_dir=tmp_path / ".harness")
    manager1 = MemoryManager(storage=storage)
    manager1.initialize_task("task-persist-canon", "Test persistence canonicalization")

    manager1.add_relevant_file("./src/controller.py")
    manager1.add_relevant_file("src/controller.py")
    manager1.add_relevant_file("src/sub/../controller.py")
    manager1.add_relevant_file("./src/views/index.py")

    manager1.mark_file_touched("./src/controller.py")
    manager1.mark_file_touched("src/views/./index.py")

    manager1.save()

    # Verify JSON content on disk directly
    saved_json_path = storage.get_state_path("task-persist-canon")
    assert saved_json_path.is_file()
    with open(saved_json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    assert raw_data["relevant_files"] == ["src/controller.py", "src/views/index.py"]
    assert raw_data["touched_files"] == ["src/controller.py", "src/views/index.py"]

    # Verify reloaded state via MemoryManager
    manager2 = MemoryManager(storage=storage)
    loaded_state = manager2.load("task-persist-canon")

    assert loaded_state.relevant_files == ["src/controller.py", "src/views/index.py"]
    assert loaded_state.touched_files == ["src/controller.py", "src/views/index.py"]


def test_loading_legacy_unnormalized_json_canonicalizes_on_load(tmp_path: Path) -> None:
    """If legacy JSON contains unnormalized duplicate paths, deserializing canonicalizes and deduplicates."""
    storage = TaskStorage(base_dir=tmp_path / ".harness")
    legacy_json_path = storage.get_state_path("task-legacy")
    legacy_json_path.parent.mkdir(parents=True, exist_ok=True)

    legacy_data = {
        "task_id": "task-legacy",
        "task": "Legacy task",
        "phase": "EXECUTE",
        "status": "RUNNING",
        "relevant_files": ["./src/auth.py", "src/auth.py", "src/foo/../auth.py", "src/models.py"],
        "touched_files": ["src/./auth.py", "src/auth.py"],
    }
    with open(legacy_json_path, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    manager = MemoryManager(storage=storage)
    state = manager.load("task-legacy")

    assert state.relevant_files == ["src/auth.py", "src/models.py"]
    assert state.touched_files == ["src/auth.py"]


def test_no_conversion_to_absolute_paths(tmp_manager: MemoryManager) -> None:
    """Stored paths must remain repository-relative and never convert to absolute paths."""
    tmp_manager.initialize_task("task-relative", "Test paths remain relative")

    relative_inputs = [
        "src/auth.py",
        "./src/auth.py",
        "src/../src/auth.py",
        "tests/unit/test_auth.py",
    ]
    for p in relative_inputs:
        tmp_manager.add_relevant_file(p)
        tmp_manager.mark_file_touched(p)

    state = tmp_manager.get_state()
    for p in state.relevant_files + state.touched_files:
        assert not os.path.isabs(p)
        assert not p.startswith("/")
        assert not p.startswith("./")


def test_failure_occurrence_path_canonicalization(tmp_manager: MemoryManager) -> None:
    """Failure record files must be canonicalized and deduplicated across occurrences."""
    tmp_manager.initialize_task("task-fail-canon", "Test failure path canonicalization")

    # First occurrence with unnormalized path
    fail1 = Failure(
        error_signature="AuthError",
        summary="Token validation failure",
        action="verify_token",
        files=["./src/auth.py", "src/auth.py"],
    )
    tmp_manager.add_failure(fail1)

    state = tmp_manager.get_state()
    assert state.failures[0].files == ["src/auth.py"]

    # Second occurrence with another unnormalized variant
    fail2 = Failure(
        error_signature="AuthError",
        summary="Token validation failure repeated",
        action="verify_token",
        files=["src/foo/../auth.py", "./src/crypto.py"],
    )
    tmp_manager.add_failure(fail2)

    assert state.failures[0].occurrence_count == 2
    assert state.failures[0].files == ["src/auth.py", "src/crypto.py"]


def test_discovery_and_observation_path_canonicalization(tmp_manager: MemoryManager) -> None:
    """Discovery and Observation file lists must be canonicalized."""
    tmp_manager.initialize_task("task-disc-obs", "Test discovery and observation canonicalization")

    disc = Discovery(
        statement="Auth uses JWT tokens",
        evidence="Found jwt.decode call",
        files=["./src/auth.py", "src/auth.py", "src/foo/../auth.py"],
    )
    tmp_manager.add_discovery(disc)

    obs = Observation(
        type="test_result",
        source="pytest",
        summary="Tests failed in auth module",
        files=["./tests/test_auth.py", "tests/./test_auth.py"],
    )
    tmp_manager.add_observation(obs)

    state = tmp_manager.get_state()
    assert state.discoveries[0].files == ["src/auth.py"]
    assert state.recent_observations[0].files == ["tests/test_auth.py"]
