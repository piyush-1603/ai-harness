from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

class NavigationWidget(Widget):
    """Global navigation hints at the bottom of the screen."""
    
    def __init__(self, active_screen: str = "Overview", **kwargs):
        super().__init__(**kwargs)
        self.active_screen = active_screen
        
    def compose(self) -> ComposeResult:
        screens = ["Overview", "Context", "Memory", "Repository", "Events"]
        parts = []
        for i, name in enumerate(screens, 1):
            if name == self.active_screen:
                parts.append(f"[b #4ea8de]{i} {name}[/]")
            else:
                parts.append(f"{i} {name}")
                
        parts.append("? Help")
        
        yield Static("   ".join(parts))
