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
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.common.types import TaskSpec, ToolName
from src.orchestrator.model_adapter import ModelAdapter, ModelAPIError
from src.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from src.tools.registry import ToolEngine


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


if __name__ == "__main__":
    unittest.main()
