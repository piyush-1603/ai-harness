"""Tests for Person B Pipeline Repair P4: Failure Resolution and Pressure Contraction."""

from pathlib import Path
import pytest

from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.policy import (
    AdaptiveContextPolicy,
    PressureLevel,
    RepositoryScope,
)
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Failure,
    Phase,
    TaskState,
    TaskStatus,
    VerificationSnapshot,
)


def test_new_failures_start_unresolved() -> None:
    """Verify new failures default to resolved=False and resolved_at=None."""
    failure = Failure(
        error_signature="DatabaseTimeout",
        summary="DB query timed out after 30s",
        action="fetch_records",
    )
    assert failure.resolved is False
    assert failure.resolved_at is None

    # Test serialization / deserialization round-trip
    data = failure.to_dict()
    assert data["resolved"] is False
    assert data["resolved_at"] is None

    reconstructed = Failure.from_dict(data)
    assert reconstructed.resolved is False
    assert reconstructed.resolved_at is None

    # Backward compatibility with older states lacking resolved fields
    older_data = {
        "error_signature": "OldError",
        "summary": "Old error without resolution",
        "action": "run",
    }
    loaded = Failure.from_dict(older_data)
    assert loaded.resolved is False
    assert loaded.resolved_at is None


def test_resolving_failure_preserves_it_and_populates_resolved_at(tmp_path: Path) -> None:
    """Verify resolve_failure sets resolved=True and resolved_at without deleting the failure."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-res-1", "Test failure resolution")

    manager.add_failure(
        Failure(error_signature="AuthTokenExpired", summary="JWT expired", action="validate_token")
    )
    state = manager.get_state()
    assert len(state.failures) == 1
    assert state.failures[0].resolved is False

    # Resolve failure
    success = manager.resolve_failure("AuthTokenExpired")
    assert success is True

    # State still retains the failure
    assert len(state.failures) == 1
    f = state.failures[0]
    assert f.error_signature == "AuthTokenExpired"
    assert f.resolved is True
    assert f.resolved_at is not None
    assert isinstance(f.resolved_at, str)

    # Calling resolve again returns False (already resolved)
    assert manager.resolve_failure("AuthTokenExpired") is False
    # Nonexistent error returns False
    assert manager.resolve_failure("Nonexistent") is False


def test_resolve_all_failures(tmp_path: Path) -> None:
    """Verify resolve_all_failures marks all currently active failures as resolved."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-res-all", "Test resolve all")

    manager.add_failure(Failure(error_signature="ErrA", summary="A", action="actA"))
    manager.add_failure(Failure(error_signature="ErrB", summary="B", action="actB"))

    resolved_count = manager.resolve_all_failures()
    assert resolved_count == 2

    state = manager.get_state()
    assert len(state.failures) == 2
    assert all(f.resolved for f in state.failures)
    assert all(f.resolved_at is not None for f in state.failures)

    # Resolving again resolves 0
    assert manager.resolve_all_failures() == 0


def test_resolved_failure_survives_save_and_reload(tmp_path: Path) -> None:
    """Verify failure resolution state survives persistence and reload."""
    storage_dir = tmp_path / ".harness"
    manager1 = MemoryManager(base_dir=storage_dir)
    manager1.initialize_task("task-persist-res", "Test persistence")

    manager1.add_failure(Failure(error_signature="RedisConnErr", summary="timeout", action="connect"))
    manager1.resolve_failure("RedisConnErr")

    saved_resolved_at = manager1.get_state().failures[0].resolved_at
    assert saved_resolved_at is not None

    # Load in fresh manager instance
    manager2 = MemoryManager(base_dir=storage_dir)
    state2 = manager2.load("task-persist-res")
    assert len(state2.failures) == 1
    f_loaded = state2.failures[0]
    assert f_loaded.error_signature == "RedisConnErr"
    assert f_loaded.resolved is True
    assert f_loaded.resolved_at == saved_resolved_at


