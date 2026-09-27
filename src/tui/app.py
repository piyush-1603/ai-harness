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
            yield Static("↑ ↓ Navigate   Enter Inspect\nr Refresh      ? Help\nq Quit", classes="muted")

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
        workspace: str = ".",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.provider = provider
        self.live = live
        self.workspace = workspace
        self.task_running = False

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
        if hasattr(screen, "refresh_data"):
            await screen.refresh_data()

    async def action_manual_refresh(self) -> None:
        if self.provider:
            self.provider.refresh()
        await self._refresh_active_screen()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        if self.task_running:
            return
            
        prompt = event.value.strip()
        event.input.value = ""
        event.input.disabled = True
        event.input.placeholder = "Task is running..."
        self.task_running = True
        
        self.run_orchestrator(prompt)

    @work(thread=True)
    def run_orchestrator(self, prompt: str) -> None:
        task_id = f"interactive-{uuid.uuid4().hex[:8]}"
        
        task_spec = TaskSpec(
            issue_id=task_id,
            issue_description=prompt,
            workspace_dir=self.workspace,
            test_command=os.environ.get("HARNESS_TEST_COMMAND", None)
        )
        
        memory = MemoryManager(base_dir=".harness")
        memory.initialize_task(task_spec.issue_id, task_spec.issue_description)
        
        # Point UI provider to new memory
        if self.provider:
            self.provider.memory = memory
            self.provider.task_id = task_id
            self.provider.last_refresh_changed = True
            
        # Re-scan for new task to be safe
        try:
            repo_index = RepositoryScanner().scan(self.workspace)
            if self.provider:
                self.provider.repo = repo_index
        except Exception:
            repo_index = None
            
        try:
            ctx_config = ContextConfig()
            builder = ContextBuilder(repository_index=repo_index, config=ctx_config)
            context = ContextAdapter(memory=memory, builder=builder)
            
            tool_engine = ToolEngine(workspace_dir=self.workspace)
            verifier = VerificationEngine()
            model_adapter = ModelAdapter()
            config = OrchestratorConfig(step_limit=30)
            
            orch = Orchestrator(
                context=context,
                tool_engine=tool_engine,
                verifier=verifier,
                model_adapter=model_adapter,
                memory_manager=memory,
                context_builder=builder,
                config=config
            )
            
            orch.run(task_spec)
        finally:
            self.call_from_thread(self._on_task_finished)
            
    def _on_task_finished(self) -> None:
        self.task_running = False
        
        # Only re-enable inputs that are currently mounted
        for inp in self.query(Input):
            inp.disabled = False
            inp.placeholder = "Enter prompt to start a new task..."
            inp.focus()

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
        memory.initialize_task("interactive-standby", "Standby for input")
        
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

    app = HarnessTUI(provider=provider, live=not args.demo, workspace=args.workspace)
    app.run()

if __name__ == "__main__":
    main()
