import argparse
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
        state = self.memory.get_state()
        if not state:
            return self._scratchpad
            
        self._scratchpad.hypothesis = state.current_hypothesis or ""
        self._scratchpad.attempt_history = [str(a.action) for a in state.attempts]
        self._scratchpad.files_touched = state.touched_files
        self._scratchpad.task_summary = state.task
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
    args = parser.parse_args()

    # During Hr 1-14, we use stubs for Person B (Context) and Person C (Tools).
    # Once they land their code, we will swap these out for the real implementations.
    memory = MemoryManager()
    memory.initialize_task(task_id="demo_issue_1", task=args.issue)
    builder = ContextBuilder()
    context = ContextAdapter(memory=memory, builder=builder)
    tool_engine = StubToolEngine()
    verifier = StubVerifier(1)
    model = StubModel()
    
    from src.orchestrator.orchestrator import OrchestratorConfig
    config = OrchestratorConfig(step_limit=args.max_attempts)
    orchestrator = Orchestrator(context=context, tool_engine=tool_engine, verifier=verifier, model=model, config=config)
    
    print(f"Starting orchestration for issue: {args.issue}")
    report = orchestrator.run(args.issue)
    
    print("\n--- Final Report ---")
    print(f"Status: {report['status']}")
    print(f"Attempts: {report.get('n_calls', 0)}")
    print(f"Cost: ${report.get('cost', 0.0):.2f}")
    if report['last_error']:
        print(f"Last Error: {report['last_error']}")
    
    scratchpad = report['scratchpad']
    print(f"Final Hypothesis: {scratchpad.hypothesis}")
    
    if report['status'] == "resolved":
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