def test_resolved_failures_contribute_zero_repeated_failure_pressure() -> None:
    """Verify that resolved failures, even if occurring repeatedly, contribute 0 to recovery pressure."""
    state = TaskState(
        task_id="t-press-res",
        task="Test pressure with resolved failures",
        phase=Phase.EXECUTE,
        failures=[
            Failure(
                error_signature="StripeWebhookError",
                summary="Signature mismatch",
                action="verify_webhook",
                occurrence_count=5,  # strongly repeated
                resolved=True,
                resolved_at="2026-09-26T12:00:00+00:00",
            )
        ],
    )

    policy = AdaptiveContextPolicy()
    score, level, reasons = policy.calculate_pressure(state)

    # Contributes 0 repeated failure points and 0 strongly-repeated bonus
    assert score == 0
    assert level == PressureLevel.LOW
    assert any("normal execution" in r for r in reasons)


def test_unresolved_repeated_failures_raise_pressure() -> None:
    """Verify unresolved repeated failures contribute points and strongly repeated bonus."""
    state = TaskState(
        task_id="t-press-act",
        task="Test pressure with active failures",
        phase=Phase.EXECUTE,
        failures=[
            Failure(
                error_signature="StripeWebhookError",
                summary="Signature mismatch",
                action="verify_webhook",
                occurrence_count=4,  # strongly repeated threshold
                resolved=False,
            )
        ],
    )

    policy = AdaptiveContextPolicy()
    score, level, reasons = policy.calculate_pressure(state)

    # 1 base point + 1 strongly repeated bonus = 2 points
    assert score == 2
    assert any("repeated failure StripeWebhookError x4 (+2)" in r for r in reasons)


def test_successful_verification_resolves_active_failures(tmp_path: Path) -> None:
    """Verify that setting successful verification automatically resolves active failures."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-verif-res", "Test verif resolution")

    manager.add_failure(Failure(error_signature="SyntaxErr", summary="colon missing", action="edit"))
    manager.add_failure(Failure(error_signature="TypeErr", summary="wrong type", action="edit"))

    assert all(not f.resolved for f in manager.get_state().failures)

    # Set successful verification
    manager.set_verification(
        VerificationSnapshot(success=True, summary="All 10 tests passed successfully")
    )

    state = manager.get_state()
    assert len(state.failures) == 2
    assert all(f.resolved for f in state.failures)
    assert all(f.resolved_at is not None for f in state.failures)


def test_failed_verification_does_not_resolve_failures(tmp_path: Path) -> None:
    """Verify that failed verification does NOT resolve active failures."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-verif-fail", "Test verif fail")

    manager.add_failure(Failure(error_signature="AssertionError", summary="bad output", action="test"))

    manager.set_verification(
        VerificationSnapshot(success=False, summary="1 test failed with AssertionError")
    )

    state = manager.get_state()
    assert len(state.failures) == 1
    assert state.failures[0].resolved is False
    assert state.failures[0].resolved_at is None


