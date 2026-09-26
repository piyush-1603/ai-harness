import hashlib
import os
import pytest
import tempfile
from pathlib import Path

from src.memory.manager import MemoryManager, INLINE_OUTPUT_MAX_CHARS, OUTPUT_PREVIEW_CHARS
from src.memory.models import Observation
from src.memory.artifacts import ArtifactStore

def test_small_output_stays_inline(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test small")

    obs = Observation(
        type="tool_result",
        source="run_bash",
        summary="success",
        raw_output="hello world"
    )
    recorded = manager.add_observation(obs)

    assert recorded.raw_output == "hello world"
    assert recorded.output_ref is None
    assert recorded.raw_output_chars == len("hello world")

def test_large_output_is_externalized(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test large")

    large_text = "A" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = Observation(
        type="tool_result",
        source="run_bash",
        summary="success",
        raw_output=large_text
    )

    recorded = manager.add_observation(obs)

    assert recorded.output_ref is not None
    assert recorded.raw_output != large_text
    assert recorded.raw_output_chars == len(large_text)
    assert "[FULL OUTPUT STORED: artifact_" in recorded.raw_output
    assert len(recorded.raw_output) <= OUTPUT_PREVIEW_CHARS + 100 # + some buffer for the marker

def test_full_artifact_retrieval(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test ret")

    large_text = "B" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = Observation(
        type="tool_result",
        source="run_bash",
        summary="success",
        raw_output=large_text
    )
    recorded = manager.add_observation(obs)

    assert manager.has_artifact(recorded.output_ref) is True
    retrieved = manager.get_artifact(recorded.output_ref)
    assert retrieved == large_text

def test_deterministic_reference(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test det")

    large_text = "C" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs1 = Observation(type="t", source="s", summary="s", raw_output=large_text)
    obs2 = Observation(type="t", source="s", summary="s", raw_output=large_text)

    r1 = manager.add_observation(obs1)
    r2 = manager.add_observation(obs2)

    assert r1.output_ref == r2.output_ref

def test_different_content_produces_different_refs(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test det")

    text1 = "C" * (INLINE_OUTPUT_MAX_CHARS + 100)
    text2 = "D" * (INLINE_OUTPUT_MAX_CHARS + 100)

    obs1 = Observation(type="t", source="s", summary="s", raw_output=text1)
    obs2 = Observation(type="t", source="s", summary="s", raw_output=text2)

    r1 = manager.add_observation(obs1)
    r2 = manager.add_observation(obs2)

    assert r1.output_ref != r2.output_ref

def test_persistence_reload(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task2", "test persist")

    large_text = "E" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = Observation(
        type="tool_result",
        source="run_bash",
        summary="success",
        raw_output=large_text
    )
    recorded = manager.add_observation(obs)
    ref = recorded.output_ref
    preview = recorded.raw_output
    manager.save()

    # Reload
    new_manager = MemoryManager(base_dir=tmp_path)
    state = new_manager.load("task2")

    loaded_obs = state.recent_observations[0]
    assert loaded_obs.output_ref == ref
    assert loaded_obs.raw_output == preview
    assert loaded_obs.raw_output_chars == len(large_text)
    assert new_manager.get_artifact(ref) == large_text

def test_path_traversal_protection(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store._get_task_dir("../secret")

    with pytest.raises(ValueError):
        store.get_text("task1", "../passwd")

def test_context_does_not_accidentally_inline_artifact(tmp_path):
    from src.context.builder import ContextBuilder
    from src.context.config import ContextConfig

    manager = MemoryManager(base_dir=tmp_path)
    state = manager.initialize_task("task1", "test context")

    large_text = "F" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = Observation(
        type="tool_result",
        source="run_bash",
        summary="success",
        raw_output=large_text
    )
    manager.add_observation(obs)

    cfg = ContextConfig()
    cfg.include_recent_observations = True
    cfg.include_raw_output = True
    cfg.max_observations = 10

    builder = ContextBuilder(config=cfg)
    bundle = builder.build(state)

    # Check that it made it into the bundle
    assert len(bundle.recent_observations) == 1
    obs_in_context = bundle.recent_observations[0]

    assert "[FULL OUTPUT STORED: artifact_" in obs_in_context.raw_output
    assert large_text not in obs_in_context.raw_output

    # And double check the rendered text just in case
    text = bundle.render_text()
    assert large_text not in text

def test_get_text_no_create_dir(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    ref = "artifact_" + "a" * 64
    before = list(tmp_path.rglob("*"))
    with pytest.raises(FileNotFoundError):
        store.get_text("missing_task", ref)
    after = list(tmp_path.rglob("*"))
    assert before == after

def test_exists_no_create_dir(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    ref = "artifact_" + "a" * 64
    before = list(tmp_path.rglob("*"))
    assert store.exists("missing_task", ref) is False
    after = list(tmp_path.rglob("*"))
    assert before == after

def test_unsafe_task_ids_rejected(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    for bad in ["../task", "task/child", "task\\child", "/absolute", ".", "..", ""]:
        with pytest.raises(ValueError):
            store._validate_task_id(bad)
        with pytest.raises(ValueError):
            store.put_text(bad, "content")

def test_strict_artifact_ref_validation(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    for bad in [
        "artifact_abc",
        "artifact_" + "a" * 63,
        "artifact_" + "a" * 65,
        "artifact_" + "A" * 64,
        "../artifact_" + "a" * 64,
        "/artifact_" + "a" * 64,
        "artifact_" + "a" * 32 + "/" + "a" * 31,
    ]:
        with pytest.raises(ValueError):
            store._validate_ref(bad)
        with pytest.raises(ValueError):
            store.exists("task1", bad)
        with pytest.raises(ValueError):
            store.get_text("task1", bad)

def test_repeated_identical_write_mtime(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    ref = store.put_text("task1", "content")

    target_path = tmp_path / "artifacts" / "task1" / ref
    mtime_ns = target_path.stat().st_mtime_ns

    # Store identical content again
    ref2 = store.put_text("task1", "content")
    assert ref == ref2

    assert target_path.stat().st_mtime_ns == mtime_ns

def test_unicode_roundtrip(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    content = "नमस्ते\nλ = 42\n🚀"
    ref = store.put_text("task1", content)
    retrieved = store.get_text("task1", ref)
    assert retrieved == content

def test_concurrent_identical_writes(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    import concurrent.futures
    content = "Huge same content " * 1000

    def write_task():
        return store.put_text("task1", content)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(write_task) for _ in range(20)]
        results = [f.result() for f in futures]

    assert len(set(results)) == 1
    ref = results[0]

    # Final artifact exists once
    target_dir = tmp_path / "artifacts" / "task1"
    files = list(target_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == ref

    # Content is correct
    assert store.get_text("task1", ref) == content

def test_concurrent_distinct_writes(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    import concurrent.futures

    def write_task(i):
        content = f"Unique {i} " * 1000
        return store.put_text("task1", content), content

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(write_task, i) for i in range(20)]
        results = [f.result() for f in futures]

    refs = [r[0] for r in results]
    assert len(set(refs)) == 20

    target_dir = tmp_path / "artifacts" / "task1"
    files = list(target_dir.iterdir())
    assert len(files) == 20

    for ref, content in results:
        assert store.get_text("task1", ref) == content

def test_temp_files_cleaned_after_success(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    ref = store.put_text("task1", "content")

    target_dir = tmp_path / "artifacts" / "task1"
    # The only file should be the artifact itself
    files = list(target_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == ref

def test_temp_files_cleaned_after_failure(tmp_path, monkeypatch):
    store = ArtifactStore(base_dir=tmp_path)

    # Mock os.replace to raise an error
    def mock_replace(src, dst):
        raise OSError("Mock failure")

    monkeypatch.setattr(os, "replace", mock_replace)

    with pytest.raises(OSError):
        store.put_text("task1", "content")

    target_dir = tmp_path / "artifacts" / "task1"
    if target_dir.exists():
        assert len(list(target_dir.iterdir())) == 0

def test_existing_survives_failed_duplicate(tmp_path, monkeypatch):
    store = ArtifactStore(base_dir=tmp_path)
    # First write succeeds
    ref = store.put_text("task1", "content")

    # Monkeypatch to ensure if it tried to rewrite, it would fail
    # Actually, our put_text checks if it exists and returns early.
    # To test failure during duplicate write path, we can bypass the early return
    # or just trust the early return. Let's force early return bypass.

    target_path = tmp_path / "artifacts" / "task1" / ref
    original_mtime = target_path.stat().st_mtime_ns

    import src.memory.artifacts
    original_is_file = Path.is_file
    def mock_is_file(self):
        if self.name == ref:
            return False # pretend it doesn't exist
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", mock_is_file)

    def mock_replace(src, dst):
        raise OSError("Mock failure during replace")

    monkeypatch.setattr(os, "replace", mock_replace)

    with pytest.raises(OSError):
        store.put_text("task1", "content")

    # The original file should still exist, unharmed
    monkeypatch.undo() # restore original is_file
    assert target_path.exists()
    assert store.get_text("task1", ref) == "content"
    # Mtime should be unchanged because replace failed before overwriting, or we wrote to a tmp file
    assert target_path.stat().st_mtime_ns == original_mtime

def test_symlink_escape_rejected(tmp_path):
    import platform
    store = ArtifactStore(base_dir=tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()

    # Create a task dir that is a symlink to outside
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()

    task_dir = root / "task1"
    try:
        task_dir.symlink_to(secret_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/configuration")

    # Now try to write an artifact. It should resolve outside and raise ValueError.
    with pytest.raises(ValueError, match="Unsafe path resolution"):
        store.put_text("task1", "content")

    with pytest.raises(ValueError, match="Unsafe path resolution"):
        store.exists("task1", "artifact_" + "a"*64)

def test_corrupted_artifact_detection(tmp_path):
    store = ArtifactStore(base_dir=tmp_path)
    content = "hello"
    ref = store.put_text("task1", content)

    target_path = tmp_path / "artifacts" / "task1" / ref
    with open(target_path, "wb") as f:
        f.write(b"corrupted")

    from src.memory.artifacts import ArtifactIntegrityError
    with pytest.raises(ArtifactIntegrityError):
        store.get_text("task1", ref)

def test_inspection_api_compatibility(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test inspect")
    from src.memory.manager import INLINE_OUTPUT_MAX_CHARS
    large_text = "A" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="s", raw_output=large_text))

    # B9 inspection should still work
    ref = obs.output_ref
    assert manager.inspect_artifact(ref).content == large_text
