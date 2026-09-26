"""Unit tests for deterministic context builder subsystem."""

import copy
from pathlib import Path
import pytest

from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    Phase,
    TaskState,
    TaskStatus,
    TokenUsage,
    VerificationSnapshot,
)


@pytest.fixture
def empty_state() -> TaskState:
    """Provides a minimal default TaskState."""
    return TaskState(task_id="task-min", task="Minimal task")


@pytest.fixture
def populated_state() -> TaskState:
    """Provides a richly populated TaskState."""
    return TaskState(
        task_id="task-pop",
        task="Fix database connection leak in worker pool",
        phase=Phase.EXECUTE,
        status=TaskStatus.RUNNING,
        plan=["1. Identify unclosed sessions", "2. Add context manager", "3. Test under load"],
        current_hypothesis="Session is created in loop without closing in exception handler",
        relevant_files=["src/db/pool.py", "src/db/session.py"],
        touched_files=["src/db/session.py"],
        discoveries=[
            Discovery(
                statement="Session pool default size is 20",
                evidence="src/db/pool.py:15",
                files=["src/db/pool.py"],
                confidence=0.9,
            )
        ],
        recent_observations=[
            Observation(
                type="tool_result",
                source="grep_search",
                summary="Found 3 occurrences of Session()",
                raw_output="session.py:12\nsession.py:44\nsession.py:89",
                files=["src/db/session.py"],
            )
        ],
        attempts=[
            Attempt(
                id="att-1",
                hypothesis="Add try/finally block",
                action="edit session.py",
                files_touched=["src/db/session.py"],
                result="Still leaking on timeout",
                success=False,
                iteration=1,
            ),
            Attempt(
                id="att-2",
                hypothesis="Use async with context manager",
                action="edit session.py to use async with",
                files_touched=["src/db/session.py"],
                result="Tests pass cleanly",
                success=True,
                iteration=2,
            ),
        ],
        failures=[
            Failure(
                error_signature="ConnectionPoolTimeout: timeout after 30s",
                summary="Pool exhausted",
                action="run_tests",
                occurrence_count=3,
            ),
            Failure(
                error_signature="SyntaxError: invalid syntax",
                summary="Temporary typo",
                action="edit",
                occurrence_count=1,
            ),
        ],
        current_errors=["ConnectionPoolTimeout: timeout after 30s"],
        verification=VerificationSnapshot(
            success=False,
            summary="1 test failed",
            tests_passed=["test_basic_query"],
            tests_failed=["test_concurrent_load"],
        ),
        iteration=2,
        model_calls=4,
        tool_calls=7,
        token_usage=TokenUsage(input_tokens=800, output_tokens=350, total_tokens=1150),
    )


def test_empty_minimal_task_state(empty_state: TaskState) -> None:
    """Verify builder handles an empty/minimal TaskState without crashing."""
    builder = ContextBuilder()
    bundle = builder.build(empty_state)

    assert bundle.task == "Minimal task"
    assert bundle.phase == Phase.PLAN
    assert bundle.status == TaskStatus.RUNNING
    assert bundle.plan == []
    assert bundle.current_hypothesis is None
    assert bundle.relevant_files == []
    assert bundle.touched_files == []
    assert bundle.important_discoveries == []
    assert bundle.recent_observations == []
    assert bundle.recent_attempts == []
    assert bundle.failed_attempts == []
    assert bundle.repeated_failures == []
    assert bundle.current_errors == []
    assert bundle.latest_verification_result is None
    assert bundle.iteration_count == 0
    assert bundle.token_usage.total_tokens == 0

    text = bundle.render_text()
    assert "## TASK" in text
    assert "## PHASE / STATUS" in text
    assert "## PLAN" in text
    assert "## EFFICIENCY" in text
    assert "Minimal task" in text


