"""Tests for Person B Pipeline Repair P1 + P2: Adaptive Policy Propagation and Repository Context Wiring."""

from pathlib import Path
import pytest

from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.context.policy import (
    AdaptiveContextPolicy,
    ContextProfile,
    PressureLevel,
    RepositoryScope,
)
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols, SymbolRecord
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    Phase,
    TaskState,
    TaskStatus,
    VerificationSnapshot,
)


def _build_test_repo_index(root_dir: str = "/repo/secret_path") -> RepositoryIndex:
    """Helper to build a populated RepositoryIndex with symbols and local imports."""
    fs_main = FileSymbols(
        path="src/main.py",
        language="Python",
        symbols=[
            SymbolRecord(name="MainApp", kind="class", file="src/main.py"),
            SymbolRecord(name="run", kind="method", file="src/main.py", parent="MainApp"),
        ],
        imports=["os", "sys", "src.helper"],
        local_imports=["src/helper.py"],
    )
    fs_helper = FileSymbols(
        path="src/helper.py",
        language="Python",
        symbols=[
            SymbolRecord(name="Helper", kind="class", file="src/helper.py"),
            SymbolRecord(name="assist", kind="method", file="src/helper.py", parent="Helper"),
        ],
        imports=["json"],
        local_imports=[],
    )
    fs_extra = FileSymbols(
        path="src/extra.py",
        language="Python",
        symbols=[
            SymbolRecord(name="ExtraService", kind="class", file="src/extra.py"),
            SymbolRecord(name="execute", kind="method", file="src/extra.py", parent="ExtraService"),
        ],
        imports=[],
        local_imports=[],
    )

    return RepositoryIndex(
        root_dir=root_dir,
        discovered_files=["src/main.py", "src/helper.py", "src/extra.py", "tests/test_main.py", "README.md", "pyproject.toml"],
        discovered_directories=["src", "tests"],
        file_count=6,
        detected_languages={"Python": 4, "Markdown": 1, "TOML": 1},
        detected_extensions={".py": 4, ".md": 1, ".toml": 1},
        important_files=["pyproject.toml"],
        test_files=["tests/test_main.py"],
        test_directories=["tests"],
        source_files=["src/main.py", "src/helper.py", "src/extra.py"],
        source_directories=["src"],
        documentation_files=["README.md"],
        file_roles={
            "src/main.py": "SOURCE",
            "src/helper.py": "SOURCE",
            "src/extra.py": "SOURCE",
            "tests/test_main.py": "TEST",
            "README.md": "DOCUMENTATION",
            "pyproject.toml": "CONFIG",
        },
        file_symbols={
            "src/main.py": fs_main,
            "src/helper.py": fs_helper,
            "src/extra.py": fs_extra,
        },
    )


def test_all_context_profile_flags_survive_to_context_config() -> None:
    """Verify all ContextProfile flags survive to_context_config() without being dropped."""
    profile_true = ContextProfile(
        phase="RECOVER",
        pressure_level=PressureLevel.HIGH,
        pressure_score=8,
        repository_scope=RepositoryScope.BROAD,
        max_observations=14,
        max_attempts=9,
        max_discoveries=18,
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        include_successful_attempts=True,
        include_failed_attempts=True,
        include_repeated_failures=True,
        include_verification=True,
        include_telemetry=True,
        reasons=["High failure pressure"],
    )

    cfg_true = profile_true.to_context_config()
    assert cfg_true.max_observations == 14
    assert cfg_true.max_attempts == 9
    assert cfg_true.max_discoveries == 18
    assert cfg_true.include_repository_overview is True
    assert cfg_true.include_symbols is True
    assert cfg_true.include_local_imports is True
    assert cfg_true.include_successful_attempts is True
    assert cfg_true.include_failed_attempts is True
    assert cfg_true.include_repeated_failures is True
    assert cfg_true.include_verification is True
    assert cfg_true.include_telemetry is True
    assert cfg_true.repository_scope == RepositoryScope.BROAD

    # Test false flags also propagate accurately
    profile_false = ContextProfile(
        phase="EXECUTE",
        pressure_level=PressureLevel.LOW,
        pressure_score=0,
        repository_scope=RepositoryScope.MINIMAL,
        max_observations=6,
        max_attempts=4,
        max_discoveries=8,
        include_repository_overview=False,
        include_symbols=False,
        include_local_imports=False,
        include_successful_attempts=False,
        include_failed_attempts=False,
        include_repeated_failures=False,
        include_verification=False,
        include_telemetry=False,
        reasons=[],
    )

    cfg_false = profile_false.to_context_config()
    assert cfg_false.max_observations == 6
    assert cfg_false.max_attempts == 4
    assert cfg_false.max_discoveries == 8
    assert cfg_false.include_repository_overview is False
    assert cfg_false.include_symbols is False
    assert cfg_false.include_local_imports is False
    assert cfg_false.include_successful_attempts is False
    assert cfg_false.include_failed_attempts is False
    assert cfg_false.include_repeated_failures is False
    assert cfg_false.include_verification is False
    assert cfg_false.include_telemetry is False
    assert cfg_false.repository_scope == RepositoryScope.MINIMAL


