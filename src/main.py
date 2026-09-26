import argparse
import os
import sys

from src.common.types import TaskSpec
from src.orchestrator.model_adapter import ModelAdapter
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine


def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.environ.get("HARNESS_MAX_STEPS", "30")),
        help="Maximum number of steps before escalating.",
    )
    args = parser.parse_args()

    workspace_dir = os.environ.get("HARNESS_WORKSPACE_DIR", ".")
    task_spec = TaskSpec(
        issue_id="task-1",
        issue_description=args.issue,
        workspace_dir=workspace_dir,
    )

    config = OrchestratorConfig(step_limit=args.max_attempts)
    tool_engine = ToolEngine(workspace_dir=workspace_dir)
    model_adapter = ModelAdapter()

    orchestrator = Orchestrator(
        model_adapter=model_adapter,
        tool_engine=tool_engine,
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
