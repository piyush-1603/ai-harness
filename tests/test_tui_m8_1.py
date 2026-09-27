import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from src.tui.app import HarnessTUI
from src.tui.data import TUIDataProvider
from src.tui.widgets.lifecycle import LifecycleWidget
from textual.widgets import Static, RichLog
from _pytest.fixtures import FixtureRequest

def _run(coro):
    return asyncio.run(coro)

@pytest.fixture
def mock_provider():
    from unittest.mock import MagicMock
    provider = MagicMock(spec=TUIDataProvider)
    provider.task_id = "demo"
    provider.refresh.return_value = True
    provider.last_refresh_changed = False
    provider.last_refresh_error = None

    # Overview
    overview_data = MagicMock()
    overview_data.status = "RUNNING"
    overview_data.task = "Test Task"
    overview_data.phase = "PLAN"
    overview_data.active_failure = None
    overview_data.pressure_level = "LOW"
    overview_data.repository_scope = "BROAD"
    overview_data.context_tokens = 100
    overview_data.context_limit = 8000
    overview_data.relevant_files = []
    overview_data.recent_activity = []
    provider.get_overview_data.return_value = overview_data

    # Context
    context_data = MagicMock()
    context_data.selected_candidates = []
    context_data.skipped_candidates = []
    provider.get_context_data.return_value = context_data

    # Memory
    memory_data = MagicMock()
    memory_data.failures = []
    memory_data.attempts = []
    memory_data.observations = []
    memory_data.discoveries = []
    memory_data.artifacts = []
    provider.get_memory_data.return_value = memory_data

    # Events
    provider.get_events_data.return_value = []

    # Repository
    repo_data = MagicMock()
    repo_data.files = []
    provider.get_repository_data.return_value = repo_data

    return provider

def test_idle_polling_skips_refresh(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=True)
        async with app.run_test() as pilot:
            # Override refresh_data with mock
            app.screen.refresh_data = AsyncMock()

            # Poll with no changes
            mock_provider.last_refresh_changed = False
            await app._poll_refresh()

            app.screen.refresh_data.assert_not_called()
    _run(run())

def test_changed_polling_calls_refresh(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=True)
        async with app.run_test() as pilot:
            app.screen.refresh_data = AsyncMock()

            # Poll with changes
            mock_provider.last_refresh_changed = True
            await app._poll_refresh()

            app.screen.refresh_data.assert_called_once()
    _run(run())

def test_transient_failure_preserves_ui_but_updates_footer(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=True)
        async with app.run_test() as pilot:
            app.screen.refresh_data = AsyncMock()

            mock_provider.refresh.return_value = False
            mock_provider.last_refresh_error = "Network Error"

            await app._poll_refresh()

            # Should still update UI to show STALE
            app.screen.refresh_data.assert_called_once()
    _run(run())

def test_overview_inplace_update(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=False)
        async with app.run_test() as pilot:
            lc = app.screen.query_one(LifecycleWidget)
            assert lc.active_phase == "PLAN"

            # Change phase without remount
            mock_provider.get_overview_data.return_value.phase = "EXECUTE"
            await app.screen.refresh_data()

            assert lc.active_phase == "EXECUTE"

            # Recompose should not have been called
            # We verify this by checking if the widget is the exact same instance
            lc2 = app.screen.query_one(LifecycleWidget)
            assert lc is lc2
    _run(run())

def test_manual_refresh_updates_ui(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=False)
        async with app.run_test() as pilot:
            app.screen.refresh_data = AsyncMock()
            await pilot.press("r")

            mock_provider.refresh.assert_called()
            app.screen.refresh_data.assert_called_once()
    _run(run())

def test_events_log_no_duplicates(mock_provider):
    async def run():
        app = HarnessTUI(provider=mock_provider, live=False)
        async with app.run_test() as pilot:
            e1 = MagicMock()
            e1.timestamp = "10:00"
            e1.kind = "Failure"
            e1.summary = "msg"
            mock_provider.get_events_data.return_value = [e1]

            await pilot.press("5") # Events
            await pilot.pause(0.1)

            log = app.screen.query_one(RichLog)
            assert "msg" in "".join(str(L) for L in log.lines)
            assert len(log.lines) == 1

            # Same event again, should not append
            await app.screen.refresh_data()
            await pilot.pause(0.1)
            assert len(log.lines) == 1
    _run(run())
