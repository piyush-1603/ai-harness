from typing import Optional

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider
from src.tui.models import MemoryData, MemoryItemData

_KIND_COLOR = {
    "Failure": "#e63946",
    "Attempt": "#ffb703",
    "Observation": "#4ea8de",
    "Discovery": "#52b788",
    "Artifact": "#6c757d",
}

def _kind_color(kind: str) -> str:
    return _KIND_COLOR.get(kind, "#adb5bd")

class MemoryScreen(Screen):
    """Memory screen wired to TUIDataProvider."""

    BINDINGS = [
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("0", "filter_all", "All"),
        ("o", "filter_observations", "Obs"),
        ("a", "filter_attempts", "Att"),
        ("f", "filter_failures", "Fail"),
        ("d", "filter_discoveries", "Disc"),
        ("t", "filter_artifacts", "Art"),
    ]

    _filter: str = "All"
    _selected_idx: int = 0

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    async def refresh_data(self) -> None:
        await self.recompose()

    def _all_items(self, data: MemoryData) -> list[MemoryItemData]:
        return (
            data.failures +
            data.attempts +
            data.observations +
            data.discoveries +
            data.artifacts
        )

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        data = self.provider.get_memory_data()
        items = self._all_items(data)
        total = len(items)

        with Vertical(id="main-content"):
            yield Static(f"Memory [#6c757d]{total} items[/]\n", classes="header")

            # Category counts
            counts = [
                ("Observations", len(data.observations)),
                ("Attempts",     len(data.attempts)),
                ("Failures",     len(data.failures)),
                ("Discoveries",  len(data.discoveries)),
                ("Artifacts",    len(data.artifacts)),
            ]
            for label, count in counts:
                yield Static(f"{label:<15} {count}")
            yield Static("")

            if not items:
                yield Static("No memory items\n", classes="muted")
                yield NavigationWidget(active_screen="Memory", provider=self.provider)
                return

            with Horizontal():
                with Vertical(classes="list-pane"):
                    for i, item in enumerate(items):
                        marker = "›" if i == self._selected_idx else " "
                        col = _kind_color(item.kind)
                        kind_str = f"[{col}]{item.kind:<12}[/]"
                        label = item.label[:38] if item.label else ""

                        suffix = ""
                        if item.kind == "Failure" and item.count > 1:
                            suffix = f" [#e63946]×{item.count}[/]"
                        elif item.kind == "Attempt":
                            result_col = "#52b788" if item.summary == "success" else "#e63946"
                            suffix = f" [{result_col}]{item.summary or ''}[/]"
                        elif item.kind == "Artifact" and item.original_chars:
                            kb = item.original_chars // 1000
                            suffix = f" [#6c757d]{kb} KB[/]"

                        yield Static(f"{marker} {kind_str} {label:<40}{suffix}")

                with Vertical(classes="detail-pane"):
                    if items:
                        item = items[self._selected_idx % len(items)]
                        col = _kind_color(item.kind)
                        yield Static(f"[{col}]{item.kind}[/]\n", classes="header")
                        yield Static(item.label + "\n", classes="accent")

                        if item.kind == "Failure":
                            yield Static("Summary", classes="header")
                            yield Static(item.summary + "\n", classes="muted")
                            yield Static("Occurrences", classes="header")
                            yield Static(f"×{item.count}\n")

                        elif item.kind == "Observation":
                            yield Static("Summary", classes="header")
                            yield Static(item.summary + "\n", classes="muted")

                        elif item.kind == "Attempt":
                            yield Static("Result", classes="header")
                            result_col = "#52b788" if item.summary == "success" else "#e63946"
                            yield Static(f"[{result_col}]{item.summary or 'unknown'}[/]\n")

                        elif item.kind == "Discovery":
                            yield Static("Statement", classes="header")
                            yield Static(item.summary + "\n", classes="muted")

                        elif item.kind == "Artifact":
                            yield Static("Reference", classes="header")
                            yield Static(f"[#6c757d]{item.ref}[/]\n")
                            if item.original_chars:
                                yield Static("Size", classes="header")
                                yield Static(f"{item.original_chars:,} chars\n", classes="muted")
                            exists_color = "#52b788" if item.exists else "#e63946"
                            exists_label = "stored" if item.exists else "missing"
                            yield Static(f"[{exists_color}]{exists_label}[/]\n")
                            if item.preview:
                                yield Static("Preview", classes="header")
                                # Show first 3 lines of bounded preview only
                                preview_lines = item.preview.splitlines()[:3]
                                for line in preview_lines:
                                    yield Static(line[:74], classes="muted")

        yield NavigationWidget(active_screen="Memory", provider=self.provider)

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Memory  [#6c757d](demo) 24 items[/]\n", classes="header")
            yield Static(f"{'Observations':<15} 8")
            yield Static(f"{'Attempts':<15} 5")
            yield Static(f"{'Failures':<15} 2")
            yield Static(f"{'Discoveries':<15} 6")
            yield Static(f"{'Artifacts':<15} 3\n")
            with Horizontal():
                with Vertical(classes="list-pane"):
                    yield Static("› [#e63946]Failure     [/] TokenExpiredError                        [#e63946]×3[/]")
                    yield Static("  [#4ea8de]Observation [/] pytest authentication failed")
                    yield Static("  [#ffb703]Attempt     [/] modify token parser                      [#e63946]failed[/]")
                    yield Static("  [#6c757d]Artifact    [/] pytest output                            [#6c757d]34 KB[/]")
                with Vertical(classes="detail-pane"):
                    yield Static("Artifact\n", classes="header")
                    yield Static("artifact_14bc...9af2\n", classes="accent")
                    yield Static("34,000 characters\n", classes="muted")
                    yield Static("[#52b788]stored[/]\n")
                    yield Static("Preview", classes="header")
                    yield Static("FAILED test_expired_token\nTokenExpiredError...\n", classes="muted")
        yield NavigationWidget(active_screen="Memory", provider=self.provider)
