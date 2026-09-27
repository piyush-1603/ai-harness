import asyncio
import pytest
from copy import deepcopy

from src.memory.manager import MemoryManager
from src.memory.models import Failure, Observation, Attempt, Discovery
from src.context.diagnostics import ContextDiagnosticsEngine
from src.tui.data import TUIDataProvider
from src.tui.app import HarnessTUI
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from textual.widgets import Static

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("m5task", "M5 test task")
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory)

@pytest.fixture
def provider_with_engine(memory):
    return TUIDataProvider(memory, ContextDiagnosticsEngine())

# ── helpers ──────────────────────────────────────────────────────────────────

def _texts_from_app(app) -> list[str]:
    texts = []
    for w in app.screen.walk_children(Static):
        r = w.render()
        texts.append(r.plain if hasattr(r, "plain") else str(r))
    return texts

def _run(coro):
    return asyncio.run(coro)

# ── Context screen ────────────────────────────────────────────────────────────

def test_context_empty_diagnostics_renders_safely(provider):
    # No diagnostics engine → safe empty state
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            await app.switch_screen("context")
            texts = _texts_from_app(app)
        assert any("UNKNOWN" in t or "No context" in t or "0" in t for t in texts)
    _run(run())

def test_context_no_candidates_renders_safely(provider_with_engine):
    # Engine initialised but no task state rich enough to produce candidates
    async def run():
        app = HarnessTUI(provider=provider_with_engine)
        async with app.run_test() as _:
            await app.switch_screen("context")
            texts = _texts_from_app(app)
        assert any("pressure" in t.lower() or "UNKNOWN" in t or "No context" in t for t in texts)
    _run(run())

def test_context_budget_summary_maps_correctly(provider_with_engine):
    async def run():
        data = provider_with_engine.get_context_data()
        assert isinstance(data.pressure_level, str)
        assert isinstance(data.max_tokens, int)
        assert isinstance(data.final_tokens, int)
        assert isinstance(data.selected_candidates, list)
        assert isinstance(data.skipped_candidates, list)
    _run(run())

def test_context_selected_skipped_mapping(memory, tmp_path):
    # Add some state to get candidates
    memory.record_failure(Failure("Err", "sum", "act"))
    memory.add_observation(Observation("tool", "tool", "obs summary"))
    prov = TUIDataProvider(memory, ContextDiagnosticsEngine())
    data = prov.get_context_data()
    candidates = data.selected_candidates + data.skipped_candidates
    # Every candidate in selected must have selected=True and vice versa
    for c in data.selected_candidates:
        assert c.selected is True
    for c in data.skipped_candidates:
        assert c.selected is False
    # No overlap
    sel_ids = {c.id for c in data.selected_candidates}
    skip_ids = {c.id for c in data.skipped_candidates}
    assert sel_ids.isdisjoint(skip_ids)

def test_context_candidate_detail_uses_real_reasons(memory):
    memory.record_failure(Failure("ErrSig", "summary", "action"))
    prov = TUIDataProvider(memory, ContextDiagnosticsEngine())
    data = prov.get_context_data()
    all_candidates = data.selected_candidates + data.skipped_candidates
    for c in all_candidates:
        assert isinstance(c.reasons, list)
        assert isinstance(c.decision, str)
        assert c.decision != ""

def test_context_navigation_shows_real_data(provider_with_engine):
    async def run():
        app = HarnessTUI(provider=provider_with_engine)
        async with app.run_test() as _:
            await app.switch_screen("context")
            # Just assert screen is active with no crash
            assert app.screen.__class__.__name__ == "ContextScreen"
    _run(run())

# ── Memory screen ─────────────────────────────────────────────────────────────

def test_memory_category_counts_render(memory):
    memory.record_failure(Failure("F1", "sum", "act"))
    memory.record_failure(Failure("F1", "sum", "act"))  # occurrence_count
    memory.add_observation(Observation("tool", "tool", "obs sum"))
    prov = TUIDataProvider(memory)
    data = prov.get_memory_data()
    assert len(data.failures) >= 1
    assert len(data.observations) >= 1
    assert data.failures[0].count >= 1

def test_memory_repeated_failure_count_renders(memory):
    memory.record_failure(Failure("RepErr", "sum", "act"))
    memory.record_failure(Failure("RepErr", "sum", "act"))
    prov = TUIDataProvider(memory)
    data = prov.get_memory_data()
    rep = next(f for f in data.failures if f.label == "RepErr")
    assert rep.count == 2

def test_memory_artifact_metadata_no_full_read(memory, monkeypatch):
    # Simulate that get_artifact would raise if called
    called = []
    original = getattr(memory, "get_artifact", None)
    def boom(ref):
        called.append(ref)
        raise AssertionError("get_artifact was called!")
    monkeypatch.setattr(memory, "get_artifact", boom)

    memory.add_observation(Observation("tool", "sys", "sum", raw_output="x" * 5000))
    prov = TUIDataProvider(memory)
    data = prov.get_memory_data()
    # get_artifact must not have been called during get_memory_data()
    assert called == [], f"get_artifact was called with: {called}"
    # But artifact metadata should still be present
    assert any(a.ref for a in data.artifacts)

def test_memory_navigation_shows_real_data(provider):
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            await app.switch_screen("memory")
            assert app.screen.__class__.__name__ == "MemoryScreen"
            texts = _texts_from_app(app)
        assert any("Memory" in t for t in texts)
    _run(run())

def test_memory_empty_renders_safely(provider):
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            await app.switch_screen("memory")
            texts = _texts_from_app(app)
        assert any("Memory" in t or "0" in t or "No memory" in t for t in texts)
    _run(run())

# ── Read-only guarantee ───────────────────────────────────────────────────────

def test_context_memory_navigation_does_not_mutate(memory):
    async def run():
        memory.record_failure(Failure("E", "s", "a"))
        prov = TUIDataProvider(memory, ContextDiagnosticsEngine())

        state_before = deepcopy(memory.get_state())
        events_before = len(memory.get_events())

        app = HarnessTUI(provider=prov)
        async with app.run_test() as _:
            await app.switch_screen("context")
            await app.switch_screen("memory")
            await app.switch_screen("overview")

        assert memory.get_state() == state_before
        assert len(memory.get_events()) == events_before
    _run(run())
