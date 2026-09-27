from typing import Optional

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, RichLog
from textual.containers import Vertical

from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider

_KIND_COLOR: dict[str, str] = {
    "phase_changed":       "#4ea8de",
    "task_initialized":    "#4ea8de",
    "failure_recorded":    "#e63946",
    "failure_resolved":    "#52b788",
    "verify_failed":       "#e63946",
    "verify_passed":       "#52b788",
    "attempt_recorded":    "#ffb703",
    "observation_added":   "#6c757d",
    "discovery_added":     "#52b788",
    "recovery_started":    "#ffb703",
    "context_expanded":    "#ffb703",
    "tool_called":         "#6c757d",
}

_DESC_MAX = 96 - 8 - 2 - 20 - 1  # 65 chars for description

def _kind_color(kind: str) -> str:
    for key, col in _KIND_COLOR.items():
        if key in kind.lower():
            return col
    return "#6c757d"

class EventsScreen(Screen):
    """Events stream screen wired to TUIDataProvider."""

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    async def refresh_data(self) -> None:
        await self.recompose()

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        events = self.provider.get_events_data(limit=100)

        with Vertical(id="main-content"):
            yield Static(f"Events  [#6c757d]{len(events)} entries[/]\n", classes="header")

            if not events:
                yield Static("No events recorded\n", classes="muted")
                yield NavigationWidget(
            active_screen="Events",
            provider=self.provider,
            interactive=getattr(self.app, "interactive", False),
            busy=getattr(self.app, "busy", False),
        )
                return

            log = RichLog(highlight=False, markup=True)
            for e in events:
                col = _kind_color(e.kind)
                kind_str = e.kind[:18]
                desc = e.summary or ""
                if len(desc) > _DESC_MAX:
                    desc = desc[:_DESC_MAX - 1] + "…"
                log.write(
                    f"[#6c757d]{e.timestamp}[/]  [{col}]{kind_str:<20}[/] [#6c757d]{desc}[/]"
                )
            yield log

        yield NavigationWidget(
            active_screen="Events",
            provider=self.provider,
            interactive=getattr(self.app, "interactive", False),
            busy=getattr(self.app, "busy", False),
        )

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Events  [#6c757d](demo)[/]\n", classes="header")
            log = RichLog(highlight=False, markup=True)
            log.write("[#6c757d]03:40:01[/]  [#4ea8de]task_initialized    [/] [#6c757d]initialized[/]")
            log.write("[#6c757d]03:40:03[/]  [#4ea8de]phase_changed       [/] [#6c757d]PLAN[/]")
            log.write("[#6c757d]03:40:08[/]  [#6c757d]tool_called         [/] [#6c757d]read_file src/auth.py[/]")
            log.write("[#6c757d]03:40:12[/]  [#ffb703]attempt_recorded    [/] [#6c757d]failed[/]")
            log.write("[#6c757d]03:40:16[/]  [#e63946]verify_failed       [/] [#6c757d]FAILED[/]")
            log.write("[#6c757d]03:40:16[/]  [#e63946]failure_recorded    [/] [#6c757d]TokenExpiredError ×3[/]")
            log.write("[#6c757d]03:40:16[/]  [#4ea8de]phase_changed       [/] [#6c757d]RECOVER[/]")
            yield log
        yield NavigationWidget(
            active_screen="Events",
            provider=self.provider,
            interactive=getattr(self.app, "interactive", False),
            busy=getattr(self.app, "busy", False),
        )
