"""Tests for Person B Pipeline Repair P3: Independent Successful and Failed Attempt Windows."""

import pytest

from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.context.policy import (
    AdaptiveContextPolicy,
    PressureLevel,
    RepositoryScope,
)
from src.context.scanner import RepositoryIndex
from src.context.symbols import FileSymbols, SymbolRecord
from src.memory.models import (
    Attempt,
    Phase,
    TaskState,
    TaskStatus,
)


def _make_state() -> TaskState:
    """Create a minimal TaskState for attempt testing."""
    return TaskState(
        task_id="t-attempts",
        task="Test attempt windows",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
    )


def test_older_failed_attempt_remains_available_after_many_later_successes() -> None:
    """Requirement 1: An older failed attempt remains available even after more than max_attempts later successes."""
    state = _make_state()
    state.attempts.append(
        Attempt(id="f-critical", action="patch memory leak", success=False, result="SIGSEGV in worker")
    )
    for i in range(1, 6):
        state.attempts.append(
            Attempt(id=f"s{i}", action=f"step {i}", success=True, result=f"success {i}")
        )

    # max_attempts=3 (less than the 5 later successes)
    builder = ContextBuilder(ContextConfig(max_attempts=3))
    bundle = builder.build(state)

    # Failed attempt f-critical was NOT pushed out by later successes
    assert [a.id for a in bundle.failed_attempts] == ["f-critical"]
    assert [a.id for a in bundle.recent_attempts] == ["s3", "s4", "s5"]
    assert "SIGSEGV in worker" in bundle.render_text()


def test_older_successful_attempt_remains_available_independently_of_later_failures() -> None:
    """Requirement 2: An older successful attempt remains available independently of later failures."""
    state = _make_state()
    state.attempts.append(
        Attempt(id="s-baseline", action="setup base schema", success=True, result="schema created")
    )
    for i in range(1, 6):
        state.attempts.append(
            Attempt(id=f"f{i}", action=f"faulty step {i}", success=False, result=f"failure {i}")
        )

    # max_attempts=3 (less than the 5 later failures)
    builder = ContextBuilder(ContextConfig(max_attempts=3))
    bundle = builder.build(state)

    # Successful attempt s-baseline was NOT pushed out by later failures
    assert [a.id for a in bundle.recent_attempts] == ["s-baseline"]
    assert [a.id for a in bundle.failed_attempts] == ["f3", "f4", "f5"]
    assert "schema created" in bundle.render_text()


def test_failed_attempts_capped_at_max_attempts() -> None:
    """Requirement 3: Failed attempts are capped independently at max_attempts."""
    state = _make_state()
    for i in range(10):
        state.attempts.append(
            Attempt(id=f"fail-{i}", action=f"action {i}", success=False, result=f"err {i}")
        )

    builder = ContextBuilder(ContextConfig(max_attempts=4))
    bundle = builder.build(state)

    assert len(bundle.failed_attempts) == 4
    assert [a.id for a in bundle.failed_attempts] == ["fail-6", "fail-7", "fail-8", "fail-9"]


def test_successful_attempts_capped_independently_at_max_attempts() -> None:
    """Requirement 4: Successful attempts are capped independently at max_attempts."""
    state = _make_state()
    for i in range(10):
        state.attempts.append(
            Attempt(id=f"succ-{i}", action=f"action {i}", success=True, result=f"ok {i}")
        )

    builder = ContextBuilder(ContextConfig(max_attempts=4))
    bundle = builder.build(state)

    assert len(bundle.recent_attempts) == 4
    assert [a.id for a in bundle.recent_attempts] == ["succ-6", "succ-7", "succ-8", "succ-9"]


def test_ordering_remains_chronological_within_each_result() -> None:
    """Requirement 5: Ordering remains strictly chronological within each window."""
    state = _make_state()
    # Interleaved attempts
    sequence = [
        ("s1", True),
        ("f1", False),
        ("s2", True),
        ("f2", False),
        ("s3", True),
        ("f3", False),
        ("s4", True),
        ("f4", False),
    ]
    for att_id, succ in sequence:
        state.attempts.append(Attempt(id=att_id, action="act", success=succ))

    builder = ContextBuilder(ContextConfig(max_attempts=3))
    bundle = builder.build(state)

    # Last 3 successful: s2, s3, s4 in chronological order
    assert [a.id for a in bundle.recent_attempts] == ["s2", "s3", "s4"]
    # Last 3 failed: f2, f3, f4 in chronological order
    assert [a.id for a in bundle.failed_attempts] == ["f2", "f3", "f4"]


def test_include_successful_attempts_false_works() -> None:
    """Requirement 6: include_successful_attempts=False produces no successful-attempt context."""
    state = _make_state()
    state.attempts.append(Attempt(id="s1", action="A1", success=True, result="ok"))
    state.attempts.append(Attempt(id="f1", action="A2", success=False, result="fail"))

    builder = ContextBuilder()
    bundle = builder.build(state, config=ContextConfig(include_successful_attempts=False))

    assert bundle.recent_attempts == []
    assert len(bundle.failed_attempts) == 1
    assert bundle.failed_attempts[0].id == "f1"
    assert "## RECENT SUCCESSFUL ATTEMPTS\n(None)" in bundle.render_text()


