"""M9-Lite regression tests: interactive prompt rendering and state transitions."""
import asyncio
import pytest

from src.tui.app import HarnessTUI
from src.tui.data import TUIDataProvider
from src.tui.widgets.navigation import NavigationWidget
from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from textual.widgets import Static, Input


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def standby_provider(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("interactive-standby", "Ready")
    engine = ContextDiagnosticsEngine()
    return TUIDataProvider(
        memory_manager=mgr,
        diagnostics_engine=engine,
        task_id="interactive-standby",
    )


def _get_input(app) -> Input:
    return app.screen.query_one("#prompt-input", Input)


def _nav_text(app) -> str:
    nav = app.screen.query_one(NavigationWidget)
    return str(nav.query_one(Static).render())


async def _wait_for_overview(app):
    """Wait until OverviewScreen is the active screen."""
    for _ in range(20):
        if type(app.screen).__name__ == "OverviewScreen":
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"Expected OverviewScreen, got {type(app.screen).__name__}")


# ---------------------------------------------------------------------------
# 1. Interactive standby renders #prompt-input
# ---------------------------------------------------------------------------
def test_standby_renders_prompt_input(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=True)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            assert _get_input(app) is not None

    _run(run())


# ---------------------------------------------------------------------------
# 2. Standby input is enabled
# ---------------------------------------------------------------------------
def test_standby_input_is_enabled(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=True)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            assert not _get_input(app).disabled

    _run(run())


# ---------------------------------------------------------------------------
# 3. Standby status shows READY (not RUNNING)
# ---------------------------------------------------------------------------
def test_standby_status_shows_ready(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=True)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            rendered = _nav_text(app)
            assert "READY" in rendered
            assert "RUNNING" not in rendered

    _run(run())


# ---------------------------------------------------------------------------
# 4. _set_busy(True) disables input and status shows RUNNING
# ---------------------------------------------------------------------------
def test_set_busy_disables_input_and_shows_running(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=True)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            app._set_busy(True)
            await pilot.pause(0.1)
            assert _get_input(app).disabled
            assert "RUNNING" in _nav_text(app)

    _run(run())


# ---------------------------------------------------------------------------
# 5. _set_busy(False) re-enables input after task completion
# ---------------------------------------------------------------------------
def test_set_busy_false_reenables_input(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=True)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            app._set_busy(True)
            await pilot.pause(0.05)
            app._set_busy(False)
            await pilot.pause(0.1)
            assert not _get_input(app).disabled

    _run(run())


# ---------------------------------------------------------------------------
# 6. Non-interactive mode does NOT render #prompt-input
# ---------------------------------------------------------------------------
def test_non_interactive_no_prompt_input(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=False)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            assert len(app.screen.query("#prompt-input")) == 0

    _run(run())


# ---------------------------------------------------------------------------
# 7. Keyboard navigation works in non-interactive mode
#    (Input absent means 1/2/3 keys reach app bindings directly)
# ---------------------------------------------------------------------------
def test_keyboard_navigation_non_interactive(standby_provider):
    async def run():
        app = HarnessTUI(provider=standby_provider, live=False, interactive=False)
        async with app.run_test() as pilot:
            await _wait_for_overview(app)
            await pilot.press("2")
            assert type(app.screen).__name__ == "ContextScreen"
            await pilot.press("3")
            assert type(app.screen).__name__ == "MemoryScreen"
            await pilot.press("1")
            assert type(app.screen).__name__ == "OverviewScreen"

    _run(run())
