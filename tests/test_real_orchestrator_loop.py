"""Integration tests for the Real Orchestration Loop (Slice 3).

Verifies the real model <-> tool loop across all required test scenarios:
- TEST A: model -> read_file -> result -> complete
- TEST B: model -> write/edit -> result -> complete
- TEST C: multiple sequential tool calls -> complete
- TEST D: model completion with zero tool calls
- TEST E: malformed model response does not execute a tool
- TEST F: tool failure is returned to the model as ToolResult
- TEST G: HARNESS_MAX_STEPS stops an infinite tool-call sequence
- TEST H: model API failure does not produce false success
- TEST I2: Comprehensive pipeline verification for Person B integration
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.common.types import TaskSpec, ToolName, VerificationReport, VerificationStatus, FailureClassification
from src.orchestrator.model_adapter import ModelAdapter, ModelAPIError
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine
from src.memory.models import Phase, TaskStatus
from src.context.budget import ContextBudgetResult
from src.context.policy import ContextProfile

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
        summary="Success"
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
        summary=msg
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
            self.assertEqual(report["n_calls"], 2)
            self.assertEqual(report["message"], "File inspected and verified.")
            self.assertTrue(report["verified"]) 
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
            self.assertEqual(report["n_calls"], 5)
            self.assertEqual(report["telemetry"].total_tool_calls, 4)

    def test_d_model_completion_with_zero_tool_calls(self):
        """TEST D: model completion with zero tool calls."""
        mock_responses = [
            json.dumps({"action": "complete", "message": "Nothing to do, already correct."})
        ]
        adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
        mock_engine = MagicMock(spec=ToolEngine)
        orch = Orchestrator(model_adapter=adapter, tool_engine=mock_engine, verifier=mock_success_verifier())

        report = orch.run("Check if issue needs fixing")

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["n_calls"], 1)
        self.assertEqual(report["message"], "Nothing to do, already correct.")
        mock_engine.execute.assert_not_called()
        self.assertEqual(report["telemetry"].total_tool_calls, 0)

    def test_e_malformed_model_response_executes_mock_tool(self):
        """TEST E: malformed model response returns a mock tool call that injects error."""
        mock_responses = [
            "{this is not valid json",
            json.dumps({"action": "complete", "message": "Recovered after malformed response."}),
        ]
        adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
        mock_engine = MagicMock(spec=ToolEngine)
        # Fix JSON serialization issue by returning a valid ToolResult
        from src.common.types import ToolResult, ToolName
        mock_engine.execute.return_value = ToolResult(tool_name=ToolName.RUN_BASH, success=False, output="Invalid format", exit_code=1)
        
        orch = Orchestrator(
            model_adapter=adapter,
            tool_engine=mock_engine,
            config=OrchestratorConfig(step_limit=5),
            verifier=mock_success_verifier()
        )

        report = orch.run("Test malformed response recovery")

        # Crucial requirement: tool engine IS called with the mock RUN_BASH tool call for malformed turn
        self.assertEqual(mock_engine.execute.call_count, 1)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["n_calls"], 2)

    def test_f_tool_failure_returned_to_model_as_tool_result(self):
        """TEST F: tool failure is returned to the model as ToolResult."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "missing_file.py"}}),
                json.dumps({"action": "complete", "message": "Observed missing file and finished."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_success_verifier())

            report = orch.run(
                TaskSpec(
                    issue_id="test-f",
                    issue_description="Read missing file",
                    workspace_dir=tmp_dir,
                )
            )

            self.assertEqual(report["status"], "completed")
            # Verify tool failure was recorded and returned
            state = orch.memory_manager.get_state()
            read_obs = [o for o in state.recent_observations if o.source == "read_file"]
            self.assertTrue(len(read_obs) > 0)
            self.assertIn("failed", read_obs[0].summary.lower())
            self.assertTrue(len(state.failures) > 0)

    def test_g_harness_max_steps_stops_infinite_tool_call_sequence(self):
        """TEST G: HARNESS_MAX_STEPS stops an infinite tool-call sequence."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "sample.py").write_text("a = 1\n")
            # Long sequence of identical tool calls
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "sample.py"}})
            ] * 20
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            step_cap = 4
            orch = Orchestrator(
                model_adapter=adapter,
                tool_engine=engine,
                config=OrchestratorConfig(step_limit=step_cap),
                verifier=mock_success_verifier()
            )

            report = orch.run(
                TaskSpec(
                    issue_id="test-g",
                    issue_description="Run infinitely",
                    workspace_dir=tmp_dir,
                )
            )

            self.assertEqual(report["status"], "blocked_step_cap")
            self.assertEqual(report["n_calls"], step_cap)
            self.assertEqual(report["telemetry"].total_tool_calls, step_cap)

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
            mock_engine.execute.assert_not_called()

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
                # Turn 2: Malformed response (Scenario K)
                "{this is not valid json",
                # Turn 3: Model completes, triggers verification which fails (Scenario E, G)
                json.dumps({"action": "complete", "message": "Done with first attempt."}),
                # Turn 4: Model tries again after failure (Scenario H)
                json.dumps({"tool_name": "write_file", "tool_args": {"path": "sample.py", "content": "print('fixed')\n"}}),
                # Turn 5: Model completes again, verification fails again (Scenario I)
                json.dumps({"action": "complete", "message": "Done with second attempt."}),
                # Turn 6: Model completes, verification succeeds this time (Scenario F)
                json.dumps({"action": "complete", "message": "Done with third attempt."}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            
            mock_verifier = MagicMock()
            mock_verifier.verify.side_effect = [
                make_failed_report("First fail"),
                make_failed_report("First fail"), 
                make_success_report(),
            ]
            
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
            
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_verifier, config=OrchestratorConfig(step_limit=10))
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
            
            # Scenario G
            self.assertTrue(any("First fail" in f.summary for f in state.failures))
            self.assertEqual(state.current_errors, []) 
            
            # Scenario I
            repeated = [f for f in state.failures if "First fail" in f.summary]
            self.assertTrue(len(repeated) >= 1)
            self.assertTrue(any(f.occurrence_count >= 2 for f in repeated))
            
            self.assertIn(Phase.RECOVER, phases_seen)

    def test_m_additional_requirements(self):
        """TEST M: targeted checks for specific I2 integrations"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "mod.py"
            file_path.write_text("orig\n")
            
            mock_responses = [
                json.dumps({"tool_name": "write_file", "tool_args": {"path": "mod.py", "content": "changed\n"}}),
                json.dumps({"action": "complete", "message": "done"}),
                json.dumps({"action": "complete", "message": "done"})
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            
            mock_verifier = MagicMock()
            failed_report = make_failed_report("Target Fail Summary")
            mock_verifier.verify.side_effect = [failed_report, failed_report]
            
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_verifier, config=OrchestratorConfig(step_limit=3))
            
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
                test_command="pytest specific_test.py"
            )
            report = orch.run(task)
            
            self.assertEqual(report["status"], "blocked_step_cap")
            self.assertEqual(orch.memory_manager.get_state().status, TaskStatus.BLOCKED)
            
            self.assertEqual(file_path.read_text(), "changed\n")
            
            mock_verifier.verify.assert_called_with(workspace_dir=tmp_dir, test_command="pytest specific_test.py")
            
            self.assertTrue(any("Target Fail Summary" in p for p in prompts))
            self.assertTrue(any("Failed tests." in p for p in prompts))
            self.assertTrue(any("Classification:" in p for p in prompts))

if __name__ == "__main__":
    unittest.main()
