"""Unit tests for the state and memory subsystem."""

import json
from pathlib import Path
import pytest

from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Event,
    EventType,
    Failure,
    Observation,
    Phase,
    TaskState,
    TaskStatus,
    TokenUsage,
    VerificationSnapshot,
)
from src.memory.storage import TaskStorage


@pytest.fixture
def temp_harness_dir(tmp_path: Path) -> Path:
    """Fixture providing a temporary directory for task state storage."""
    return tmp_path / ".harness"


@pytest.fixture
def manager(temp_harness_dir: Path) -> MemoryManager:
    """Fixture providing a MemoryManager configured with temporary storage."""
    return MemoryManager(base_dir=temp_harness_dir)


def test_task_initialization(manager: MemoryManager) -> None:
    """Verify task initialization creates a correct default TaskState and event."""
    state = manager.initialize_task(task_id="task-001", task="Fix authentication bug")

    assert state.task_id == "task-001"
    assert state.task == "Fix authentication bug"
    assert state.phase == Phase.PLAN
    assert state.status == TaskStatus.RUNNING
    assert state.plan == []
    assert state.current_hypothesis is None
    assert state.relevant_files == []
    assert state.touched_files == []
    assert state.discoveries == []
    assert state.recent_observations == []
    assert state.attempts == []
    assert state.failures == []
    assert state.current_errors == []
    assert state.verification is None
    assert state.iteration == 0
    assert state.model_calls == 0
    assert state.tool_calls == 0
    assert state.token_usage.input_tokens == 0
    assert state.token_usage.output_tokens == 0
    assert state.token_usage.total_tokens == 0
    assert state.created_at is not None
    assert state.updated_at is not None

    events = manager.get_events()
    assert len(events) == 1
    assert events[0].type == EventType.TASK_INITIALIZED.value
    assert events[0].iteration == 0
    assert "task-001" in events[0].summary


def test_phase_updates(manager: MemoryManager) -> None:
    """Verify lifecycle phases can be set via Enum or string and emit events."""
    manager.initialize_task("task-phase", "Test phase transitions")

    manager.set_phase(Phase.EXECUTE)
    assert manager.get_state().phase == Phase.EXECUTE

    manager.set_phase("verify")
    assert manager.get_state().phase == Phase.VERIFY

    manager.set_phase(Phase.RECOVER)
    assert manager.get_state().phase == Phase.RECOVER

    manager.set_phase("PLAN")
    assert manager.get_state().phase == Phase.PLAN

    events = [e for e in manager.get_events() if e.type == EventType.PHASE_CHANGED.value]
    assert len(events) == 4
    assert events[0].metadata["phase"] == Phase.EXECUTE.value


def test_status_updates(manager: MemoryManager) -> None:
    """Verify task status can be updated and emits events."""
    manager.initialize_task("task-status", "Test status transitions")

    manager.set_status(TaskStatus.BLOCKED)
    assert manager.get_state().status == TaskStatus.BLOCKED

    manager.set_status("done")
    assert manager.get_state().status == TaskStatus.DONE

    manager.set_status("failed")
    assert manager.get_state().status == TaskStatus.FAILED

    manager.set_status(TaskStatus.RUNNING)
    assert manager.get_state().status == TaskStatus.RUNNING

    events = [e for e in manager.get_events() if e.type == EventType.STATUS_CHANGED.value]
    assert len(events) == 4


def test_plan_updates(manager: MemoryManager) -> None:
    """Verify plan steps can be updated and retrieved."""
    manager.initialize_task("task-plan", "Test plan updates")

    plan_steps = ["1. Locate buggy function", "2. Write regression test", "3. Apply patch"]
    manager.update_plan(plan_steps)

    assert manager.get_state().plan == plan_steps
    # Ensure plan is a copy, not shared reference
    plan_steps.append("4. Extra step")
    assert len(manager.get_state().plan) == 3

    events = [e for e in manager.get_events() if e.type == EventType.PLAN_UPDATED.value]
    assert len(events) == 1
    assert events[0].metadata["plan"] == ["1. Locate buggy function", "2. Write regression test", "3. Apply patch"]


def test_hypothesis_updates(manager: MemoryManager) -> None:
    """Verify hypothesis can be set and cleared."""
    manager.initialize_task("task-hypo", "Test hypothesis updates")

    manager.update_hypothesis("Off-by-one error in pagination loop")
    assert manager.get_state().current_hypothesis == "Off-by-one error in pagination loop"

    manager.update_hypothesis(None)
    assert manager.get_state().current_hypothesis is None

    events = [e for e in manager.get_events() if e.type == EventType.HYPOTHESIS_UPDATED.value]
    assert len(events) == 2


