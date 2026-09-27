import pytest
import sys
from unittest.mock import patch
from io import StringIO
from copy import deepcopy

from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from src.tui.app import HarnessTUI, main
from src.tui.data import TUIDataProvider
from src.memory.models import Failure, Observation, Attempt, Discovery
from textual.widgets import Static

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("t1", "test task")
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory)

def test_real_task_loads_into_overview(provider):
    import asyncio
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as pilot:
            screen = app.screen
            texts = []
            for w in screen.query(Static):
                r = w.render()
                if hasattr(r, "plain"):
                    texts.append(r.plain)
                else:
                    texts.append(str(r))

            assert any("test task" in str(t) for t in texts)
            assert not any("Fix authentication token expiration" in str(t) for t in texts)
            assert not any("DEMO" in str(t) for t in texts)
    asyncio.run(run())

def test_phase_maps_correctly_to_lifecycle(provider, memory):
    import asyncio
    async def run():
        memory.get_state().phase = "EXECUTE"
        app = HarnessTUI(provider=provider)
        async with app.run_test() as pilot:
            from src.tui.widgets.lifecycle import LifecycleWidget
            lc = app.screen.query_one(LifecycleWidget)
            assert lc.active_phase == "EXECUTE"
    asyncio.run(run())

def test_active_failure_renders_correctly(provider, memory):
    import asyncio
    async def run():
        memory.record_failure(Failure("MyError", "Summary", "Action"))
        memory.record_failure(Failure("MyError", "Summary", "Action"))

        app = HarnessTUI(provider=provider)
        async with app.run_test() as pilot:
            texts = []
            for w in app.screen.query(Static):
                r = w.render()
                if hasattr(r, "plain"):
                    texts.append(r.plain)
                else:
                    texts.append(str(r))

            assert any("MyError" in str(t) for t in texts)
            assert any("×2" in str(t) for t in texts)
    asyncio.run(run())

def test_empty_failure_renders_safely(provider, memory):
    import asyncio
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as pilot:
            texts = []
            for w in app.screen.query(Static):
                r = w.render()
                if hasattr(r, "plain"):
                    texts.append(r.plain)
                else:
                    texts.append(str(r))

            assert any("No active failure" in str(t) for t in texts)
    asyncio.run(run())

def test_invalid_task_exits_cleanly(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py", "--task", "invalid_missing"])

    # Expect SystemExi
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 1

def test_no_task_argument_enters_standby(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py"])

    with patch.object(HarnessTUI, "run") as mock_run:
        main()
        mock_run.assert_called_once()

def test_app_bootstrap_does_not_mutate_state(tmp_path):
    import asyncio
    async def run():
        mgr = MemoryManager(base_dir=str(tmp_path))
        mgr.initialize_task("mut_task", "Mut task")
        mgr.record_failure(Failure("Err", "Sum", "Act"))

        state_before = deepcopy(mgr.get_state())
        events_before = len(mgr.get_events())

        engine = ContextDiagnosticsEngine()
        prov = TUIDataProvider(mgr, engine)

        app = HarnessTUI(provider=prov)
        async with app.run_test() as pilot:
            pass

        assert mgr.get_state() == state_before
        assert len(mgr.get_events()) == events_before
    asyncio.run(run())
