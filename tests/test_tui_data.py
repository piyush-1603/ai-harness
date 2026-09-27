import pytest
from copy import deepcopy

from src.memory.manager import MemoryManager
from src.memory.models import Failure, Observation
from src.context.diagnostics import ContextDiagnosticsEngine
from src.tui.data import TUIDataProvider

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("t1", "test task")
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory)

def test_overview_maps_task_state(provider, memory):
    state = memory.get_state()
    state.model_calls = 5
    data = provider.get_overview_data()
    assert data.task_id == "t1"
    assert data.task == "test task"
    assert data.model_calls == 5

def test_provider_does_not_mutate_state(provider, memory):
    state_before = deepcopy(memory.get_state())
    events_before = len(memory.get_events())
    
    provider.get_overview_data()
    provider.get_context_data()
    provider.get_memory_data()
    provider.get_repository_data()
    provider.get_events_data()
    
    assert memory.get_state() == state_before
    assert len(memory.get_events()) == events_before

def test_repeated_failure_count(provider, memory):
    memory.record_failure(Failure(error_signature="err1", summary="sum1", action="act1"))
    memory.record_failure(Failure(error_signature="err1", summary="sum1", action="act1"))
    memory.record_failure(Failure(error_signature="err2", summary="sum2", action="act2"))
    
    data = provider.get_overview_data()
    assert data.active_failure == "err1"
    assert data.active_failure_count == 2

def test_touched_precedence(provider, memory):
    memory.add_relevant_file("src/foo.py")
    memory.add_touched_file("src/foo.py")
    memory.add_relevant_file("src/bar.py")
    
    data = provider.get_overview_data()
    paths = [f.path for f in data.relevant_files]
    assert len(paths) == 2
    
    foo_file = next(f for f in data.relevant_files if f.path == "src/foo.py")
    assert foo_file.status == "touched"

def test_missing_repository_returns_empty(provider):
    data = provider.get_repository_data()
    assert data.files == []

def test_empty_event_history(provider):
    data = provider.get_events_data()
    provider.memory._events.clear()
    assert provider.get_events_data() == []

def test_artifact_metadata_without_reading(monkeypatch, memory):
    def fake_get_artifact(ref):
        raise AssertionError("get_artifact was called!")
        
    monkeypatch.setattr(memory, "get_artifact", fake_get_artifact)
    
    # Store a real artifact (must be large enough to trigger externalization > 2000 chars)
    obs = Observation(type="log", source="sys", summary="sum", raw_output="preview" * 1000)
    # Simulate an artifact-backed observation
    memory.add_observation(obs)
    
    # Ensure it's externalized
    assert memory.get_state().recent_observations[-1].output_ref is not None
    
    prov = TUIDataProvider(memory)
    data = prov.get_memory_data()
    
    art = data.artifacts[0]
    assert art.ref is not None
    # We shouldn't crash with AssertionError
    assert art.exists is True

def test_diagnostics_mapping(memory):
    engine = ContextDiagnosticsEngine()
    
    prov = TUIDataProvider(memory, engine)
    
    ctx = prov.get_context_data()
    # Ensure we get safe defaults or real mapping without crashing
    assert ctx.pressure_level is not None

def test_deterministic_ordering(provider, memory):
    memory.add_relevant_file("c.py")
    memory.add_relevant_file("a.py")
    memory.add_touched_file("b.py")
    
    data = provider.get_overview_data()
    paths = [f.path for f in data.relevant_files]
    assert paths == ["b.py", "c.py", "a.py"]