def test_add_discoveries(manager: MemoryManager) -> None:
    """Verify adding discoveries via objects or dictionaries."""
    manager.initialize_task("task-disc", "Test discoveries")

    discovery_obj = Discovery(
        statement="Auth middleware requires Bearer prefix",
        evidence="Found check in src/auth/middleware.py:45",
        files=["src/auth/middleware.py"],
        confidence=0.95,
    )
    manager.add_discovery(discovery_obj)

    discovery_dict = {
        "statement": "Redis connection pool default is 10",
        "evidence": "Config setting in src/config.py",
        "files": ["src/config.py"],
        "confidence": 0.8,
    }
    manager.add_discovery(discovery_dict)

    state = manager.get_state()
    assert len(state.discoveries) == 2
    assert state.discoveries[0].statement == "Auth middleware requires Bearer prefix"
    assert state.discoveries[1].confidence == 0.8

    events = [e for e in manager.get_events() if e.type == EventType.DISCOVERY_ADDED.value]
    assert len(events) == 2


def test_add_observations(manager: MemoryManager) -> None:
    """Verify adding observations via objects or dictionaries."""
    manager.initialize_task("task-obs", "Test observations")

    obs = Observation(
        type="test_output",
        source="pytest",
        summary="Failed 1 of 5 tests",
        raw_output="FAILED tests/test_auth.py::test_login - AssertionError",
        files=["tests/test_auth.py"],
    )
    manager.add_observation(obs)

    obs_dict = {
        "type": "file_read",
        "source": "read_file",
        "summary": "Read auth.py header",
        "files": ["src/auth.py"],
    }
    manager.add_observation(obs_dict)

    state = manager.get_state()
    assert len(state.recent_observations) == 2
    assert state.recent_observations[0].type == "test_output"
    assert state.recent_observations[1].files == ["src/auth.py"]

    events = [e for e in manager.get_events() if e.type == EventType.OBSERVATION_ADDED.value]
    assert len(events) == 2


def test_record_attempts(manager: MemoryManager) -> None:
    """Verify attempts are recorded and files_touched are tracked without duplication."""
    manager.initialize_task("task-attempt", "Test attempts")

    attempt = Attempt(
        id="attempt-001",
        hypothesis="Token expiry check uses wrong timestamp unit",
        action="modify token expiry condition in src/auth/token.py",
        files_touched=["src/auth/token.py"],
        result="Tests still failed with ValueError",
        success=False,
        iteration=1,
    )
    manager.add_attempt(attempt)

    state = manager.get_state()
    assert len(state.attempts) == 1
    assert state.attempts[0].id == "attempt-001"
    assert "src/auth/token.py" in state.touched_files

    events = [e for e in manager.get_events() if e.type == EventType.ATTEMPT_RECORDED.value]
    assert len(events) == 1


def test_duplicate_relevant_file_prevention(manager: MemoryManager) -> None:
    """Verify duplicate relevant_files entries are prevented."""
    manager.initialize_task("task-files", "Test duplicate prevention")

    assert manager.add_relevant_file("src/auth.py") is True
    assert manager.add_relevant_file("src/auth.py") is False
    assert manager.add_relevant_file("./src/auth.py") is False
    assert manager.add_relevant_file("src/models.py") is True

    state = manager.get_state()
    assert len(state.relevant_files) == 2
    assert state.relevant_files == ["src/auth.py", "src/models.py"]


def test_duplicate_touched_file_prevention(manager: MemoryManager) -> None:
    """Verify duplicate touched_files entries are prevented."""
    manager.initialize_task("task-touched", "Test duplicate touched files")

    assert manager.mark_file_touched("src/core.py") is True
    assert manager.mark_file_touched("src/core.py") is False
    assert manager.mark_file_touched("./src/core.py") is False
    assert manager.mark_file_touched("tests/test_core.py") is True

    state = manager.get_state()
    assert len(state.touched_files) == 2
    assert state.touched_files == ["src/core.py", "tests/test_core.py"]