def test_populated_task_state(populated_state: TaskState) -> None:
    """Verify builder extracts all populated fields and formats them cleanly."""
    builder = ContextBuilder()
    bundle = builder.build(populated_state)

    assert bundle.task == populated_state.task
    assert bundle.phase == Phase.EXECUTE
    assert len(bundle.plan) == 3
    assert bundle.current_hypothesis == populated_state.current_hypothesis
    assert bundle.relevant_files == ["src/db/pool.py", "src/db/session.py"]
    assert bundle.touched_files == ["src/db/session.py"]
    assert len(bundle.important_discoveries) == 1
    assert len(bundle.recent_observations) == 1
    assert len(bundle.recent_attempts) == 1
    assert bundle.recent_attempts[0].id == "att-2"
    assert len(bundle.failed_attempts) == 1
    assert bundle.failed_attempts[0].id == "att-1"
    assert len(bundle.repeated_failures) == 1
    assert bundle.current_errors == ["ConnectionPoolTimeout: timeout after 30s"]
    assert bundle.latest_verification_result is not None
    assert bundle.latest_verification_result.success is False
    assert bundle.iteration_count == 2
    assert bundle.model_call_count == 4
    assert bundle.tool_call_count == 7
    assert bundle.token_usage.total_tokens == 1150

    text = bundle.render_text()
    for section in [
        "## TASK",
        "## PHASE / STATUS",
        "## PLAN",
        "## CURRENT HYPOTHESIS",
        "## RELEVANT FILES",
        "## DISCOVERIES",
        "## RECENT OBSERVATIONS",
        "## RECENT SUCCESSFUL ATTEMPTS",
        "## FAILED ATTEMPTS",
        "## REPEATED FAILURES",
        "## CURRENT ERRORS",
        "## VERIFICATION",
        "## EFFICIENCY",
    ]:
        assert section in text


def test_observation_recency_limits(empty_state: TaskState) -> None:
    """Verify only the most recent N observations are included."""
    for i in range(12):
        empty_state.recent_observations.append(
            Observation(type="log", source="cli", summary=f"Observation {i}")
        )

    builder = ContextBuilder(ContextConfig(max_observations=5))
    bundle = builder.build(empty_state)

    assert len(bundle.recent_observations) == 5
    # Should be observations 7, 8, 9, 10, 11
    summaries = [o.summary for o in bundle.recent_observations]
    assert summaries == ["Observation 7", "Observation 8", "Observation 9", "Observation 10", "Observation 11"]

    # Test default of 8
    default_builder = ContextBuilder()
    default_bundle = default_builder.build(empty_state)
    assert len(default_bundle.recent_observations) == 8
    assert default_bundle.recent_observations[-1].summary == "Observation 11"


def test_discovery_recency_limits(empty_state: TaskState) -> None:
    """Verify discoveries are bounded by max_discoveries with non-positive values returning none."""
    for i in range(20):
        empty_state.discoveries.append(
            Discovery(statement=f"Discovery {i}", evidence=f"evidence {i}")
        )

    # Default max_discoveries = 12
    builder = ContextBuilder()
    bundle = builder.build(empty_state)
    assert len(bundle.important_discoveries) == 12
    assert bundle.important_discoveries[0].statement == "Discovery 8"
    assert bundle.important_discoveries[-1].statement == "Discovery 19"

    # Custom max_discoveries = 4
    bundle_4 = builder.build(empty_state, config=ContextConfig(max_discoveries=4))
    assert len(bundle_4.important_discoveries) == 4
    assert [d.statement for d in bundle_4.important_discoveries] == [
        "Discovery 16", "Discovery 17", "Discovery 18", "Discovery 19"
    ]

    # Non-positive max_discoveries <= 0 yields no discoveries
    bundle_0 = builder.build(empty_state, config=ContextConfig(max_discoveries=0))
    assert len(bundle_0.important_discoveries) == 0
    assert "## DISCOVERIES\n(None)" in bundle_0.render_text()

    bundle_neg = builder.build(empty_state, config=ContextConfig(max_discoveries=-5))
    assert len(bundle_neg.important_discoveries) == 0


