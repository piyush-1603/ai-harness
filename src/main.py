import argparse
import os
import sys

import uuid

from src.context.scanner import RepositoryScanner
from src.memory.manager import MemoryManager
from src.common.types import TaskSpec
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.verification.verifier import VerificationEngine


def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument("--max-attempts", type=int, default=8, help="Maximum number of steps before escalating.")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace root directory to scan.")
    args = parser.parse_args()

    # Build RepositoryIndex once at run start
    print(f"Scanning repository at {args.workspace}...")
    
    # Real Integration Path
    task_spec = TaskSpec(
        issue_id=f"task-{str(uuid.uuid4())[:8]}",
        issue_description=args.issue,
        workspace_dir=args.workspace,
    )
    

    config = OrchestratorConfig(step_limit=args.max_attempts)
    orchestrator = Orchestrator(config=config)
    
    print(f"Starting orchestration for issue: {args.issue}")
    report = orchestrator.run(task_spec)

    print("\n--- Final Report ---")
    print(f"Status: {report['status']}")
    print(f"Attempts: {report.get('n_calls', 0)}")
    print(f"Cost: ${report.get('cost', 0.0):.2f}")
    print(f"Verified: {report.get('verified', False)}")
    if report.get("last_error"):
        print(f"Last Error: {report['last_error']}")

    if report.get("status") in ("resolved", "completed") and report.get("verified", False):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