def test_repeated_failure_detection_and_occurrence_counting(manager: MemoryManager) -> None:
    """
    Verify that repeated failures with matching error_signature:
    - do not create duplicate records
    - increment occurrence_count
    - update last_seen
    - preserve first_seen
    """
    manager.initialize_task("task-fail", "Test repeated failures")

    fail1 = Failure(
        error_signature="ZeroDivisionError: division by zero",
        summary="Zero division in calculate_discount",
        action="run_tests",
        files=["src/discount.py"],
        first_seen="2026-09-26T10:00:00Z",
        last_seen="2026-09-26T10:00:00Z",
    )
    manager.add_failure(fail1)

    state = manager.get_state()
    assert len(state.failures) == 1
    assert state.failures[0].occurrence_count == 1
    assert state.failures[0].first_seen == "2026-09-26T10:00:00Z"
    assert state.failures[0].last_seen == "2026-09-26T10:00:00Z"

    # Add same failure signature again with a later last_seen
    fail2 = Failure(
        error_signature="ZeroDivisionError: division by zero",
        summary="Zero division in calculate_discount (second try)",
        action="run_tests_again",
        files=["src/discount.py", "tests/test_discount.py"],
        last_seen="2026-09-26T10:05:00Z",
    )
    manager.add_failure(fail2)

    assert len(state.failures) == 1
    recorded = state.failures[0]
    assert recorded.occurrence_count == 2
    assert recorded.first_seen == "2026-09-26T10:00:00Z"
    assert recorded.last_seen == "2026-09-26T10:05:00Z"
    assert "tests/test_discount.py" in recorded.files

    # Add a different failure signature
    fail3 = Failure(
        error_signature="TypeError: unsupported operand type",
        summary="Type mismatch",
        action="run_tests",
    )
    manager.add_failure(fail3)

    assert len(state.failures) == 2
    repeated = manager.get_repeated_failures(min_occurrences=2)
    assert len(repeated) == 1
    assert repeated[0].error_signature == "ZeroDivisionError: division by zero"

    not_repeated = manager.get_repeated_failures(min_occurrences=3)
    assert len(not_repeated) == 0


def test_token_accounting(manager: MemoryManager) -> None:
    """Verify input, output, and total token accumulation."""
    manager.initialize_task("task-tokens", "Test token tracking")

    manager.record_model_call(input_tokens=120, output_tokens=40)
    usage = manager.get_state().token_usage
    assert usage.input_tokens == 120
    assert usage.output_tokens == 40
    assert usage.total_tokens == 160
    assert manager.get_state().model_calls == 1

    manager.record_token_usage(input_tokens=80, output_tokens=20)
    usage = manager.get_state().token_usage
    assert usage.input_tokens == 200
    assert usage.output_tokens == 60
    assert usage.total_tokens == 260
    # model_calls should not increment on record_token_usage
    assert manager.get_state().model_calls == 1


def test_model_call_and_tool_call_counting(manager: MemoryManager) -> None:
    """Verify model_calls and tool_calls counters increment correctly."""
    manager.initialize_task("task-calls", "Test call counters")

    assert manager.get_state().model_calls == 0
    assert manager.get_state().tool_calls == 0

    manager.record_model_call(input_tokens=10, output_tokens=5)
    manager.record_model_call(input_tokens=15, output_tokens=10)
    assert manager.get_state().model_calls == 2

    assert manager.record_tool_call() == 1
    assert manager.record_tool_call() == 2
    assert manager.record_tool_call() == 3
    assert manager.get_state().tool_calls == 3


def test_verification_result_storage(manager: MemoryManager) -> None:
    """Verify verification results can be stored and retrieved."""
    manager.initialize_task("task-verify", "Test verification storage")

    res = VerificationSnapshot(
        success=True,
        summary="All 12 unit tests passed",
        tests_passed=["test_a", "test_b"],
        tests_failed=[],
        details={"exit_code": 0, "duration": 1.42},
    )
    manager.set_verification(res)

    state = manager.get_state()
    assert state.verification is not None
    assert state.verification.success is True
    assert state.verification.summary == "All 12 unit tests passed"
    assert state.verification.tests_passed == ["test_a", "test_b"]

    events = [e for e in manager.get_events() if e.type == EventType.VERIFICATION_UPDATED.value]
    assert len(events) == 1