def test_attempt_recency_limits(empty_state: TaskState) -> None:
    """Verify only the most recent N attempts are considered and partitioned mutually exclusively."""
    for i in range(10):
        empty_state.attempts.append(
            Attempt(id=f"att-{i}", action=f"Action {i}", result=f"Res {i}", success=(i % 2 == 0))
        )

    builder = ContextBuilder(ContextConfig(max_attempts=3))
    bundle = builder.build(empty_state)

    # Independent recency windows: last 3 successful and last 3 failed attempts
    assert [a.id for a in bundle.recent_attempts] == ["att-4", "att-6", "att-8"]
    assert [a.id for a in bundle.failed_attempts] == ["att-5", "att-7", "att-9"]
    assert len(bundle.recent_attempts) == 3
    assert len(bundle.failed_attempts) == 3


def test_separation_of_failed_and_successful_attempts(empty_state: TaskState) -> None:
    """Verify mutual exclusivity: no attempt appears in both recent_attempts and failed_attempts."""
    empty_state.attempts = [
        Attempt(id="att-1", action="A1", success=False, result="failed 1"),
        Attempt(id="att-2", action="A2", success=True, result="success 2"),
        Attempt(id="att-3", action="A3", success=False, result="failed 3"),
        Attempt(id="att-4", action="A4", success=True, result="success 4"),
    ]

    builder = ContextBuilder()
    bundle = builder.build(empty_state)

    # Failed attempts: only failed
    assert len(bundle.failed_attempts) == 2
    assert all(not a.success for a in bundle.failed_attempts)
    assert [a.id for a in bundle.failed_attempts] == ["att-1", "att-3"]

    # Recent attempts: only successful when include_successful_attempts=True
    assert len(bundle.recent_attempts) == 2
    assert all(a.success for a in bundle.recent_attempts)
    assert [a.id for a in bundle.recent_attempts] == ["att-2", "att-4"]

    # Mutual exclusivity: intersection of IDs is empty
    failed_ids = {a.id for a in bundle.failed_attempts}
    successful_ids = {a.id for a in bundle.recent_attempts}
    assert failed_ids.isdisjoint(successful_ids)

    # Verify rendering of successful attempts
    text = bundle.render_text()
    assert "## RECENT SUCCESSFUL ATTEMPTS" in text
    assert "- Attempt att-2 (iter 0): A2 -> success 2" in text
    assert "- Attempt att-4 (iter 0): A4 -> success 4" in text

    # When include_successful_attempts=False, recent_attempts must be empty
    cfg = ContextConfig(include_successful_attempts=False)
    bundle_no_success = builder.build(empty_state, config=cfg)
    assert bundle_no_success.recent_attempts == []
    assert len(bundle_no_success.failed_attempts) == 2
    assert "## RECENT SUCCESSFUL ATTEMPTS\n(None)" in bundle_no_success.render_text()


def test_repeated_failure_inclusion(empty_state: TaskState) -> None:
    """Verify only failures with occurrence_count >= threshold are in repeated_failures."""
    empty_state.failures = [
        Failure(error_signature="ErrOnce", summary="Occurred 1 time", action="act", occurrence_count=1),
        Failure(error_signature="ErrTwice", summary="Occurred 2 times", action="act", occurrence_count=2),
        Failure(error_signature="ErrThrice", summary="Occurred 3 times", action="act", occurrence_count=3),
    ]

    builder = ContextBuilder()
    bundle = builder.build(empty_state)

    assert len(bundle.repeated_failures) == 2
    sigs = [f.error_signature for f in bundle.repeated_failures]
    assert "ErrTwice" in sigs
    assert "ErrThrice" in sigs
    assert "ErrOnce" not in sigs

    # Custom threshold = 3
    custom_bundle = builder.build(empty_state, config=ContextConfig(min_failure_occurrences=3))
    assert len(custom_bundle.repeated_failures) == 1
    assert custom_bundle.repeated_failures[0].error_signature == "ErrThrice"


def test_raw_output_excluded_by_default(empty_state: TaskState) -> None:
    """Verify Observation.raw_output is None by default in ContextBundle."""
    empty_state.recent_observations.append(
        Observation(
            type="bash",
            source="run_bash",
            summary="Command failed with exit code 1",
            raw_output="Massive 500-line dump of stack traces and internal logs...",
        )
    )

    builder = ContextBuilder()
    bundle = builder.build(empty_state)

    assert bundle.recent_observations[0].summary == "Command failed with exit code 1"
    assert bundle.recent_observations[0].raw_output is None
    assert "Massive 500-line dump" not in bundle.render_text()

    # Verify original state observation is NOT modified
    assert empty_state.recent_observations[0].raw_output == "Massive 500-line dump of stack traces and internal logs..."


