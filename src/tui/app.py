from textual.app import App
from src.tui.screens.overview import OverviewScreen
from src.tui.screens.context import ContextScreen
from src.tui.screens.memory import MemoryScreen
from src.tui.screens.repository import RepositoryScreen
from src.tui.screens.events import EventsScreen

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
    
    SCREENS = {
        "overview": OverviewScreen,
        "context": ContextScreen,
        "memory": MemoryScreen,
        "repository": RepositoryScreen,
        "events": EventsScreen,
    }
    
    def on_mount(self) -> None:
        self.push_screen("overview")
        
    def action_help(self) -> None:
        pass # Placeholder

if __name__ == "__main__":
    app = HarnessTUI()
    app.run()
