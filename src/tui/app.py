import argparse
import sys
from typing import Optional

from textual.app import App
from src.tui.screens.overview import OverviewScreen
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from src.tui.screens.repository import RepositoryScreen
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Vertical

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

from src.tui.screens.events import EventsScreen

from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from src.context.scanner import RepositoryScanner
from src.tui.data import TUIDataProvider

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
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.provider = provider
        self.live = live  # False in demo mode; True in real-task mode

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
        """Called every _REFRESH_INTERVAL seconds."""
        if not self.provider:
            return
        success = self.provider.refresh()
        if not success:
            # Keep previous snapshot but update UI for STALE footer
            await self._refresh_active_screen()
        elif getattr(self.provider, "last_refresh_changed", True):
            await self._refresh_active_screen()

    async def _refresh_active_screen(self) -> None:
        screen = self.screen
        if hasattr(screen, "refresh_data"):
            await screen.refresh_data()

    async def action_manual_refresh(self) -> None:
        """r key: same path as poll."""
        if self.provider:
            self.provider.refresh()
        await self._refresh_active_screen()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

def main():
    parser = argparse.ArgumentParser(description="AI Harness TUI")
    parser.add_argument("--task", type=str, help="Load a specific task ID")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode with fake data")
    args = parser.parse_args()

    if not args.task and not args.demo:
        print("No task selected.\nRun:\npython -m src.tui.app --task <task-id>")
        sys.exit(1)

    provider = None
    if args.task:
        try:
            memory = MemoryManager(base_dir=".harness")
            memory.load(args.task)
            engine = ContextDiagnosticsEngine()

            # Build repository index once at bootstrap — read-only, never re-scanned
            repo_index = None
            try:
                scanner = RepositoryScanner()
                repo_index = scanner.scan(".")
            except Exception:
                pass  # Repository index is optional

            provider = TUIDataProvider(
                memory_manager=memory,
                diagnostics_engine=engine,
                repository_index=repo_index,
                task_id=args.task,
            )
        except Exception:
            print(f"Error: Could not load task '{args.task}'")
            sys.exit(1)

    app = HarnessTUI(provider=provider, live=bool(args.task))
    app.run()

if __name__ == "__main__":
    main()
