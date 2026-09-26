from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Tree
from textual.containers import Vertical, Horizontal

from src.tui.widgets.navigation import NavigationWidget

class RepositoryScreen(Screen):
    """Static Repository File Tree Screen."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="main-content"):
            yield Static("Repository\n", classes="header box")
            
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
