from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical, Horizontal

from src.tui.widgets.lifecycle import LifecycleWidget
from src.tui.widgets.navigation import NavigationWidget

class OverviewScreen(Screen):
    """The main overview screen."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="main-content"):
            # Header
            with Horizontal(classes="title-bar"):
                yield Static("AI HARNESS", classes="title-bar-left")
                yield Static("RUNNING", classes="title-bar-right")
                
            yield Static("Fix authentication token expiration", classes="task-title")
            yield Static("Recovering from TokenExpiredError\n", classes="muted")
            
            yield LifecycleWidget()
            
            yield Static("─" * 96, classes="separator")
            
            # Context Summary
            yield Static("Context", classes="header")
            yield Static("[b #e63946]HIGH[/] [#6c757d]pressure · BROAD scope · 6.3k / 8k tokens[/]\n")
            
            # Current Failure
            yield Static("Current failure", classes="header")
            yield Static(f"{'TokenExpiredError':<80}[#e63946]×3[/]\n")
            
            # Relevant files
            yield Static("Relevant files", classes="header")
            yield Static(f"{'src/auth.py':<80}[#52b788]touched[/]")
            yield Static(f"{'src/token.py':<80}[#4ea8de]relevant[/]")
            yield Static(f"{'tests/test_auth.py':<80}[#6c757d]related[/]\n")
            
            yield Static("─" * 96, classes="separator")
            
            # Recent Activity
            yield Static("Recent activity\n", classes="header")
            
            yield Static(f"[#6c757d]03:41[/]  [#4ea8de]{'read':<10}[/] [#6c757d]src/auth.py[/]")
            yield Static(f"[#6c757d]03:42[/]  [#4ea8de]{'execute':<10}[/] [#6c757d]pytest tests/test_auth.py[/]")
            yield Static(f"[#6c757d]03:42[/]  [#e63946]{'verify':<10}[/] [#6c757d]failed[/]")
            yield Static(f"[#6c757d]03:42[/]  [#ffb703]{'recover':<10}[/] [#6c757d]context expanded to BROAD[/]\n")
            
            yield Static("─" * 96, classes="separator")
            
        yield NavigationWidget(active_screen="Overview")