def test_include_failed_attempts_false_works() -> None:
    """Requirement 7: include_failed_attempts=False produces no failed-attempt context."""
    state = _make_state()
    state.attempts.append(Attempt(id="s1", action="A1", success=True, result="ok"))
    state.attempts.append(Attempt(id="f1", action="A2", success=False, result="fail"))

    builder = ContextBuilder()
    bundle = builder.build(state, config=ContextConfig(include_failed_attempts=False))

    assert bundle.failed_attempts == []
    assert len(bundle.recent_attempts) == 1
    assert bundle.recent_attempts[0].id == "s1"
    assert "## FAILED ATTEMPTS" not in bundle.render_text()


def test_max_attempts_zero_or_negative_produces_no_attempt_history() -> None:
    """Requirement 8: A max_attempts <= 0 value produces empty bounded attempt collections."""
    state = _make_state()
    state.attempts.append(Attempt(id="s1", success=True))
    state.attempts.append(Attempt(id="f1", success=False))

    builder = ContextBuilder()

    bundle_0 = builder.build(state, config=ContextConfig(max_attempts=0))
    assert bundle_0.recent_attempts == []
    assert bundle_0.failed_attempts == []

    bundle_neg = builder.build(state, config=ContextConfig(max_attempts=-3))
    assert bundle_neg.recent_attempts == []
    assert bundle_neg.failed_attempts == []


def test_task_state_remains_unchanged_by_attempt_partitioning() -> None:
    """Requirement 9: TaskState is never mutated by attempt partitioning or slicing."""
    state = _make_state()
    state.attempts = [
        Attempt(id="a1", success=True),
        Attempt(id="a2", success=False),
        Attempt(id="a3", success=True),
        Attempt(id="a4", success=False),
    ]
    snapshot = state.to_dict()

    builder = ContextBuilder(ContextConfig(max_attempts=2))
    _ = builder.build(state)

    assert state.to_dict() == snapshot
    assert len(state.attempts) == 4


def test_existing_p1_p2_policy_wiring_continues_to_work() -> None:
    """Requirement 10: Existing P1/P2 policy wiring works harmoniously with independent attempt windows."""
    fs = FileSymbols(
        path="src/service.py",
        language="Python",
        symbols=[SymbolRecord(name="Service", kind="class", file="src/service.py")],
        imports=[],
        local_imports=[],
    )
    repo = RepositoryIndex(
        root_dir="/repo",
        discovered_files=["src/service.py"],
        discovered_directories=["src"],
        file_count=1,
        source_files=["src/service.py"],
        source_directories=["src"],
        file_symbols={"src/service.py": fs},
    )

    state = _make_state()
    state.relevant_files = ["src/service.py"]
    state.attempts = [
        Attempt(id="f-old", action="try buggy approach", success=False, result="crashed"),
        Attempt(id="s-new1", action="apply fix 1", success=True, result="step 1 ok"),
        Attempt(id="s-new2", action="apply fix 2", success=True, result="step 2 ok"),
        Attempt(id="s-new3", action="apply fix 3", success=True, result="step 3 ok"),
    ]

    policy = AdaptiveContextPolicy()
    profile = policy.evaluate(state, repo_index=repo)
    cfg = profile.to_context_config()
    cfg.max_attempts = 2  # Set window smaller than total attempts

    builder = ContextBuilder(repository_index=repo)
    bundle = builder.build(state, config=cfg)
    rendered = bundle.render_text()

    # P1/P2 features intact
    assert "## REPOSITORY SYMBOLS" in rendered
    assert "class: Service" in rendered

    # P3 independent attempt window intact
    assert [a.id for a in bundle.failed_attempts] == ["f-old"]
    assert [a.id for a in bundle.recent_attempts] == ["s-new2", "s-new3"]


def test_regression_important_old_failure_retained_across_successes() -> None:
    """
    Explicit regression test:
    FAIL important-old-failure
    SUCCESS s1
    SUCCESS s2
    SUCCESS s3
    SUCCESS s4

    with small max_attempts, verify important-old-failure is still present in failed-attempt context.
    """
    state = _make_state()
    state.attempts.append(
        Attempt(
            id="important-old-failure",
            action="modify database schema without lock",
            result="DeadlockDetected in payment worker",
            success=False,
        )
    )
    state.attempts.append(Attempt(id="s1", action="step 1", success=True, result="ok 1"))
    state.attempts.append(Attempt(id="s2", action="step 2", success=True, result="ok 2"))
    state.attempts.append(Attempt(id="s3", action="step 3", success=True, result="ok 3"))
    state.attempts.append(Attempt(id="s4", action="step 4", success=True, result="ok 4"))

    # small max_attempts = 2
    builder = ContextBuilder(ContextConfig(max_attempts=2))
    bundle = builder.build(state)

    # 1. Verify in structured bundle
    failed_ids = [a.id for a in bundle.failed_attempts]
    assert "important-old-failure" in failed_ids
    assert len(bundle.failed_attempts) == 1

    # 2. Verify successful attempts got their own window of 2
    assert [a.id for a in bundle.recent_attempts] == ["s3", "s4"]

    # 3. Verify in rendered text
    rendered = bundle.render_text()
    assert "## FAILED ATTEMPTS" in rendered
    assert "important-old-failure" in rendered
    assert "DeadlockDetected in payment worker" in rendered
    assert "## RECENT SUCCESSFUL ATTEMPTS" in rendered
    assert "Attempt s3" in rendered
    assert "Attempt s4" in rendered
    assert "Attempt s1" not in rendered  # older successes rolled off
