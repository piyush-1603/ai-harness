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
        store._get_dir("../secret")
        
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
