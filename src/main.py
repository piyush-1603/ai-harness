import argparse
import sys
from src.orchestrator.orchestrator import Orchestrator, StubContext, StubTools

def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument("--max-attempts", type=int, default=8, help="Maximum number of steps before escalating.")
    args = parser.parse_args()

    # During Hr 1-14, we use stubs for Person B (Context) and Person C (Tools).
    # Once they land their code, we will swap these out for the real implementations.
    context = StubContext()
    tools = StubTools()
    
    from src.orchestrator.orchestrator import OrchestratorConfig
    config = OrchestratorConfig(step_limit=args.max_attempts)
    orchestrator = Orchestrator(context=context, tools=tools, config=config)
    
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
