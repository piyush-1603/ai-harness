"""Unit and integration tests for Phase B4 Deterministic Adaptive Context Policy."""

from pathlib import Path
import pytest

from src.context.config import ContextConfig
from src.context.policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextProfile,
    PressureLevel,
    RepositoryScope,
)
from src.context.scanner import RepositoryIndex, RepositoryScanner
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
    VerificationResult,
)


def _make_dummy_repo_index() -> RepositoryIndex:
    """Create a populated RepositoryIndex for testing eligibility."""
    return RepositoryIndex(
        root_dir="/repo",
        discovered_files=["src/app.py", "src/utils.py", "tests/test_app.py"],
        discovered_directories=["src", "tests"],
        file_count=3,
        detected_languages={"Python": 3},
        detected_extensions={".py": 3},
        important_files=["pyproject.toml"],
        test_files=["tests/test_app.py"],
        test_directories=["tests"],
        source_files=["src/app.py", "src/utils.py"],
        source_directories=["src"],
        documentation_files=["README.md"],
        file_roles={
            "src/app.py": "SOURCE",
            "src/utils.py": "SOURCE",
            "tests/test_app.py": "TEST",
        },
        file_symbols={
            "src/app.py": FileSymbols(
                path="src/app.py",
                language="Python",
                symbols=[SymbolRecord(name="main", kind="function", file="src/app.py")],
                imports=["src.utils"],
                local_imports=["src/utils.py"],
            ),
            "src/utils.py": FileSymbols(
                path="src/utils.py",
                language="Python",
                symbols=[SymbolRecord(name="helper", kind="function", file="src/utils.py")],
                imports=[],
                local_imports=[],
            ),
        },
    )


def test_plan_profile() -> None:
    """Verify PLAN phase profile favors broad repository orientation with small execution history."""
    state = TaskState(task_id="t1", task="Plan task", phase=Phase.PLAN)
    repo = _make_dummy_repo_index()

    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo_index=repo)

    assert profile.phase == "PLAN"
    assert profile.repository_scope == RepositoryScope.BROAD
    assert profile.include_repository_overview is True
    assert profile.include_symbols is True
    assert profile.include_local_imports is True
    assert profile.include_failed_attempts is False
    assert profile.include_repeated_failures is False
    assert profile.include_verification is False
    assert profile.max_observations <= profile.max_discoveries


def test_execute_profile() -> None:
    """Verify EXECUTE phase profile favors focused implementation context."""
    state = TaskState(task_id="t1", task="Execute task", phase=Phase.EXECUTE)
    repo = _make_dummy_repo_index()

    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo_index=repo)

    assert profile.phase == "EXECUTE"
    assert profile.repository_scope == RepositoryScope.FOCUSED
    assert profile.include_symbols is True
    assert profile.include_successful_attempts is True
    assert profile.include_telemetry is True


def test_verify_profile() -> None:
    """Verify VERIFY phase profile favors evidence with minimal/focused repository context."""
    state = TaskState(
        task_id="t1",
        task="Verify task",
        phase=Phase.VERIFY,
        verification=VerificationResult(success=True, summary="Tests passed"),
    )
    repo = _make_dummy_repo_index()

    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo_index=repo)

    assert profile.phase == "VERIFY"
    assert profile.repository_scope == RepositoryScope.MINIMAL
    assert profile.include_verification is True
    assert profile.include_repository_overview is False
    assert profile.include_local_imports is False


def test_recover_profile() -> None:
    """Verify RECOVER phase profile favors failure analysis, repeated failures, and expanding context."""
    state = TaskState(
        task_id="t1",
        task="Recover task",
        phase=Phase.RECOVER,
        current_errors=["ConnectionError: timeout"],
        verification=VerificationResult(success=False, summary="3 tests failed"),
        failures=[Failure(error_signature="ConnectionError", summary="timed out", action="call", occurrence_count=2)],
    )
    repo = _make_dummy_repo_index()

    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo_index=repo)

    assert profile.phase == "RECOVER"
    assert profile.pressure_level in (PressureLevel.MEDIUM, PressureLevel.HIGH)
    assert profile.repository_scope == RepositoryScope.BROAD
    assert profile.include_repeated_failures is True
    assert profile.include_failed_attempts is True
    assert profile.include_local_imports is True
    assert profile.include_verification is True


def test_low_pressure() -> None:
    """Verify low pressure score and PressureLevel.LOW when no indicators are present."""
    state = TaskState(task_id="t1", task="Clean state")
    policy = AdaptiveContextPolicy()

    score, level, reasons = policy.calculate_pressure(state)
    assert score == 0
    assert level == PressureLevel.LOW
    assert any("normal execution" in r for r in reasons)


