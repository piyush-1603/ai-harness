from typing import Optional
from collections import defaultdict

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Tree
from textual.containers import Vertical, Horizontal
from textual.events import Mount

from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider
from src.tui.models import RepositoryFileData

class RepositoryScreen(Screen):
    """Repository file tree screen wired to TUIDataProvider."""

    _selected_path: Optional[str] = None
    _last_repo_hash: int = 0

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def on_mount(self) -> None:
        if self.provider:
            self.run_worker(self.refresh_data())

    async def refresh_data(self) -> None:
        if not self.provider:
            return

        data = self.provider.get_repository_data()
        if not data.files:
            return

        # Check if the file list or relevant/touched status changed
        current_hash = hash(str([(f.path, f.touched, f.relevant) for f in data.files]))

        if current_hash != self._last_repo_hash:
            self._last_repo_hash = current_hash

            # Rebuild tree in-place
            try:
                tree = self.query_one(Tree)
                tree.clear()
                tree.root.expand()

                dir_groups: dict[str, list[RepositoryFileData]] = defaultdict(list)
                for f in sorted(data.files, key=lambda x: x.path):
                    parts = f.path.split("/")
                    top = parts[0] + "/" if len(parts) > 1 else ""
                    dir_groups[top].append(f)

                for dir_prefix, files in sorted(dir_groups.items()):
                    if dir_prefix:
                        node = tree.root.add(dir_prefix, data=None)
                        node.expand()
                    else:
                        node = tree.root

                    for f in files:
                        filename = f.path.split("/")[-1]
                        if f.touched:
                            status = " [#52b788]touched[/]"
                        elif f.relevant:
                            status = " [#4ea8de]relevant[/]"
                        else:
                            status = ""
                        node.add_leaf(f"{filename:<30}{status}", data=f.path)
            except Exception:
                pass

        # Validate or default the selection
        if self._selected_path:
            selected = next((f for f in data.files if f.path == self._selected_path), None)
            if not selected:
                selected = data.files[0] if data.files else None
        else:
            selected = data.files[0] if data.files else None

        if selected:
            self._selected_path = selected.path
            self._update_detail_pane(selected)

        try:
            self.query_one(NavigationWidget).refresh(layout=True)
        except Exception:
            pass

    def _update_detail_pane(self, selected: RepositoryFileData) -> None:
        try:
            self.query_one("#detail-path", Static).update(f"{selected.path}\n")
            self.query_one("#detail-role", Static).update(f"{selected.role.upper()} · Python\n")

            touched_col = "#52b788" if selected.touched else "#6c757d"
            self.query_one("#detail-touched", Static).update(f"[{touched_col}]{'yes' if selected.touched else 'no'}[/]\n")

            rel_col = "#4ea8de" if selected.relevant else "#6c757d"
            self.query_one("#detail-relevant", Static).update(f"[{rel_col}]{'yes' if selected.relevant else 'no'}[/]\n")

            if selected.symbols:
                sym_lines = ["Symbols"]
                for sym in selected.symbols[:10]:
                    sym_lines.append(f"  [#6c757d]{sym}[/]")
                if len(selected.symbols) > 10:
                    sym_lines.append(f"  [#6c757d]… {len(selected.symbols) - 10} more[/]")
                sym_lines.append("")
                self.query_one("#detail-symbols", Static).update("\n".join(sym_lines))
            else:
                self.query_one("#detail-symbols", Static).update("")

            if selected.imports:
                imp_lines = ["Imports"]
                for imp in selected.imports[:8]:
                    imp_lines.append(f"  [#6c757d]{imp}[/]")
                if len(selected.imports) > 8:
                    imp_lines.append(f"  [#6c757d]… {len(selected.imports) - 8} more[/]")
                imp_lines.append("")
                self.query_one("#detail-imports", Static).update("\n".join(imp_lines))
            else:
                self.query_one("#detail-imports", Static).update("")
        except Exception:
            pass

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        if not event.node.data:
            return

        self._selected_path = event.node.data
        if self.provider:
            data = self.provider.get_repository_data()
            selected = next((f for f in data.files if f.path == self._selected_path), None)
            if selected:
                self._update_detail_pane(selected)

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        with Vertical(id="main-content"):
            yield Static("Repository\n", id="repo-header", classes="header")

            with Horizontal():
                with Vertical(classes="list-pane"):
                    tree: Tree[str] = Tree("repo")
                    tree.root.expand()
                    yield tree

                with Vertical(classes="detail-pane"):
                    yield Static("", id="detail-path", classes="accent header")
                    yield Static("", id="detail-role", classes="muted")

                    yield Static("Touched", classes="header")
                    yield Static("", id="detail-touched")

                    yield Static("Relevant", classes="header")
                    yield Static("", id="detail-relevant")

                    yield Static("", id="detail-symbols")
                    yield Static("", id="detail-imports")

        yield NavigationWidget(active_screen="Repository", provider=self.provider)

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Repository  [#6c757d](demo)[/]\n", classes="header")
            with Horizontal():
                with Vertical(classes="list-pane"):
                    tree: Tree[str] = Tree("repo")
                    tree.root.expand()
                    src = tree.root.add("src/")
                    src.expand()
                    src.add_leaf(f"{'auth.py':<30} [#52b788]touched[/]", data="src/auth.py")
                    src.add_leaf(f"{'token.py':<30} [#4ea8de]relevant[/]", data="src/token.py")
                    src.add_leaf("database.py", data="src/database.py")
                    tests = tree.root.add("tests/")
                    tests.expand()
                    tests.add_leaf(f"{'test_auth.py':<30} [#6c757d]related[/]", data="tests/test_auth.py")
                    yield tree
                with Vertical(classes="detail-pane"):
                    yield Static("src/auth.py\n", classes="accent header")
                    yield Static("SOURCE · Python\n", classes="muted")
                    yield Static("Symbols", classes="header")
                    yield Static("AuthService\nvalidate_token()\nrefresh_token()\n", classes="muted")
                    yield Static("Imports", classes="header")
                    yield Static("src/token.py\nsrc/database.py", classes="muted")
        yield NavigationWidget(active_screen="Repository", provider=self.provider)
