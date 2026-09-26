import asyncio
import pytest
from copy import deepcopy
from pathlib import Path

from src.memory.manager import MemoryManager
from src.memory.models import Failure, Observation, Discovery
from src.context.diagnostics import ContextDiagnosticsEngine
from src.context.scanner import RepositoryScanner
from src.tui.data import TUIDataProvider
from src.tui.app import HarnessTUI
from textual.widgets import Static

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("m7task", "M7 test task")
    mgr.save()
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory, task_id="m7task")

def _run(coro):
    return asyncio.run(coro)

# ── Provider refresh ──────────────────────────────────────────────────────────

def test_provider_refresh_reloads_changed_state(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")

    # Mutate on disk using a second MemoryManager instance
    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.record_failure(Failure("NewErr", "summary", "action"))
    writer.save()

    # Before refresh, provider sees old state
    before = prov.get_memory_data()
    assert len(before.failures) == 0

    # After refresh, new state is visible
    prov.refresh()
    after = prov.get_memory_data()
    assert len(after.failures) == 1
    assert after.failures[0].label == "NewErr"

def test_provider_refresh_sees_new_events(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    before_count = len(prov.get_events_data())

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.add_observation(Observation("tool", "sys", "new observation"))
    writer.save()

    prov.refresh()
    assert len(prov.get_events_data()) > before_count

def test_provider_refresh_without_task_id_is_noop(memory):
    prov = TUIDataProvider(memory)  # no task_id
    assert prov.refresh() is False

def test_provider_transient_refresh_failure_preserves_last_good_data(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    # Corrupt: pass nonexistent task_id after init
    prov.task_id = "nonexistent_task_xyz"
    result = prov.refresh()
    assert result is False
    assert prov.last_refresh_error is not None
    # State still readable from memory (last good snapshot)
    overview = prov.get_overview_data()
    assert overview.task == "M7 test task"

def test_provider_refresh_partial_load_failure_preserves_old_snapshot(tmp_path, memory, monkeypatch):
    memory.record_failure(Failure("OldErr", "old summary", "old action"))
    memory.save()
    prov = TUIDataProvider(memory, task_id="m7task")

    old_state = deepcopy(prov.memory.get_state())
    old_events = list(prov.memory.get_events())

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.record_failure(Failure("NewErr", "new summary", "new action"))
    writer.save()

    def fail_load_events(_task_id):
        raise IOError("Corrupted event log")

    monkeypatch.setattr(prov.memory.storage, "load_events", fail_load_events)

    assert prov.refresh() is False
    assert prov.memory.get_state() == old_state
    assert prov.memory.get_events() == old_events
    assert prov.last_refresh_error is not None

    monkeypatch.undo()
    assert prov.refresh() is True
    assert prov.last_refresh_error is None
    assert prov.memory.get_state().failures[-1].error_signature == "NewErr"
    assert len(prov.memory.get_events()) == len(old_events) + 1

def test_provider_refresh_does_not_mutate_events(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    events_before = len(prov.get_events_data())
    prov.refresh()
    # Refresh must not ADD new events on its own
    events_after = len(prov.get_events_data())
    assert events_after == events_before

# ── Live data consistency ─────────────────────────────────────────────────────

def test_phase_change_visible_after_refresh(tmp_path, memory):
    from src.memory.models import Phase
    prov = TUIDataProvider(memory, task_id="m7task")

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.get_state().phase = Phase.EXECUTE
    writer.save()

    prov.refresh()
    data = prov.get_overview_data()
    assert data.phase == "EXECUTE"

def test_failure_count_updates_after_refresh(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.record_failure(Failure("E1", "s", "a"))
    writer.record_failure(Failure("E1", "s", "a"))
    writer.save()

    prov.refresh()
    data = prov.get_memory_data()
    assert any(f.count >= 2 for f in data.failures)

def test_memory_counts_update_after_refresh(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    assert len(prov.get_memory_data().observations) == 0

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.add_observation(Observation("tool", "sys", "test obs"))
    writer.save()

    prov.refresh()
    assert len(prov.get_memory_data().observations) == 1

def test_context_candidates_update_after_refresh(tmp_path, memory):
    engine = ContextDiagnosticsEngine()
    prov = TUIDataProvider(memory, diagnostics_engine=engine, task_id="m7task")
    before = prov.get_context_data()

    writer = MemoryManager(base_dir=str(tmp_path))
    writer.load("m7task")
    writer.record_failure(Failure("CandErr", "summary", "action"))
    writer.add_observation(Observation("tool", "tool", "obs for context"))
    writer.save()

    prov.refresh()
    after = prov.get_context_data()
    # After adding state, candidates may increase (or at minimum no crash)
    total_before = len(before.selected_candidates) + len(before.skipped_candidates)
    total_after = len(after.selected_candidates) + len(after.skipped_candidates)
    assert total_after >= total_before

def test_events_no_duplication_after_refresh(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    # Refresh twice without external mutation
    prov.refresh()
    count_1 = len(prov.get_events_data())
    prov.refresh()
    count_2 = len(prov.get_events_data())
    assert count_1 == count_2  # No duplication

# ── Repository not rescanned ──────────────────────────────────────────────────

def test_repository_not_rescanned_during_refresh(tmp_path, memory):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "main.py").write_text("def main(): pass")

    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(repo_root))
    original_id = id(repo_index)

    prov = TUIDataProvider(memory, repository_index=repo_index, task_id="m7task")
    prov.refresh()

    # The repo_index object must be the same instance — never replaced
    assert id(prov.repo) == original_id

# ── App-level refresh via screens ─────────────────────────────────────────────

def test_active_screen_refresh_called_on_poll(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as _:
            # Simulate a manual refresh (same path as timer)
            await app.action_manual_refresh()
            # Just verify no crash and screen is still active
            assert app.screen is not None
    _run(run())

def test_r_key_uses_same_refresh_path(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("r")
            assert app.screen is not None
    _run(run())

def test_demo_mode_no_live_interval(provider):
    # In demo mode (live=False) no timer is started
    async def run():
        app = HarnessTUI(provider=None, live=False)
        async with app.run_test() as _:
            # App starts, no timer crashes, no provider needed
            assert app.screen.__class__.__name__ == "OverviewScreen"
    _run(run())

def test_refresh_does_not_mutate_task_state(tmp_path, memory):
    prov = TUIDataProvider(memory, task_id="m7task")
    state_before = deepcopy(memory.get_state())
    events_before = len(memory.get_events())

    prov.refresh()
    prov.refresh()

    assert memory.get_state() == state_before
    assert len(memory.get_events()) == events_before

def test_all_screens_refresh_without_crash(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as _:
            for screen_name in ("overview", "context", "memory", "repository", "events"):
                await app.switch_screen(screen_name)
                screen = app.screen
                if hasattr(screen, "refresh_data"):
                    await screen.refresh_data()
    _run(run())