def test_json_serialization_deserialization() -> None:
    """Verify round-trip JSON serialization and deserialization preserves all fields."""
    state = TaskState(
        task_id="task-json",
        task="Test JSON serialization",
        phase=Phase.VERIFY,
        status=TaskStatus.BLOCKED,
        plan=["step 1", "step 2"],
        current_hypothesis="Some hypothesis",
        relevant_files=["file1.py"],
        touched_files=["file1.py", "file2.py"],
        discoveries=[
            Discovery(statement="fact", evidence="code", files=["file1.py"], confidence=0.9)
        ],
        recent_observations=[
            Observation(type="log", source="cli", summary="log message", files=["file1.py"])
        ],
        attempts=[
            Attempt(id="att-1", hypothesis="hyp", action="act", files_touched=["file1.py"], success=True, iteration=1)
        ],
        failures=[
            Failure(
                error_signature="Err1",
                summary="Sum",
                action="act",
                files=["file1.py"],
                occurrence_count=3,
                first_seen="2026-09-26T00:00:00Z",
                last_seen="2026-09-26T01:00:00Z",
            )
        ],
        current_errors=["Err1: occurred"],
        verification=VerificationSnapshot(
            success=False,
            summary="1 test failed",
            tests_passed=["t1"],
            tests_failed=["t2"],
        ),
        iteration=4,
        model_calls=7,
        tool_calls=12,
        token_usage=TokenUsage(input_tokens=500, output_tokens=250, total_tokens=750),
        created_at="2026-09-26T00:00:00Z",
        updated_at="2026-09-26T01:30:00Z",
    )

    json_str = state.to_json()
    reloaded = TaskState.from_json(json_str)

    assert reloaded.task_id == state.task_id
    assert reloaded.phase == Phase.VERIFY
    assert reloaded.status == TaskStatus.BLOCKED
    assert reloaded.plan == ["step 1", "step 2"]
    assert reloaded.current_hypothesis == "Some hypothesis"
    assert reloaded.relevant_files == ["file1.py"]
    assert reloaded.touched_files == ["file1.py", "file2.py"]
    assert len(reloaded.discoveries) == 1
    assert reloaded.discoveries[0].confidence == 0.9
    assert len(reloaded.recent_observations) == 1
    assert len(reloaded.attempts) == 1
    assert reloaded.attempts[0].success is True
    assert len(reloaded.failures) == 1
    assert reloaded.failures[0].occurrence_count == 3
    assert reloaded.failures[0].first_seen == "2026-09-26T00:00:00Z"
    assert reloaded.failures[0].last_seen == "2026-09-26T01:00:00Z"
    assert reloaded.verification is not None
    assert reloaded.verification.success is False
    assert reloaded.iteration == 4
    assert reloaded.model_calls == 7
    assert reloaded.tool_calls == 12
    assert reloaded.token_usage.total_tokens == 750


def test_persistence_to_disk_and_reload(temp_harness_dir: Path) -> None:
    """Verify state is persisted under .harness/state/<task_id>.json and reloaded cleanly."""
    manager1 = MemoryManager(base_dir=temp_harness_dir)
    manager1.initialize_task("task-disk", "Persist to disk test")
    manager1.add_relevant_file("module.py")
    manager1.record_model_call(input_tokens=100, output_tokens=50)
    save_path = manager1.save()

    expected_path = temp_harness_dir / "state" / "task-disk.json"
    assert save_path == expected_path
    assert expected_path.is_file()

    # Load in a completely fresh manager instance
    manager2 = MemoryManager(base_dir=temp_harness_dir)
    loaded_state = manager2.load("task-disk")

    assert loaded_state.task_id == "task-disk"
    assert loaded_state.relevant_files == ["module.py"]
    assert loaded_state.token_usage.total_tokens == 150
    assert loaded_state.model_calls == 1

    # Verify event log reloaded
    events = manager2.get_events()
    assert len(events) >= 2


def test_event_log_generation(temp_harness_dir: Path) -> None:
    """Verify append-only event log file contains valid JSONL lines."""
    manager = MemoryManager(base_dir=temp_harness_dir)
    manager.initialize_task("task-events", "Event logging test")
    manager.set_phase(Phase.EXECUTE)
    manager.update_hypothesis("Root cause identified")
    manager.increment_iteration()

    events_path = temp_harness_dir / "events" / "task-events.jsonl"
    assert events_path.is_file()

    lines = events_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4

    parsed_events = [json.loads(line) for line in lines]
    types = [e["type"] for e in parsed_events]
    assert types == [
        EventType.TASK_INITIALIZED.value,
        EventType.PHASE_CHANGED.value,
        EventType.HYPOTHESIS_UPDATED.value,
        EventType.ITERATION_INCREMENTED.value,
    ]


def test_uninitialized_access_raises_error(manager: MemoryManager) -> None:
    """Verify operations on an uninitialized manager raise clear RuntimeErrors."""
    with pytest.raises(RuntimeError):
        manager.get_state()

    with pytest.raises(RuntimeError):
        manager.save()

    with pytest.raises(RuntimeError):
        manager.set_phase(Phase.EXECUTE)