def test_medium_pressure() -> None:
    """Verify medium pressure classification when score is between medium and high threshold."""
    # Verification failure (+2) + 1 active error (+1) = 3 -> MEDIUM
    state = TaskState(
        task_id="t1",
        task="Medium pressure task",
        verification=VerificationResult(success=False, summary="tests failed"),
        current_errors=["AssertionError"],
    )
    policy = AdaptiveContextPolicy()

    score, level, reasons = policy.calculate_pressure(state)
    assert score == 3
    assert level == PressureLevel.MEDIUM
    assert any("verification failed" in r for r in reasons)
    assert any("active error" in r for r in reasons)


def test_high_pressure() -> None:
    """Verify high pressure classification when score exceeds high pressure threshold."""
    # Verification failure (+2) + 3 failed attempts (+3) + active error (+1) + repeated failure (+1) = 7 -> HIGH
    state = TaskState(
        task_id="t1",
        task="High pressure task",
        verification=VerificationResult(success=False, summary="failed"),
        attempts=[
            Attempt(id="a1", success=False, result="fail 1"),
            Attempt(id="a2", success=False, result="fail 2"),
            Attempt(id="a3", success=False, result="fail 3"),
        ],
        current_errors=["TokenExpiredError"],
        failures=[
            Failure(error_signature="TokenExpiredError", summary="token expired", action="auth", occurrence_count=3)
        ],
    )
    policy = AdaptiveContextPolicy()

    score, level, reasons = policy.calculate_pressure(state)
    assert score == 7
    assert level == PressureLevel.HIGH
    assert any("verification failed" in r for r in reasons)
    assert any("3 consecutive failed attempts" in r for r in reasons)
    assert any("active errors present" in r for r in reasons)
    assert any("repeated failure TokenExpiredError x3" in r for r in reasons)


def test_failed_verification_raising_pressure() -> None:
    """Verify that a failing verification result directly increases the pressure score."""
    state_ok = TaskState(
        task_id="t1",
        task="Task",
        verification=VerificationResult(success=True, summary="passed"),
    )
    state_fail = TaskState(
        task_id="t1",
        task="Task",
        verification=VerificationResult(success=False, summary="failed"),
    )
    policy = AdaptiveContextPolicy()

    score_ok, _, _ = policy.calculate_pressure(state_ok)
    score_fail, _, _ = policy.calculate_pressure(state_fail)

    assert score_ok == 0
    assert score_fail == 2


def test_consecutive_failed_attempts_raising_pressure() -> None:
    """Verify that consecutive failed attempts from the end increase pressure proportionally."""
    state = TaskState(
        task_id="t1",
        task="Task",
        attempts=[
            Attempt(id="a1", success=True),
            Attempt(id="a2", success=False),
            Attempt(id="a3", success=False),
        ],
    )
    policy = AdaptiveContextPolicy()
    score, _, reasons = policy.calculate_pressure(state)

    assert score == 2
    assert any("2 consecutive failed attempts" in r for r in reasons)


def test_successful_attempt_resetting_consecutive_failure_streak() -> None:
    """Verify that a recent successful attempt resets the consecutive failure streak."""
    state = TaskState(
        task_id="t1",
        task="Task",
        attempts=[
            Attempt(id="a1", success=False),
            Attempt(id="a2", success=False),
            Attempt(id="a3", success=True),  # latest attempt succeeded
        ],
    )
    policy = AdaptiveContextPolicy()
    score, level, reasons = policy.calculate_pressure(state)

    assert score == 0
    assert level == PressureLevel.LOW
    assert not any("consecutive failed" in r for r in reasons)


def test_active_errors_raising_pressure() -> None:
    """Verify active current errors contribute to the pressure score."""
    state = TaskState(
        task_id="t1",
        task="Task",
        current_errors=["RuntimeError: out of bounds"],
    )
    policy = AdaptiveContextPolicy()
    score, _, reasons = policy.calculate_pressure(state)

    assert score == 1
    assert any("active error" in r for r in reasons)


def test_repeated_failures_raising_pressure() -> None:
    """Verify repeated failure signatures increase pressure."""
    state = TaskState(
        task_id="t1",
        task="Task",
        failures=[
            Failure(error_signature="KeyError: id", summary="missing", action="lookup", occurrence_count=2),
        ],
    )
    policy = AdaptiveContextPolicy()
    score, _, reasons = policy.calculate_pressure(state)

    assert score == 1
    assert any("repeated failure KeyError: id x2 (+1)" in r for r in reasons)


