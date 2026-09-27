import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from textual.widgets import Static, Tree
from src.tui.app import HarnessTUI
from src.tui.data import TUIDataProvider
from src.tui.models import RepositoryFileData

def _run(coro):
    return asyncio.run(coro)

@pytest.fixture
def mock_provider():
    provider = MagicMock(spec=TUIDataProvider)
    provider.task_id = "demo"
    provider.refresh.return_value = True
    provider.last_refresh_changed = False
    provider.last_refresh_error = None
    
    # Overview
    overview_data = MagicMock()
    overview_data.status = "RUNNING"
    overview_data.task = "Test Task"
    overview_data.phase = "PLAN"
    overview_data.active_failure = None
    overview_data.pressure_level = "LOW"
    overview_data.repository_scope = "BROAD"
    overview_data.context_tokens = 100
    overview_data.context_limit = 8000
    overview_data.relevant_files = []
    overview_data.recent_activity = []
    provider.get_overview_data.return_value = overview_data
    
    # Repository Data
    repo_data = MagicMock()
    
    file1 = RepositoryFileData(path="src/a.py", role="source", touched=True, relevant=False, symbols=[], imports=[])
    file2 = RepositoryFileData(path="src/b.py", role="source", touched=False, relevant=True, symbols=[], imports=[])
    file3 = RepositoryFileData(path="tests/test_a.py", role="test", touched=False, relevant=False, symbols=[], imports=[])
    
    repo_data.files = [file1, file2, file3]
    provider.get_repository_data.return_value = repo_data
    
    return provider

    def test_repository_screen_highlight_updates_detail_pane(mock_provider):
        async def run():
            app = HarnessTUI(provider=mock_provider, live=False)
            async with app.run_test() as pilot:
                await pilot.press("4")
                await pilot.pause(0.1)
            
            # Initially, first file (src/a.py) should be selected
            assert "src/a.py" in str(app.screen.query_one("#detail-path", Static).render())
            assert "yes" in str(app.screen.query_one("#detail-touched", Static).render())
            assert "no" in str(app.screen.query_one("#detail-relevant", Static).render())
            
            # Now highlight another node in the tree
            tree = app.screen.query_one(Tree)
            
            # Find leaf node for src/b.py
            b_node = None
            for child in tree.root.children:
                if child.label.plain == "src/":
                    for leaf in child.children:
                        if "b.py" in str(leaf.label.plain):
                            b_node = leaf
                            break
            
            assert b_node is not None
            
            # Highlight it
            tree.action_cursor_down() # moves to src/
            tree.action_cursor_down() # moves to a.py
            tree.action_cursor_down() # moves to b.py
            
            await pilot.pause(0.1)
            
            # It should have updated the detail pane immediately without recompose
            assert "src/b.py" in str(app.screen.query_one("#detail-path", Static).render())
            assert "no" in str(app.screen.query_one("#detail-touched", Static).render())
            assert "yes" in str(app.screen.query_one("#detail-relevant", Static).render())
            
            # Check state was not mutated (still 3 files, unchanged)
            assert len(mock_provider.get_repository_data().files) == 3
            
            # No full screen recompose check
            # We mock recompose on the screen and make sure it wasn't called
            app.screen.recompose = AsyncMock()
            tree.action_cursor_down() # move to another node
            await pilot.pause(0.1)
            app.screen.recompose.assert_not_called()
            
    _run(run())
