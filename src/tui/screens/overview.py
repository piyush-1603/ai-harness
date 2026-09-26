from typing import Optional

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical, Horizontal

from src.tui.widgets.lifecycle import LifecycleWidget
from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider

class OverviewScreen(Screen):
    """The main overview screen."""

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self.compose_demo()
            return

        data = self.provider.get_overview_data()

        with Vertical(id="main-content"):
            # Header
            with Horizontal(classes="title-bar"):
                yield Static("AI HARNESS", classes="title-bar-left")
                yield Static(data.status, classes="title-bar-right")

            yield Static(data.task, classes="task-title")

            if data.active_failure:
                yield Static(f"Recovering from {data.active_failure}\n", classes="muted")
            else:
                yield Static(f"Phase: {data.phase}\n", classes="muted")

            lc = LifecycleWidget()
            lc.active_phase = data.phase
            yield lc

            yield Static("─" * 96, classes="separator")

            # Context Summary
            yield Static("Context", classes="header")
            pressure_color = "#e63946" if data.pressure_level in ("HIGH", "CRITICAL") else "#52b788"

            # Use appropriate context summary presentation
            yield Static(f"[b {pressure_color}]{data.pressure_level}[/] [#6c757d]pressure · {data.repository_scope} scope · {data.context_tokens / 1000:.1f}k / {data.context_limit / 1000:.1f}k tokens[/]\n")

            # Current Failure
            yield Static("Current failure", classes="header")
            if data.active_failure:
                yield Static(f"{data.active_failure:<80}[#e63946]×{data.active_failure_count}[/]\n")
            else:
                yield Static("No active failure\n", classes="muted")

            # Relevant files
            yield Static("Relevant files", classes="header")
            if data.relevant_files:
                for f in data.relevant_files:
                    color = "#52b788" if f.status == "touched" else "#4ea8de"
                    yield Static(f"{f.path:<80}[{color}]{f.status}[/]")
                yield Static("\n")
            else:
                yield Static("No relevant files\n", classes="muted")

            yield Static("─" * 96, classes="separator")

            # Recent Activity
            yield Static("Recent activity\n", classes="header")
            if data.recent_activity:
                # timestamp(8) + 2 spaces + label(10) + 1 space = 21 chars overhead
                # plus 2 spaces for Rich markup spacing
                DESC_MAX = 96 - 8 - 2 - 10 - 1  # = 75 chars
                for act in data.recent_activity:
                    color = "#4ea8de"
                    if "fail" in act.label.lower() or "error" in act.label.lower():
                        color = "#e63946"
                    elif "recover" in act.label.lower():
                        color = "#ffb703"

                    desc = act.description or ""
                    if len(desc) > DESC_MAX:
                        desc = desc[:DESC_MAX - 1] + "…"

                    yield Static(f"[#6c757d]{act.timestamp}[/]  [{color}]{act.label:<10}[/] [#6c757d]{desc}[/]")
                yield Static("\n")
            else:
                yield Static("No recent activity\n", classes="muted")

            yield Static("─" * 96, classes="separator")

        yield NavigationWidget(active_screen="Overview")

    def compose_demo(self) -> ComposeResult:
        """Original fake data compose logic for --demo."""
        with Vertical(id="main-content"):
            # Header
            with Horizontal(classes="title-bar"):
                yield Static("AI HARNESS (DEMO)", classes="title-bar-left")
                yield Static("RUNNING", classes="title-bar-right")

            yield Static("Fix authentication token expiration", classes="task-title")
            yield Static("Recovering from TokenExpiredError\n", classes="muted")

            lc = LifecycleWidget()
            lc.active_phase = "RECOVER"
            yield lc

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
