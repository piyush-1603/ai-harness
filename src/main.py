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
from src.tools.file_ops import truncate_output
from src.tools.registry import ToolEngine
from src.auth.github_auth import check_repo_access, clone_authenticated, parse_repo_identifier
from src.verification.verifier import VerificationEngine


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
    parser.add_argument(
        "--test-command",
        type=str,
        default=os.environ.get("HARNESS_TEST_COMMAND", ""),
        help="Command to run tests for verification.",
    )
    parser.add_argument("--repo", type=str, help="GitHub repository to authenticate and clone (e.g. owner/name)")
    parser.add_argument("--github-token", type=str, default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub token for auth")
    parser.add_argument("--require-auth", action="store_true", help="Require authorization via --repo")
    parser.add_argument("--mock", action="store_true", help="Run with a mocked ModelAdapter")

    args = parser.parse_args()

    workspace_dir = os.environ.get("HARNESS_WORKSPACE_DIR") or args.workspace or "."
    max_steps = int(os.environ.get("HARNESS_MAX_STEPS") or args.max_attempts or 30)
    test_command = args.test_command or os.environ.get("HARNESS_TEST_COMMAND")

    # --- Auth Hardening Gate ---
    require_auth = args.require_auth or os.environ.get("HARNESS_REQUIRE_AUTH", "").lower() in ("true", "1")
    if require_auth and not args.repo:
        print("Error: --require-auth is set but --repo is missing.", file=sys.stderr)
        sys.exit(1)

    auth_result = None
    if args.repo:
        auth_result = check_repo_access(args.repo, args.github_token)
        print(f"Authorized {auth_result.owner}/{auth_result.name}")
        
        if not os.path.exists(workspace_dir):
            clone_authenticated(args.repo, workspace_dir, args.github_token)
        else:
            # Check if workspace is non-empty
            if os.path.isdir(workspace_dir) and os.listdir(workspace_dir):
                import subprocess
                try:
                    res = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=workspace_dir,
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    url = res.stdout.strip()
                    if "github.com" in url:
                        part = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
                        ws_owner, ws_name = parse_repo_identifier(part)
                    else:
                        ws_owner, ws_name = "", ""
                        
                    if (ws_owner.lower(), ws_name.lower()) != (auth_result.owner.lower(), auth_result.name.lower()):
                        print(f"Error: Existing workspace {workspace_dir} does not match authorized repo {auth_result.owner}/{auth_result.name}.", file=sys.stderr)
                        sys.exit(1)
                except subprocess.CalledProcessError:
                    print(f"Error: Existing workspace {workspace_dir} is not a valid git repository or missing origin remote.", file=sys.stderr)
                    sys.exit(1)

    # --- Core Subsystems Initialization ---
    verbose = os.environ.get("HARNESS_VERBOSE", "").lower() in ("true", "1", "yes")
    if verbose:
        print(f"Scanning repository at {workspace_dir}...")
    repo_index = RepositoryScanner().scan(workspace_dir)

    task_spec = TaskSpec(
        issue_id="task-1",
        issue_description=args.issue,
        workspace_dir=workspace_dir,
        test_command=test_command,
    )

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
    verifier = VerificationEngine()
    
    # --- Model Mocking Injection ---
    if args.mock:
        import json
        mock_responses = [
            json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo 'print(\"hello world\")' > script.py"}}),
            json.dumps({"action": "complete", "message": "I have fixed the issue by updating script.py."})
        ]
        model_adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    else:
        model_adapter = ModelAdapter()

    config = OrchestratorConfig(step_limit=max_steps)
    orchestrator = Orchestrator(
        context=context,
        tool_engine=tool_engine,
        verifier=verifier,
        model_adapter=model_adapter,
        memory_manager=memory,
        context_builder=builder,
        config=config,
    )

    print(f"Starting orchestration for issue: {args.issue}")

    report = orchestrator.run(task_spec)

    # --- Output Generation ---
    print("\n--- Final Report ---")
    print(f"Status: {report['status']}")
    print(f"Verified: {report.get('verified', False)}")

    verification_status = report.get("verification_status")
    if verification_status:
        print(f"Verification: {verification_status}")

    failure_class = report.get("failure_classification")
    if failure_class:
        print(f"Failure Classification: {failure_class}")

    test_cmd = report.get("test_command")
    if test_cmd:
        print(f"Test Command: {test_cmd}")

    if not report.get("verified", False):
        v_rep = report.get("verification_report")
        if v_rep and getattr(v_rep, "summary", None):
            print(f"Verification Summary: {v_rep.summary}")
        test_out = (report.get("test_output") or "").strip()
        if test_out:
            print("Test Output:")
            print(truncate_output(test_out, max_chars=1000))
        elif report.get("verification_error"):
            print(f"Verification Error: {report['verification_error']}")

    print(f"Attempts: {report.get('n_calls', 0)}")
    print(f"Cost: ${report.get('cost', 0.0):.2f}")
    
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