from typing import Optional
from collections import defaultdict

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Tree
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget
from src.tui.data import TUIDataProvider
from src.tui.models import RepositoryFileData

class RepositoryScreen(Screen):
    """Repository file tree screen wired to TUIDataProvider."""

    _selected_path: Optional[str] = None

    def __init__(self, provider: Optional[TUIDataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def compose(self) -> ComposeResult:
        if not self.provider:
            yield from self._compose_demo()
            return

        data = self.provider.get_repository_data()

        if not data.files:
            with Vertical(id="main-content"):
                yield Static("Repository\n", classes="header")
                yield Static("Repository index unavailable\n", classes="muted")
            yield NavigationWidget(active_screen="Repository")
            return

        # Group files by top-level directory for tree rendering
        dir_groups: dict[str, list[RepositoryFileData]] = defaultdict(list)
        for f in sorted(data.files, key=lambda x: x.path):
            parts = f.path.split("/")
            top = parts[0] + "/" if len(parts) > 1 else ""
            dir_groups[top].append(f)

        # Select first file by default
        first_file = data.files[0] if data.files else None
        if self._selected_path:
            selected = next((f for f in data.files if f.path == self._selected_path), first_file)
        else:
            selected = first_file

        with Vertical(id="main-content"):
            yield Static(f"Repository  [#6c757d]{len(data.files)} files[/]\n", classes="header")

            with Horizontal():
                with Vertical(classes="list-pane"):
                    tree: Tree[str] = Tree("repo")
                    tree.root.expand()

                    for dir_prefix, files in sorted(dir_groups.items()):
                        if dir_prefix:
                            node = tree.root.add(dir_prefix)
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
                            node.add_leaf(f"{filename:<30}{status}")

                    yield tree

                with Vertical(classes="detail-pane"):
                    if selected:
                        yield Static(f"{selected.path}\n", classes="accent header")
                        yield Static(f"{selected.role.upper()} · Python\n", classes="muted")

                        yield Static("Touched", classes="header")
                        touched_col = "#52b788" if selected.touched else "#6c757d"
                        yield Static(f"[{touched_col}]{'yes' if selected.touched else 'no'}[/]\n")

                        yield Static("Relevant", classes="header")
                        rel_col = "#4ea8de" if selected.relevant else "#6c757d"
                        yield Static(f"[{rel_col}]{'yes' if selected.relevant else 'no'}[/]\n")

                        if selected.symbols:
                            yield Static("Symbols", classes="header")
                            for sym in selected.symbols[:10]:
                                yield Static(f"  {sym}", classes="muted")
                            if len(selected.symbols) > 10:
                                yield Static(f"  … {len(selected.symbols) - 10} more", classes="muted")
                            yield Static("")

                        if selected.imports:
                            yield Static("Imports", classes="header")
                            for imp in selected.imports[:8]:
                                yield Static(f"  {imp}", classes="muted")
                            if len(selected.imports) > 8:
                                yield Static(f"  … {len(selected.imports) - 8} more", classes="muted")

        yield NavigationWidget(active_screen="Repository")

    def _compose_demo(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Repository  [#6c757d](demo)[/]\n", classes="header")
            with Horizontal():
                with Vertical(classes="list-pane"):
                    tree: Tree[str] = Tree("repo")
                    tree.root.expand()
                    src = tree.root.add("src/")
                    src.expand()
                    src.add_leaf(f"{'auth.py':<30} [#52b788]touched[/]")
                    src.add_leaf(f"{'token.py':<30} [#4ea8de]relevant[/]")
                    src.add_leaf("database.py")
                    tests = tree.root.add("tests/")
                    tests.expand()
                    tests.add_leaf(f"{'test_auth.py':<30} [#6c757d]related[/]")
                    yield tree
                with Vertical(classes="detail-pane"):
                    yield Static("src/auth.py\n", classes="accent header")
                    yield Static("SOURCE · Python\n", classes="muted")
                    yield Static("Symbols", classes="header")
                    yield Static("AuthService\nvalidate_token()\nrefresh_token()\n", classes="muted")
                    yield Static("Imports", classes="header")
                    yield Static("src/token.py\nsrc/database.py", classes="muted")
        yield NavigationWidget(active_screen="Repository")
