"""Integration tests for the Real Orchestration Loop (Slice 3 & Slice 4).

Verifies the real model <-> tool loop across all required test scenarios:
- TEST A: model -> read_file -> result -> complete
- TEST B: model -> write/edit -> result -> complete
- TEST C: multiple sequential tool calls -> complete
- TEST D: model completion with zero tool calls
- TEST E: malformed model response returns a mock tool call
- TEST F: tool failure is returned to the model as ToolResult
- TEST G: HARNESS_MAX_STEPS stops an infinite tool-call sequence
- TEST H: model API failure does not produce false success
- TEST I2: Comprehensive pipeline verification for Person B integration
- TEST M: targeted checks for specific I2 integrations
- Slice 4 Verification Integration tests (9a - 9j)
- Slice 4 Regression tests (invariant & legacy context)
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.common.types import (
    FailureClassification,
    TaskSpec,
    ToolName,
    ToolResult,
    VerificationReport,
    VerificationStatus,
)
from src.context.budget import ContextBudgetResult
from src.context.policy import ContextProfile
from src.memory.manager import MemoryManager
from src.memory.models import Phase, TaskStatus
from src.orchestrator.model_adapter import ModelAdapter, ModelAPIError
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine
from src.verification.verifier import VerificationEngine


def make_success_report():
    return VerificationReport(
        status=VerificationStatus.PASSED,
        is_verified=True,
        tests_passed=True,
        test_command="",
        test_output="All tests passed.",
        files_modified=[],
        git_diff="",
        failure_classification=None,
        syntax_valid=True,
        summary="Success",
    )


def make_failed_report(msg="Failed"):
    return VerificationReport(
        status=VerificationStatus.FAILED,
        is_verified=False,
        tests_passed=False,
        test_command="",
        test_output="Failed tests.",
        files_modified=[],
        git_diff="",
        failure_classification=FailureClassification.TEST_EXECUTION_ERROR,
        syntax_valid=True,
        summary=msg,
    )


def mock_success_verifier():
    mock_verifier = MagicMock()
    mock_verifier.verify.return_value = make_success_report()
    return mock_verifier


class TestRealOrchestrationLoop(unittest.TestCase):
    """Hermetic integration tests for the real model <-> tool orchestration loop."""

    def test_a_model_read_file_result_complete(self):
        """TEST A: model -> read_file -> result -> complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "sample.py"
            file_path.write_text("print('hello from sample')\n")

            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "sample.py"}}),
                json.dumps({"action": "complete", "message": "File inspected and verified."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            task = TaskSpec(
                issue_id="test-a",
                issue_description="Inspect sample.py",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 2)
            self.assertEqual(report["message"], "File inspected and verified.")
            self.assertIn("read_file", report["telemetry"].tool_call_counts)
            self.assertEqual(report["telemetry"].tool_call_counts["read_file"], 1)
            self.assertIn("sample.py", report["telemetry"].files_inspected)

    def test_b_model_write_edit_result_complete(self):
        """TEST B: model -> write/edit -> result -> complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({
                    "tool_name": "write_file",
                    "tool_args": {"path": "calc.py", "content": "x = 10\n"},
                }),
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "x = 10\n",
                        "replace_block": "x = 42\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "calc.py written and updated."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            task = TaskSpec(
                issue_id="test-b",
                issue_description="Write and fix calc.py",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 3)
            self.assertEqual((Path(tmp_dir) / "calc.py").read_text(), "x = 42\n")
            self.assertIn("calc.py", report["telemetry"].files_modified)
            self.assertEqual(report["telemetry"].total_tool_calls, 2)

    def test_c_multiple_sequential_tool_calls_complete(self):
        """TEST C: multiple sequential tool calls -> complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "doc.txt").write_text("Hello Documentation\n")
            mock_responses = [
                json.dumps({"tool_name": "list_directory", "tool_args": {"path": "."}}),
                json.dumps({"tool_name": "grep_search", "tool_args": {"query": "Documentation", "path": "."}}),
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "doc.txt"}}),
                json.dumps({"tool_name": "git_status", "tool_args": {}}),
                json.dumps({"action": "complete", "message": "Sequential inspection done."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            task = TaskSpec(
                issue_id="test-c",
                issue_description="Search and inspect documentation",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 5)
            self.assertEqual(report["telemetry"].total_tool_calls, 4)
            self.assertIn("list_directory", report["telemetry"].tool_call_counts)
            self.assertIn("grep_search", report["telemetry"].tool_call_counts)
            self.assertIn("read_file", report["telemetry"].tool_call_counts)
            self.assertIn("git_status", report["telemetry"].tool_call_counts)

    def test_d_model_completion_with_zero_tool_calls(self):
        """TEST D: model completion with zero tool calls."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"action": "complete", "message": "No changes needed, issue is invalid."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            task = TaskSpec(
                issue_id="test-d",
                issue_description="Trivial issue",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 1)
            self.assertEqual(report["telemetry"].total_tool_calls, 0)
            self.assertEqual(len(report["telemetry"].files_inspected), 0)
            self.assertEqual(len(report["telemetry"].files_modified), 0)

    def test_e_malformed_model_response_executes_mock_tool(self):
        """TEST E: malformed model response returns a mock tool call that injects error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                "{this is not valid json",
                json.dumps({"action": "complete", "message": "Recovered after malformed response."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            mock_engine = MagicMock(spec=ToolEngine)
            mock_engine.workspace_dir = tmp_dir
            
            # Fix JSON serialization issue by returning a valid ToolResult
            mock_engine.execute.return_value = ToolResult(
                tool_name=ToolName.RUN_BASH, 
                success=False, 
                output="Invalid format", 
                exit_code=1
            )
            
            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=mock_engine,
                config=OrchestratorConfig(step_limit=5),
                verifier=mock_success_verifier()
            )

            task = TaskSpec(
                issue_id="test-e",
                issue_description="Test malformed parsing",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            # Crucial requirement: tool engine IS called with the mock tool call for malformed turn
            self.assertEqual(mock_engine.execute.call_count, 1)
            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 2)

    def test_f_tool_failure_returned_to_model_as_tool_result(self):
        """TEST F: tool failure is returned to the model as ToolResult."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "does_not_exist.txt"}}),
                json.dumps({"action": "complete", "message": "Observed missing file."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            task = TaskSpec(
                issue_id="test-f",
                issue_description="Read missing file",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(report["n_calls"], 2)
            self.assertEqual(report["telemetry"].total_tool_calls, 1)

    def test_g_harness_max_steps_stops_infinite_tool_call_sequence(self):
        """TEST G: HARNESS_MAX_STEPS stops an infinite tool-call sequence."""
        step_cap = 4
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo looping"}}),
            ] * 10
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            config = OrchestratorConfig(step_limit=step_cap)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, config=config)

            task = TaskSpec(
                issue_id="test-g",
                issue_description="Looping task",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "blocked_step_cap")
            self.assertEqual(report["n_calls"], step_cap)
            self.assertEqual(report["telemetry"].total_tool_calls, step_cap)
            self.assertFalse(report["verified"])

    def test_h_model_api_failure_does_not_produce_false_success(self):
        """TEST H: model API failure does not produce false success."""
        adapter = ModelAdapter(mock_mode=True, mock_responses=[])
        with patch.object(adapter, "decide", side_effect=ModelAPIError("API down HTTP 503")):
            mock_engine = MagicMock(spec=ToolEngine)
            orch = Orchestrator(model_adapter=adapter, tool_engine=mock_engine, verifier=mock_success_verifier())

            report = orch.run("Test API failure")

            self.assertNotEqual(report["status"], "completed")
            self.assertNotEqual(report["status"], "resolved")
            self.assertEqual(report["status"], "error")
            self.assertIn("API down HTTP 503", report["last_error"])
            self.assertFalse(report["verified"])
            mock_engine.execute.assert_not_called()

    def test_preserve_last_error_when_model_completes_after_tool_failure(self):
        """Regression: earlier tool failure must be preserved in last_error upon model completion."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "nonexistent_file.py"}}),
                json.dumps({"action": "complete", "message": "Handled nonexistent file gracefully."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

            report = orch.run(
                TaskSpec(
                    issue_id="test-preserve-error",
                    issue_description="Test preserving last error",
                    workspace_dir=tmp_dir,
                )
            )

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertIsNotNone(report["last_error"])
            self.assertTrue(
                "does not exist" in report["last_error"].lower()
                or "not found" in report["last_error"].lower()
                or "failed" in report["last_error"].lower()
            )

    def test_no_manufactured_last_error_on_clean_completion(self):
        """Regression: no error should be manufactured when all steps succeed cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "clean.py").write_text("pass\n")
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "clean.py"}}),
                json.dumps({"action": "complete", "message": "All clean."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

            report = orch.run(
                TaskSpec(
                    issue_id="test-clean",
                    issue_description="Clean run",
                    workspace_dir=tmp_dir,
                )
            )

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertIsNone(report["last_error"])

    def test_production_real_loop_does_not_instantiate_stub_context(self):
        """Regression: production real loop construction and execution must not instantiate StubContext."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "sample.py").write_text("a = 1\n")
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "sample.py"}}),
                json.dumps({"action": "complete", "message": "done"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

            # Before running, orch.context must be None
            self.assertIsNone(orch.context)

            task = TaskSpec(
                issue_id="test-no-stub-ctx",
                issue_description="Verify no StubContext",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            # After running, orch.context must STILL be None (StubContext never instantiated)
            self.assertIsNone(orch.context)

            # Report scratchpad must be populated from MemoryManager authoritative state
            sp = report["scratchpad"]
            self.assertIsNotNone(sp)
            self.assertIn("Verify no StubContext", sp.task_summary)
            self.assertIn("Verify no StubContext", sp.hypothesis)
            self.assertIn("sample.py", sp.identified_files)
            self.assertTrue(len(sp.attempt_history) > 0)
            self.assertIn("read_file", sp.attempt_history[0])

    @patch("src.orchestrator.orchestrator.RepositoryScanner")
    def test_i2_scenarios(self, mock_scanner_cls):
        """TEST I2: Comprehensive pipeline verification for Person B integration."""
        mock_scanner = MagicMock()
        mock_scanner.scan.return_value = "fake_repo_index"
        mock_scanner_cls.return_value = mock_scanner

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "sample.py"
            file_path.write_text("print('hello')\n")

            mock_responses = [
                # Turn 1: Valid tool call (PLAN -> EXECUTE)
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "sample.py"}}),
                # Turn 2: Malformed response
                "{this is not valid json",
                # Turn 3: Model completes, triggers verification which succeeds
                json.dumps({"action": "complete", "message": "Done with implementation."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)

            mock_verifier = MagicMock()
            mock_verifier.verify.return_value = make_success_report()

            mock_policy = MagicMock()
            mock_profile = MagicMock(spec=ContextProfile)

            phases_seen = []

            def evaluate_side_effect(manager, repo_index=None):
                phases_seen.append(manager.get_state().phase)
                return mock_profile

            mock_policy.evaluate.side_effect = evaluate_side_effect

            mock_budgeter = MagicMock()
            mock_budget_result = ContextBudgetResult(
                text="MOCK_FINAL_BUDGET_CONTEXT_TEXT",
                estimated_tokens_before=100,
                estimated_tokens_after=100,
                max_tokens=1000,
                was_reduced=False,
                hard_truncated=False,
            )
            mock_budgeter.fit.return_value = mock_budget_result

            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                verifier=mock_verifier,
                config=OrchestratorConfig(step_limit=10),
            )
            orch.context_policy = mock_policy
            orch.context_budgeter = mock_budgeter

            original_decide = adapter.decide
            prompts_seen = []

            def decide_wrapper(prompt):
                prompts_seen.append(prompt)
                return original_decide(prompt)

            adapter.decide = decide_wrapper

            task = TaskSpec(
                issue_id="test-all",
                issue_description="Fix the issue",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            # Scenario B
            mock_scanner.scan.assert_called_once_with(tmp_dir)
            # Scenario C
            self.assertTrue(mock_policy.evaluate.called)
            # Scenario D
            self.assertTrue(mock_budgeter.fit.called)
            for p in prompts_seen:
                self.assertIn("MOCK_FINAL_BUDGET_CONTEXT_TEXT", p)

            # Scenario F, H
            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(orch.memory_manager.get_state().status, TaskStatus.DONE)

            # Scenario J
            state = orch.memory_manager.get_state()
            self.assertTrue(any(a.action.startswith("read_file") for a in state.attempts))
            self.assertTrue(any(o.source == "read_file" for o in state.recent_observations))
            self.assertIn(Phase.PLAN, phases_seen)
            self.assertIn(Phase.EXECUTE, phases_seen)

    def test_m_additional_requirements(self):
        """TEST M: targeted checks for specific I2 integrations"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "mod.py"
            file_path.write_text("orig\n")

            mock_responses = [
                json.dumps({"tool_name": "write_file", "tool_args": {"path": "mod.py", "content": "changed\n"}}),
                json.dumps({"action": "complete", "message": "done"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)

            mock_verifier = MagicMock()
            failed_report = make_failed_report("Target Fail Summary")
            mock_verifier.verify.return_value = failed_report

            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                verifier=mock_verifier,
                config=OrchestratorConfig(step_limit=3),
            )

            original_decide = adapter.decide
            prompts = []

            def decide_wrapper(prompt):
                prompts.append(prompt)
                return original_decide(prompt)

            adapter.decide = decide_wrapper

            task = TaskSpec(
                issue_id="test-m",
                issue_description="do stuff",
                workspace_dir=tmp_dir,
                test_command="pytest specific_test.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertEqual(file_path.read_text(), "changed\n")

            mock_verifier.verify.assert_called_with(workspace_dir=tmp_dir, test_command="pytest specific_test.py")
            self.assertEqual(report["verification_report"].summary, "Target Fail Summary")


class TestSlice4VerificationIntegration(unittest.TestCase):
    """Hermetic regression tests covering Slice 4 Verification + Recovery Integration:
    A. ModelCompletion triggers VerificationEngine.
    B. Passing verification produces verified=True.
    C. Failing verification produces verified=False.
    D. ModelCompletion alone cannot produce verified=True.
    E. VerificationEngine exception fails closed.
    F. Verification report evidence reaches the final report.
    G. Existing last_error from an earlier tool failure is preserved.
    H. Existing HARNESS_MAX_STEPS behavior remains unchanged.
    I. Existing malformed-model-response behavior remains unchanged.
    J. Existing model/tool/memory integration remains intact.
    """

    def _init_repo(self, ws: Path):
        subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(ws), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(ws), check=True, capture_output=True)

    def test_9a_model_completion_triggers_verification_engine(self):
        """A. ModelCompletion triggers VerificationEngine."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
            (ws / "test_calc.py").write_text(
                "from calc import add\nassert add(2, 3) == 5\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "return a - b\n",
                        "replace_block": "return a + b\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Fixed addition"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9a",
                issue_description="Fix addition in calc.py",
                workspace_dir=tmp_dir,
                test_command="python3 test_calc.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertIsNotNone(report["verification_report"])
            self.assertTrue(report["verified"])
            self.assertEqual(report["verification_status"], VerificationStatus.PASSED.value)

    def test_9b_passing_verification_produces_verified_true(self):
        """B. Passing verification produces verified=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "app.py").write_text("def status():\n    return 'broken'\n")
            (ws / "test_app.py").write_text(
                "from app import status\nassert status() == 'ok'\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "app.py",
                        "search_block": "return 'broken'\n",
                        "replace_block": "return 'ok'\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Fixed app"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9b",
                issue_description="Fix app status",
                workspace_dir=tmp_dir,
                test_command="python3 test_app.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertTrue(report["verification_report"].tests_passed)
            self.assertTrue(report["verification_report"].is_verified)
            self.assertEqual(report["verification_status"], VerificationStatus.PASSED.value)
            self.assertIsNone(report["failure_classification"])

    def test_9c_failing_verification_produces_verified_false(self):
        """C. Failing verification produces verified=False and status='failed'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
            (ws / "test_calc.py").write_text(
                "from calc import add\nassert add(2, 3) == 5\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            # Model attempts fix with wrong logic
            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "return a - b\n",
                        "replace_block": "return a * b\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Fixed by multiplying"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9c",
                issue_description="Fix addition in calc.py",
                workspace_dir=tmp_dir,
                test_command="python3 test_calc.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertEqual(report["verification_status"], VerificationStatus.FAILED.value)
            self.assertFalse(report["verification_report"].tests_passed)
            self.assertFalse(report["verification_report"].is_verified)
            self.assertEqual(
                report["failure_classification"],
                FailureClassification.ASSERTION_FAILED.value,
            )

    def test_9d_model_completion_alone_cannot_produce_verified_true(self):
        """D. ModelCompletion alone cannot produce verified=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "readme.txt").write_text("Unchanged repo\n")
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            # Model claims completion without making any changes
            mock_responses = [
                json.dumps({"action": "complete", "message": "I solved everything!"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9d",
                issue_description="Fake resolution test",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertEqual(report["verification_status"], VerificationStatus.NO_CHANGES.value)
            self.assertFalse(report["verification_report"].is_verified)

    def test_9e_verification_engine_exception_fails_closed(self):
        """E. VerificationEngine exception fails closed (verified=False, non-success status)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "test.py").write_text("print('hello')\n")
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({"action": "complete", "message": "Done"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)

            # Verifier that throws an unhandled exception
            exploding_verifier = MagicMock()
            exploding_verifier.verify.side_effect = RuntimeError("Disk IO error during verification")

            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=exploding_verifier)

            task = TaskSpec(
                issue_id="test-9e",
                issue_description="Test exception handling",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertFalse(report["verified"])
            self.assertEqual(report["status"], "failed")
            self.assertIsNotNone(report.get("verification_error"))
            self.assertIn("Disk IO error", report["verification_error"])
            self.assertEqual(report["verification_status"], VerificationStatus.FAILED.value)

    def test_9f_verification_report_evidence_reaches_final_report(self):
        """F. Verification report evidence reaches the final report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def sub(a, b):\n    return a + b\n")
            (ws / "test_calc.py").write_text(
                "from calc import sub\nassert sub(5, 3) == 2\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "return a + b\n",
                        "replace_block": "return a - b\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Fixed subtraction"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9f",
                issue_description="Fix subtraction in calc.py",
                workspace_dir=tmp_dir,
                test_command="python3 test_calc.py",
            )
            report = orch.run(task)

            self.assertTrue(report["verified"])
            self.assertIn("verification_report", report)
            v_rep = report["verification_report"]
            self.assertIsInstance(v_rep, VerificationReport)
            self.assertIn("test_command", report)
            self.assertEqual(report["test_command"], "python3 test_calc.py")
            self.assertIn("test_output", report)
            self.assertIn("git_diff", report)
            self.assertIn("calc.py", report["git_diff"])
            self.assertIn("calc.py", report["telemetry"].files_modified)

    def test_9g_existing_last_error_from_earlier_tool_failure_preserved(self):
        """G. Existing last_error from an earlier tool failure is preserved even when verified=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def mul(a, b):\n    return a + b\n")
            (ws / "test_calc.py").write_text(
                "from calc import mul\nassert mul(2, 3) == 6\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                # Turn 1: Failing tool call
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "nonexistent.py"}}),
                # Turn 2: Valid tool call fixing the bug
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "return a + b\n",
                        "replace_block": "return a * b\n",
                    },
                }),
                # Turn 3: Complete
                json.dumps({"action": "complete", "message": "Fixed multiplication"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9g",
                issue_description="Fix multiplication",
                workspace_dir=tmp_dir,
                test_command="python3 test_calc.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["verified"])
            self.assertIsNotNone(report["last_error"])
            self.assertTrue(
                "does not exist" in report["last_error"].lower()
                or "not found" in report["last_error"].lower()
                or "failed" in report["last_error"].lower()
            )

    def test_9h_existing_harness_max_steps_behavior_unchanged(self):
        """H. Existing HARNESS_MAX_STEPS behavior remains unchanged."""
        step_cap = 3
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "app.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({"tool_name": "run_bash", "tool_args": {"command": "echo 1"}}),
            ] * 10
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            config = OrchestratorConfig(step_limit=step_cap)
            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                verifier=verifier,
                config=config,
            )

            task = TaskSpec(
                issue_id="test-9h",
                issue_description="Step cap test",
                workspace_dir=tmp_dir,
                test_command="echo passed",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "blocked_step_cap")
            self.assertFalse(report["verified"])
            self.assertEqual(report["n_calls"], step_cap)

    def test_9i_existing_malformed_model_response_fails_closed(self):
        """I. Existing malformed-model-response behavior remains intact and does not produce verified=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "app.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                "BAD JSON {not json}",
                "ANOTHER BAD JSON",
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            config = OrchestratorConfig(step_limit=2)
            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                verifier=verifier,
                config=config,
            )

            task = TaskSpec(
                issue_id="test-9i",
                issue_description="Malformed response test",
                workspace_dir=tmp_dir,
                test_command="echo passed",
            )
            report = orch.run(task)

            self.assertFalse(report["verified"])
            self.assertEqual(report["status"], "blocked_step_cap")

    def test_9j_model_tool_memory_integration_intact(self):
        """J. Existing model/tool/memory integration remains intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
            (ws / "test_calc.py").write_text(
                "from calc import add\nassert add(2, 3) == 5\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "calc.py",
                        "search_block": "return a - b\n",
                        "replace_block": "return a * b\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Multiplied instead"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            memory_mgr = MemoryManager()
            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                verifier=verifier,
                memory_manager=memory_mgr,
            )

            task = TaskSpec(
                issue_id="test-9j",
                issue_description="Verify memory state",
                workspace_dir=tmp_dir,
                test_command="python3 test_calc.py",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])

            # MemoryManager task state
            task_state = memory_mgr.get_state()
            self.assertEqual(task_state.task_id, "test-9j")

            # MemoryManager verification result
            v_res = task_state.verification
            self.assertIsNotNone(v_res)
            self.assertFalse(v_res.success)
            self.assertEqual(v_res.tests_passed, 0)
            self.assertEqual(v_res.tests_failed, 1)

            # MemoryManager failures recorded
            failures = task_state.failures
            self.assertTrue(len(failures) > 0)
            self.assertEqual(failures[0].error_signature, FailureClassification.ASSERTION_FAILED.value)

            # Observations recorded
            obs = task_state.recent_observations
            self.assertTrue(any(o.type == "verification_failure" for o in obs))
            self.assertTrue(any(o.type == "tool_result" for o in obs))

    def test_completed_status_never_paired_with_unverified(self):
        """Regression: status == 'completed' AND verified == False cannot be produced by real verification."""
        scenarios = [
            # 1. Failing tests
            (
                "def add(a, b): return a * b\n",
                "python3 test_calc.py",
                "Fixed by multiply",
            ),
            # 2. Syntax error
            (
                "def add(a, b): return a ++\n",
                "python3 test_calc.py",
                "Fixed with syntax error",
            ),
        ]
        for code_fix, cmd, msg in scenarios:
            with tempfile.TemporaryDirectory() as tmp_dir:
                ws = Path(tmp_dir)
                self._init_repo(ws)
                (ws / "calc.py").write_text("def add(a, b): return a - b\n")
                (ws / "test_calc.py").write_text(
                    "from calc import add\nassert add(2, 3) == 5\n"
                )
                subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
                subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

                mock_responses = [
                    json.dumps({
                        "tool_name": "edit_file",
                        "tool_args": {
                            "path": "calc.py",
                            "search_block": "return a - b\n",
                            "replace_block": code_fix,
                        },
                    }),
                    json.dumps({"action": "complete", "message": msg}),
                ]
                adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
                engine = ToolEngine(workspace_dir=tmp_dir)
                verifier = VerificationEngine()
                orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

                task = TaskSpec(
                    issue_id="test-invariant",
                    issue_description="Invariant check",
                    workspace_dir=tmp_dir,
                    test_command=cmd,
                )
                report = orch.run(task)

                # Authoritative invariant: status == "completed" and verified == False CANNOT happen
                self.assertFalse(
                    report["status"] == "completed" and not report["verified"],
                    f"Invariant violated: status={report['status']}, verified={report['verified']}",
                )
                self.assertEqual(report["status"], "failed")
                self.assertFalse(report["verified"])

    def test_orchestrator_construction_no_undefined_stub_context(self):
        """Regression: orchestrator construction with context=None runs legacy cleanly without NameError."""
        from src.orchestrator.test_orchestrator import StubToolEngine, StubVerifier, StubModel
        orch = Orchestrator(
            context=None,
            tools=StubToolEngine(),
            verifier=StubVerifier(0),
            model=StubModel(),
            config=OrchestratorConfig(step_limit=10),
        )
        report = orch.run("test legacy context fallback")
        self.assertEqual(report["status"], "resolved")
        self.assertIsNotNone(report["scratchpad"])


if __name__ == "__main__":
    unittest.main()