import argparse
import sys
from typing import Optional

from textual.app import App
from src.tui.screens.overview import OverviewScreen
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from src.tui.screens.repository import RepositoryScreen
from src.tui.screens.events import EventsScreen

from src.memory.manager import MemoryManager
from src.context.diagnostics import ContextDiagnosticsEngine
from src.tui.data import TUIDataProvider

class HarnessTUI(App):
    """AI Harness Terminal UI."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("1", "switch_screen('overview')", "Overview"),
        ("2", "switch_screen('context')", "Context"),
        ("3", "switch_screen('memory')", "Memory"),
        ("4", "switch_screen('repository')", "Repository"),
        ("5", "switch_screen('events')", "Events"),
        ("q", "quit", "Quit"),
        ("?", "help", "Help"),
    ]

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def on_mount(self) -> None:
        self.install_screen(OverviewScreen(self.provider), "overview")
        self.install_screen(ContextScreen(), "context")
        self.install_screen(MemoryScreen(), "memory")
        self.install_screen(RepositoryScreen(), "repository")
        self.install_screen(EventsScreen(), "events")

        self.push_screen("overview")

    def action_help(self) -> None:
        pass # Placeholder

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
            # Load actual task state
            memory = MemoryManager(base_dir=".harness")
            memory.load(args.task)
            engine = ContextDiagnosticsEngine()
            provider = TUIDataProvider(memory_manager=memory, diagnostics_engine=engine)
        except Exception:
            print(f"Error: Could not load task '{args.task}'")
            sys.exit(1)

    app = HarnessTUI(provider=provider)
    app.run()

if __name__ == "__main__":
    main()