def test_disabling_verification_removes_verification_content() -> None:
    """Verify include_verification=False removes verification data and header from output."""
    state = TaskState(
        task_id="t-v1",
        task="Verification task",
        phase=Phase.VERIFY,
        verification=VerificationSnapshot(
            success=False,
            summary="AssertionError: expected 200 got 500",
            tests_failed=["test_api"],
        ),
    )

    builder = ContextBuilder()
    cfg_disabled = ContextConfig(include_verification=False)
    bundle = builder.build(state, config=cfg_disabled)

    assert bundle.latest_verification_result is None
    assert bundle.include_verification is False
    rendered = bundle.render_text()
    assert "## VERIFICATION" not in rendered
    assert "AssertionError" not in rendered


def test_disabling_telemetry_removes_efficiency_content() -> None:
    """Verify include_telemetry=False removes iteration/tool/model/token stats and EFFICIENCY section."""
    state = TaskState(
        task_id="t-t1",
        task="Telemetry task",
        phase=Phase.EXECUTE,
        iteration=5,
        model_calls=12,
        tool_calls=25,
    )
    state.token_usage.total_tokens = 5000

    builder = ContextBuilder()
    cfg_disabled = ContextConfig(include_telemetry=False)
    bundle = builder.build(state, config=cfg_disabled)

    assert bundle.iteration_count == 0
    assert bundle.model_call_count == 0
    assert bundle.tool_call_count == 0
    assert bundle.token_usage.total_tokens == 0
    assert bundle.include_telemetry is False
    rendered = bundle.render_text()
    assert "## EFFICIENCY" not in rendered
    assert "Iteration: 5" not in rendered
    assert "5000 total" not in rendered


def test_disabling_repeated_failures_removes_repeated_failure_content() -> None:
    """Verify include_repeated_failures=False omits repeated failures and section."""
    state = TaskState(
        task_id="t-f1",
        task="Repeated failure task",
        phase=Phase.RECOVER,
        failures=[
            Failure(
                error_signature="ConnectionError",
                summary="Connection timeout after 30s",
                action="call db",
                occurrence_count=3,
            )
        ],
    )

    builder = ContextBuilder()
    cfg_disabled = ContextConfig(include_repeated_failures=False)
    bundle = builder.build(state, config=cfg_disabled)

    assert bundle.repeated_failures == []
    assert bundle.include_repeated_failures is False
    rendered = bundle.render_text()
    assert "## REPEATED FAILURES" not in rendered
    assert "Connection timeout after 30s" not in rendered


