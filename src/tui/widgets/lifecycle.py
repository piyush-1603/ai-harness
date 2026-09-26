from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

class LifecycleWidget(Widget):
    """A minimal widget displaying the lifecycle phase."""
    
    def compose(self) -> ComposeResult:
        # PLAN        EXECUTE        VERIFY        RECOVER
        #                                            ●
        yield Static("○ PLAN    ○ EXECUTE    ○ VERIFY    [b #4ea8de]● RECOVER[/]")
