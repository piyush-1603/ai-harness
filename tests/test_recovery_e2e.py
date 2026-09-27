"""Phase 8 Hermetic E2E Test: Autonomous Recovery Loop Across Production Path.

Proves the actual recovery loop across the real production orchestration path:
1. Workspace with an intentionally incorrect implementation where test command initially fails.
2. Production components wired together: TaskSpec, MemoryManager, ContextBuilder,
   ContextAdapter, ToolEngine, VerificationEngine, RecoveryManager, and Orchestrator.
3. Turn 1: Model performs an insufficient edit (logic remains broken).
4. Turn 2: Model completes -> Verification fails -> RecoveryManager produces RETRY_EDIT.
5. Turn 3: Model receives failure evidence in prompt and performs corrective edit.
6. Turn 4: Model completes -> Verification passes -> status="completed", verified=True.
7. Validates:
   - status == "completed" and verified is True
   - recovery_attempts >= 1
   - Final test command actually passes in the workspace
   - Verification report, test output, git diff, and modified files are preserved
   - No premature/fake success before verification passed
"""

import json
from pathlib import Path
import subprocess

from src.common.types import FailureClassification, TaskSpec, VerificationStatus
from src.context.builder import ContextBuilder
from src.context.scanner import RepositoryScanner
from src.main import ContextAdapter
from src.memory.manager import MemoryManager
from src.orchestrator.model_adapter import ModelAdapter
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine
from src.verification.verifier import VerificationEngine


def _init_git_repo(ws: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "E2E Tester"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.com"], cwd=str(ws), check=True, capture_output=True)


def test_production_path_autonomous_recovery_e2e(tmp_path: Path) -> None:
    """End-to-end proof of autonomous recovery loop on production orchestration path."""
    ws = tmp_path
    _init_git_repo(ws)

    # 1. Tiny Python project with an intentionally incorrect implementation
    app_file = ws / "app.py"
    app_file.write_text("def solve(x: int) -> int:\n    return x - 1\n", encoding="utf-8")

    test_file = ws / "test_app.py"
    test_file.write_text(
        "from app import solve\nassert solve(10) == 20\nprint('Tests passed successfully')\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init: broken solve implementation"], cwd=str(ws), check=True, capture_output=True)

    test_cmd = "python3 -B test_app.py"

    # Verify test command initially fails
    initial_test = subprocess.run(["python3", "-B", "test_app.py"], cwd=str(ws), capture_output=True)
    assert initial_test.returncode != 0

    # 2. Wire production orchestration components identically to main.py
    task_spec = TaskSpec(
        issue_id="e2e-recovery-proof",
        issue_description="Fix solve function so solve(10) == 20",
        workspace_dir=str(ws),
        test_command=test_cmd,
    )
    repo_index = RepositoryScanner().scan(str(ws))
    memory = MemoryManager(base_dir=ws / ".harness")
    memory.initialize_task(task_id=task_spec.issue_id, task=task_spec.issue_description)
    builder = ContextBuilder(repository_index=repo_index)
    context = ContextAdapter(memory=memory, builder=builder)
    tool_engine = ToolEngine(workspace_dir=str(ws))
    verifier = VerificationEngine()

    # 3. Deterministic model responses demonstrating the complete recovery cycle
    mock_responses = [
        # Turn 1: Model performs an insufficient edit (10 + 5 = 15 != 20)
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "app.py",
                "search_block": "return x - 1\n",
                "replace_block": "return x + 5\n",
            },
        }),
        # Turn 2: Model prematurely declares completion before verified fix
        json.dumps({"action": "complete", "message": "Attempted logic fix"}),
        # Turn 3: Model receives failure evidence in prompt and performs corrective edit (10 * 2 = 20)
        json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "app.py",
                "search_block": "return x + 5\n",
                "replace_block": "return x * 2\n",
            },
        }),
        # Turn 4: Model completes after verified fix
        json.dumps({"action": "complete", "message": "Verified solve logic doubles input"}),
    ]

    model_adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
    config = OrchestratorConfig(step_limit=10, max_recoveries=3)

    orchestrator = Orchestrator(
        context=context,
        tool_engine=tool_engine,
        verifier=verifier,
        model_adapter=model_adapter,
        memory_manager=memory,
        context_builder=builder,
        config=config,
    )

    report = orchestrator.run(task_spec)

    # 4. Assert final success status and recovery count
    assert report["status"] == "completed"
    assert report["verified"] is True
    assert report["recovery_attempts"] >= 1
    assert report["telemetry"].recovery_attempts >= 1

    # 5. Assert final test command actually passes in the workspace
    final_test = subprocess.run(["python3", "-B", "test_app.py"], cwd=str(ws), capture_output=True, text=True)
    assert final_test.returncode == 0

    # 6. Assert verification evidence is present in the final report
    v_report = report["verification_report"]
    assert v_report is not None
    assert v_report.is_verified is True
    assert v_report.tests_passed is True
    assert report["verification_status"] == VerificationStatus.PASSED.value
    assert report["test_command"] == test_cmd
    assert report["test_output"] != ""
    assert "app.py" in report["files_modified"]
    assert "app.py" in v_report.files_modified
    assert "app.py" in report["telemetry"].files_modified
    assert "app.py" in report["git_diff"]
    assert "+    return x * 2" in report["git_diff"]

    # 7. Assert no fake success occurred before verification:
    # MemoryManager recorded the failure from Turn 2 before the recovery succeeded
    final_state = memory.get_state()
    assert any(f.error_signature == FailureClassification.ASSERTION_FAILED.value for f in final_state.failures)
    assert any("recovery_1" in a.id for a in final_state.attempts)

    # Observations trace: verification_failure must precede verification_success
    obs_types = [o.type for o in final_state.recent_observations]
    assert "verification_failure" in obs_types
    assert "verification_success" in obs_types
    assert obs_types.index("verification_failure") < obs_types.index("verification_success")