def test_disabling_failed_attempts_removes_failed_attempt_content() -> None:
    """Verify include_failed_attempts=False excludes failed attempts and section."""
    state = TaskState(
        task_id="t-a1",
        task="Attempt task",
        phase=Phase.EXECUTE,
        attempts=[
            Attempt(id="att-1", action="patch auth.py", success=False, result="failed with ImportError"),
            Attempt(id="att-2", action="fix import", success=True, result="import fixed"),
        ],
    )

    builder = ContextBuilder()
    cfg_disabled = ContextConfig(include_failed_attempts=False)
    bundle = builder.build(state, config=cfg_disabled)

    assert bundle.failed_attempts == []
    assert bundle.include_failed_attempts is False
    assert len(bundle.recent_attempts) == 1
    rendered = bundle.render_text()
    assert "## FAILED ATTEMPTS" not in rendered
    assert "failed with ImportError" not in rendered
    assert "## RECENT SUCCESSFUL ATTEMPTS" in rendered


def test_repository_overview_appears_only_when_enabled_and_index_exists() -> None:
    """Verify repository overview renders only when include_repository_overview=True and RepositoryIndex is present."""
    repo = _build_test_repo_index()
    state = TaskState(task_id="t-ro1", task="Repo task", phase=Phase.PLAN)
    builder = ContextBuilder(repository_index=repo)

    # 1. Enabled with repo -> present
    cfg_enabled = ContextConfig(include_repository_overview=True)
    bundle_enabled = builder.build(state, config=cfg_enabled)
    assert bundle_enabled.repository_overview is not None
    assert "## REPOSITORY" in bundle_enabled.render_text()
    assert "Source Directories: src" in bundle_enabled.render_text()
    assert "Test Directories: tests" in bundle_enabled.render_text()
    assert "Important Files: pyproject.toml" in bundle_enabled.render_text()

    # 2. Disabled with repo -> absent
    cfg_disabled = ContextConfig(include_repository_overview=False)
    bundle_disabled = builder.build(state, config=cfg_disabled)
    assert bundle_disabled.repository_overview is None
    assert "## REPOSITORY" not in bundle_disabled.render_text()

    # 3. Enabled but repo is None -> absent
    builder_none = ContextBuilder(repository_index=None)
    bundle_no_repo = builder_none.build(state, config=cfg_enabled)
    assert bundle_no_repo.repository_overview is None
    assert "## REPOSITORY" not in bundle_no_repo.render_text()


def test_symbol_context_appears_only_when_enabled() -> None:
    """Verify symbol context appears only when include_symbols=True and index is present."""
    repo = _build_test_repo_index()
    state = TaskState(
        task_id="t-sym1",
        task="Symbol task",
        phase=Phase.EXECUTE,
        relevant_files=["src/main.py"],
    )
    builder = ContextBuilder(repository_index=repo)

    # Enabled
    cfg_enabled = ContextConfig(include_symbols=True, repository_scope=RepositoryScope.FOCUSED)
    bundle_enabled = builder.build(state, config=cfg_enabled)
    assert len(bundle_enabled.repository_symbols) > 0
    rendered_enabled = bundle_enabled.render_text()
    assert "## REPOSITORY SYMBOLS" in rendered_enabled
    assert "src/main.py" in rendered_enabled
    assert "class: MainApp" in rendered_enabled
    assert "methods: run" in rendered_enabled

    # Disabled
    cfg_disabled = ContextConfig(include_symbols=False)
    bundle_disabled = builder.build(state, config=cfg_disabled)
    assert len(bundle_disabled.repository_symbols) == 0
    assert "## REPOSITORY SYMBOLS" not in bundle_disabled.render_text()


def test_local_imports_appear_only_when_enabled() -> None:
    """Verify local imports appear only when include_local_imports=True."""
    repo = _build_test_repo_index()
    state = TaskState(
        task_id="t-loc1",
        task="Local import task",
        phase=Phase.EXECUTE,
        relevant_files=["src/main.py"],
    )
    builder = ContextBuilder(repository_index=repo)

    # Enabled
    cfg_enabled = ContextConfig(include_local_imports=True)
    bundle_enabled = builder.build(state, config=cfg_enabled)
    assert len(bundle_enabled.repository_local_imports) > 0
    rendered_enabled = bundle_enabled.render_text()
    assert "## LOCAL IMPORTS" in rendered_enabled
    assert "src/main.py" in rendered_enabled
    assert "- src/helper.py" in rendered_enabled

    # Disabled
    cfg_disabled = ContextConfig(include_local_imports=False)
    bundle_disabled = builder.build(state, config=cfg_disabled)
    assert len(bundle_disabled.repository_local_imports) == 0
    assert "## LOCAL IMPORTS" not in bundle_disabled.render_text()


