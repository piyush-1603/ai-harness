import pytest
import os
import copy
from src.memory.manager import MemoryManager, INLINE_OUTPUT_MAX_CHARS
from src.memory.models import Observation, Failure
from src.memory.inspection import ObservationInspection, FailureInspection, ArtifactInspection

def test_inspect_inline_observation_by_index(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    manager.add_observation(Observation(type="t", source="s", summary="small", raw_output="hello"))
    
    insp = manager.inspect_observation(0)
    assert insp.summary == "small"
    assert insp.raw_output_preview == "hello"
    assert insp.output_ref is None
    assert insp.has_external_artifact is False
    assert insp.full_output is None

def test_inspect_inline_observation_full_output(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    manager.add_observation(Observation(type="t", source="s", summary="small", raw_output="hello"))
    
    insp = manager.inspect_observation(0, include_full_output=True)
    assert insp.full_output == "hello"

def test_inspect_externalized_observation_by_index(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "A" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    
    insp = manager.inspect_observation(0)
    assert insp.output_ref == obs.output_ref
    assert insp.output_ref is not None
    assert insp.has_external_artifact is True
    assert insp.full_output is None
    assert "FULL OUTPUT STORED" in insp.raw_output_preview

def test_inspect_externalized_observation_full_output(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "A" * (INLINE_OUTPUT_MAX_CHARS + 100)
    manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    
    insp = manager.inspect_observation(0, include_full_output=True)
    assert insp.full_output == large_text

def test_inspect_observation_by_artifact_ref(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "B" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    
    insp = manager.inspect_observation(obs.output_ref)
    assert insp.index == 0
    assert insp.summary == "large"

def test_unknown_observation_index_raises_lookuperror(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    with pytest.raises(LookupError):
        manager.inspect_observation(0)
    with pytest.raises(LookupError):
        manager.inspect_observation(100)

def test_unknown_artifact_ref_raises_lookuperror(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    with pytest.raises(LookupError):
        manager.inspect_observation("artifact_1234567890abcdef1234567890abcdef")

def test_inspect_failure_by_index(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    manager.add_failure(Failure(error_signature="Sig1", summary="sum1", action="act1"))
    
    insp = manager.inspect_failure(0)
    assert insp.error_signature == "Sig1"
    assert insp.summary == "sum1"

def test_inspect_failure_by_exact_signature(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    manager.add_failure(Failure(error_signature="Sig1", summary="sum1", action="act1"))
    
    insp = manager.inspect_failure("Sig1")
    assert insp.index == 0
    assert insp.error_signature == "Sig1"

def test_missing_failure_raises_lookuperror(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    with pytest.raises(LookupError):
        manager.inspect_failure(0)
    with pytest.raises(LookupError):
        manager.inspect_failure("UnknownSig")

def test_inspect_artifact_returns_exact_original_content(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "C" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    
    insp = manager.inspect_artifact(obs.output_ref)
    assert insp.ref == obs.output_ref
    assert insp.content == large_text
    assert insp.chars == len(large_text)
    assert insp.exists is True

def test_malformed_artifact_ref_raises_valueerror(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    with pytest.raises(ValueError):
        manager.inspect_artifact("not_an_artifact_ref")

def test_valid_looking_missing_artifact_raises_filenotfounderror(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    # 64 char hex string for valid format but non-existent
    with pytest.raises(FileNotFoundError):
        manager.inspect_artifact("artifact_0000000000000000000000000000000000000000000000000000000000000000")

def test_list_artifact_refs_returns_deterministic_unique_refs(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text1 = "D" * (INLINE_OUTPUT_MAX_CHARS + 100)
    large_text2 = "E" * (INLINE_OUTPUT_MAX_CHARS + 100)
    
    manager.add_observation(Observation(type="t", source="s", summary="l1", raw_output=large_text1))
    manager.add_observation(Observation(type="t", source="s", summary="l1_dup", raw_output=large_text1))
    manager.add_observation(Observation(type="t", source="s", summary="l2", raw_output=large_text2))
    
    refs = manager.list_artifact_refs()
    assert len(refs) == 2
    # Ensure they are unique and in order of first appearance
    assert refs[0] == manager.state.recent_observations[0].output_ref
    assert refs[1] == manager.state.recent_observations[2].output_ref

def test_inspection_does_not_mutate_taskstate(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "F" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    manager.add_failure(Failure(error_signature="Sig1", summary="sum1", action="act1"))
    
    before_state = copy.deepcopy(manager.get_state().to_dict())
    
    # Run inspections
    manager.inspect_observation(0, include_full_output=True)
    manager.inspect_observation(obs.output_ref)
    manager.inspect_failure(0)
    manager.inspect_failure("Sig1")
    manager.inspect_artifact(obs.output_ref)
    manager.list_artifact_refs()
    
    after_state = manager.get_state().to_dict()
    assert before_state == after_state

def test_inspection_does_not_append_events(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task1", "test")
    
    large_text = "G" * (INLINE_OUTPUT_MAX_CHARS + 100)
    obs = manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    manager.add_failure(Failure(error_signature="Sig1", summary="sum1", action="act1"))
    
    num_events_before = len(manager.get_events())
    
    manager.inspect_observation(0, include_full_output=True)
    manager.inspect_failure("Sig1")
    manager.inspect_artifact(obs.output_ref)
    manager.list_artifact_refs()
    
    num_events_after = len(manager.get_events())
    assert num_events_before == num_events_after

def test_persistence_reload(tmp_path):
    manager = MemoryManager(base_dir=tmp_path)
    manager.initialize_task("task2", "test persist")
    
    large_text = "H" * (INLINE_OUTPUT_MAX_CHARS + 100)
    manager.add_observation(Observation(type="t", source="s", summary="large", raw_output=large_text))
    manager.save()
    
    # Reload
    new_manager = MemoryManager(base_dir=tmp_path)
    new_manager.load("task2")
    
    insp = new_manager.inspect_observation(0, include_full_output=True)
    assert insp.full_output == large_text