def test_high_occurrence_repeated_failure_raising_pressure_further() -> None:
    """Verify that highly repeated failure occurrences earn the strongly repeated bonus."""
    state = TaskState(
        task_id="t1",
        task="Task",
        failures=[
            Failure(error_signature="KeyError: id", summary="missing", action="lookup", occurrence_count=4),
        ],
    )
    policy = AdaptiveContextPolicy()
    score, _, reasons = policy.calculate_pressure(state)

    # Base repeated (1) + bonus (1) = 2
    assert score == 2
    assert any("repeated failure KeyError: id x4 (+2)" in r for r in reasons)


def test_pressure_based_context_limits_monotonicity() -> None:
    """Verify that for each phase, HIGH context limits >= MEDIUM >= LOW."""
    policy = AdaptiveContextPolicy()
    cfg = policy.config

    for phase in ("PLAN", "EXECUTE", "VERIFY", "RECOVER"):
        low = cfg.limits[phase]["LOW"]
        med = cfg.limits[phase]["MEDIUM"]
        high = cfg.limits[phase]["HIGH"]

        # max_observations monotonic
        assert high["max_observations"] >= med["max_observations"] >= low["max_observations"]
        # max_attempts monotonic
        assert high["max_attempts"] >= med["max_attempts"] >= low["max_attempts"]
        # max_discoveries monotonic
        assert high["max_discoveries"] >= med["max_discoveries"] >= low["max_discoveries"]


def test_recover_high_broader_scope_than_execute_low() -> None:
    """Verify RECOVER at HIGH pressure has broader repository scope than EXECUTE at LOW pressure."""
    state_exec_low = TaskState(task_id="t1", task="Exec", phase=Phase.EXECUTE)
    state_rec_high = TaskState(
        task_id="t2",
        task="Rec",
        phase=Phase.RECOVER,
        verification=VerificationResult(success=False, summary="failed"),
        attempts=[Attempt(id=f"a{i}", success=False) for i in range(4)],
        current_errors=["Error1"],
    )
    repo = _make_dummy_repo_index()
    policy = AdaptiveContextPolicy()

    p_exec = policy.evaluate(state_exec_low, repo_index=repo)
    p_rec = policy.evaluate(state_rec_high, repo_index=repo)

    assert p_exec.repository_scope == RepositoryScope.FOCUSED
    assert p_rec.repository_scope == RepositoryScope.BROAD
    assert p_rec.max_observations > p_exec.max_observations
    assert p_rec.max_attempts > p_exec.max_attempts


def test_deterministic_profile_generation() -> None:
    """Verify repeated evaluations on identical TaskState produce identical profiles."""
    state = TaskState(
        task_id="t1",
        task="Task",
        phase=Phase.EXECUTE,
        attempts=[Attempt(id="a1", success=False)],
        current_errors=["SyntaxError"],
    )
    repo = _make_dummy_repo_index()
    policy = AdaptiveContextPolicy()

    prof1 = policy.evaluate(state, repo_index=repo)
    prof2 = policy.evaluate(state, repo_index=repo)

    assert prof1.to_dict() == prof2.to_dict()
    assert prof1.render_text() == prof2.render_text()
    assert str(prof1) == str(prof2)


def test_context_profile_to_context_config_conversion() -> None:
    """Verify ContextProfile.to_context_config() accurately maps parameters to B2 ContextConfig."""
    profile = ContextProfile(
        phase="RECOVER",
        pressure_level=PressureLevel.HIGH,
        pressure_score=7,
        repository_scope=RepositoryScope.BROAD,
        max_observations=16,
        max_attempts=10,
        max_discoveries=20,
        include_repository_overview=True,
        include_symbols=True,
        include_local_imports=True,
        include_successful_attempts=True,
        include_failed_attempts=True,
        include_repeated_failures=True,
        include_verification=True,
        include_telemetry=True,
        reasons=["failure"],
    )

    cfg = profile.to_context_config()
    assert isinstance(cfg, ContextConfig)
    assert cfg.max_observations == 16
    assert cfg.max_attempts == 10
    assert cfg.max_discoveries == 20
    assert cfg.include_successful_attempts is True
    assert cfg.include_raw_output is False


def test_task_state_is_never_mutated() -> None:
    """Verify policy evaluation never mutates the underlying TaskState."""
    state = TaskState(
        task_id="t1",
        task="Non-mutating task",
        phase=Phase.EXECUTE,
        plan=["step 1", "step 2"],
        attempts=[Attempt(id="a1", success=False)],
        current_errors=["Error"],
    )
    initial_snapshot = state.to_dict()

    policy = AdaptiveContextPolicy()
    _ = policy.evaluate(state, repo_index=_make_dummy_repo_index())

    assert state.to_dict() == initial_snapshot


