from typing import Optional

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider
from src.tui.models import ContextCandidateData

_PRIORITY_COLOR = {
    "CRITICAL": "#e63946",
    "VERY HIGH": "#ffb703",
    "HIGH": "#52b788",
}

def _priority_color(p: str) -> str:
    return _PRIORITY_COLOR.get(p.upper(), "#4ea8de")

class ContextScreen(Screen):
    """Context screen wired to TUIDataProvider."""

    BINDINGS = [
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("a", "filter_all", "All"),
        ("s", "filter_selected", "Selected"),
        ("x", "filter_skipped", "Skipped"),
    ]

    _filter: str = "All"   # All | Selected | Skipped
    _selected_idx: int = 0
    _selected_id: Optional[str] = None

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def on_mount(self) -> None:
        if self.provider:
            self.run_worker(self.refresh_data())

    async def refresh_data(self) -> None:
        if not self.provider:
            return

        data = self.provider.get_context_data()
        candidates = self._candidates(data)

        if candidates:
            if self._selected_id is not None:
                found = next((i for i, c in enumerate(candidates) if c.id == self._selected_id), -1)
                if found != -1:
                    self._selected_idx = found
                else:
                    self._selected_idx = min(self._selected_idx, len(candidates) - 1)
            else:
                self._selected_idx = min(self._selected_idx, len(candidates) - 1)
            self._selected_id = candidates[self._selected_idx].id
        else:
            self._selected_idx = 0
            self._selected_id = None

        n_sel = len(data.selected_candidates)
        n_skip = len(data.skipped_candidates)

        try:
            pressure_color = _priority_color(data.pressure_level)
            self.query_one("#budget-summary-1", Static).update(
                f"[b]{data.pressure_level}[/b] [{pressure_color}]pressure[/] [#6c757d]· {data.repository_scope} scope[/]"
            )
            self.query_one("#budget-summary-2", Static).update(
                f"[b]{data.final_tokens:,}[/] [#6c757d]/ {data.max_tokens:,} tokens[/]  [#52b788]{n_sel} selected[/] · [#6c757d]{n_skip} skipped[/]\n"
            )

            if data.reductions:
                self.query_one("#reductions-header", Static).update("Reductions")
                self.query_one("#reductions-list", Static).update("  " + " · ".join(data.reductions) + "\n")
            else:
                self.query_one("#reductions-header", Static).update("")
                self.query_one("#reductions-list", Static).update("")

            filters = ["All", "Selected", "Skipped"]
            filter_parts = []
            for f in filters:
                if f == self._filter:
                    filter_parts.append(f"[b #4ea8de]{f}[/]")
                else:
                    filter_parts.append(f"[#6c757d]{f}[/]")
            self.query_one("#filter-bar", Static).update("Filter:  " + "   ".join(filter_parts) + "\n")

            if not candidates:
                self.query_one("#candidate-list", Static).update("No context candidates\n")
                self.query_one("#candidate-detail", Static).update("")
            else:
                lines = []
                for i, c in enumerate(candidates):
                    marker = "›" if i == self._selected_idx else " "
                    col = _priority_color(c.priority)
                    sel_marker = "" if c.selected else " [#6c757d](skip)[/]"
                    title = c.category[:28]
                    lines.append(f"{marker} {title:<28} [{col}]{c.priority:>10}[/]{sel_marker}  {c.estimated_tokens} tok")
                self.query_one("#candidate-list", Static).update("\n".join(lines))

                detail_lines = []
                c = candidates[self._selected_idx]
                decision_color = "#52b788" if c.selected else "#e63946"

                detail_lines.append(f"[#ffffff b]Why {'selected' if c.selected else 'skipped'}[/]\n")
                detail_lines.append(f"[#4ea8de]{c.category}[/]\n\n")

                detail_lines.append(f"[#ffffff b]Priority[/]\n[{_priority_color(c.priority)}]{c.priority}[/]\n\n")
                detail_lines.append(f"[#ffffff b]Score[/]\n{c.score}\n\n")

                if c.reasons:
                    detail_lines.append("[#ffffff b]Reasons[/]\n")
                    for r in c.reasons:
                        detail_lines.append(f"• [#6c757d]{r}[/]\n")
                    detail_lines.append("\n")

                detail_lines.append(f"[#ffffff b]Decision[/]\n[{decision_color}]{c.decision}[/]\n\n")
                detail_lines.append(f"[#ffffff b]Estimated tokens[/]\n{c.estimated_tokens}\n\n")

                if c.files:
                    detail_lines.append("[#ffffff b]Files[/]\n")
                    for f in c.files[:5]:
                        detail_lines.append(f"  [#6c757d]{f}[/]\n")
                self.query_one("#candidate-detail", Static).update("".join(detail_lines))

            self.query_one(NavigationWidget).refresh(layout=True)
        except Exception:
            pass

    def _candidates(self, data) -> list[ContextCandidateData]:
        if self._filter == "Selected":
            return data.selected_candidates
        if self._filter == "Skipped":
            return data.skipped_candidates
        return data.selected_candidates + data.skipped_candidates

    async def action_move_up(self) -> None:
        self._selected_idx = max(0, self._selected_idx - 1)
        if self.provider:
            candidates = self._candidates(self.provider.get_context_data())
            if candidates:
                self._selected_id = candidates[self._selected_idx].id
        await self.refresh_data()

    async def action_move_down(self) -> None:
        if not self.provider: return
        data = self.provider.get_context_data()
        count = len(self._candidates(data))
        if count > 0:
            self._selected_idx = min(count - 1, self._selected_idx + 1)
            self._selected_id = self._candidates(data)[self._selected_idx].id
        await self.refresh_data()

    async def action_filter_all(self) -> None:
        self._filter = "All"
        self._selected_idx = 0
        self._selected_id = None
        await self.refresh_data()

    async def action_filter_selected(self) -> None:
        self._filter = "Selected"
        self._selected_idx = 0
        self._selected_id = None
        await self.refresh_data()

    async def action_filter_skipped(self) -> None:
        self._filter = "Skipped"
        self._selected_idx = 0
        self._selected_id = None
        await self.refresh_data()

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        with Vertical(id="main-content"):
            yield Static("", id="budget-summary-1", classes="header")
            yield Static("", id="budget-summary-2")

            yield Static("", id="reductions-header", classes="header")
            yield Static("", id="reductions-list", classes="muted")

            yield Static("", id="filter-bar", classes="muted")

            with Horizontal():
                with Vertical(classes="list-pane"):
                    yield Static("", id="candidate-list")
                with Vertical(classes="detail-pane"):
                    yield Static("", id="candidate-detail")

        yield NavigationWidget(
            active_screen="Context",
            provider=self.provider,
            interactive=getattr(self.app, "interactive", False),
            busy=getattr(self.app, "busy", False),
        )

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("[b]HIGH[/b] [#52b788]pressure[/] [#6c757d]· BROAD scope[/]", classes="header")
            yield Static("[b]6,300[/] [#6c757d]/ 8,000 tokens[/]  [#52b788]12 selected[/] · [#6c757d]4 skipped[/]\n")
            yield Static("Reductions", classes="header")
            yield Static("  · dropped history (oldest first)\n", classes="muted")
            yield Static("Filter:  [b #4ea8de]All[/]   [#6c757d]Selected[/]   [#6c757d]Skipped[/]\n", classes="muted")
            with Horizontal():
                with Vertical(classes="list-pane"):
                    yield Static("› Current error                [#e63946]  CRITICAL[/]  22 tok")
                    yield Static("  Failed verification          [#e63946]  CRITICAL[/]  41 tok")
                    yield Static("  src/auth.py symbols          [#52b788]      HIGH[/]  81 tok")
                with Vertical(classes="detail-pane"):
                    yield Static("Why selected\n", classes="header")
                    yield Static("Failed verification\n", classes="accent")
                    yield Static("Decision", classes="header")
                    yield Static("[#52b788]selected_within_budget[/]\n")
                    yield Static("Estimated tokens", classes="header")
                    yield Static("41\n")
        yield NavigationWidget(
            active_screen="Context",
            provider=self.provider,
            interactive=getattr(self.app, "interactive", False),
            busy=getattr(self.app, "busy", False),
        )