def test_no_absolute_repository_path_leaks_into_rendered_context() -> None:
    """Verify absolute root path of repository index never leaks into rendered model-facing text."""
    secret_root = "/Users/secret_agent_007/top_secret/harness_workspace"
    repo = _build_test_repo_index(root_dir=secret_root)
    state = TaskState(
        task_id="t-leak1",
        task="Check leaks",
        phase=Phase.PLAN,
        relevant_files=["src/main.py"],
        touched_files=["src/helper.py"],
    )

    builder = ContextBuilder(repository_index=repo)
    cfg = ContextConfig(
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        repository_scope=RepositoryScope.BROAD,
    )
    bundle = builder.build(state, config=cfg)
    rendered = bundle.render_text()

    assert secret_root not in rendered
    assert "secret_agent_007" not in rendered
    assert "top_secret" not in rendered


def test_absent_repository_index_handled_cleanly() -> None:
    """Verify ContextBuilder handles absent RepositoryIndex gracefully without crashing."""
    state = TaskState(task_id="t-abs1", task="Absent repo task", phase=Phase.PLAN)
    builder = ContextBuilder(
        config=ContextConfig(
            include_repository_overview=True,
            include_symbols=True,
            include_local_imports=True,
        ),
        repository_index=None,
    )

    bundle = builder.build(state)
    assert bundle.repository_overview is None
    assert bundle.repository_symbols == []
    assert bundle.repository_local_imports == []

    rendered = bundle.render_text()
    assert "## REPOSITORY" not in rendered
    assert "## REPOSITORY SYMBOLS" not in rendered
    assert "## LOCAL IMPORTS" not in rendered


def test_minimal_focused_broad_scopes_progressive_breadth() -> None:
    """
    Verify MINIMAL, FOCUSED, and BROAD repository scopes produce progressively broader structural context.
    - MINIMAL: only symbols for relevant/touched files.
    - FOCUSED: symbols for relevant/touched files + immediate local import neighbors.
    - BROAD: repository overview + broader symbol eligibility (including other repo files).
    """
    repo = _build_test_repo_index()
    # Relevant file is only src/main.py. Local import neighbor is src/helper.py. Broader repo file is src/extra.py.
    state = TaskState(
        task_id="t-scope1",
        task="Test progressive scopes",
        phase=Phase.EXECUTE,
        relevant_files=["src/main.py"],
    )
    builder = ContextBuilder(repository_index=repo)

    # 1. MINIMAL
    cfg_min = ContextConfig(
        include_symbols=True,
        include_local_imports=True,
        include_repository_overview=False,
        repository_scope=RepositoryScope.MINIMAL,
    )
    bundle_min = builder.build(state, config=cfg_min)
    symbols_min = "\n".join(bundle_min.repository_symbols)
    assert "src/main.py" in symbols_min
    assert "src/helper.py" not in symbols_min
    assert "src/extra.py" not in symbols_min
    assert bundle_min.repository_overview is None

    # 2. FOCUSED
    cfg_foc = ContextConfig(
        include_symbols=True,
        include_local_imports=True,
        include_repository_overview=False,
        repository_scope=RepositoryScope.FOCUSED,
    )
    bundle_foc = builder.build(state, config=cfg_foc)
    symbols_foc = "\n".join(bundle_foc.repository_symbols)
    assert "src/main.py" in symbols_foc
    assert "src/helper.py" in symbols_foc  # neighbor included
    assert "src/extra.py" not in symbols_foc
    assert len(bundle_foc.repository_symbols) > len(bundle_min.repository_symbols)

    # 3. BROAD
    cfg_broad = ContextConfig(
        include_symbols=True,
        include_local_imports=True,
        include_repository_overview=True,
        repository_scope=RepositoryScope.BROAD,
    )
    bundle_broad = builder.build(state, config=cfg_broad)
    symbols_broad = "\n".join(bundle_broad.repository_symbols)
    assert "src/main.py" in symbols_broad
    assert "src/helper.py" in symbols_broad
    assert "src/extra.py" in symbols_broad  # broader repo file included
    assert bundle_broad.repository_overview is not None
    assert len(bundle_broad.repository_symbols) > len(bundle_foc.repository_symbols)

    # Verify overall text length strictly increases: MINIMAL < FOCUSED < BROAD
    text_min = bundle_min.render_text()
    text_foc = bundle_foc.render_text()
    text_broad = bundle_broad.render_text()
    assert len(text_min) < len(text_foc) < len(text_broad)