def test_absence_of_repository_index_handled_cleanly() -> None:
    """Verify calling policy with repo_index=None handles missing repository cleanly without crashing."""
    state = TaskState(task_id="t1", task="Task", phase=Phase.PLAN)
    policy = AdaptiveContextPolicy()

    profile = policy.evaluate(state, repo_index=None)
    assert profile.include_repository_overview is False
    assert profile.include_symbols is False
    assert profile.include_local_imports is False


def test_context_profile_serialization() -> None:
    """Verify ContextProfile serialization and deserialization."""
    profile = ContextProfile(
        phase="EXECUTE",
        pressure_level=PressureLevel.MEDIUM,
        pressure_score=4,
        repository_scope=RepositoryScope.FOCUSED,
        max_observations=10,
        max_attempts=6,
        max_discoveries=12,
        include_repository_overview=False,
        include_symbols=True,
        include_local_imports=True,
        include_successful_attempts=True,
        include_failed_attempts=True,
        include_repeated_failures=True,
        include_verification=False,
        include_telemetry=True,
        reasons=["active errors present (+1)", "2 consecutive failed attempts (+2)"],
    )

    data = profile.to_dict()
    reloaded = ContextProfile.from_dict(data)

    assert profile == reloaded
    assert profile.to_json() == reloaded.to_json()


def test_integration_lifecycle_plan_execute_verify_recover(tmp_path: Path) -> None:
    """
    Integration test: simulate PLAN -> EXECUTE -> VERIFY(failure) -> RECOVER lifecycle
    and verify the profile adapts and expands context upon failure.
    """
    storage_dir = tmp_path / ".harness"
    manager = MemoryManager(base_dir=storage_dir)
    manager.initialize_task(task="Implement payment webhook handling", task_id="task_lifecycle_100")
    policy = AdaptiveContextPolicy()
    repo = _make_dummy_repo_index()

    # 1. Stage: PLAN
    p_plan = policy.evaluate(manager, repo_index=repo)
    assert p_plan.phase == "PLAN"
    assert p_plan.pressure_level == PressureLevel.LOW
    assert p_plan.repository_scope == RepositoryScope.BROAD
    assert p_plan.max_observations == 4
    assert p_plan.include_verification is False

    # 2. Stage: EXECUTE
    manager.set_phase(Phase.EXECUTE)
    manager.update_hypothesis("Webhook signature validation requires secret key")
    manager.record_attempt(
        Attempt(
            id="att-1",
            hypothesis="Add HMAC verification",
            action="edit webhook.py",
            success=True,
        )
    )
    p_exec = policy.evaluate(manager, repo_index=repo)
    assert p_exec.phase == "EXECUTE"
    assert p_exec.pressure_level == PressureLevel.LOW
    assert p_exec.repository_scope == RepositoryScope.FOCUSED
    assert p_exec.max_observations == 6
    assert p_exec.include_successful_attempts is True

    # 3. Stage: VERIFY (with failure)
    manager.set_phase(Phase.VERIFY)
    manager.set_verification(
        VerificationResult(
            success=False,
            summary="TestWebhookHMAC failed: Invalid signature",
        )
    )
    manager.set_current_errors(["SignatureVerificationError: key mismatch"])
    manager.record_attempt(
        Attempt(
            id="att-2",
            hypothesis="HMAC verification",
            action="run pytest",
            success=False,
        )
    )
    p_verify = policy.evaluate(manager, repo_index=repo)
    assert p_verify.phase == "VERIFY"
    # Verification failure (+2) + 1 failed attempt (+1) + active error (+1) = 4 -> MEDIUM
    assert p_verify.pressure_level == PressureLevel.MEDIUM
    assert p_verify.pressure_score == 4
    assert p_verify.include_verification is True

    # 4. Stage: RECOVER
    manager.set_phase(Phase.RECOVER)
    manager.record_failure(
        Failure(
            error_signature="SignatureVerificationError",
            summary="key mismatch",
            action="run test",
        )
    )
    manager.record_failure(
        Failure(
            error_signature="SignatureVerificationError",
            summary="key mismatch",
            action="retry test",
        )
    )
    # Repeated failure (+1) brings score to 5 -> MEDIUM (or if more attempts, HIGH)
    p_recover = policy.evaluate(manager, repo_index=repo)
    assert p_recover.phase == "RECOVER"
    assert p_recover.pressure_score >= 4
    assert p_recover.repository_scope == RepositoryScope.BROAD
    assert p_recover.include_repeated_failures is True
    assert p_recover.include_local_imports is True
    # Expanded context limits
    assert p_recover.max_observations >= 12
    assert p_recover.max_attempts >= 8
    assert p_recover.max_discoveries >= 16

    # Verify explainability string
    render = p_recover.render_text()
    assert "Phase: RECOVER" in render
    assert "Repository Scope: BROAD" in render
    assert "Context Policy:" in render
    assert "max observations: 12" in render or "max observations: 16" in render
