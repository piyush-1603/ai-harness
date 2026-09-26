from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, RichLog
from textual.containers import Vertical

from src.tui.widgets.navigation import NavigationWidget

class EventsScreen(Screen):
    """Static Events Stream Screen."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Events\n", classes="header box")
            
            log = RichLog(highlight=True, markup=True)
            log.write(f"[#6c757d]03:40:01[/]  [#4ea8de]{'task':<10}[/]        [#6c757d]initialized[/]")
            log.write(f"[#6c757d]03:40:03[/]  [#4ea8de]{'phase':<10}[/]       [#6c757d]PLAN[/]")
            log.write(f"[#6c757d]03:40:08[/]  [#4ea8de]{'tool':<10}[/]        [#6c757d]read_file src/auth.py[/]")
            log.write(f"[#6c757d]03:40:12[/]  [#ffb703]{'attempt':<10}[/]     [#6c757d]failed[/]")
            log.write(f"[#6c757d]03:40:16[/]  [#e63946]{'verify':<10}[/]      [#6c757d]FAILED[/]")
            log.write(f"[#6c757d]03:40:16[/]  [#e63946]{'failure':<10}[/]     [#6c757d]TokenExpiredError ×3[/]")
            log.write(f"[#6c757d]03:40:16[/]  [#4ea8de]{'phase':<10}[/]       [#6c757d]RECOVER[/]")
            
            yield log
            
        yield NavigationWidget(active_screen="Events")
