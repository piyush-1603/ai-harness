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

    def on_mount(self) -> None:
        if self.provider:
            self.run_worker(self.refresh_data())

    async def refresh_data(self) -> None:
        if not self.provider:
            return

        data = self.provider.get_overview_data()

        try:
            self.query_one("#task-status", Static).update(data.status)
            self.query_one("#task-title", Static).update(data.task)

            subtitle = f"Recovering from {data.active_failure}\n" if data.active_failure else f"Phase: {data.phase}\n"
            self.query_one("#task-subtitle", Static).update(subtitle)

            lc = self.query_one(LifecycleWidget)
            lc.active_phase = data.phase
            lc.task_status = data.status
            lc.refresh(layout=True)

            pressure_color = "#e63946" if data.pressure_level in ("HIGH", "CRITICAL") else "#52b788"
            ctx_summary = f"[b {pressure_color}]{data.pressure_level}[/] [#6c757d]pressure · {data.repository_scope} scope · {data.context_tokens / 1000:.1f}k / {data.context_limit / 1000:.1f}k tokens[/]\n"
            self.query_one("#context-summary", Static).update(ctx_summary)

            if data.active_failure:
                fail_summary = f"{data.active_failure:<80}[#e63946]×{data.active_failure_count}[/]\n"
            else:
                fail_summary = "No active failure\n"
            self.query_one("#failure-summary", Static).update(fail_summary)

            if data.relevant_files:
                files_lines = []
                for f in data.relevant_files:
                    color = "#52b788" if f.status == "touched" else "#4ea8de"
                    files_lines.append(f"{f.path:<80}[{color}]{f.status}[/]")
                self.query_one("#relevant-files", Static).update("\n".join(files_lines) + "\n")
            else:
                self.query_one("#relevant-files", Static).update("No relevant files\n")

            if data.recent_activity:
                DESC_MAX = 96 - 8 - 2 - 10 - 1
                act_lines = []
                for act in data.recent_activity:
                    color = "#4ea8de"
                    if "fail" in act.label.lower() or "error" in act.label.lower():
                        color = "#e63946"
                    elif "recover" in act.label.lower():
                        color = "#ffb703"
                    desc = act.description or ""
                    if len(desc) > DESC_MAX:
                        desc = desc[:DESC_MAX - 1] + "…"
                    act_lines.append(f"[#6c757d]{act.timestamp}[/]  [{color}]{act.label:<10}[/] [#6c757d]{desc}[/]")
                self.query_one("#recent-activity", Static).update("\n".join(act_lines) + "\n")
            else:
                self.query_one("#recent-activity", Static).update("No recent activity\n")

            self.query_one(NavigationWidget).refresh(layout=True)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self.compose_demo()
            return

        with Vertical(id="main-content"):
            with Horizontal(classes="title-bar"):
                yield Static("AI HARNESS", classes="title-bar-left")
                yield Static("", id="task-status", classes="title-bar-right")

            yield Static("", id="task-title", classes="task-title")
            yield Static("", id="task-subtitle", classes="muted")

            yield LifecycleWidget()
            yield Static("─" * 96, classes="separator")

            yield Static("Context", classes="header")
            yield Static("", id="context-summary")

            yield Static("Current failure", classes="header")
            yield Static("", id="failure-summary", classes="muted")

            yield Static("Relevant files", classes="header")
            yield Static("", id="relevant-files", classes="muted")

            yield Static("─" * 96, classes="separator")

            yield Static("Recent activity\n", classes="header")
            yield Static("", id="recent-activity", classes="muted")

            yield Static("─" * 96, classes="separator")

        yield NavigationWidget(active_screen="Overview", provider=self.provider)

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
