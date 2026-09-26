"""Orchestrator module for Track A.

Manages the core autonomous coding loop, coordinating:
- TaskSpec issue definition
- MemoryManager and ContextBuilder (Track B)
- ModelAdapter querying and decision parsing (Track A)
- ToolEngine execution (Track C)
- Recovery and escalation mechanisms (Track A)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from src.common.types import ToolResult, ScratchpadState, VerificationResult
from src.common.types import ToolCall, ToolName
from src.orchestrator.model_adapter import ModelCompletion

from enum import Enum, auto
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from src.common.types import (
    AgentStatus,
    FailureClassification,
    ScratchpadState,
    TaskSpec,
    TelemetryReport,
    ToolCall,
    ToolName,
    ToolResult,
    VerificationReport,
    VerificationResult,
    VerificationStatus,
)
from src.context.builder import ContextBuilder
from src.context.bundle import ContextBundle
from src.context.config import ContextConfig
from src.memory.manager import MemoryManager
from src.memory.models import (
    Attempt,
    Failure,
    Observation,
    TaskState,
    TaskStatus,
)
from src.memory.adapters import (
    attempt_from_tool_result,
    failure_from_verification_report,
    observation_from_tool_result,
    verification_from_report,
)
from src.verification.verifier import VerificationEngine
from src.orchestrator.model_adapter import (
    ModelAdapter,
    ModelAPIError,
    ModelCompletion,
    ModelParseError,
)
from src.orchestrator.prompting import PromptBuilder
from src.orchestrator.recovery import RecoveryDecision, RecoveryManager
from src.tools.registry import ToolEngine

logger = logging.getLogger(__name__)


class OrchestratorConfig(BaseModel):
    step_limit: int = 8
    cost_limit: float = 3.0
    system_template: str = ""
    instance_template: str = ""


# ---------------------------------------------------------------------------
# State machine for legacy stub compatibility
# ---------------------------------------------------------------------------

class State(Enum):
    EXPLORING = auto()
    EDITING = auto()
    TESTING = auto()
    DONE = auto()
    ESCALATE = auto()


class _LegacyContext:
    """Internal context fallback for legacy test harness backward compatibility."""

    def __init__(self) -> None:
        self._state = ScratchpadState()

    def get_scratchpad(self) -> ScratchpadState:
        return self._state

    def update_scratchpad(
        self, hypothesis: Optional[str] = None, attempt: Optional[str] = None
    ) -> None:
        if hypothesis:
            self._state.hypothesis = hypothesis
        if attempt:
            self._state.attempt_history.append(attempt)


class Orchestrator:
    def __init__(
        self,
        context=None,
        tools=None,
        verifier=None,
        model=None,
        config: OrchestratorConfig = None,
        model_adapter=None,
        tool_engine=None,
        memory_manager=None,
        context_builder=None,
    ):
        """
        context: object implementing get_scratchpad() / update_scratchpad()
        tools: object implementing explore(), edit(), verify() (legacy)
        tool_engine: object implementing execute(ToolCall)
        verifier: object implementing verify() -> VerificationResult
        model: object implementing decide(prompt) -> Union[ToolCall, ModelCompletion]
        config: OrchestratorConfig holding step_limit, cost_limit, templates
        """
        self.context = context
        self.verifier = verifier if verifier is not None else VerificationEngine()
        self.model = model
        self.config = config or OrchestratorConfig()

        self._is_legacy_stub = (
            model_adapter is None and tool_engine is None
        )

        # ModelAdapter
        self.model_adapter = model_adapter or ModelAdapter()

        # ToolEngine resolution
        if tool_engine is not None and hasattr(tool_engine, "execute"):
            self.tool_engine = tool_engine
        elif tools is not None and hasattr(tools, "execute"):
            self.tool_engine = tools
        elif tools is not None:
            # Fallback to tools for legacy tests that pass StubToolEngine
            self.tool_engine = tools
        else:
            workspace_dir = os.environ.get("HARNESS_WORKSPACE_DIR", ".")
            self.tool_engine = ToolEngine(workspace_dir=workspace_dir)

        # Memory and Context systems
        self.memory_manager = memory_manager or MemoryManager()
        self.context_builder = context_builder or ContextBuilder()

        # Legacy and state tracking
        self.state = State.EXPLORING
        self.n_calls = 0
        self.cost = 0.0
        self.last_error: Optional[str] = None
        self.recovery_manager = RecoveryManager()

        kwargs = {}
        if self.config.system_template:
            kwargs["system_template"] = self.config.system_template
        if self.config.instance_template:
            kwargs["instance_template"] = self.config.instance_template
        self.prompt_builder = PromptBuilder(**kwargs)

    def run(self, task: Union[TaskSpec, str]) -> dict:
        """Drive the orchestrator loop for an issue. Returns a report dict."""
        if self._is_legacy_stub:
            issue_str = task.issue_description if isinstance(task, TaskSpec) else str(task)
            return self._run_legacy(issue_str)

        return self._run_real_loop(task)

    # -----------------------------------------------------------------------
    # Real Model <-> Tool Loop (Slice 3)
    # -----------------------------------------------------------------------

    def _run_real_loop(self, task: Union[TaskSpec, str]) -> dict:
        if isinstance(task, TaskSpec):
            task_spec = task
        else:
            task_spec = TaskSpec(
                issue_id="task-1",
                issue_description=str(task),
                workspace_dir=os.environ.get("HARNESS_WORKSPACE_DIR", "."),
            )

        self.memory_manager.initialize_task(
            task_id=task_spec.issue_id,
            task=task_spec.issue_description,
        )
        self.memory_manager.set_hypothesis(f"Investigating: {task_spec.issue_description}")

        step_count = 0
        max_steps = self.config.step_limit
        last_tool_call: Optional[ToolCall] = None
        last_tool_result: Optional[ToolResult] = None
        start_time = time.perf_counter()

        tool_call_counts: Dict[str, int] = {}
        files_inspected: List[str] = []
        files_modified: List[str] = []
        prompt_tokens_accum = 0
        completion_tokens_accum = 0

        while step_count < max_steps:
            step_count += 1
            self.n_calls = step_count
            self.memory_manager.increment_iteration()

            # 1. Build context from MemoryManager and accumulated interaction state
            context_bundle = self.context_builder.build(self.memory_manager)
            turn_prompt = self._build_turn_prompt(
                task_spec=task_spec,
                context_bundle=context_bundle,
                last_tool_call=last_tool_call,
                last_tool_result=last_tool_result,
            )

            # 2. Query model and parse decision
            try:
                decision = self.model_adapter.decide(turn_prompt)
            except ModelAPIError as e:
                self.last_error = str(e)
                self.memory_manager.record_observation(
                    Observation(
                        type="error",
                        source="model_adapter",
                        summary=f"ModelAPIError: {e}",
                    )
                )
                return self._make_report(
                    status="error",
                    step_count=step_count,
                    task_spec=task_spec,
                    last_error=self.last_error,
                    start_time=start_time,
                    tool_call_counts=tool_call_counts,
                    files_inspected=files_inspected,
                    files_modified=files_modified,
                    prompt_tokens=prompt_tokens_accum,
                    completion_tokens=completion_tokens_accum,
                )
            except ModelParseError as e:
                self.last_error = str(e)
                self.memory_manager.record_observation(
                    Observation(
                        type="error",
                        source="model_parser",
                        summary=f"ModelParseError: {e}",
                    )
                )
                # Malformed model response: NEVER execute a tool!
                last_tool_call = None
                last_tool_result = ToolResult(
                    tool_name=ToolName.RUN_BASH,
                    success=False,
                    output=f"ModelParseError: {e}. You must output a single valid JSON object.",
                    error=str(e),
                )
                continue

            # Record token usage and cost
            if hasattr(self.model_adapter, "last_token_usage"):
                p_tok = self.model_adapter.last_token_usage.get("prompt_tokens", 0)
                c_tok = self.model_adapter.last_token_usage.get("completion_tokens", 0)
                prompt_tokens_accum += p_tok
                completion_tokens_accum += c_tok
                self.memory_manager.record_model_call(input_tokens=p_tok, output_tokens=c_tok)
                self.cost += (p_tok * 0.00000015) + (c_tok * 0.0000006)

            # 3. Handle Decision: ModelCompletion vs ToolCall
            if isinstance(decision, ModelCompletion):
                self.memory_manager.record_observation(
                    Observation(
                        type="completion",
                        source="model",
                        summary=f"Model completion: {decision.message}",
                    )
                )

                # Slice 4: Independent verification before declaring task completion
                verification_report: Optional[VerificationReport] = None
                verification_error: Optional[str] = None
                try:
                    if self.verifier is not None:
                        verification_report = self.verifier.verify(
                            workspace_dir=task_spec.workspace_dir,
                            test_command=task_spec.test_command,
                        )
                except Exception as e:
                    logger.error(f"VerificationEngine raised exception: {e}", exc_info=True)
                    verification_error = f"Verification raised unexpected exception: {e}"
                    verification_report = VerificationReport(
                        status=VerificationStatus.FAILED,
                        is_verified=False,
                        tests_passed=False,
                        test_command=task_spec.test_command or "",
                        test_output="",
                        files_modified=[],
                        git_diff="",
                        failure_classification=FailureClassification.UNKNOWN_ERROR,
                        syntax_valid=False,
                        summary=f"Verification raised unexpected exception: {e}",
                    )

                is_verified = bool(verification_report and verification_report.is_verified)

                # Record verification into MemoryManager via typed adapters
                if verification_report is not None:
                    v_res = verification_from_report(verification_report)
                    self.memory_manager.set_verification(v_res)
                    fail = failure_from_verification_report(verification_report)
                    if fail is not None:
                        self.memory_manager.add_failure(fail)
                        self.memory_manager.record_observation(
                            Observation(
                                type="verification_failure",
                                source="verification_engine",
                                summary=verification_report.summary,
                                raw_output=verification_report.test_output,
                                files=verification_report.files_modified,
                            )
                        )
                        failing_items = (
                            [verification_report.failure_classification.value]
                            if verification_report.failure_classification
                            else []
                        )
                        self.recovery_manager.analyze_failure(
                            failing_items,
                            verification_report.test_output or verification_report.summary,
                        )
                    else:
                        self.memory_manager.record_observation(
                            Observation(
                                type="verification_success",
                                source="verification_engine",
                                summary=verification_report.summary,
                                raw_output=verification_report.test_output,
                                files=verification_report.files_modified,
                            )
                        )

                final_status = "completed" if is_verified else "failed"

                return self._make_report(
                    status=final_status,
                    step_count=step_count,
                    task_spec=task_spec,
                    completion_message=decision.message,
                    last_error=self.last_error,
                    verified=is_verified,
                    verification_report=verification_report,
                    verification_error=verification_error,
                    start_time=start_time,
                    tool_call_counts=tool_call_counts,
                    files_inspected=files_inspected,
                    files_modified=files_modified,
                    prompt_tokens=prompt_tokens_accum,
                    completion_tokens=completion_tokens_accum,
                )

            elif isinstance(decision, ToolCall):
                call_name = decision.tool_name.value
                tool_call_counts[call_name] = tool_call_counts.get(call_name, 0) + 1

                # Execute through canonical ToolEngine
                tool_result = self.tool_engine.execute(decision)
                last_tool_call = decision
                last_tool_result = tool_result

                # Track inspected/modified paths
                arg_path = decision.tool_args.get("path")
                if arg_path and isinstance(arg_path, str):
                    if decision.tool_name in (ToolName.READ_FILE, ToolName.GREP_SEARCH, ToolName.LIST_DIRECTORY):
                        if arg_path not in files_inspected:
                            files_inspected.append(arg_path)
                            self.memory_manager.add_relevant_file(arg_path)
                    elif decision.tool_name in (ToolName.EDIT_FILE, ToolName.WRITE_FILE):
                        if arg_path not in files_modified:
                            files_modified.append(arg_path)
                            self.memory_manager.add_touched_file(arg_path)
                            if hasattr(self.context, "get_scratchpad"):
                                sp = self.context.get_scratchpad()
                                if hasattr(sp, "files_touched") and arg_path not in sp.files_touched:
                                    sp.files_touched.append(arg_path)

                # Record observation
                obs = observation_from_tool_result(
                    result=tool_result,
                    files=[arg_path] if arg_path else []
                )
                self.memory_manager.record_observation(obs)

                # Record attempt
                action_str = f"{decision.tool_name.value}({json.dumps(decision.tool_args, sort_keys=True)})"
                attempt = attempt_from_tool_result(
                    result=tool_result,
                    attempt_id=f"step_{step_count}",
                    action=action_str,
                    files_touched=[arg_path] if decision.tool_name in (ToolName.EDIT_FILE, ToolName.WRITE_FILE) and arg_path else [],
                    iteration=step_count
                )
                self.memory_manager.record_attempt(attempt)
                self.memory_manager.record_tool_call()

                if not tool_result.success:
                    self.last_error = tool_result.error or f"Tool {decision.tool_name.value} failed"
                    output_summary = (tool_result.output or tool_result.error or "")[:300]
                    self.memory_manager.add_failure(
                        Failure(
                            error_signature=f"{decision.tool_name.value}:{tool_result.error or 'failed'}",
                            summary=f"Tool {decision.tool_name.value} failed: {tool_result.error or output_summary}",
                            action=decision.tool_name.value,
                            files=[arg_path] if arg_path else [],
                        )
                    )

        # Exceeded step limit without completion
        self.state = State.ESCALATE
        return self._make_report(
            status="blocked_step_cap",
            step_count=step_count,
            task_spec=task_spec,
            last_error=self.last_error or "Exceeded HARNESS_MAX_STEPS limit",
            start_time=start_time,
            tool_call_counts=tool_call_counts,
            files_inspected=files_inspected,
            files_modified=files_modified,
            prompt_tokens=prompt_tokens_accum,
            completion_tokens=completion_tokens_accum,
        )

    def _build_turn_prompt(
        self,
        task_spec: TaskSpec,
        context_bundle: ContextBundle,
        last_tool_call: Optional[ToolCall] = None,
        last_tool_result: Optional[ToolResult] = None,
        recovery_hint: Optional[str] = None,
    ) -> str:
        prompt_parts: list[str] = [
            "You are an autonomous AI coding agent. You can explore a repository, edit files, and run tests.",
            "Your goal is to fix the issue described and verify your changes.",
            "",
            "AVAILABLE TOOLS:",
            "1. read_file(path: str, start_line: int = 1, end_line: int = -1)",
            "2. edit_file(path: str, search_block: str, replace_block: str)",
            "3. write_file(path: str, content: str)",
            "4. run_bash(command: str, timeout: int = 60)",
            "5. run_tests(test_command: str = None, timeout: int = 60)",
            "6. git_diff(path: str = None)",
            "7. git_status()",
            "8. grep_search(query: str, path: str = '.')",
            "9. list_directory(path: str = '.')",
            "",
            "RESPONSE FORMAT:",
            "Respond ONLY with a single JSON object. No conversational prefix or markdown outside the JSON.",
            "For a tool call:",
            '{"tool_name": "<tool_name>", "tool_args": { ... }}',
            "For completion:",
            '{"action": "complete", "message": "<resolution description>"}',
            "",
            context_bundle.render_text(),
        ]

        if last_tool_call is not None and last_tool_result is not None:
            prompt_parts.extend([
                "",
                "## LAST TOOL EXECUTION",
                f"Tool: {last_tool_call.tool_name.value}",
                f"Arguments: {json.dumps(last_tool_call.tool_args)}",
                f"Success: {last_tool_result.success}",
                f"Output:\n{last_tool_result.output if last_tool_result.success else (last_tool_result.error or last_tool_result.output or 'Tool failed without error output')}",
            ])

        if recovery_hint:
            prompt_parts.extend([
                "",
                "## RECOVERY HINT",
                recovery_hint,
            ])

        prompt_parts.extend([
            "",
            "What is your next action? Respond with a single JSON object.",
        ])

        return "\n".join(prompt_parts)

    def _build_scratchpad_from_memory(self, task_spec: Optional[TaskSpec] = None) -> ScratchpadState:
        try:
            mem_state = self.memory_manager.get_state()
        except Exception:
            mem_state = None

        if mem_state is not None:
            hypothesis = mem_state.current_hypothesis or ""
            identified = list(mem_state.relevant_files)
            touched = list(mem_state.touched_files)
            task_summary = mem_state.task
            attempt_history = [
                f"{a.action} -> {'success' if a.success else 'failure'}: {a.result}"
                for a in mem_state.attempts
            ]
            return ScratchpadState(
                task_summary=task_summary,
                identified_files=identified,
                active_hypothesis=hypothesis,
                files_modified=touched,
                hypothesis=hypothesis,
                files_touched=touched,
                attempt_history=attempt_history,
            )

        summary = task_spec.issue_description if task_spec else ""
        return ScratchpadState(task_summary=summary)

    def _make_report(
        self,
        status: str,
        step_count: int,
        task_spec: TaskSpec,
        completion_message: Optional[str] = None,
        last_error: Optional[str] = None,
        verified: bool = False,
        verification_report: Optional[VerificationReport] = None,
        verification_error: Optional[str] = None,
        start_time: float = 0.0,
        tool_call_counts: Optional[Dict[str, int]] = None,
        files_inspected: Optional[List[str]] = None,
        files_modified: Optional[List[str]] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> dict:
        wall_time = time.perf_counter() - start_time if start_time > 0 else 0.0
        if hasattr(self.context, "get_scratchpad"):
            sp = self.context.get_scratchpad()
        else:
            sp = self._build_scratchpad_from_memory(task_spec)

        effective_last_error = last_error if last_error is not None else self.last_error
        if effective_last_error is None and verification_error is not None:
            effective_last_error = verification_error

        merged_files_modified = list(files_modified or [])
        if verification_report and verification_report.files_modified:
            for f in verification_report.files_modified:
                if f not in merged_files_modified:
                    merged_files_modified.append(f)

        telemetry = TelemetryReport(
            total_model_calls=step_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            total_tool_calls=sum((tool_call_counts or {}).values()),
            tool_call_counts=dict(tool_call_counts or {}),
            files_inspected=list(files_inspected or []),
            files_modified=merged_files_modified,
            recovery_attempts=len(getattr(sp, "history_attempts", [])),
            total_wall_time_sec=wall_time,
        )

        return {
            "status": status,
            "n_calls": step_count,
            "cost": self.cost,
            "last_error": effective_last_error,
            "verification_error": verification_error,
            "message": completion_message or ("Task completed." if status == "completed" else ""),
            "verified": verified,
            "verification_report": verification_report,
            "test_command": verification_report.test_command if verification_report else (task_spec.test_command or ""),
            "test_output": verification_report.test_output if verification_report else "",
            "git_diff": verification_report.git_diff if verification_report else "",
            "verification_status": verification_report.status.value if verification_report else None,
            "failure_classification": (
                verification_report.failure_classification.value
                if verification_report and verification_report.failure_classification
                else None
            ),
            "scratchpad": sp,
            "telemetry": telemetry,
        }

    # -----------------------------------------------------------------------
    # Legacy Stub Handlers (Backward Compatibility for Unit Tests)
    # -----------------------------------------------------------------------

    def _run_legacy(self, issue: str) -> dict:
        if self.context is None:
            self.context = _LegacyContext()
        self.context.update_scratchpad(hypothesis=f"Investigating: {issue}")

        while self.n_calls < self.config.step_limit and self.cost < self.config.cost_limit:
            self.n_calls += 1
            self.cost += 0.05

            if self.state in (State.EXPLORING, State.EDITING):
                self._run_tool_loop(issue)

            elif self.state == State.TESTING:
                self._test()
            elif self.state == State.DONE:
                return self._report("resolved")
            elif self.state == State.ESCALATE:
                return self._report("blocked")

        self.state = State.ESCALATE
        return self._report("blocked_step_cap")

    # -- state handlers ----------------------------------------------------

    def _run_tool_loop(self, issue: str):
        last_obs = self.context.get_scratchpad().attempt_history[-1] if self.context.get_scratchpad().attempt_history else "No observation yet."
        hint = self.recovery_manager.last_error_signature if self.state == State.EDITING else None
        
        prompt = self.prompt_builder.build_turn_prompt(
            issue=issue,
            scratchpad_state=self.context.get_scratchpad(),
            last_observation=last_obs,
            recovery_hint=hint
        )
        decision = self.model.decide(prompt)
        
        if isinstance(decision, ModelCompletion):
            if self.state == State.EXPLORING:
                self.state = State.EDITING
            else:
                self.state = State.TESTING
        else:
            result: ToolResult = self.tool_engine.execute(decision)
            self.context.update_scratchpad(attempt=f"{self.state.name} tool {decision.tool_name.value} output: {result.output[:200]}")

    def _test(self):
        verification: VerificationResult = self.verifier.verify()

        if verification.passed:
            self.state = State.DONE
            self.recovery_manager.reset()
            return

        advice = self.recovery_manager.analyze_failure(
            verification.failing_tests, verification.stderr
        )

        last_observation = verification.failing_tests or verification.stderr[:100]
        self.context.update_scratchpad(
            attempt=f"test failed: {last_observation}"
        )

        if advice.decision == RecoveryDecision.FORCE_EXPLORE:
            self.context.update_scratchpad(hypothesis=advice.hint)
            self.state = State.EXPLORING
        elif advice.decision == RecoveryDecision.RETRY_EDIT:
            self.state = State.EDITING
        else:
            self.state = State.ESCALATE

    def _report(self, status: str) -> dict:
        return {
            "status": status,
            "n_calls": self.n_calls,
            "cost": self.cost,
            "last_error": self.recovery_manager.last_error_signature,
            "scratchpad": self.context.get_scratchpad(),
        }


# ---------------------------------------------------------------------------
# Stubs for backward-compatible imports and testing
# ---------------------------------------------------------------------------

