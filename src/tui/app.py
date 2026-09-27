import argparse
import sys
import os
import uuid
from typing import Optional

from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Vertical
from textual import work
from textual.worker import Worker, WorkerState

from src.tui.screens.overview import OverviewScreen
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from src.tui.screens.repository import RepositoryScreen
from src.tui.screens.events import EventsScreen

from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from src.context.scanner import RepositoryScanner
from src.tui.data import TUIDataProvider

from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.tools.registry import ToolEngine
from src.verification.verifier import VerificationEngine
from src.orchestrator.model_adapter import ModelAdapter
from src.common.types import TaskSpec
from src.main import ContextAdapter

class HelpScreen(ModalScreen):
    """A small help overlay/modal."""

    BINDINGS = [
        ("escape", "dismiss", "Dismiss"),
        ("?", "dismiss", "Dismiss"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog", classes="help-dialog"):
            yield Static("Navigation", classes="header")
            yield Static("1 Overview   2 Context   3 Memory\n4 Repository 5 Events", classes="muted")

            yield Static("\nControls", classes="header")
            yield Static("↑ ↓ Navigate   Enter Inspect\nr Refresh      ? Help\nq Quit\nTab Focus prompt   Esc Leave prompt", classes="muted")

            yield Static("\nContext", classes="header")
            yield Static("a All   s Selected   x Skipped", classes="muted")

    def action_dismiss(self) -> None:
        self.app.pop_screen()

_REFRESH_INTERVAL = 0.75  # seconds

class HarnessTUI(App):
    """AI Harness Terminal UI."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("1", "switch_screen('overview')", "Overview"),
        ("2", "switch_screen('context')", "Context"),
        ("3", "switch_screen('memory')", "Memory"),
        ("4", "switch_screen('repository')", "Repository"),
        ("5", "switch_screen('events')", "Events"),
        ("r", "manual_refresh", "Refresh"),
        ("q", "quit", "Quit"),
        ("?", "help", "Help"),
    ]

    def __init__(
        self,
        provider: Optional[TUIDataProvider] = None,
        live: bool = False,
        interactive: bool = False,
        workspace: str = ".",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.provider = provider
        self.live = live
        self.interactive = interactive
        self.workspace = workspace
        self.busy = False  # True only while orchestrator is executing

    def on_mount(self) -> None:
        self.install_screen(OverviewScreen(self.provider), "overview")
        self.install_screen(ContextScreen(self.provider), "context")
        self.install_screen(MemoryScreen(self.provider), "memory")
        self.install_screen(RepositoryScreen(self.provider), "repository")
        self.install_screen(EventsScreen(self.provider), "events")

        self.push_screen("overview")

        if self.live and self.provider:
            self.set_interval(_REFRESH_INTERVAL, self._poll_refresh)

    async def _poll_refresh(self) -> None:
        if not self.provider:
            return
        success = self.provider.refresh()
        if not success:
            await self._refresh_active_screen()
        elif getattr(self.provider, "last_refresh_changed", True):
            await self._refresh_active_screen()

    async def _refresh_active_screen(self) -> None:
        screen = self.screen
        inputs = list(screen.query("#prompt-input"))
        value = inputs[0].value if inputs else ""
        focused = bool(inputs and self.focused is inputs[0])
        if hasattr(screen, "refresh_data"):
            await screen.refresh_data()
        for inp in screen.query("#prompt-input"):
            inp.value = value
            if focused:
                inp.focus()
        self._update_nav_status()

    async def action_manual_refresh(self) -> None:
        if self.provider:
            self.provider.refresh()
        await self._refresh_active_screen()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def on_key(self, event) -> None:
        if event.key == "escape" and isinstance(self.focused, Input):
            self.set_focus(None)
            event.stop()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self.interactive or not event.value.strip() or self.busy:
            return

        prompt = event.value.strip()
        event.input.value = ""
        self._set_busy(True)
        self.run_orchestrator(prompt)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        placeholder = "Task is running..." if busy else "› Enter a coding task..."
        for screen in (self.get_screen(name) for name in ("overview", "context", "memory", "repository", "events")):
            for inp in screen.query("#prompt-input"):
                inp.disabled = busy
                inp.placeholder = placeholder
        self._update_nav_status()

    @work(thread=True, exit_on_error=False)
    def run_orchestrator(self, prompt: str) -> None:
        try:
            task_id = f"interactive-{uuid.uuid4().hex[:8]}"
            task_spec = TaskSpec(
                issue_id=task_id,
                issue_description=prompt,
                workspace_dir=self.workspace,
                test_command=os.environ.get("HARNESS_TEST_COMMAND"),
            )
            memory = MemoryManager(base_dir=".harness")
            memory.initialize_task(task_id, prompt)
            repo_index = RepositoryScanner().scan(self.workspace)
            self.call_from_thread(self._show_task, memory, task_id, repo_index)

            builder = ContextBuilder(repository_index=repo_index, config=ContextConfig())
            orch = Orchestrator(
                context=ContextAdapter(memory=memory, builder=builder),
                tool_engine=ToolEngine(workspace_dir=self.workspace),
                verifier=VerificationEngine(),
                model_adapter=ModelAdapter(),
                memory_manager=memory,
                context_builder=builder,
                config=OrchestratorConfig(step_limit=30),
            )
            orch.run(task_spec)
        finally:
            self.call_from_thread(self._on_task_finished)

    async def _show_task(self, memory, task_id, repo_index) -> None:
        if self.provider:
            # Keep the UI reader separate from the worker's mutable task state.
            reader = MemoryManager(storage=memory.storage, artifact_store=memory.artifact_store)
            reader.load(task_id)
            self.provider.memory = reader
            self.provider.task_id = task_id
            self.provider.repo = repo_index
            self.provider.refresh()
        await self._refresh_active_screen()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "run_orchestrator" and event.state == WorkerState.ERROR:
            self.notify(str(event.worker.error), title="Task failed", severity="error")

    async def _on_task_finished(self) -> None:
        self._set_busy(False)
        if self.provider:
            self.provider.refresh()
        await self._refresh_active_screen()
        for inp in self.screen.query("#prompt-input"):
            inp.focus()

    def _update_nav_status(self) -> None:
        """Push busy state into NavigationWidget so status line updates in-place."""
        for screen in (self.get_screen(name) for name in ("overview", "context", "memory", "repository", "events")):
            for nav in screen.query("NavigationWidget"):
                nav.busy = self.busy
                nav.update_status()

def main():
    parser = argparse.ArgumentParser(description="AI Harness TUI")
    parser.add_argument("--task", type=str, help="Load a specific task ID")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode with fake data")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace directory for interactive mode")
    args = parser.parse_args()

    provider = None
    if args.task:
        try:
            memory = MemoryManager(base_dir=".harness")
            memory.load(args.task)
            engine = ContextDiagnosticsEngine()

            repo_index = None
            try:
                scanner = RepositoryScanner()
                repo_index = scanner.scan(args.workspace)
            except Exception:
                pass

            provider = TUIDataProvider(
                memory_manager=memory,
                diagnostics_engine=engine,
                repository_index=repo_index,
                task_id=args.task,
            )
        except Exception:
            print(f"Error: Could not load task '{args.task}'")
            sys.exit(1)
    elif not args.demo:
        # M9-Lite interactive mode standby
        memory = MemoryManager(base_dir=".harness")
        memory.initialize_task("interactive-standby", "Ready")

        repo_index = None
        try:
            scanner = RepositoryScanner()
            repo_index = scanner.scan(args.workspace)
        except Exception:
            pass

        engine = ContextDiagnosticsEngine()
        provider = TUIDataProvider(
            memory_manager=memory,
            diagnostics_engine=engine,
            repository_index=repo_index,
            task_id="interactive-standby"
        )

    interactive = not args.demo and not args.task
    app = HarnessTUI(
        provider=provider,
        live=not args.demo,
        interactive=interactive,
        workspace=args.workspace,
    )
    app.run()

if __name__ == "__main__":
    main()