def test_storage_task_exists_and_list_tasks(temp_harness_dir: Path) -> None:
    """Verify task_exists and list_tasks methods on TaskStorage."""
    storage = TaskStorage(base_dir=temp_harness_dir)
    assert not storage.task_exists("task-missing")
    assert storage.list_tasks() == []

    manager = MemoryManager(storage=storage)
    manager.initialize_task("task-alpha", "Alpha task")
    manager.save()
    manager.initialize_task("task-beta", "Beta task")
    manager.save()

    assert storage.task_exists("task-alpha")
    assert storage.task_exists("task-beta")
    tasks = storage.list_tasks()
    assert sorted(tasks) == ["task-alpha", "task-beta"]


def test_crash_safe_reload_without_manual_save(temp_harness_dir: Path) -> None:
    """
    Verify that state mutations are automatically crash-safe and persisted
    atomically without calling save() manually, that only the canonical event
    log is created, and that subsequent loads preserve exact event counts.
    """
    task_id = "task-crash-safe-01"
    manager1 = MemoryManager(base_dir=temp_harness_dir)

    # 1. Initialize a task
    manager1.initialize_task(task_id, "Fix crash safety ordering")

    # 2. Perform several mutations
    manager1.set_phase(Phase.EXECUTE)
    manager1.update_plan(["1. Inspect code", "2. Fix order", "3. Test"])
    manager1.update_hypothesis("Ordering causes event/state desync")
    manager1.add_relevant_file("src/memory/manager.py")
    manager1.mark_file_touched("src/memory/manager.py")
    manager1.add_observation(
        Observation(
            type="code_inspect",
            source="manual",
            summary="Found save_state needed before append_event",
            files=["src/memory/manager.py"],
        )
    )
    manager1.add_attempt(
        Attempt(
            id="att-01",
            hypothesis="Reorder in _record_event",
            action="edit _record_event",
            files_touched=["src/memory/manager.py"],
            success=True,
            iteration=1,
        )
    )
    manager1.increment_iteration()
    manager1.record_model_call(input_tokens=250, output_tokens=75)

    # 3. Record the exact number of emitted events
    original_events = manager1.get_events()
    expected_event_count = len(original_events)
    assert expected_event_count == 10

    # Note: save() is intentionally NOT called here!

    # 4. Create a fresh MemoryManager
    manager2 = MemoryManager(base_dir=temp_harness_dir)

    # 5. Load the task without calling save() manually
    reloaded1 = manager2.load(task_id)

    # 6. Verify all latest state mutations survived
    assert reloaded1.task_id == task_id
    assert reloaded1.phase == Phase.EXECUTE
    assert reloaded1.plan == ["1. Inspect code", "2. Fix order", "3. Test"]
    assert reloaded1.current_hypothesis == "Ordering causes event/state desync"
    assert reloaded1.relevant_files == ["src/memory/manager.py"]
    assert reloaded1.touched_files == ["src/memory/manager.py"]
    assert len(reloaded1.recent_observations) == 1
    assert reloaded1.recent_observations[0].summary == "Found save_state needed before append_event"
    assert len(reloaded1.attempts) == 1
    assert reloaded1.attempts[0].id == "att-01"
    assert reloaded1.attempts[0].success is True
    assert reloaded1.iteration == 1
    assert reloaded1.model_calls == 1
    assert reloaded1.token_usage.input_tokens == 250
    assert reloaded1.token_usage.output_tokens == 75
    assert reloaded1.token_usage.total_tokens == 325

    # 7. Verify the loaded event count exactly equals the original count
    loaded_events1 = manager2.get_events()
    assert len(loaded_events1) == expected_event_count

    # 8. Create another fresh manager and loads again
    manager3 = MemoryManager(base_dir=temp_harness_dir)
    reloaded2 = manager3.load(task_id)

    # 9. Verify the event count remains exactly unchanged
    loaded_events2 = manager3.get_events()
    assert len(loaded_events2) == expected_event_count
    assert reloaded2.task_id == task_id

    # Verify only .harness/events/<task_id>.jsonl exists and no mirrored .events.jsonl exists
    canonical_event_file = temp_harness_dir / "events" / f"{task_id}.jsonl"
    assert canonical_event_file.is_file()

    mirrored_event_file = temp_harness_dir / "state" / f"{task_id}.events.jsonl"
    assert not mirrored_event_file.exists()

    state_dir_event_files = list((temp_harness_dir / "state").glob("*.events.jsonl"))
    assert len(state_dir_event_files) == 0
