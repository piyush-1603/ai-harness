from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, ListView, ListItem, Label
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget

class MemoryScreen(Screen):
    """Static Memory Screen."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static(f"{'Memory':<85} 24 items\n", classes="header box")
            
            yield Static(f"{'Observations':<15} 8")
            yield Static(f"{'Attempts':<15} 5")
            yield Static(f"{'Failures':<15} 2")
            yield Static(f"{'Discoveries':<15} 6")
            yield Static(f"{'Artifacts':<15} 3\n")
            
            with Horizontal():
                with Vertical(classes="list-pane"):
                    yield ListView(
                        ListItem(Label(f"› {'Failure':<15} {'TokenExpiredError':<40} [#e63946]×3[/]")),
                        ListItem(Label(f"  {'Observation':<15} {'pytest authentication failed':<40}")),
                        ListItem(Label(f"  {'Attempt':<15} {'modify token parser':<40} [#e63946]failed[/]")),
                        ListItem(Label(f"  {'Artifact':<15} {'pytest output':<40} 34 KB")),
                    )
                    
                with Vertical(classes="detail-pane"):
                    yield Static("Artifact\n", classes="header")
                    yield Static("artifact_14bc...9af2\n", classes="accent")
                    
                    yield Static("34,000 characters", classes="muted")
                    yield Static("Stored externally", classes="muted")
                    yield Static("[#52b788]Integrity verified[/]\n")
                    
                    yield Static("Preview", classes="header")
                    yield Static("FAILED test_expired_token\nTokenExpiredError...\n", classes="muted")
                    
                    yield Static("[b]Enter[/b]  View full output")
                    
        yield NavigationWidget(active_screen="Memory")