def test_task_state_remains_unmodified() -> None:
    """Verify building context with repository integration never mutates TaskState."""
    repo = _build_test_repo_index()
    state = TaskState(
        task_id="t-unmod1",
        task="State immutability check",
        phase=Phase.EXECUTE,
        relevant_files=["src/main.py"],
        touched_files=["src/helper.py"],
        attempts=[Attempt(id="att-1", action="action", success=True)],
        failures=[Failure(error_signature="Err", summary="sum", action="act", occurrence_count=2)],
        verification=VerificationSnapshot(success=True, summary="passed"),
        current_errors=["Err"],
    )
    initial_dict = state.to_dict()

    builder = ContextBuilder(repository_index=repo)
    cfg = ContextConfig(
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        include_failed_attempts=True,
        include_repeated_failures=True,
        include_verification=True,
        include_telemetry=True,
        repository_scope=RepositoryScope.BROAD,
    )
    _ = builder.build(state, config=cfg)

    assert state.to_dict() == initial_dict


def test_deterministic_repeated_builds_identical_output() -> None:
    """Verify repeated builds produce identical output."""
    repo = _build_test_repo_index()
    state = TaskState(
        task_id="t-det1",
        task="Deterministic test",
        phase=Phase.PLAN,
        relevant_files=["src/main.py"],
    )
    builder = ContextBuilder(repository_index=repo)
    cfg = ContextConfig(
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        repository_scope=RepositoryScope.BROAD,
    )

    bundle1 = builder.build(state, config=cfg)
    bundle2 = builder.build(state, config=cfg)
    assert bundle1.render_text() == bundle2.render_text()
    assert bundle1.to_dict() == bundle2.to_dict()


