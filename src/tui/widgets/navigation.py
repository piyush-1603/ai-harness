from typing import Optional
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input


class NavigationWidget(Widget):
    """Global navigation hints and optional prompt input at the bottom of the screen."""

    def __init__(
        self,
        active_screen: str = "Overview",
        provider: Optional["TUIDataProvider"] = None,
        interactive: bool = False,
        busy: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.active_screen = active_screen
        self.provider = provider
        self.interactive = interactive
        self.busy = busy

    def _build_status_line(self) -> str:
        if self.provider:
            try:
                data = self.provider.get_overview_data()
                refresh_health = "[b #e63946]● STALE[/]" if self.provider.last_refresh_error else "[b #52b788]● LIVE[/]"

                pressure = data.pressure_level
                scope = data.repository_scope
                ctx_tokens = data.context_tokens
                ctx_limit = data.context_limit

                if self.interactive and self.busy:
                    run_status = "[b #ffb703]RUNNING[/]"
                elif self.interactive:
                    run_status = "[b #52b788]READY[/]"
                else:
                    run_status = data.phase

                return " · ".join([
                    run_status, pressure, scope,
                    f"{ctx_tokens} / {ctx_limit} ctx",
                    refresh_health, "r refresh", "? help", "q quit",
                ])
            except Exception:
                return "RECOVER · HIGH · BROAD · 0 / 0 ctx · [b #e63946]● STALE[/] · r refresh · ? help · q quit"
        return "RECOVER · HIGH · BROAD · 6300 / 8000 ctx · [b #52b788]● LIVE[/] · r refresh · ? help · q quit"

    def update_status(self) -> None:
        """Update the status Static in-place — no recompose."""
        try:
            s = self.query_one(Static)
            # content is the markup string passed to Static; first line is the nav line
            nav_line = str(s.content).split("\n")[0]
            s.update(f"{nav_line}\n{self._build_status_line()}")
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        screens = ["Overview", "Context", "Memory", "Repository", "Events"]
        parts = []
        for i, name in enumerate(screens, 1):
            if name == self.active_screen:
                parts.append(f"[b #4ea8de]{i} {name}[/]")
            else:
                parts.append(f"{i} {name}")

        nav_line = "   ".join(parts)
        yield Static(f"{nav_line}\n{self._build_status_line()}")

        if self.interactive:
            placeholder = "Task is running..." if self.busy else "› Enter a coding task..."
            yield Input(
                id="prompt-input",
                placeholder=placeholder,
                disabled=self.busy,
                classes="prompt-input",
            )
