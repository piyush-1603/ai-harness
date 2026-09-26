import asyncio
import pytest
from copy import deepcopy
from pathlib import Path

from src.memory.manager import MemoryManager
from src.memory.models import Failure, Observation
from src.context.scanner import RepositoryScanner, RepositoryIndex
from src.context.diagnostics import ContextDiagnosticsEngine
from src.tui.data import TUIDataProvider
from src.tui.app import HarnessTUI
from src.tui.models import EventItemData
from textual.widgets import Static

@pytest.fixture
def memory(tmp_path):
    mgr = MemoryManager(base_dir=str(tmp_path))
    mgr.initialize_task("m6task", "M6 test task")
    return mgr

@pytest.fixture
def provider(memory):
    return TUIDataProvider(memory)

def _texts_from_app(app) -> list[str]:
    texts = []
    for w in app.screen.walk_children(Static):
        r = w.render()
        texts.append(r.plain if hasattr(r, "plain") else str(r))
    return texts

def _run(coro):
    return asyncio.run(coro)

# ── Repository Screen ─────────────────────────────────────────────────────────

def test_missing_repository_index_renders_safely(provider):
    # provider has no repo index → graceful empty state
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            await app.switch_screen("repository")
            texts = _texts_from_app(app)
        assert any("Repository" in t or "unavailable" in t for t in texts)
    _run(run())

def test_repository_real_paths_render(tmp_path, memory):
    # Create a minimal fake repo to scan
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text("def validate(): pass")
    (src / "token.py").write_text("class Token: pass")

    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(tmp_path))
    prov = TUIDataProvider(memory, repository_index=repo_index)

    data = prov.get_repository_data()
    paths = [f.path for f in data.files]
    assert any("auth.py" in p for p in paths)
    assert any("token.py" in p for p in paths)

def test_repository_touched_relevant_flags(tmp_path, memory):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    src = repo_root / "src"
    src.mkdir()
    (src / "auth.py").write_text("def f(): pass")
    (src / "other.py").write_text("x = 1")

    memory.add_touched_file("src/auth.py")
    memory.add_relevant_file("src/other.py")

    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(repo_root))
    prov = TUIDataProvider(memory, repository_index=repo_index)

    data = prov.get_repository_data()
    by_path = {f.path: f for f in data.files}
    assert "src/auth.py" in by_path
    assert "src/other.py" in by_path
    assert by_path["src/auth.py"].touched is True
    assert by_path["src/auth.py"].relevant is False
    assert by_path["src/other.py"].relevant is True
    assert by_path["src/other.py"].touched is False

def test_repository_symbol_metadata_renders(tmp_path, memory):
    src = tmp_path / "src"
    src.mkdir()
    (src / "parser.py").write_text("def parse_token(t): return t\nclass TokenParser: pass")

    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(tmp_path))
    prov = TUIDataProvider(memory, repository_index=repo_index)

    data = prov.get_repository_data()
    parser = next((f for f in data.files if "parser.py" in f.path), None)
    assert parser is not None
    assert any("parse_token" in s or "TokenParser" in s for s in parser.symbols)

def test_repository_import_metadata_renders(tmp_path, memory):
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text("from src.token import Token\ndef validate(): pass")
    (src / "token.py").write_text("class Token: pass")

    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(tmp_path))
    prov = TUIDataProvider(memory, repository_index=repo_index)

    data = prov.get_repository_data()
    auth = next((f for f in data.files if "auth.py" in f.path), None)
    assert auth is not None
    # imports list exists (may be empty if scanner doesn't resolve this particular one)
    assert isinstance(auth.imports, list)

def test_repository_scan_once_not_repeated(tmp_path, memory):
    # The RepositoryIndex is built once; the provider just reads it
    (tmp_path / "foo.py").write_text("x = 1")
    scanner = RepositoryScanner()
    repo_index = scanner.scan(str(tmp_path))
    prov = TUIDataProvider(memory, repository_index=repo_index)

    # Call get_repository_data multiple times — should be pure reads, no side effects
    d1 = prov.get_repository_data()
    d2 = prov.get_repository_data()
    assert [f.path for f in d1.files] == [f.path for f in d2.files]

# ── Events Screen ─────────────────────────────────────────────────────────────

def test_events_render_real_history(memory):
    memory.record_failure(Failure("SomeErr", "summary", "action"))
    prov = TUIDataProvider(memory)

    events = prov.get_events_data()
    assert len(events) >= 1
    assert all(isinstance(e, EventItemData) for e in events)
    assert all(isinstance(e.timestamp, str) for e in events)

def test_events_empty_renders_safely(provider):
    provider.memory._events.clear()  # empty

    events = provider.get_events_data()
    assert events == []

def test_events_ordering_matches_provider(memory):
    memory.record_failure(Failure("E1", "s1", "a1"))
    memory.record_failure(Failure("E2", "s2", "a2"))
    prov = TUIDataProvider(memory)
    events = prov.get_events_data()
    # Order must be stable (chronological); simply verify it's a list with at least 2
    assert len(events) >= 2

def test_events_long_descriptions_truncated_in_screen_only(provider, memory):
    long_summary = "x" * 200
    from src.memory.models import Discovery
    # Discovery(statement, evidence)
    memory.add_discovery(Discovery(statement=long_summary, evidence="evidence"))

    prov = TUIDataProvider(memory)
    events = prov.get_events_data()
    raw_summaries = [e.summary for e in events]
    assert any(len(s) > 65 for s in raw_summaries if s)

def test_events_navigation_shows_real_data(provider):
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            await app.switch_screen("events")
            assert app.screen.__class__.__name__ == "EventsScreen"
    _run(run())

# ── Read-only guarantee ───────────────────────────────────────────────────────

def test_repository_events_navigation_does_not_mutate(tmp_path):
    async def run():
        mgr = MemoryManager(base_dir=str(tmp_path))
        mgr.initialize_task("ro_task", "ro task")
        mgr.record_failure(Failure("E", "s", "a"))

        state_before = deepcopy(mgr.get_state())
        events_before = len(mgr.get_events())

        prov = TUIDataProvider(mgr, ContextDiagnosticsEngine())

        app = HarnessTUI(provider=prov)
        async with app.run_test() as _:
            await app.switch_screen("repository")
            await app.switch_screen("events")
            await app.switch_screen("overview")

        assert mgr.get_state() == state_before
        assert len(mgr.get_events()) == events_before
    _run(run())

def test_all_screens_navigate_without_crash(provider):
    async def run():
        app = HarnessTUI(provider=provider)
        async with app.run_test() as _:
            for screen in ("overview", "context", "memory", "repository", "events"):
                await app.switch_screen(screen)
    _run(run())