def test_end_to_end_pipeline_integration(tmp_path: Path) -> None:
    """
    Integration test:
    TaskState + RepositoryIndex + AdaptiveContextPolicy
    -> ContextProfile
    -> ContextConfig
    -> ContextBuilder
    -> ContextBundle.render_text()

    Verify that B4's qualitative decisions visibly change the rendered output across phases.
    """
    repo = _build_test_repo_index()
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task(task="Implement secure API authentication", task_id="task_e2e_p1p2")
    manager.add_relevant_file("src/main.py")

    policy = AdaptiveContextPolicy()
    builder = ContextBuilder(repository_index=repo)

    # 1. PLAN Phase: Policy generates BROAD scope, overview enabled, symbols enabled, verification disabled
    prof_plan = policy.evaluate(manager, repo_index=repo)
    assert prof_plan.phase == "PLAN"
    assert prof_plan.repository_scope == RepositoryScope.BROAD
    assert prof_plan.include_repository_overview is True
    assert prof_plan.include_verification is False

    cfg_plan = prof_plan.to_context_config()
    bundle_plan = builder.build(manager, config=cfg_plan)
    rendered_plan = bundle_plan.render_text()

    assert "## REPOSITORY" in rendered_plan
    assert "Source Directories: src" in rendered_plan
    assert "## REPOSITORY SYMBOLS" in rendered_plan
    assert "class: MainApp" in rendered_plan
    assert "## VERIFICATION" not in rendered_plan
    assert "## FAILED ATTEMPTS" not in rendered_plan

    # 2. EXECUTE Phase: Focused scope, no repository overview, successful attempt recorded
    manager.set_phase(Phase.EXECUTE)
    manager.update_hypothesis("Use HMAC sha256 authentication")
    manager.record_attempt(
        Attempt(id="att-1", hypothesis="Add HMAC", action="edit src/main.py", success=True)
    )

    prof_exec = policy.evaluate(manager, repo_index=repo)
    assert prof_exec.phase == "EXECUTE"
    assert prof_exec.repository_scope == RepositoryScope.FOCUSED
    assert prof_exec.include_repository_overview is False

    cfg_exec = prof_exec.to_context_config()
    bundle_exec = builder.build(manager, config=cfg_exec)
    rendered_exec = bundle_exec.render_text()

    assert "## REPOSITORY\n" not in rendered_exec
    assert "## RECENT SUCCESSFUL ATTEMPTS" in rendered_exec
    assert "Attempt att-1" in rendered_exec
    assert "## VERIFICATION" not in rendered_exec

    # 3. VERIFY Phase (with failure): Verification result surfaced
    manager.set_phase(Phase.VERIFY)
    manager.set_verification(
        VerificationSnapshot(success=False, summary="TestAuthHMAC failed: Invalid signature")
    )
    manager.set_current_errors(["SignatureVerificationError: key mismatch"])

    prof_verify = policy.evaluate(manager, repo_index=repo)
    assert prof_verify.phase == "VERIFY"
    assert prof_verify.include_verification is True

    cfg_verify = prof_verify.to_context_config()
    bundle_verify = builder.build(manager, config=cfg_verify)
    rendered_verify = bundle_verify.render_text()

    assert "## VERIFICATION" in rendered_verify
    assert "Success: False" in rendered_verify
    assert "TestAuthHMAC failed: Invalid signature" in rendered_verify
    assert "## CURRENT ERRORS" in rendered_verify
    assert "SignatureVerificationError: key mismatch" in rendered_verify
    assert "## REPOSITORY\n" not in rendered_verify

    # 4. RECOVER Phase (High Pressure): Multiple failures recorded, policy expands to BROAD
    manager.set_phase(Phase.RECOVER)
    manager.record_failure(
        Failure(error_signature="SignatureVerificationError", summary="key mismatch", action="run test")
    )
    manager.record_failure(
        Failure(error_signature="SignatureVerificationError", summary="key mismatch", action="retry test")
    )
    manager.record_attempt(
        Attempt(id="att-2", hypothesis="Retry HMAC", action="run test", success=False)
    )
    manager.record_attempt(
        Attempt(id="att-3", hypothesis="Retry HMAC with new key", action="run test", success=False)
    )

    prof_recover = policy.evaluate(manager, repo_index=repo)
    assert prof_recover.phase == "RECOVER"
    assert prof_recover.pressure_level == PressureLevel.HIGH
    assert prof_recover.repository_scope == RepositoryScope.BROAD
    assert prof_recover.include_repeated_failures is True
    assert prof_recover.include_local_imports is True
    assert prof_recover.include_repository_overview is True
    assert prof_recover.max_observations >= 12
    assert prof_recover.max_attempts >= 8

    cfg_recover = prof_recover.to_context_config()
    bundle_recover = builder.build(manager, config=cfg_recover)
    rendered_recover = bundle_recover.render_text()

    # Visibly expanded output
    assert "## REPOSITORY" in rendered_recover
    assert "## REPOSITORY SYMBOLS" in rendered_recover
    assert "## LOCAL IMPORTS" in rendered_recover
    assert "## REPEATED FAILURES" in rendered_recover
    assert "[x2] SignatureVerificationError" in rendered_recover
    assert "## FAILED ATTEMPTS" in rendered_recover
    assert "Attempt att-2" in rendered_recover
    assert "Attempt att-3" in rendered_recover
    assert "## VERIFICATION" in rendered_recover
