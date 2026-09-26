"""Integration test for Phase B2 Deterministic Context Builder with realistic B1 memory."""

from pathlib import Path
import pytest

from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Discovery,
    Failure,
    Observation,
    Phase,
    TaskStatus,
    VerificationResult,
)


def test_b2_realistic_memory_to_context_bundle_integration(tmp_path: Path) -> None:
    """
    Creates realistic B1 memory containing plans, hypotheses, observations, attempts,
    repeated failures, verification results, and token usage.
    Builds a context bundle and verifies only the intended information appears,
    without mutating the underlying TaskState.
    """
    harness_dir = tmp_path / ".harness"
    manager = MemoryManager(base_dir=harness_dir)

    # 1. Initialize Task
    task_desc = "Resolve race condition in payment checkout webhook"
    manager.initialize_task(task_id="task-b2-int", task=task_desc)
    manager.set_phase(Phase.EXECUTE)
    manager.set_status(TaskStatus.RUNNING)

    # 2. Plan & Hypothesis
    plan_steps = [
        "1. Inspect webhook handler in src/payment/webhook.py",
        "2. Check database lock timeout in src/payment/db.py",
        "3. Add idempotency key check",
        "4. Run integration tests",
    ]
    manager.set_plan(plan_steps)
    manager.set_hypothesis(
        "Concurrent webhook retries process the same invoice simultaneously "
        "because idempotency check is after balance deduction."
    )

    # 3. Relevant & Touched Files
    manager.add_relevant_file("src/payment/webhook.py")
    manager.add_relevant_file("src/payment/db.py")
    manager.add_relevant_file("src/payment/models.py")
    manager.mark_file_touched("src/payment/webhook.py")

    # 4. Discoveries
    manager.add_discovery(
        Discovery(
            statement="Stripe webhook header contains Stripe-Signature and idempotency key",
            evidence="src/payment/webhook.py:22",
            files=["src/payment/webhook.py"],
            confidence=0.98,
        )
    )
    manager.add_discovery(
        Discovery(
            statement="PostgreSQL row-level lock FOR UPDATE NOWAIT is supported",
            evidence="src/payment/db.py:54",
            files=["src/payment/db.py"],
            confidence=0.90,
        )
    )

    # 5. Populate 12 Observations with raw outputs
    for i in range(12):
        manager.add_observation(
            Observation(
                type="tool_execution",
                source=f"tool_{i % 3}",
                summary=f"Observation summary step {i:02d}",
                raw_output=f"VERBOSE_RAW_OUTPUT_DATA_STEP_{i}\nLots of detailed log lines...\nEND",
                files=["src/payment/webhook.py"],
            )
        )

    # 6. Populate 6 Attempts (mix of failed and successful)
    attempts_data = [
        ("att-1", "Add mutex in python memory", False, "Fails across multiple worker processes", 1),
        ("att-2", "Add Redis lock", False, "Redis connection pool timeout", 1),
        ("att-3", "Database lock with select_for_update", True, "Lock acquired successfully", 2),
        ("att-4", "Check idempotency table before lock", True, "Duplicate checks pass", 2),
        ("att-5", "Shorten lock timeout to 2s", False, "Heavy load tests timed out", 3),
        ("att-6", "Use optimistic concurrency token", True, "All race conditions resolved", 3),
    ]
    for att_id, action, success, result, iter_num in attempts_data:
        manager.add_attempt(
            Attempt(
                id=att_id,
                hypothesis="Resolve race condition",
                action=action,
                files_touched=["src/payment/webhook.py"],
                result=result,
                success=success,
                iteration=iter_num,
            )
        )

    # 7. Add Failures (one repeated twice, one occurring only once)
    repeated_sig = "IntegrityError: duplicate key value violates unique constraint"
    manager.add_failure(
        Failure(
            error_signature=repeated_sig,
            summary="Duplicate invoice insert on concurrent webhooks",
            action="execute_webhook",
            files=["src/payment/webhook.py"],
        )
    )
    manager.add_failure(
        Failure(
            error_signature=repeated_sig,
            summary="Duplicate invoice insert on concurrent webhooks (second run)",
            action="execute_webhook",
            files=["src/payment/webhook.py"],
        )
    )
    single_sig = "LockWaitTimeout: lock wait timeout exceeded"
    manager.add_failure(
        Failure(
            error_signature=single_sig,
            summary="DB lock wait timeout under stress test",
            action="run_stress_test",
            files=["src/payment/db.py"],
        )
    )

    # 8. Current Errors & Verification
    manager.set_current_errors([repeated_sig])
    manager.set_verification(
        VerificationResult(
            success=True,
            summary="All 15 checkout integration tests passed",
            tests_passed=["test_webhook_retry", "test_idempotency_concurrent"],
            tests_failed=[],
        )
    )

    # 9. Telemetry
    manager.record_model_call(input_tokens=1200, output_tokens=450)
    manager.record_model_call(input_tokens=800, output_tokens=300)
    manager.record_tool_call()
    manager.record_tool_call()
    manager.record_tool_call()

    # -------------------------------------------------------------------------
    # Build ContextBundle with Default Rules
    # -------------------------------------------------------------------------
    builder = ContextBuilder()
    bundle = builder.build(manager)

    # Verify Task & Lifecycle
    assert bundle.task == task_desc
    assert bundle.phase == Phase.EXECUTE
    assert bundle.status == TaskStatus.RUNNING
    assert bundle.plan == plan_steps
    assert bundle.current_hypothesis is not None

    # Verify Files
    assert bundle.relevant_files == [
        "src/payment/webhook.py",
        "src/payment/db.py",
        "src/payment/models.py",
    ]
    assert bundle.touched_files == ["src/payment/webhook.py"]

    # Verify Discoveries
    assert len(bundle.important_discoveries) == 2

    # Verify Observation Recency & Raw Output Excluded by Default
    # Out of 12 observations, only the last 8 must appear
    assert len(bundle.recent_observations) == 8
    expected_summaries = [f"Observation summary step {i:02d}" for i in range(4, 12)]
    assert [o.summary for o in bundle.recent_observations] == expected_summaries
    # Raw output must be None by default
    for o in bundle.recent_observations:
        assert o.raw_output is None

    # Verify Attempt Recency (default max_attempts=5 out of 6)
    assert len(bundle.recent_attempts) == 5
    assert [a.id for a in bundle.recent_attempts] == ["att-2", "att-3", "att-4", "att-5", "att-6"]

    # Verify Failed Attempts
    assert len(bundle.failed_attempts) == 3
    assert all(not a.success for a in bundle.failed_attempts)
    assert [a.id for a in bundle.failed_attempts] == ["att-1", "att-2", "att-5"]

    # Verify Repeated Failures (only count >= 2)
    assert len(bundle.repeated_failures) == 1
    assert bundle.repeated_failures[0].error_signature == repeated_sig
    assert bundle.repeated_failures[0].occurrence_count == 2

    # Verify Current Errors
    assert bundle.current_errors == [repeated_sig]

    # Verify Verification & Telemetry
    assert bundle.latest_verification_result is not None
    assert bundle.latest_verification_result.success is True
    assert bundle.model_call_count == 2
    assert bundle.tool_call_count == 3
    assert bundle.token_usage.total_tokens == 2750

    # -------------------------------------------------------------------------
    # Verify Rendered Text Output
    # -------------------------------------------------------------------------
    rendered = bundle.render_text()
    assert "## TASK\nResolve race condition in payment checkout webhook" in rendered
    assert "## PHASE / STATUS\nPhase: EXECUTE\nStatus: RUNNING" in rendered
    assert "## PLAN" in rendered
    assert "## CURRENT HYPOTHESIS" in rendered
    assert "## RELEVANT FILES" in rendered
    assert "## DISCOVERIES" in rendered
    assert "## RECENT OBSERVATIONS" in rendered
    assert "## FAILED ATTEMPTS" in rendered
    assert "## REPEATED FAILURES" in rendered
    assert "## CURRENT ERRORS" in rendered
    assert "## VERIFICATION" in rendered
    assert "## EFFICIENCY" in rendered

    # Verify dropped/excluded items are NOT in rendered text
    assert "VERBOSE_RAW_OUTPUT_DATA" not in rendered
    assert "Observation summary step 00" not in rendered
    assert "Observation summary step 01" not in rendered
    assert "Observation summary step 02" not in rendered
    assert "Observation summary step 03" not in rendered
    assert "LockWaitTimeout" not in rendered

    # Verify included items ARE in rendered text
    assert "Observation summary step 11" in rendered
    assert repeated_sig in rendered
    assert "2750 total" in rendered

    # -------------------------------------------------------------------------
    # Verify Underling Memory State Was NOT Mutated
    # -------------------------------------------------------------------------
    orig_state = manager.get_state()
    assert len(orig_state.recent_observations) == 12
    assert orig_state.recent_observations[0].raw_output is not None
    assert "VERBOSE_RAW_OUTPUT_DATA_STEP_0" in orig_state.recent_observations[0].raw_output
    assert len(orig_state.attempts) == 6
    assert len(orig_state.failures) == 2
