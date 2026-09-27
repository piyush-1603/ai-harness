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


@pytest.mark.parametrize(("width", "height"), [(80, 24), (120, 40)])
def test_visible_prompt_focus_navigation_and_refresh(standby_provider, width, height):
    async def run():
        app = HarnessTUI(provider=standby_provider, interactive=True)
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            inp = _get_input(app)
            assert inp.content_region.height >= 1
            assert inp.region.bottom <= height
            await pilot.press("q", "2", "question_mark")
            assert inp.value == "q2?"
            await pilot.press("escape", "2")
            assert type(app.screen).__name__ == "ContextScreen"
            for key in ("3", "4", "5", "1"):
                await pilot.press("escape", key)
                await pilot.pause()
                assert _get_input(app).content_region.height >= 1
                assert _get_input(app).region.bottom <= height
            await pilot.press("escape", "5")
            _get_input(app).focus()
            await pilot.press("d", "r", "a", "f", "t")
            await app.action_manual_refresh()
            await pilot.pause()
            assert _get_input(app).value == "draft"
            assert app.focused is _get_input(app)
            app._set_busy(True)
            await pilot.press("1")
            assert _get_input(app).disabled
            await app._on_task_finished()
            assert not _get_input(app).disabled
            await pilot.press("escape", "5")
            assert not _get_input(app).disabled

    _run(run())


def test_worker_repeated_prompts_without_credentials(tmp_path):
    """Exercise the real worker/orchestrator failure path without API calls or patches."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    workspace = tmp_path / "external"
    workspace.mkdir()
    (workspace / "external_module.py").write_text("value = 1\n")
    env = dict(os.environ, AI_API_KEY="", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-c", "from tests.test_tui_m9 import _check_real_worker; "
         "import asyncio, sys; asyncio.run(_check_real_worker(sys.argv[1]))", str(workspace)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def _check_real_worker(workspace):
    from src.context.scanner import RepositoryScanner

    memory = MemoryManager(base_dir=".harness")
    memory.initialize_task("interactive-standby", "Ready")
    provider = TUIDataProvider(memory, repository_index=RepositoryScanner().scan(workspace))
    app = HarnessTUI(provider=provider, interactive=True, live=True, workspace=workspace)
    async with app.run_test() as pilot:
        await pilot.pause()
        task_ids = []
        for prompt in ("Inspect external module", "Inspect it again"):
            inp = _get_input(app)
            inp.focus()
            await pilot.pause()
            await pilot.press(*prompt, "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.busy
            assert not _get_input(app).disabled
            assert app.focused is _get_input(app)
            assert provider.memory.get_state().task == prompt
            assert any("AI_API_KEY" in e.summary for e in provider.memory.get_events())
            task_ids.append(provider.task_id)
        assert len(set(task_ids)) == 2
        assert "external_module.py" in provider.repo.discovered_files
        await pilot.press("escape", "4")
        assert type(app.screen).__name__ == "RepositoryScreen"
        assert "external_module.py" in [f.path for f in provider.get_repository_data().files]
        assert "src/tui/app.py" not in provider.repo.discovered_files
