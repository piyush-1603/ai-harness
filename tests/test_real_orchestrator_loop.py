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
    VerificationReport,
    VerificationStatus,
)
from src.memory.manager import MemoryManager
from src.orchestrator.model_adapter import ModelAdapter, ModelAPIError
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine
from src.verification.verifier import VerificationEngine


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
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

            task = TaskSpec(
                issue_id="test-a",
                issue_description="Inspect sample.py",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["n_calls"], 2)
            self.assertEqual(report["message"], "File inspected and verified.")
            self.assertFalse(report["verified"])  # Distinction preserved for next slice
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
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

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
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

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
        orch = Orchestrator(model_adapter=adapter, tool_engine=mock_engine)

        report = orch.run("Check if issue needs fixing")

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["n_calls"], 1)
        self.assertEqual(report["message"], "Nothing to do, already correct.")
        mock_engine.execute.assert_not_called()
        self.assertEqual(report["telemetry"].total_tool_calls, 0)

    def test_e_malformed_model_response_does_not_execute_tool(self):
        """TEST E: malformed model response does not execute a tool."""
        mock_responses = [
            "{this is not valid json",
            json.dumps({"action": "complete", "message": "Recovered after malformed response."}),
        ]
        adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
        mock_engine = MagicMock(spec=ToolEngine)
        orch = Orchestrator(
            model_adapter=adapter,
            tool_engine=mock_engine,
            config=OrchestratorConfig(step_limit=5),
        )

        report = orch.run("Test malformed response recovery")

        # Crucial requirement: tool engine was NEVER called for malformed turn
        mock_engine.execute.assert_not_called()
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
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine)

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
            orch = Orchestrator(model_adapter=adapter, tool_engine=mock_engine)

            report = orch.run("Test API failure")

            self.assertNotEqual(report["status"], "completed")
            self.assertNotEqual(report["status"], "resolved")
            self.assertEqual(report["status"], "error")
            self.assertIn("API down HTTP 503", report["last_error"])
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

            self.assertEqual(report["status"], "completed")
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

            self.assertEqual(report["status"], "completed")
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
                json.dumps({"action": "complete", "message": "App fixed."}),
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
            self.assertEqual(report["verification_status"], VerificationStatus.PASSED.value)
            self.assertTrue(report["verification_report"].tests_passed)
            self.assertTrue(report["verification_report"].is_verified)

    def test_9c_failing_verification_produces_verified_false(self):
        """C. Failing verification produces verified=False."""
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

            self.assertEqual(report["status"], "completed")
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
                issue_description="Claim completion without edits",
                workspace_dir=tmp_dir,
                test_command="python3 -c 'exit(0)'",
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "completed")
            self.assertFalse(report["verified"])
            self.assertEqual(report["verification_status"], VerificationStatus.NO_CHANGES.value)
            self.assertFalse(report["verification_report"].is_verified)

    def test_9e_verification_engine_exception_fails_closed(self):
        """E. VerificationEngine exception fails closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                json.dumps({"action": "complete", "message": "Done"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)

            # Verifier raises an unexpected exception
            mock_verifier = MagicMock()
            mock_verifier.verify.side_effect = RuntimeError("Catastrophic disk/subprocess failure")

            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=mock_verifier)
            task = TaskSpec(
                issue_id="test-9e",
                issue_description="Test crash fail closed",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertFalse(report["verified"])
            self.assertEqual(report["verification_status"], VerificationStatus.FAILED.value)
            self.assertFalse(report["verification_report"].is_verified)
            self.assertIn(
                "Catastrophic disk/subprocess failure",
                report.get("verification_error", "") or report["last_error"],
            )

    def test_9f_verification_report_evidence_reaches_final_report(self):
        """F. Verification report evidence reaches the final report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "greeter.py").write_text("def greet(): return 'bye'\n")
            (ws / "test_greeter.py").write_text(
                "from greeter import greet\nassert greet() == 'hello'\nprint('GREETER_TEST_PASSED')\n"
            )
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "greeter.py",
                        "search_block": "return 'bye'\n",
                        "replace_block": "return 'hello'\n",
                    },
                }),
                json.dumps({"action": "complete", "message": "Updated greeter"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9f",
                issue_description="Update greeter",
                workspace_dir=tmp_dir,
                test_command="python3 test_greeter.py",
            )
            report = orch.run(task)

            self.assertTrue(report["verified"])
            self.assertEqual(report["test_command"], "python3 test_greeter.py")
            self.assertIn("GREETER_TEST_PASSED", report["test_output"])
            self.assertIn("+def greet(): return 'hello'", report["git_diff"])
            self.assertIn("greeter.py", report["telemetry"].files_modified)

    def test_9g_existing_last_error_from_earlier_tool_failure_preserved(self):
        """G. Existing last_error from an earlier tool failure is preserved."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "mod.py").write_text("x = 1\n")
            (ws / "test_mod.py").write_text("import mod\nassert mod.x == 2\n")
            subprocess.run(["git", "add", "."], cwd=str(ws), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), check=True)

            mock_responses = [
                # Tool 1: fails because target file does not exist
                json.dumps({
                    "tool_name": "read_file",
                    "tool_args": {"path": "missing_ghost.py"},
                }),
                # Tool 2: modifies mod.py
                json.dumps({
                    "tool_name": "edit_file",
                    "tool_args": {
                        "path": "mod.py",
                        "search_block": "x = 1\n",
                        "replace_block": "x = 2\n",
                    },
                }),
                # Step 3: complete
                json.dumps({"action": "complete", "message": "Fixed mod.py"}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9g",
                issue_description="Fix mod with earlier tool error",
                workspace_dir=tmp_dir,
                test_command="python3 test_mod.py",
            )
            report = orch.run(task)

            self.assertTrue(report["verified"])
            self.assertIsNotNone(report["last_error"])
            self.assertTrue(
                "missing_ghost.py" in report["last_error"]
                or "does not exist" in report["last_error"].lower()
                or "not found" in report["last_error"].lower()
            )

    def test_9h_existing_harness_max_steps_behavior_unchanged(self):
        """H. Existing HARNESS_MAX_STEPS behavior remains unchanged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "file.py").write_text("print(1)\n")
            mock_responses = [
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "file.py"}}),
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "file.py"}}),
                json.dumps({"tool_name": "read_file", "tool_args": {"path": "file.py"}}),
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            config = OrchestratorConfig(step_limit=2)
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier, config=config)

            task = TaskSpec(
                issue_id="test-9h",
                issue_description="Infinite read loop",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "blocked_step_cap")
            self.assertFalse(report["verified"])
            self.assertEqual(report["n_calls"], 2)

    def test_9i_existing_malformed_model_response_fails_closed(self):
        """I. Existing malformed-model-response behavior remains unchanged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_responses = [
                "Malformed invalid JSON string {{{",
            ]
            adapter = ModelAdapter(mock_mode=True, mock_responses=mock_responses)
            engine = ToolEngine(workspace_dir=tmp_dir)
            verifier = VerificationEngine()
            orch = Orchestrator(model_adapter=adapter, tool_engine=engine, verifier=verifier)

            task = TaskSpec(
                issue_id="test-9i",
                issue_description="Malformed model response",
                workspace_dir=tmp_dir,
            )
            report = orch.run(task)

            self.assertEqual(report["status"], "error")
            self.assertFalse(report["verified"])
            self.assertIsNotNone(report["last_error"])

    def test_9j_model_tool_memory_integration_intact(self):
        """J. Existing model/tool/memory integration remains intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            self._init_repo(ws)
            (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
            (ws / "test_calc.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
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


if __name__ == "__main__":
    unittest.main()
