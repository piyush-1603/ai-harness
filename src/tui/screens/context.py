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

    _filter: str = "All"   # All | Selected | Skipped
    _selected_idx: int = 0

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    async def refresh_data(self) -> None:
        await self.recompose()

    def _candidates(self, data) -> list[ContextCandidateData]:
        if self._filter == "Selected":
            return data.selected_candidates
        if self._filter == "Skipped":
            return data.skipped_candidates
        return data.selected_candidates + data.skipped_candidates

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        data = self.provider.get_context_data()
        candidates = self._candidates(data)
        n_sel = len(data.selected_candidates)
        n_skip = len(data.skipped_candidates)

        with Vertical(id="main-content"):
            # Budget summary
            pressure_color = _priority_color(data.pressure_level)
            yield Static(
                f"[b]{data.pressure_level}[/b] [{pressure_color}]pressure[/] [#6c757d]· "
                f"{data.repository_scope} scope[/]",
                classes="header"
            )
            yield Static(
                f"[b]{data.final_tokens:,}[/] [#6c757d]/ {data.max_tokens:,} tokens[/]  "
                f"[#52b788]{n_sel} selected[/] · [#6c757d]{n_skip} skipped[/]\n"
            )

            if data.reductions:
                yield Static("Reductions", classes="header")
                yield Static("  " + " · ".join(data.reductions) + "\n", classes="muted")

            # Filter bar
            filters = ["All", "Selected", "Skipped"]
            filter_parts = []
            for f in filters:
                if f == self._filter:
                    filter_parts.append(f"[b #4ea8de]{f}[/]")
                else:
                    filter_parts.append(f"[#6c757d]{f}[/]")
            yield Static("Filter:  " + "   ".join(filter_parts) + "\n", classes="muted")

            if not candidates:
                yield Static("No context candidates\n", classes="muted")
                yield NavigationWidget(active_screen="Context")
                return

            with Horizontal():
                with Vertical(classes="list-pane"):
                    for i, c in enumerate(candidates):
                        marker = "›" if i == self._selected_idx else " "
                        col = _priority_color(c.priority)
                        sel_marker = "" if c.selected else " [#6c757d](skip)[/]"
                        # Truncate title to fit 28 chars
                        title = c.category[:28]
                        yield Static(
                            f"{marker} {title:<28} [{col}]{c.priority:>10}[/]{sel_marker}  {c.estimated_tokens} tok"
                        )

                with Vertical(classes="detail-pane"):
                    if candidates:
                        c = candidates[self._selected_idx % len(candidates)]
                        decision_color = "#52b788" if c.selected else "#e63946"
                        yield Static("Why " + ("selected" if c.selected else "skipped") + "\n", classes="header")
                        yield Static(c.category + "\n", classes="accent")

                        yield Static("Priority", classes="header")
                        col = _priority_color(c.priority)
                        yield Static(f"[{col}]{c.priority}[/]\n")

                        yield Static("Score", classes="header")
                        yield Static(f"{c.score}\n")

                        if c.reasons:
                            yield Static("Reasons", classes="header")
                            for r in c.reasons:
                                yield Static(f"• {r}", classes="muted")
                            yield Static("")

                        yield Static("Decision", classes="header")
                        yield Static(f"[{decision_color}]{c.decision}[/]\n")

                        yield Static("Estimated tokens", classes="header")
                        yield Static(f"{c.estimated_tokens}\n")

                        if c.files:
                            yield Static("Files", classes="header")
                            for f in c.files[:5]:
                                yield Static(f"  {f}", classes="muted")

        yield NavigationWidget(active_screen="Context")

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Context  [#6c757d](demo)[/]\n", classes="header")
            yield Static("Filter:  [b #4ea8de]All[/]   [#6c757d]Selected   Skipped[/]\n", classes="muted")
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
        yield NavigationWidget(active_screen="Context")
