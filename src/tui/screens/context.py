from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, ListView, ListItem, Label
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget

class ContextScreen(Screen):
    """Static Master-Detail Context Screen."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static(f"{'Context':<85} 8 selected\n", classes="header box")
            yield Static("Filter:\n[#4ea8de]All[/]   Selected   Skipped\n", classes="muted box")
            
            with Horizontal():
                with Vertical(classes="list-pane"):
                    yield ListView(
                        ListItem(Label(f"› {'Current error':<30} [#e63946]{'CRITICAL':>15}[/]     22 tok")),
                        ListItem(Label(f"  {'Failed verification':<30} [#e63946]{'CRITICAL':>15}[/]     41 tok")),
                        ListItem(Label(f"  {'Failed attempt':<30} [#ffb703]{'VERY HIGH':>15}[/]     63 tok")),
                        ListItem(Label(f"  {'src/auth.py symbols':<30} [#52b788]{'HIGH':>15}[/]     81 tok")),
                        ListItem(Label(f"  {'pytest observation':<30} [#52b788]{'HIGH':>15}[/]     94 tok")),
                    )
                    
                with Vertical(classes="detail-pane"):
                    yield Static("Why selected\n", classes="header")
                    yield Static("Failed verification\n", classes="accent")
                    
                    yield Static("Critical because:", classes="muted")
                    yield Static("• verification currently failing", classes="muted")
                    yield Static("• associated with active failure", classes="muted")
                    yield Static("• references touched file src/auth.py\n", classes="muted")
                    
                    yield Static("Decision", classes="header")
                    yield Static("[#52b788]selected_within_budget[/]\n")
                    
                    yield Static("Estimated tokens", classes="header")
                    yield Static("41\n")
                    
        yield NavigationWidget(active_screen="Context")
