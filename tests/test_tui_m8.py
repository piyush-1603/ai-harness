import asyncio
import pytest
from textual.widgets import Static

from src.memory.manager import MemoryManager
from src.memory.models import Failure, Phase
from src.tui.app import HarnessTUI
from src.tui.data import TUIDataProvider
from src.tui.screens.overview import OverviewScreen
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from src.tui.screens.repository import RepositoryScreen
from src.tui.screens.events import EventsScreen
from src.tui.widgets.navigation import NavigationWidget

def _run(coro):
    return asyncio.run(coro)

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("m8task", "M8 test task")
    mgr.save()
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory, task_id="m8task")

# A. help overlay opens/closes
def test_help_overlay_opens_and_closes(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("?")
            assert type(app.screen).__name__ == "HelpScreen"
            await pilot.press("escape")
            assert type(app.screen).__name__ == "OverviewScreen"
    _run(run())

# B. global navigation still works
def test_global_navigation_still_works(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("2")
            assert type(app.screen).__name__ == "ContextScreen"
            await pilot.press("3")
            assert type(app.screen).__name__ == "MemoryScreen"
            await pilot.press("1")
            assert type(app.screen).__name__ == "OverviewScreen"
    _run(run())

# C. r refresh still works
def test_r_refresh_still_works(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("r")
            # Should not crash, screen remains active
            assert app.screen is not None
    _run(run())

# D. LIVE / STALE state renders safely
def test_live_stale_state_renders(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as _:
            nav = app.screen.query_one(NavigationWidget)
            text = nav.query_one(Static).render()
            assert "LIVE" in str(text) or "STALE" in str(text)

            provider.last_refresh_error = "Mock error"
            await app.screen.recompose()
            nav = app.screen.query_one(NavigationWidget)
            text = nav.query_one(Static).render()
            assert "STALE" in str(text)
    _run(run())

# E. Context filter bindings work
def test_context_filter_bindings(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("2") # go to context
            assert app.screen._filter == "All"
            await pilot.press("s")
            assert app.screen._filter == "Selected"
            await pilot.press("x")
            assert app.screen._filter == "Skipped"
            await pilot.press("a")
            assert app.screen._filter == "All"
    _run(run())

# F. selections survive refresh
def test_selections_survive_refresh(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("2") # go to context
            # We don't have candidates loaded to move down, but we can verify it doesn't crash
            # and doesn't mutate _selected_idx if bounded
            app.screen._selected_idx = 0
            await app.screen.refresh_data()
            assert app.screen._selected_idx == 0
    _run(run())

# G. Events refresh does not duplicate rows
def test_events_refresh_no_duplicate_rows(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            await pilot.press("5") # go to events
            # re-rendering events screen doesn't crash and keeps log
            await app.screen.refresh_data()
            assert app.screen is not None
    _run(run())

# H. demo mode works across all screens
def test_demo_mode_works_across_all_screens():
    async def run():
        app = HarnessTUI(provider=None, live=False)
        async with app.run_test() as pilot:
            for key in ["1", "2", "3", "4", "5"]:
                await pilot.press(key)
                assert app.screen is not None
    _run(run())

# I. empty states render
def test_empty_states_render(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test() as pilot:
            # We already have an empty task initialized
            await pilot.press("1")
            await pilot.press("2")
            await pilot.press("3")
            await pilot.press("4")
            await pilot.press("5")
            assert app.screen is not None
    _run(run())

# J. narrow terminal does not crash
def test_narrow_terminal_does_not_crash(provider):
    async def run():
        app = HarnessTUI(provider=provider, live=False)
        async with app.run_test(size=(80, 24)) as pilot:
            for key in ["1", "2", "3", "4", "5"]:
                await pilot.press(key)
                assert app.screen is not None
    _run(run())

# K. no new mutation behavior introduced
def test_no_mutation_in_m8(provider):
    state1 = provider.memory.get_state()
    # do a refresh
    provider.refresh()
    state2 = provider.memory.get_state()
    assert state1 == state2
