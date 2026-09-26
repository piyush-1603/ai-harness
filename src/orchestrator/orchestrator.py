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
    VerificationResult,
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


class Orchestrator:
    """Core autonomous coding orchestrator."""

    def __init__(
        self,
        context: Optional[Any] = None,
        tools: Optional[Any] = None,
        config: Optional[OrchestratorConfig] = None,
        model_adapter: Optional[ModelAdapter] = None,
        tool_engine: Optional[ToolEngine] = None,
        memory_manager: Optional[MemoryManager] = None,
        context_builder: Optional[ContextBuilder] = None,
    ):
        self.context = context
        self.tools = tools
        self.config = config or OrchestratorConfig()

        self._is_legacy_stub = (
            self.tools is not None
            and hasattr(self.tools, "explore")
            and hasattr(self.tools, "edit")
            and hasattr(self.tools, "verify")
            and not hasattr(self.tools, "execute")
            and model_adapter is None
        )

        # ModelAdapter
        self.model_adapter = model_adapter or ModelAdapter()

        # ToolEngine resolution
        if tool_engine is not None:
            self.tool_engine = tool_engine
        elif isinstance(tools, ToolEngine):
            self.tool_engine = tools
        elif tools is not None and hasattr(tools, "execute"):
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

        if self.context is None:
            self.context = StubContext()
        self.context.update_scratchpad(hypothesis=f"Investigating: {task_spec.issue_description}")

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
                # Slice 3: Terminate loop with completion state. Verification hard gate is subsequent step.
                return self._make_report(
                    status="completed",
                    step_count=step_count,
                    task_spec=task_spec,
                    completion_message=decision.message,
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
                output_summary = (tool_result.output or tool_result.error or "")[:300]
                self.memory_manager.record_observation(
                    Observation(
                        type="tool_result",
                        source=decision.tool_name.value,
                        summary=f"{decision.tool_name.value} ({'success' if tool_result.success else 'failed'}): {output_summary}",
                        raw_output=tool_result.output if tool_result.success else (tool_result.error or tool_result.output),
                        files=[arg_path] if arg_path else [],
                    )
                )

                # Record attempt
                self.memory_manager.record_attempt(
                    Attempt(
                        id=f"step_{step_count}",
                        action=f"{decision.tool_name.value}({json.dumps(decision.tool_args, sort_keys=True)})",
                        success=tool_result.success,
                        files_touched=[arg_path] if decision.tool_name in (ToolName.EDIT_FILE, ToolName.WRITE_FILE) and arg_path else [],
                        result=output_summary,
                    )
                )
                self.memory_manager.record_tool_call()

                if hasattr(self.context, "update_scratchpad"):
                    self.context.update_scratchpad(
                        attempt=f"{decision.tool_name.value} -> {'success' if tool_result.success else 'failure'}: {output_summary}"
                    )

                if not tool_result.success:
                    self.last_error = tool_result.error or f"Tool {decision.tool_name.value} failed"
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

    def _make_report(
        self,
        status: str,
        step_count: int,
        task_spec: TaskSpec,
        completion_message: Optional[str] = None,
        last_error: Optional[str] = None,
        start_time: float = 0.0,
        tool_call_counts: Optional[Dict[str, int]] = None,
        files_inspected: Optional[List[str]] = None,
        files_modified: Optional[List[str]] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> dict:
        wall_time = time.perf_counter() - start_time if start_time > 0 else 0.0
        sp = self.context.get_scratchpad() if hasattr(self.context, "get_scratchpad") else ScratchpadState()

        telemetry = TelemetryReport(
            total_model_calls=step_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            total_tool_calls=sum((tool_call_counts or {}).values()),
            tool_call_counts=dict(tool_call_counts or {}),
            files_inspected=list(files_inspected or []),
            files_modified=list(files_modified or []),
            recovery_attempts=len(getattr(sp, "history_attempts", [])),
            total_wall_time_sec=wall_time,
        )

        return {
            "status": status,
            "n_calls": step_count,
            "cost": self.cost,
            "last_error": last_error,
            "message": completion_message or ("Task completed." if status == "completed" else ""),
            "verified": False,
            "scratchpad": sp,
            "telemetry": telemetry,
        }

    # -----------------------------------------------------------------------
    # Legacy Stub Handlers (Backward Compatibility for Unit Tests)
    # -----------------------------------------------------------------------

    def _run_legacy(self, issue: str) -> dict:
        self.context.update_scratchpad(hypothesis=f"Investigating: {issue}")

        while self.n_calls < self.config.step_limit and self.cost < self.config.cost_limit:
            self.n_calls += 1
            self.cost += 0.05

            if self.state == State.EXPLORING:
                self._explore(issue)
            elif self.state == State.EDITING:
                self._edit()
            elif self.state == State.TESTING:
                self._test()
            elif self.state == State.DONE:
                return self._report("resolved")
            elif self.state == State.ESCALATE:
                return self._report("blocked")

        self.state = State.ESCALATE
        return self._report("blocked_step_cap")

    def _explore(self, issue: str):
        result: ToolResult = self.tools.explore(issue, self.context.get_scratchpad())
        self.context.update_scratchpad(attempt=f"explore: {result.output[:200]}")
        self.state = State.EDITING

    def _edit(self):
        result: ToolResult = self.tools.edit(self.context.get_scratchpad())
        self.context.update_scratchpad(attempt=f"edit: {result.output[:200]}")
        self.state = State.TESTING

    def _test(self):
        verification: VerificationResult = self.tools.verify()

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

class StubContext:
    def __init__(self):
        self._state = ScratchpadState()

    def get_scratchpad(self) -> ScratchpadState:
        return self._state

    def update_scratchpad(self, hypothesis: str = None, attempt: str = None):
        if hypothesis:
            self._state.hypothesis = hypothesis
        if attempt:
            self._state.attempt_history.append(attempt)


class StubTools:
    """Simulates: fails once with error A, then succeeds — to test recovery."""
    def __init__(self):
        self._call_count = 0

    def explore(self, issue, scratchpad) -> ToolResult:
        return ToolResult(success=True, output=f"found relevant file for: {issue}")

    def edit(self, scratchpad) -> ToolResult:
        self._call_count += 1
        return ToolResult(success=True, output=f"applied edit attempt #{self._call_count}")

    def verify(self) -> VerificationResult:
        if self._call_count < 2:
            return VerificationResult(passed=False, failing_tests=["test_discounts.py::test_zero_qty"])
        return VerificationResult(passed=True)
