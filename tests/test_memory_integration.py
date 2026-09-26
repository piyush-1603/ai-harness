"""Integration test for the Phase B1 State and Memory subsystem."""

from pathlib import Path
import pytest

from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Failure,
    Observation,
    Phase,
    TaskStatus,
)


def test_b1_end_to_end_integration_flow(tmp_path: Path) -> None:
    """
    Executes the prescribed 13-step integration sequence:
    1. initialize a task
    2. set a plan
    3. add a hypothesis
    4. mark a relevant file
    5. record an observation
    6. record a failed attempt
    7. add the same failure signature twice
    8. confirm its occurrence_count == 2
    9. record token usage
    10. save state
    11. create a fresh manager
    12. reload the state
    13. verify that all fields and counters are preserved
    """
    harness_dir = tmp_path / ".harness"
    task_id = "TASK-INTEGRATION-42"
    task_desc = "Fix TypeError in data formatting pipeline when value is None"

    # Step 1: initialize a task
    manager1 = MemoryManager(base_dir=harness_dir)
    initial_state = manager1.initialize_task(task_id=task_id, task=task_desc)
    assert initial_state.task_id == task_id
    assert initial_state.phase == Phase.PLAN
    assert initial_state.status == TaskStatus.RUNNING

    # Step 2: set a plan
    plan_steps = [
        "Search codebase for format_pipeline",
        "Inspect test suite in tests/test_format.py",
        "Add None guard in format_value()",
        "Run test suite to verify fix",
    ]
    manager1.set_plan(plan_steps)

    # Step 3: add a hypothesis
    hypothesis_str = "format_value assumes non-null string, crashing on None inputs."
    manager1.set_hypothesis(hypothesis_str)

    # Step 4: mark a relevant file
    relevant_path = "src/pipeline/formatter.py"
    added = manager1.mark_relevant_file(relevant_path)
    assert added is True

    # Step 5: record an observation
    obs = Observation(
        type="grep_result",
        source="grep_search",
        summary="Found format_value definition at line 34",
        raw_output="def format_value(val):\n    return val.strip().lower()",
        files=[relevant_path],
    )
    manager1.record_observation(obs)

    # Step 6: record a failed attempt
    attempt = Attempt(
        id="attempt-01",
        hypothesis=hypothesis_str,
        action="modify format_value to return empty string on None",
        files_touched=[relevant_path],
        result="Tests failed with assertion error in test_null_handling",
        success=False,
        iteration=1,
    )
    manager1.record_attempt(attempt)

    # Step 7: add the same failure signature twice
    err_sig = "TypeError: 'NoneType' object has no attribute 'strip'"
    fail1 = Failure(
        error_signature=err_sig,
        summary="AttributeError/TypeError on None input",
        action="run_tests",
        files=[relevant_path],
    )
    fail2 = Failure(
        error_signature=err_sig,
        summary="AttributeError/TypeError on None input (second trigger)",
        action="run_tests",
        files=[relevant_path, "tests/test_format.py"],
    )
    manager1.add_failure(fail1)
    manager1.add_failure(fail2)

    # Step 8: confirm its occurrence_count == 2
    state_before_save = manager1.get_state()
    assert len(state_before_save.failures) == 1
    recorded_failure = state_before_save.failures[0]
    assert recorded_failure.error_signature == err_sig
    assert recorded_failure.occurrence_count == 2
    repeated_failures = manager1.get_repeated_failures(min_occurrences=2)
    assert len(repeated_failures) == 1
    assert repeated_failures[0].error_signature == err_sig

    # Step 9: record token usage
    manager1.record_token_usage(input_tokens=350, output_tokens=125)
    assert state_before_save.token_usage.input_tokens == 350
    assert state_before_save.token_usage.output_tokens == 125
    assert state_before_save.token_usage.total_tokens == 475

    # Step 10: save state
    saved_file = manager1.save()
    expected_file = harness_dir / "state" / f"{task_id}.json"
    assert saved_file == expected_file
    assert expected_file.is_file()

    # Step 11: create a fresh manager
    fresh_manager = MemoryManager(base_dir=harness_dir)

    # Step 12: reload the state
    reloaded_state = fresh_manager.load(task_id=task_id)

    # Step 13: verify that all fields and counters are preserved
    assert reloaded_state.task_id == task_id
    assert reloaded_state.task == task_desc
    assert reloaded_state.phase == Phase.PLAN
    assert reloaded_state.status == TaskStatus.RUNNING
    assert reloaded_state.plan == plan_steps
    assert reloaded_state.current_hypothesis == hypothesis_str
    assert reloaded_state.relevant_files == [relevant_path]
    assert reloaded_state.touched_files == [relevant_path]

    # Observations preserved
    assert len(reloaded_state.recent_observations) == 1
    assert reloaded_state.recent_observations[0].type == "grep_result"
    assert reloaded_state.recent_observations[0].summary == "Found format_value definition at line 34"
    assert reloaded_state.recent_observations[0].raw_output == "def format_value(val):\n    return val.strip().lower()"
    assert reloaded_state.recent_observations[0].files == [relevant_path]

    # Attempts preserved
    assert len(reloaded_state.attempts) == 1
    assert reloaded_state.attempts[0].id == "attempt-01"
    assert reloaded_state.attempts[0].success is False
    assert reloaded_state.attempts[0].action == "modify format_value to return empty string on None"
    assert reloaded_state.attempts[0].files_touched == [relevant_path]

    # Failures preserved with occurrence_count == 2
    assert len(reloaded_state.failures) == 1
    assert reloaded_state.failures[0].error_signature == err_sig
    assert reloaded_state.failures[0].occurrence_count == 2
    assert reloaded_state.failures[0].first_seen is not None
    assert reloaded_state.failures[0].last_seen is not None
    assert "tests/test_format.py" in reloaded_state.failures[0].files

    # Token counters preserved
    assert reloaded_state.token_usage.input_tokens == 350
    assert reloaded_state.token_usage.output_tokens == 125
    assert reloaded_state.token_usage.total_tokens == 475

    # Events reloaded in fresh manager
    reloaded_events = fresh_manager.get_events()
    assert len(reloaded_events) >= 6
