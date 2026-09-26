import argparse
import os
import sys
import uuid

from src.context.scanner import RepositoryScanner
from src.memory.manager import MemoryManager
from src.memory.models import Observation
from src.common.types import TaskSpec
from src.orchestrator.model_adapter import ModelAdapter
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.verification.verifier import VerificationEngine
from src.tools.registry import ToolEngine
from src.auth.github_auth import check_repo_access, clone_authenticated, parse_repo_identifier


def main():
    parser = argparse.ArgumentParser(description="AI Coding Harness - Orchestrator")
    parser.add_argument("--issue", type=str, required=True, help="The issue description to solve.")
    parser.add_argument("--max-attempts", type=int, default=8, help="Maximum number of steps before escalating.")
    parser.add_argument("--workspace", type=str, default=".", help="Workspace root directory to scan.")
    parser.add_argument("--test-command", type=str, default=os.environ.get("HARNESS_TEST_COMMAND", ""), help="Test command to run during verification.")
    parser.add_argument("--repo", type=str, help="GitHub repository to authenticate and clone (e.g. owner/name)")
    parser.add_argument("--github-token", type=str, default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub token for auth")
    parser.add_argument("--require-auth", action="store_true", help="Require authorization via --repo")
    parser.add_argument("--mock", action="store_true", help="Run with a mocked ModelAdapter")

    args = parser.parse_args()


    require_auth = args.require_auth or os.environ.get("HARNESS_REQUIRE_AUTH", "").lower() in ("true", "1")
    if require_auth and not args.repo:
        print("Error: --require-auth is set but --repo is missing.", file=sys.stderr)
        sys.exit(1)

    auth_result = None
    if args.repo:
        auth_result = check_repo_access(args.repo, args.github_token)
        print(f"Authorized {auth_result.owner}/{auth_result.name}")
        
        if not os.path.exists(args.workspace):
            clone_authenticated(args.repo, args.workspace, args.github_token)
        else:
            # Check if workspace is non-empty
            if os.path.isdir(args.workspace) and os.listdir(args.workspace):
                import subprocess
                try:
                    res = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=args.workspace,
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    url = res.stdout.strip()
                    # Example URLs: https://github.com/owner/name.git or git@github.com:owner/name.git
                    # We can use parse_repo_identifier on a cleaned url, or just simple substring match
                    # Let's extract the owner/name part
                    if "github.com" in url:
                        part = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
                        ws_owner, ws_name = parse_repo_identifier(part)
                    else:
                        ws_owner, ws_name = "", ""
                        
                    if (ws_owner.lower(), ws_name.lower()) != (auth_result.owner.lower(), auth_result.name.lower()):
                        print(f"Error: Existing workspace {args.workspace} does not match authorized repo {auth_result.owner}/{auth_result.name}.", file=sys.stderr)
                        sys.exit(1)
                except subprocess.CalledProcessError:
                    print(f"Error: Existing workspace {args.workspace} is not a valid git repository or missing origin remote.", file=sys.stderr)
                    sys.exit(1)


    print(f"Scanning repository at {args.workspace}...")
    
    # Real Integration Path
    task_spec = TaskSpec(
        issue_id=f"task-{str(uuid.uuid4())[:8]}",
        issue_description=args.issue,
        workspace_dir=args.workspace,
        test_command=args.test_command,
    )
    
    config = OrchestratorConfig(step_limit=args.max_attempts)
    tool_engine = ToolEngine(workspace_dir=args.workspace)
    
    if args.mock:
        import json
        mock_responses = [
            json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo 'print(\"hello world\")' > script.py"}}),
            json.dumps({"action": "complete", "message": "I have fixed the issue by updating script.py."})
        ]
        adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    else:
        adapter = ModelAdapter()
        
    orchestrator = Orchestrator(
        model_adapter=adapter,
        config=config,
        tool_engine=tool_engine,
    )
    

    print(f"Starting orchestration for issue: {args.issue}")


    report = orchestrator.run(task_spec)

    print("\n--- Final Report ---")
    print(f"Status: {report['status']}")
    print(f"Attempts: {report.get('n_calls', 0)}")
    print(f"Cost: ${report.get('cost', 0.0):.2f}")
    print(f"Verified: {report.get('verified', False)}")
    if auth_result:
        report["auth"] = {
            "owner": auth_result.owner,
            "name": auth_result.name,
            "authorized_at": auth_result.authorized_at
        }
        print(f"Auth Evidence: {auth_result.owner}/{auth_result.name}")

    if report.get("last_error"):
        print(f"Last Error: {report['last_error']}")

    if "telemetry" in report:
        print("\n--- Telemetry Report ---")
        tel = report["telemetry"]
        print(f"Total Model Calls: {tel.total_model_calls}")
        print(f"Total Tool Calls:  {tel.total_tool_calls}")
        print(f"Prompt Tokens:     {tel.prompt_tokens}")
        print(f"Completion Tokens: {tel.completion_tokens}")
        print(f"Files Modified:    {len(tel.files_modified)}")
        print(f"Time Elapsed:      {tel.total_wall_time_sec:.2f}s")

    if report.get("status") in ("resolved", "completed") and report.get("verified", False):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
