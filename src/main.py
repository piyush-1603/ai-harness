import argparse
import os
import sys
import uuid

from src.common.types import ScratchpadState, TaskSpec
from src.context.builder import ContextBuilder
from src.context.config import ContextConfig
from src.context.scanner import RepositoryScanner
from src.memory.manager import MemoryManager
from src.orchestrator.model_adapter import ModelAdapter
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine


class ContextAdapter:
    def __init__(self, memory: MemoryManager, builder: ContextBuilder):
        self.memory = memory
        self.builder = builder
        self._scratchpad = ScratchpadState()

    def get_scratchpad(self) -> ScratchpadState:
        bundle = self.builder.build(self.memory)

        self._scratchpad.hypothesis = bundle.current_hypothesis or ""
        self._scratchpad.files_touched = bundle.touched_files
        self._scratchpad.task_summary = bundle.task

        attempts = []
        if bundle.recent_attempts:
            attempts.extend([a.action for a in bundle.recent_attempts])
        if bundle.failed_attempts:
            attempts.extend([a.action for a in bundle.failed_attempts])
        self._scratchpad.attempt_history = attempts

        return self._scratchpad

    def update_scratchpad(self, hypothesis: str = None, attempt: str = None):
        if hypothesis:
            self.memory.set_hypothesis(hypothesis)
        if attempt:
            self.memory.add_attempt({
                "id": str(uuid.uuid4())[:8],
                "action": attempt,
            })


def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.environ.get("HARNESS_MAX_STEPS", "30")),
        help="Maximum number of steps before escalating.",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=os.environ.get("HARNESS_WORKSPACE_DIR", "."),
        help="Workspace root directory to scan.",
    )
    args = parser.parse_args()

    workspace_dir = os.environ.get("HARNESS_WORKSPACE_DIR") or args.workspace or "."
    max_steps = int(os.environ.get("HARNESS_MAX_STEPS") or args.max_attempts or 30)

    task_spec = TaskSpec(
        issue_id="task-1",
        issue_description=args.issue,
        workspace_dir=workspace_dir,
    )

    verbose = os.environ.get("HARNESS_VERBOSE", "").lower() in ("true", "1", "yes")
    if verbose:
        print(f"Scanning repository at {workspace_dir}...")
    repo_index = RepositoryScanner().scan(workspace_dir)

    memory = MemoryManager()
    memory.initialize_task(task_id=task_spec.issue_id, task=task_spec.issue_description)

    ctx_kwargs = {}
    if "HARNESS_CONTEXT_MAX_TOKENS" in os.environ:
        try:
            ctx_kwargs["max_context_tokens"] = int(os.environ["HARNESS_CONTEXT_MAX_TOKENS"])
        except ValueError:
            pass
    context_config = ContextConfig(**ctx_kwargs) if ctx_kwargs else None
    builder = ContextBuilder(repository_index=repo_index, config=context_config)

    context = ContextAdapter(memory=memory, builder=builder)

    tool_timeout = int(os.environ.get("HARNESS_TOOL_TIMEOUT", "60"))
    tool_engine = ToolEngine(workspace_dir=workspace_dir, default_timeout=tool_timeout)
    model_adapter = ModelAdapter()

    config = OrchestratorConfig(step_limit=max_steps)
    orchestrator = Orchestrator(
        context=context,
        tool_engine=tool_engine,
        model_adapter=model_adapter,
        memory_manager=memory,
        context_builder=builder,
        config=config,
    )

    print(f"Starting orchestration for issue: {args.issue}")
    report = orchestrator.run(task_spec)

    print("\n--- Final Report ---")
    print(f"Status: {report['status']}")
    print(f"Attempts: {report.get('n_calls', 0)}")
    print(f"Cost: ${report.get('cost', 0.0):.2f}")
    if report.get("last_error"):
        print(f"Last Error: {report['last_error']}")

    scratchpad = report.get("scratchpad")
    if scratchpad:
        hypothesis = getattr(scratchpad, "hypothesis", None) or getattr(scratchpad, "active_hypothesis", "")
        print(f"Final Hypothesis: {hypothesis}")

    if report.get("status") in ("resolved", "completed"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
