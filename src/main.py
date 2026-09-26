from src.context.scanner import RepositoryScanner
import os
import argparse
import os
import sys
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.test_orchestrator import StubToolEngine, StubVerifier, StubModel
from src.memory.manager import MemoryManager
from src.context.builder import ContextBuilder
from src.common.types import ScratchpadState
import uuid

class ContextAdapter:
    def __init__(self, memory: MemoryManager, builder: ContextBuilder):
        self.memory = memory
        self.builder = builder
        self._scratchpad = ScratchpadState()
        
    def get_scratchpad(self) -> ScratchpadState:
        # Build the context bundle using ContextBuilder to apply relevance packing, budgeting, etc.
        bundle = self.builder.build(self.memory)
        
        self._scratchpad.hypothesis = bundle.current_hypothesis or ""
        self._scratchpad.files_touched = bundle.touched_files
        self._scratchpad.task_summary = bundle.task
        
        # Combine recent and failed attempts from the bundle
        attempts = []
        if bundle.recent_attempts:
            attempts.extend([a.action for a in bundle.recent_attempts])
        if bundle.failed_attempts:
            attempts.extend([a.action for a in bundle.failed_attempts])
        self._scratchpad.attempt_history = attempts
        
        # NOTE: This is a highly lossy mapping.
        # ContextBundle contains rich intelligence (repository_overview, repository_symbols, 
        # important_discoveries, recent_observations, token_usage, repeated_failures) 
        # that ScratchpadState simply does not have fields for. The Orchestrator is currently 
        # dropping all of Person B's advanced context packing.
        
        return self._scratchpad

    def update_scratchpad(self, hypothesis: str = None, attempt: str = None):
        if hypothesis:
            self.memory.set_hypothesis(hypothesis)
        if attempt:
            self.memory.add_attempt({
                "id": str(uuid.uuid4())[:8],
                "action": attempt
            })


def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument("--max-attempts", type=int, default=8, help="Maximum number of steps before escalating.")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace root directory to scan.")
    args = parser.parse_args()

    # During Hr 1-14, we use stubs for Person B (Context) and Person C (Tools).
    # Once they land their code, we will swap these out for the real implementations.

    # Build RepositoryIndex once at run start
    print(f"Scanning repository at {args.workspace}...")
    repo_index = RepositoryScanner().scan(args.workspace)
    
    memory = MemoryManager()
    memory.initialize_task(task_id="demo_issue_1", task=args.issue)
    
    # Pass repository_index to ContextBuilder so it is NOT rebuilt inside the loop
    builder = ContextBuilder(repository_index=repo_index)

    context = ContextAdapter(memory=memory, builder=builder)
    tool_engine = StubToolEngine()
    verifier = StubVerifier(1)
    model = StubModel()
    
    from src.orchestrator.orchestrator import OrchestratorConfig
    config = OrchestratorConfig(step_limit=args.max_attempts)
    orchestrator = Orchestrator(context=context, tool_engine=tool_engine, verifier=verifier, model=model, config=config)
    
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