def test_reoccurrence_of_resolved_signature_reopens_existing_failure(tmp_path: Path) -> None:
    """Verify that a resolved failure reopens if the same error signature occurs again."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-reopen", "Test reopening")

    manager.add_failure(Failure(error_signature="DeadlockError", summary="Deadlock 1", action="txn 1"))
    state = manager.get_state()
    assert state.failures[0].occurrence_count == 1
    assert state.failures[0].resolved is False

    # Resolve it
    manager.resolve_failure("DeadlockError")
    assert state.failures[0].resolved is True
    assert state.failures[0].resolved_at is not None

    # Same error occurs again
    manager.add_failure(Failure(error_signature="DeadlockError", summary="Deadlock 2", action="txn 2"))

    # Must NOT create a duplicate record
    assert len(state.failures) == 1
    f = state.failures[0]
    assert f.error_signature == "DeadlockError"
    # Reopened: resolved=False, resolved_at=None, occurrence_count incremented
    assert f.resolved is False
    assert f.resolved_at is None
    assert f.occurrence_count == 2
    assert f.summary == "Deadlock 2"


def test_reopened_failure_persists_across_save_reload(tmp_path: Path) -> None:
    """Verify reopened failure state persists across save/reload."""
    storage_dir = tmp_path / ".harness"
    manager1 = MemoryManager(base_dir=storage_dir)
    manager1.initialize_task("task-persist-reopen", "Test persist reopen")

    manager1.add_failure(Failure(error_signature="E1", summary="sum1", action="act1"))
    manager1.resolve_failure("E1")
    manager1.add_failure(Failure(error_signature="E1", summary="sum2", action="act2"))

    manager2 = MemoryManager(base_dir=storage_dir)
    state2 = manager2.load("task-persist-reopen")
    assert len(state2.failures) == 1
    assert state2.failures[0].resolved is False
    assert state2.failures[0].occurrence_count == 2


def test_pressure_contraction_from_high_to_low(tmp_path: Path) -> None:
    """
    Primary acceptance criterion:
    Verification fails, repeated failures occur, consecutive attempts fail -> HIGH pressure.
    Then successful attempt occurs, verification succeeds -> pressure contracts back to LOW.
    """
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-contraction", "Contract pressure from HIGH to LOW")
    policy = AdaptiveContextPolicy()

    # 1. Elevate to HIGH pressure
    manager.set_phase(Phase.RECOVER)
    manager.add_failure(Failure(error_signature="TimeoutErr", summary="timeout", action="query"))
    manager.add_failure(Failure(error_signature="TimeoutErr", summary="timeout", action="query"))
    manager.add_failure(Failure(error_signature="TimeoutErr", summary="timeout", action="query"))
    manager.add_failure(Failure(error_signature="TimeoutErr", summary="timeout", action="query"))  # count=4 -> +2 points
    manager.record_attempt(Attempt(id="att-f1", action="retry 1", success=False))
    manager.record_attempt(Attempt(id="att-f2", action="retry 2", success=False))
    manager.record_attempt(Attempt(id="att-f3", action="retry 3", success=False))  # 3 streak -> +3 points
    manager.set_current_errors(["TimeoutErr: DB connection timed out"])  # +1 point
    manager.set_verification(VerificationSnapshot(success=False, summary="Verification failed"))  # +2 points

    # Total score = 2 + 3 + 1 + 2 = 8 -> HIGH
    score_high, level_high, _ = policy.calculate_pressure(manager)
    assert score_high == 8
    assert level_high == PressureLevel.HIGH

    # 2. Recovery succeeds: successful attempt, verification passes, current errors cleared
    manager.record_attempt(Attempt(id="att-s1", action="increase pool size and timeout", success=True))
    manager.set_verification(VerificationSnapshot(success=True, summary="All stress tests passed"))
    manager.set_current_errors([])

    # Total score = 0 (streak reset, verification passed, failures resolved, errors empty) -> LOW!
    score_low, level_low, _ = policy.calculate_pressure(manager)
    assert score_low == 0
    assert level_low == PressureLevel.LOW

    # Failure history is NOT deleted
    state = manager.get_state()
    assert len(state.failures) == 1
    assert state.failures[0].error_signature == "TimeoutErr"
    assert state.failures[0].occurrence_count == 4
    assert state.failures[0].resolved is True


def test_lifecycle_regression_plan_execute_recover_high_then_recover_low(tmp_path: Path) -> None:
    """
    Simulate full lifecycle:
    PLAN -> EXECUTE -> repeated failures -> VERIFY failure -> RECOVER/HIGH
    -> successful attempt -> VERIFY success -> PLAN/EXECUTE LOW
    Verifying that context profile and rendered output expand during recovery and contract after resolution.
    """
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-lifecycle-contraction", "Lifecycle contraction test")
    policy = AdaptiveContextPolicy()
    builder = ContextBuilder()

    # 1. PLAN Phase
    prof_plan = policy.evaluate(manager)
    assert prof_plan.phase == "PLAN"
    assert prof_plan.pressure_level == PressureLevel.LOW

    # 2. EXECUTE Phase
    manager.set_phase(Phase.EXECUTE)
    manager.record_attempt(Attempt(id="att-1", action="initial code edit", success=True))
    prof_exec = policy.evaluate(manager)
    assert prof_exec.phase == "EXECUTE"
    assert prof_exec.pressure_level == PressureLevel.LOW

    # 3. VERIFY Phase (fails)
    manager.set_phase(Phase.VERIFY)
    manager.add_failure(Failure(error_signature="ValidationError", summary="schema mismatch", action="validate"))
    manager.add_failure(Failure(error_signature="ValidationError", summary="schema mismatch", action="validate"))
    manager.record_attempt(Attempt(id="att-2", action="run verify", success=False))
    manager.set_current_errors(["ValidationError: schema mismatch"])
    manager.set_verification(VerificationSnapshot(success=False, summary="1 schema validation test failed"))

    # 4. RECOVER Phase (HIGH pressure)
    manager.set_phase(Phase.RECOVER)
    manager.add_failure(Failure(error_signature="ValidationError", summary="schema mismatch", action="validate"))
    manager.add_failure(Failure(error_signature="ValidationError", summary="schema mismatch", action="validate"))  # count=4
    manager.record_attempt(Attempt(id="att-3", action="retry 1", success=False))
    manager.record_attempt(Attempt(id="att-4", action="retry 2", success=False))  # 3 consecutive failed attempts (att-2, 3, 4)

    prof_recover = policy.evaluate(manager)
    assert prof_recover.phase == "RECOVER"
    assert prof_recover.pressure_level == PressureLevel.HIGH
    assert prof_recover.pressure_score >= 6
    assert prof_recover.include_repeated_failures is True

    bundle_high = builder.build(manager, config=prof_recover.to_context_config())
    rendered_high = bundle_high.render_text()
    assert "## REPEATED FAILURES" in rendered_high
    assert "[x4] ValidationError" in rendered_high

    # 5. Fix succeeds: successful attempt, verification passes, errors cleared
    manager.record_attempt(Attempt(id="att-5", action="fix schema definition", success=True))
    manager.set_verification(VerificationSnapshot(success=True, summary="All validation tests pass"))
    manager.set_current_errors([])

    # 6. Return to EXECUTE: pressure must contract to LOW!
    manager.set_phase(Phase.EXECUTE)
    prof_contracted = policy.evaluate(manager)
    assert prof_contracted.phase == "EXECUTE"
    assert prof_contracted.pressure_score == 0
    assert prof_contracted.pressure_level == PressureLevel.LOW

    # Rendered context in EXECUTE reflects normal low-pressure context (no verification or repeated failures needed)
    bundle_contracted = builder.build(manager, config=prof_contracted.to_context_config())
    rendered_contracted = bundle_contracted.render_text()
    assert "## VERIFICATION" not in rendered_contracted
    assert "## REPEATED FAILURES" not in rendered_contracted

    # When auditing repeated failures, the resolved failure is marked (resolved)
    bundle_audit = builder.build(manager, config=ContextConfig(include_repeated_failures=True))
    assert "[x4] ValidationError" in bundle_audit.render_text()
    assert "(resolved)" in bundle_audit.render_text()

    # And in VERIFY phase, successful verification is surfaced with LOW pressure
    manager.set_phase(Phase.VERIFY)
    prof_verify_succ = policy.evaluate(manager)
    assert prof_verify_succ.pressure_level == PressureLevel.LOW
    bundle_verify_succ = builder.build(manager, config=prof_verify_succ.to_context_config())
    assert "Success: True" in bundle_verify_succ.render_text()
