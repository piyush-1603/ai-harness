from typing import Optional
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

class NavigationWidget(Widget):
    """Global navigation hints at the bottom of the screen."""

    def __init__(self, active_screen: str = "Overview", provider: Optional["TUIDataProvider"] = None, **kwargs):
        super().__init__(**kwargs)
        self.active_screen = active_screen
        self.provider = provider

    def compose(self) -> ComposeResult:
        screens = ["Overview", "Context", "Memory", "Repository", "Events"]
        parts = []
        for i, name in enumerate(screens, 1):
            if name == self.active_screen:
                parts.append(f"[b #4ea8de]{i} {name}[/]")
            else:
                parts.append(f"{i} {name}")

        nav_line = "   ".join(parts)

        status_line = ""
        if self.provider:
            try:
                data = self.provider.get_overview_data()
                refresh_health = "[b #e63946]● STALE[/]" if self.provider.last_refresh_error else "[b #52b788]● LIVE[/]"

                phase = data.phase
                pressure = data.pressure_level
                scope = data.repository_scope
                ctx_tokens = data.context_tokens
                ctx_limit = data.context_limit

                status_parts = [
                    f"{phase}",
                    f"{pressure}",
                    f"{scope}",
                    f"{ctx_tokens} / {ctx_limit} ctx",
                    refresh_health,
                    "r refresh",
                    "? help",
                    "q quit"
                ]
                status_line = " · ".join(status_parts)
            except Exception:
                status_line = "RECOVER · HIGH · BROAD · 0 / 0 ctx · [b #e63946]● STALE[/] · r refresh · ? help · q quit"
        else:
            # Demo mode
            status_line = "RECOVER · HIGH · BROAD · 6300 / 8000 ctx · [b #52b788]● LIVE[/] · r refresh · ? help · q quit"

        yield Static(f"{nav_line}\n{status_line}")
        
        from textual.widgets import Input
        # Only enable input if we are in interactive live mode
        if getattr(self.app, "live", False) and self.provider and getattr(self.provider, "task_id", None) != "demo":
            yield Input(
                id="prompt-input",
                placeholder="Enter prompt to start a new task...",
                classes="prompt-input"
            )