def test_raw_output_included_only_when_configured(empty_state: TaskState) -> None:
    """Verify Observation.raw_output is included when include_raw_output=True."""
    raw_str = "Explicit stack trace:\nFile foo.py, line 10"
    empty_state.recent_observations.append(
        Observation(
            type="bash",
            source="run_bash",
            summary="Command failed",
            raw_output=raw_str,
        )
    )

    builder = ContextBuilder(ContextConfig(include_raw_output=True))
    bundle = builder.build(empty_state)

    assert bundle.recent_observations[0].raw_output == raw_str
    assert raw_str in bundle.render_text()


def test_verification_inclusion(empty_state: TaskState) -> None:
    """Verify verification report inclusion."""
    builder = ContextBuilder()
    bundle_none = builder.build(empty_state)
    assert bundle_none.latest_verification_result is None
    assert "## VERIFICATION\n(None)" in bundle_none.render_text()

    empty_state.verification = VerificationSnapshot(
        success=True,
        summary="All tests passed",
        tests_passed=["test_1", "test_2"],
        tests_failed=[],
    )
    bundle_verified = builder.build(empty_state)
    assert bundle_verified.latest_verification_result is not None
    assert bundle_verified.latest_verification_result.success is True
    assert "Success: True" in bundle_verified.render_text()
    assert "Summary: All tests passed" in bundle_verified.render_text()


def test_telemetry_inclusion(populated_state: TaskState) -> None:
    """Verify iteration, calls, and tokens are accurately surfaced."""
    builder = ContextBuilder()
    bundle = builder.build(populated_state)

    assert bundle.iteration_count == 2
    assert bundle.model_call_count == 4
    assert bundle.tool_call_count == 7
    assert bundle.token_usage.input_tokens == 800
    assert bundle.token_usage.output_tokens == 350
    assert bundle.token_usage.total_tokens == 1150

    rendered = bundle.render_text()
    assert "Iteration: 2" in rendered
    assert "Model Calls: 4" in rendered
    assert "Tool Calls: 7" in rendered
    assert "1150 total" in rendered


def test_deterministic_output_across_repeated_builds(populated_state: TaskState) -> None:
    """Verify identical TaskState produces identical dict and text across multiple runs."""
    builder = ContextBuilder()
    bundle1 = builder.build(populated_state)
    bundle2 = builder.build(populated_state)

    assert bundle1.to_dict() == bundle2.to_dict()
    assert bundle1.render_text() == bundle2.render_text()
    assert str(bundle1) == str(bundle2)


def test_confirmation_that_building_context_does_not_mutate_task_state(populated_state: TaskState) -> None:
    """Verify building context never modifies TaskState attributes or nested objects."""
    state_snapshot = copy.deepcopy(populated_state.to_dict())

    builder = ContextBuilder(ContextConfig(max_observations=1, max_attempts=1, include_raw_output=False))
    bundle = builder.build(populated_state)

    # Bundle has reduced limits and stripped raw_output
    assert len(bundle.recent_observations) == 1
    assert bundle.recent_observations[0].raw_output is None

    # Original state must remain completely untouched
    assert populated_state.to_dict() == state_snapshot
    assert populated_state.recent_observations[0].raw_output is not None


def test_builder_accepts_memory_manager(tmp_path: Path) -> None:
    """Verify ContextBuilder can directly accept a MemoryManager instance."""
    manager = MemoryManager(base_dir=tmp_path / ".harness")
    manager.initialize_task("task-mgr", "Direct manager build test")
    manager.add_relevant_file("app.py")
    manager.record_model_call(input_tokens=100, output_tokens=50)

    builder = ContextBuilder()
    bundle = builder.build(manager)

    assert bundle.task == "Direct manager build test"
    assert bundle.relevant_files == ["app.py"]
    assert bundle.token_usage.total_tokens == 150

    rendered = builder.render_text(manager)
    assert "## TASK\nDirect manager build test" in rendered
