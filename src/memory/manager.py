"""MemoryManager providing a clean, single-source-of-truth API for task state and memory."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
from typing import Any, Optional, Union

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
    VerificationResult,
    now_iso,
)
from src.memory.storage import TaskStorage


class MemoryManager:
    """
    Coordinates task state and persistent memory for a single coding task.
    Acts as the single source of truth for what the agent is doing, what it has learned,
    what it has tried, what failed, and how much budget it has consumed.
    """

    def __init__(
        self,
        storage: Optional[TaskStorage] = None,
        base_dir: Union[Path, str] = ".harness",
    ) -> None:
        self.storage = storage or TaskStorage(base_dir=base_dir)
        self._state: Optional[TaskState] = None
        self._events: list[Event] = []

    # -------------------------------------------------------------------------
    # Core Lifecycle & State Access
    # -------------------------------------------------------------------------

    def initialize_task(self, task_id: str, task: str) -> TaskState:
        """
        Initialize a new task state and reset the in-memory event stream.
        """
        now = now_iso()
        self._state = TaskState(
            task_id=task_id,
            task=task,
            phase=Phase.PLAN,
            status=TaskStatus.RUNNING,
            plan=[],
            current_hypothesis=None,
            relevant_files=[],
            touched_files=[],
            discoveries=[],
            recent_observations=[],
            attempts=[],
            failures=[],
            current_errors=[],
            verification=None,
            iteration=0,
            model_calls=0,
            tool_calls=0,
            token_usage=TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            created_at=now,
            updated_at=now,
        )
        self._events = []
        self._record_event(
            EventType.TASK_INITIALIZED,
            summary=f"Task initialized: {task_id}",
            metadata={"task_id": task_id, "task": task},
        )
        return self._state

    def load(self, task_id: str) -> TaskState:
        """
        Load task state from disk storage, restoring all records, enums, and event logs.
        """
        self._state = self.storage.load_state(task_id)
        self._events = self.storage.load_events(task_id)
        return self._state

    def save(self) -> Path:
        """
        Persist the current TaskState to disk under .harness/state/<task_id>.json.
        """
        state = self.get_state()
        state.updated_at = now_iso()
        return self.storage.save_state(state)

    def get_state(self) -> TaskState:
        """
        Retrieve the current TaskState. Raises RuntimeError if no state is loaded.
        """
        if self._state is None:
            raise RuntimeError(
                "No active task state. Call initialize_task() or load() before accessing state."
            )
        return self._state

    @property
    def state(self) -> TaskState:
        """Property shorthand for get_state()."""
        return self.get_state()

    # -------------------------------------------------------------------------
    # Lifecycle & Status Mutations
    # -------------------------------------------------------------------------

    def set_phase(self, phase: Union[Phase, str]) -> None:
        """Update the current lifecycle phase (PLAN, EXECUTE, VERIFY, RECOVER)."""
        state = self.get_state()
        if isinstance(phase, str):
            try:
                phase = Phase(phase.upper())
            except (ValueError, KeyError):
                phase = Phase[phase]

        state.phase = phase
        state.updated_at = now_iso()
        self._record_event(
            EventType.PHASE_CHANGED,
            summary=f"Phase changed to {phase.value}",
            metadata={"phase": phase.value},
        )

    def set_status(self, status: Union[TaskStatus, str]) -> None:
        """Update the task status (RUNNING, DONE, BLOCKED, FAILED)."""
        state = self.get_state()
        if isinstance(status, str):
            try:
                status = TaskStatus(status.upper())
            except (ValueError, KeyError):
                status = TaskStatus[status]

        state.status = status
        state.updated_at = now_iso()
        self._record_event(
            EventType.STATUS_CHANGED,
            summary=f"Status changed to {status.value}",
            metadata={"status": status.value},
        )

    # -------------------------------------------------------------------------
    # Plan & Hypothesis Management
    # -------------------------------------------------------------------------

    def update_plan(self, plan: list[str]) -> None:
        """Update the current execution plan steps."""
        state = self.get_state()
        state.plan = list(plan)
        state.updated_at = now_iso()
        self._record_event(
            EventType.PLAN_UPDATED,
            summary=f"Plan updated ({len(plan)} steps)",
            metadata={"plan": list(plan)},
        )

    def set_plan(self, plan: list[str]) -> None:
        """Alias for update_plan."""
        self.update_plan(plan)

    def update_hypothesis(self, hypothesis: Optional[str]) -> None:
        """Update or clear the active hypothesis."""
        state = self.get_state()
        state.current_hypothesis = hypothesis
        state.updated_at = now_iso()
        self._record_event(
            EventType.HYPOTHESIS_UPDATED,
            summary=f"Hypothesis updated: {hypothesis}" if hypothesis else "Hypothesis cleared",
            metadata={"hypothesis": hypothesis},
        )

    def set_hypothesis(self, hypothesis: Optional[str]) -> None:
        """Alias for update_hypothesis."""
        self.update_hypothesis(hypothesis)

    # -------------------------------------------------------------------------
    # File Tracking with Deduplication
    # -------------------------------------------------------------------------

    def add_relevant_file(self, path: str) -> bool:
        """
        Add a file to relevant_files, preventing duplicates.
        Returns True if added, False if already present.
        """
        state = self.get_state()
        norm_path = os.path.normpath(path)
        if path in state.relevant_files or norm_path in state.relevant_files:
            return False

        state.relevant_files.append(path)
        state.updated_at = now_iso()
        self._record_event(
            EventType.RELEVANT_FILE_ADDED,
            summary=f"Relevant file added: {path}",
            metadata={"path": path},
        )
        return True

    def mark_relevant_file(self, path: str) -> bool:
        """Alias for add_relevant_file."""
        return self.add_relevant_file(path)

    def mark_file_touched(self, path: str) -> bool:
        """
        Add a file to touched_files, preventing duplicates.
        Returns True if added, False if already present.
        """
        state = self.get_state()
        norm_path = os.path.normpath(path)
        if path in state.touched_files or norm_path in state.touched_files:
            return False

        state.touched_files.append(path)
        state.updated_at = now_iso()
        self._record_event(
            EventType.FILE_TOUCHED,
            summary=f"File touched: {path}",
            metadata={"path": path},
        )
        return True

    def add_touched_file(self, path: str) -> bool:
        """Alias for mark_file_touched."""
        return self.mark_file_touched(path)

    # -------------------------------------------------------------------------
    # Discoveries, Observations & Attempts
    # -------------------------------------------------------------------------

    def add_discovery(self, discovery: Union[Discovery, dict[str, Any]]) -> Discovery:
        """Record a new discovery / insight."""
        state = self.get_state()
        if isinstance(discovery, dict):
            discovery = Discovery.from_dict(discovery)

        state.discoveries.append(discovery)
        state.updated_at = now_iso()
        self._record_event(
            EventType.DISCOVERY_ADDED,
            summary=f"Discovery added: {discovery.statement[:80]}",
            metadata={"statement": discovery.statement, "confidence": discovery.confidence},
        )
        return discovery

    def add_observation(self, observation: Union[Observation, dict[str, Any]]) -> Observation:
        """Record a new observation or tool execution output."""
        state = self.get_state()
        if isinstance(observation, dict):
            observation = Observation.from_dict(observation)

        state.recent_observations.append(observation)
        state.updated_at = now_iso()
        self._record_event(
            EventType.OBSERVATION_ADDED,
            summary=f"Observation added: {observation.summary[:80]}",
            metadata={"type": observation.type, "source": observation.source},
        )
        return observation

    def record_observation(self, observation: Union[Observation, dict[str, Any]]) -> Observation:
        """Alias for add_observation."""
        return self.add_observation(observation)

    def add_attempt(self, attempt: Union[Attempt, dict[str, Any]]) -> Attempt:
        """
        Record an action attempt, automatically updating touched_files if specified.
        """
        state = self.get_state()
        if isinstance(attempt, dict):
            attempt = Attempt.from_dict(attempt)

        state.attempts.append(attempt)
        for f in attempt.files_touched:
            norm_f = os.path.normpath(f)
            if f not in state.touched_files and norm_f not in state.touched_files:
                state.touched_files.append(f)

        state.updated_at = now_iso()
        self._record_event(
            EventType.ATTEMPT_RECORDED,
            summary=f"Attempt {attempt.id} recorded (success={attempt.success})",
            metadata={"id": attempt.id, "success": attempt.success, "action": attempt.action},
        )
        return attempt

    def record_attempt(self, attempt: Union[Attempt, dict[str, Any]]) -> Attempt:
        """Alias for add_attempt."""
        return self.add_attempt(attempt)

    # -------------------------------------------------------------------------
    # Failure Handling & Occurrence Deduplication
    # -------------------------------------------------------------------------

    def add_failure(self, failure: Union[Failure, dict[str, Any]]) -> Failure:
        """
        Record a failure. If a failure with the same error_signature already exists:
        - increment occurrence_count
        - update last_seen
        - preserve first_seen
        - update summary, action, and files
        """
        state = self.get_state()
        if isinstance(failure, dict):
            failure = Failure.from_dict(failure)

        existing = next(
            (f for f in state.failures if f.error_signature == failure.error_signature),
            None,
        )

        now = now_iso()
        if existing is not None:
            reopened = False
            if existing.resolved:
                existing.resolved = False
                existing.resolved_at = None
                reopened = True

            existing.occurrence_count += 1
            existing.last_seen = failure.last_seen or now
            if failure.summary:
                existing.summary = failure.summary
            if failure.action:
                existing.action = failure.action
            for f in failure.files:
                if f not in existing.files:
                    existing.files.append(f)

            state.updated_at = now
            self._record_event(
                EventType.FAILURE_RECORDED,
                summary=f"Repeated failure updated: {existing.error_signature} (count={existing.occurrence_count})",
                metadata={
                    "error_signature": existing.error_signature,
                    "occurrence_count": existing.occurrence_count,
                    "is_repeated": True,
                    "reopened": reopened,
                },
            )
            return existing

        # First encounter of this error_signature
        if not failure.first_seen:
            failure.first_seen = now
        if not failure.last_seen:
            failure.last_seen = failure.first_seen
        if failure.occurrence_count <= 0:
            failure.occurrence_count = 1

        state.failures.append(failure)
        state.updated_at = now
        self._record_event(
            EventType.FAILURE_RECORDED,
            summary=f"Failure recorded: {failure.error_signature} (count={failure.occurrence_count})",
            metadata={
                "error_signature": failure.error_signature,
                "occurrence_count": failure.occurrence_count,
                "is_repeated": False,
            },
        )
        return failure

    def record_failure(self, failure: Union[Failure, dict[str, Any]]) -> Failure:
        """Alias for add_failure."""
        return self.add_failure(failure)

    def resolve_failure(self, error_signature: str) -> bool:
        """
        Mark an active failure as resolved without deleting it from historical memory.
        Returns True if an unresolved failure was found and marked resolved, False otherwise.
        """
        state = self.get_state()
        failure = next(
            (f for f in state.failures if f.error_signature == error_signature and not f.resolved),
            None,
        )
        if failure is None:
            return False

        now = now_iso()
        failure.resolved = True
        failure.resolved_at = now
        state.updated_at = now
        self._record_event(
            EventType.FAILURE_RESOLVED,
            summary=f"Failure resolved: {failure.error_signature}",
            metadata={"error_signature": failure.error_signature, "resolved_at": failure.resolved_at},
        )
        return True

    def resolve_all_failures(self) -> int:
        """
        Mark all currently active (unresolved) failures as resolved.
        Returns the number of failures resolved.
        """
        state = self.get_state()
        unresolved = [f for f in state.failures if not f.resolved]
        if not unresolved:
            return 0

        now = now_iso()
        for f in unresolved:
            f.resolved = True
            f.resolved_at = now

        state.updated_at = now
        self._record_event(
            EventType.FAILURE_RESOLVED,
            summary=f"All active failures resolved ({len(unresolved)} failures)",
            metadata={
                "count": len(unresolved),
                "signatures": [f.error_signature for f in unresolved],
            },
        )
        return len(unresolved)

    def get_repeated_failures(self, min_occurrences: int = 2) -> list[Failure]:
        """
        Return list of failures that have occurred at least min_occurrences times.
        """
        state = self.get_state()
        return [f for f in state.failures if f.occurrence_count >= min_occurrences]

    # -------------------------------------------------------------------------
    # Verification & Errors
    # -------------------------------------------------------------------------

    def set_current_errors(self, errors: list[str]) -> None:
        """Update current active errors."""
        state = self.get_state()
        state.current_errors = list(errors)
        state.updated_at = now_iso()
        self._record_event(
            EventType.ERRORS_UPDATED,
            summary=f"Current errors updated ({len(errors)} errors)",
            metadata={"errors": list(errors)},
        )

    def set_verification(
        self, result: Optional[Union[VerificationResult, dict[str, Any]]]
    ) -> Optional[VerificationResult]:
        """Record the latest verification / test run outcome."""
        state = self.get_state()
        if isinstance(result, dict):
            result = VerificationResult.from_dict(result)

        state.verification = result
        state.updated_at = now_iso()
        self._record_event(
            EventType.VERIFICATION_UPDATED,
            summary=f"Verification updated: success={result.success if result else None}",
            metadata={
                "success": result.success if result else None,
                "summary": result.summary if result else "",
            },
        )
        if result is not None and result.success:
            self.resolve_all_failures()
        return result

    # -------------------------------------------------------------------------
    # Iterations, Tool Calls & Token Accounting
    # -------------------------------------------------------------------------

    def increment_iteration(self) -> int:
        """Increment task iteration counter by 1."""
        state = self.get_state()
        state.iteration += 1
        state.updated_at = now_iso()
        self._record_event(
            EventType.ITERATION_INCREMENTED,
            summary=f"Iteration incremented to {state.iteration}",
            metadata={"iteration": state.iteration},
        )
        return state.iteration

    def record_model_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Increment model_calls and accumulate input/output tokens."""
        state = self.get_state()
        state.model_calls += 1
        state.token_usage.add(input_tokens=input_tokens, output_tokens=output_tokens)
        state.updated_at = now_iso()
        self._record_event(
            EventType.MODEL_CALL_RECORDED,
            summary=f"Model call #{state.model_calls} (input={input_tokens}, output={output_tokens})",
            metadata={
                "model_calls": state.model_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": state.token_usage.total_tokens,
            },
        )

    def record_token_usage(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Accumulate token usage without incrementing model calls."""
        state = self.get_state()
        state.token_usage.add(input_tokens=input_tokens, output_tokens=output_tokens)
        state.updated_at = now_iso()
        self._record_event(
            EventType.TOKEN_USAGE_RECORDED,
            summary=f"Token usage recorded (input={input_tokens}, output={output_tokens})",
            metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": state.token_usage.total_tokens,
            },
        )

    def record_tool_call(self) -> int:
        """Increment tool_calls counter by 1."""
        state = self.get_state()
        state.tool_calls += 1
        state.updated_at = now_iso()
        self._record_event(
            EventType.TOOL_CALL_RECORDED,
            summary=f"Tool call #{state.tool_calls}",
            metadata={"tool_calls": state.tool_calls},
        )
        return state.tool_calls

    # -------------------------------------------------------------------------
    # Event Log Management
    # -------------------------------------------------------------------------

    def _record_event(
        self,
        event_type: Union[EventType, str],
        summary: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Event:
        type_str = event_type.value if isinstance(event_type, Enum) else str(event_type)
        state = self._state
        iteration = state.iteration if state is not None else 0
        task_id = state.task_id if state is not None else "unknown"

        event = Event(
            type=type_str,
            timestamp=now_iso(),
            iteration=iteration,
            summary=summary,
            metadata=metadata or {},
        )
        self._events.append(event)
        if self.storage and state is not None and task_id != "unknown":
            # Canonical ordering: mutate TaskState -> persist TaskState -> append Event
            self.storage.save_state(state)
            self.storage.append_event(task_id, event)
        return event

    def get_events(self) -> list[Event]:
        """Return a copy of the recorded event list."""
        return list(self._events)

    @property
    def events(self) -> list[Event]:
        """Property shorthand for get_events()."""
        return self.get_events()
