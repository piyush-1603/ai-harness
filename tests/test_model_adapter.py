"""Comprehensive tests for ModelAdapter (Slice 2).

Verifies:
- Robust ToolCall parsing from JSON (bare and markdown code blocks).
- Distinct ModelCompletion representation.
- Explicit failure on malformed JSON, missing fields, unknown tools, invalid types, ambiguity.
- Explicit mock mode behavior and exhaustion.
- Production HTTP caller with mocked responses (200, 401, 403, 429, 500, network error).
- Credential security and sanitization (never leaking API keys).
"""

from io import BytesIO
import json
import socket
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from src.common.types import ToolCall, ToolName
from src.orchestrator.model_adapter import (
    ModelAdapter,
    ModelAPIError,
    ModelCompletion,
    ModelParseError,
)


class TestModelAdapterParser(unittest.TestCase):
    """Unit tests for ModelAdapter.parse_decision."""

    def test_valid_tool_call_bare_json(self):
        raw = '{"tool_name": "read_file", "tool_args": {"path": "calculator.py"}}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.READ_FILE)
        self.assertEqual(decision.tool_args, {"path": "calculator.py"})
        self.assertTrue(decision.call_id.startswith("call_"))

    def test_valid_tool_call_with_call_id(self):
        raw = '{"tool_name": "run_bash", "tool_args": {"command": "pytest"}, "call_id": "custom_123"}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.RUN_BASH)
        self.assertEqual(decision.tool_args, {"command": "pytest"})
        self.assertEqual(decision.call_id, "custom_123")

    def test_valid_tool_call_markdown_code_block(self):
        raw = """
Here is the tool I want to call to explore:
```json
{
  "tool_name": "list_directory",
  "tool_args": {"path": "src", "max_depth": 2}
}
```
Please run it.
"""
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.LIST_DIRECTORY)
        self.assertEqual(decision.tool_args, {"path": "src", "max_depth": 2})

    def test_all_canonical_tool_names_parseable(self):
        for tool_name in ToolName:
            raw = json.dumps({"tool_name": tool_name.value, "tool_args": {"k": "v"}})
            decision = ModelAdapter.parse_decision(raw)
            self.assertIsInstance(decision, ToolCall)
            self.assertEqual(decision.tool_name, tool_name)

    def test_valid_completion_action_complete(self):
        raw = '{"action": "complete", "message": "All unit tests pass and bug is fixed."}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "All unit tests pass and bug is fixed.")

    def test_valid_completion_status_completed(self):
        raw = '{"status": "completed", "message": "Task complete."}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "Task complete.")

    def test_valid_completion_completed_boolean(self):
        raw = '{"completed": true, "message": "Everything verified."}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "Everything verified.")

    def test_valid_completion_tool_name_complete(self):
        raw = '{"tool_name": "complete", "tool_args": {"message": "Verified success."}}'
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "Verified success.")

    def test_missing_tool_name(self):
        raw = '{"tool_args": {"path": "calculator.py"}}'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Missing 'tool_name'", str(cm.exception))

    def test_unknown_tool_name(self):
        raw = '{"tool_name": "execute_arbitrary_python", "tool_args": {}}'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Unknown tool name", str(cm.exception))

    def test_missing_tool_args(self):
        raw = '{"tool_name": "read_file"}'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Missing 'tool_args'", str(cm.exception))

    def test_invalid_tool_args_type(self):
        raw = '{"tool_name": "read_file", "tool_args": "calculator.py"}'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Invalid 'tool_args'", str(cm.exception))
        self.assertIn("expected dictionary", str(cm.exception))

    def test_malformed_json(self):
        raw = '{"tool_name": "read_file", "tool_args": {'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Malformed JSON", str(cm.exception))

    def test_empty_response(self):
        for empty_val in ("", "   \n\t  ", None):
            with self.assertRaises(ModelParseError) as cm:
                ModelAdapter.parse_decision(empty_val)  # type: ignore
            self.assertIn("Empty response", str(cm.exception))

    def test_unexpected_response_structure_array(self):
        raw = '[{"tool_name": "read_file", "tool_args": {}}]'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Unexpected response structure", str(cm.exception))

    def test_unexpected_response_structure_string(self):
        raw = "I think we should read calculator.py first."
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("No valid JSON object found", str(cm.exception))

    def test_ambiguous_multiple_code_blocks(self):
        raw = """
```json
{"tool_name": "read_file", "tool_args": {"path": "a.py"}}
```
and then:
```json
{"tool_name": "read_file", "tool_args": {"path": "b.py"}}
```
"""
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Ambiguous response", str(cm.exception))

    def test_ambiguous_multiple_top_level_objects(self):
        raw = '{"tool_name": "read_file", "tool_args": {"path": "a.py"}}{"tool_name": "read_file", "tool_args": {"path": "b.py"}}'
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Ambiguous response", str(cm.exception))

    def test_valid_tool_call_with_braces_in_string_values(self):
        # H-1: braces in string values must not trigger false ambiguity error
        raw = json.dumps({
            "tool_name": "edit_file",
            "tool_args": {
                "path": "calc.py",
                "old_str": "x = {} {}"
            }
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.EDIT_FILE)
        self.assertEqual(decision.tool_args, {"path": "calc.py", "old_str": "x = {} {}"})

    def test_ambiguous_multiple_tool_calls_whitespace_separated(self):
        # H-1: genuine multiple tool-call response must be rejected with ModelParseError
        raw = """
        {"tool_name": "read_file", "tool_args": {"path": "a.py"}}
        {"tool_name": "edit_file", "tool_args": {"path": "b.py"}}
        """
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Ambiguous response", str(cm.exception))

    def test_call_id_deterministic_without_call_id(self):
        # H-2: parse_decision(raw_without_call_id) == parse_decision(raw_without_call_id)
        raw = '{"tool_name": "read_file", "tool_args": {"path": "calculator.py"}}'
        d1 = ModelAdapter.parse_decision(raw)
        d2 = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(d1, ToolCall)
        self.assertIsInstance(d2, ToolCall)
        self.assertEqual(d1.call_id, d2.call_id)
        self.assertTrue(d1.call_id.startswith("call_"))

    def test_call_id_changes_when_arguments_change(self):
        # H-2: changing meaningful tool-call input changes generated call_id
        raw1 = '{"tool_name": "read_file", "tool_args": {"path": "a.py"}}'
        raw2 = '{"tool_name": "read_file", "tool_args": {"path": "b.py"}}'
        d1 = ModelAdapter.parse_decision(raw1)
        d2 = ModelAdapter.parse_decision(raw2)
        self.assertNotEqual(d1.call_id, d2.call_id)

    def test_explicit_call_id_preserved(self):
        # H-2: explicitly supplied model call_id is preserved exactly
        raw = '{"tool_name": "read_file", "tool_args": {"path": "a.py"}, "call_id": "model_explicit_id_42"}'
        d = ModelAdapter.parse_decision(raw)
        self.assertEqual(d.call_id, "model_explicit_id_42")

    def test_contradictory_completion_and_tool_call_rejected(self):
        # M-1: contradictory completion + tool signals must be rejected
        contradictory_cases = [
            '{"action": "complete", "tool_name": "read_file", "tool_args": {"path": "a.py"}}',
            '{"status": "completed", "tool_name": "edit_file", "tool_args": {"path": "a.py"}}',
            '{"completed": true, "tool_name": "run_bash", "tool_args": {"command": "pytest"}}',
            '{"action": "finish", "tool_name": "git_status", "tool_args": {}}',
        ]
        for raw in contradictory_cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ModelParseError) as cm:
                    ModelAdapter.parse_decision(raw)
                self.assertIn("Contradictory response", str(cm.exception))

    def test_null_content_with_single_tool_call_json_string_args(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_12345",
                    "type": "function",
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({
                            "path": "calculator.py",
                            "search_block": "return a - b\n",
                            "replace_block": "return a + b\n",
                        }),
                    },
                }
            ],
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.EDIT_FILE)
        self.assertEqual(
            decision.tool_args,
            {
                "path": "calculator.py",
                "search_block": "return a - b\n",
                "replace_block": "return a + b\n",
            },
        )
        self.assertEqual(decision.call_id, "call_12345")

    def test_null_content_with_multiple_tool_calls_deterministically_picks_first(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_first",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "first.py"}),
                    },
                },
                {
                    "id": "call_second",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "second.py", "content": "x"}),
                    },
                },
            ],
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.READ_FILE)
        self.assertEqual(decision.tool_args, {"path": "first.py"})
        self.assertEqual(decision.call_id, "call_first")

    def test_tool_calls_with_first_invalid_picks_first_valid(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_invalid",
                    "type": "function",
                    "function": {
                        "name": "unknown_tool",
                        "arguments": json.dumps({}),
                    },
                },
                {
                    "id": "call_valid",
                    "type": "function",
                    "function": {
                        "name": "git_status",
                        "arguments": "{}",
                    },
                },
            ],
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.GIT_STATUS)
        self.assertEqual(decision.call_id, "call_valid")

    def test_null_content_with_dict_tool_args(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_dict",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "calculator.py"},
                    },
                }
            ],
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ToolCall)
        self.assertEqual(decision.tool_name, ToolName.READ_FILE)
        self.assertEqual(decision.tool_args, {"path": "calculator.py"})
        self.assertEqual(decision.call_id, "call_dict")

    def test_null_content_with_complete_tool_call(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_finish",
                    "type": "function",
                    "function": {
                        "name": "complete",
                        "arguments": json.dumps({"message": "Fixed bug successfully"}),
                    },
                }
            ],
        })
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "Fixed bug successfully")

    def test_null_content_with_empty_tool_calls_raises_parse_error(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [],
        })
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Response has neither usable content nor tool_calls", str(cm.exception))

    def test_null_content_without_tool_calls_raises_parse_error(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
        })
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Response has neither usable content nor tool_calls", str(cm.exception))

    def test_tool_calls_with_malformed_arguments_json_raises(self):
        raw = json.dumps({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_bad",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": "{not-valid-json",
                    },
                }
            ],
        })
        with self.assertRaises(ModelParseError) as cm:
            ModelAdapter.parse_decision(raw)
        self.assertIn("Malformed JSON in tool_call arguments", str(cm.exception))

    def test_normal_completion_parsing_still_works(self):
        raw = json.dumps({"action": "complete", "message": "Everything verified and all tests pass."})
        decision = ModelAdapter.parse_decision(raw)
        self.assertIsInstance(decision, ModelCompletion)
        self.assertEqual(decision.message, "Everything verified and all tests pass.")


class TestModelAdapterMockMode(unittest.TestCase):
    """Unit tests for ModelAdapter explicit mock mode."""

    def test_mock_mode_requires_explicit_flag(self):
        # Default mock_mode is False
        adapter = ModelAdapter(mock_mode=False, api_key=None)
        with patch.dict("os.environ", {}, clear=True):
            adapter.api_key = None
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("test prompt")
            self.assertIn("AI_API_KEY is not set", str(cm.exception))

    def test_mock_mode_not_inferred_from_environment(self):
        # HARNESS_MOCK_MODE=true must not activate mock mode
        with patch.dict("os.environ", {"HARNESS_MOCK_MODE": "true"}, clear=True):
            adapter = ModelAdapter()
            self.assertFalse(adapter.mock_mode)
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("test prompt")
            self.assertIn("AI_API_KEY is not set", str(cm.exception))

    def test_mock_mode_sequential_responses(self):
        responses = [
            '{"tool_name": "read_file", "tool_args": {"path": "main.py"}}',
            '{"tool_name": "edit_file", "tool_args": {"path": "main.py", "old_str": "1", "new_str": "2"}}',
            '{"action": "complete", "message": "Done!"}',
        ]
        adapter = ModelAdapter(mock_mode=True, mock_responses=responses)

        d1 = adapter.decide("step 1")
        self.assertIsInstance(d1, ToolCall)
        self.assertEqual(d1.tool_name, ToolName.READ_FILE)

        d2 = adapter.decide("step 2")
        self.assertIsInstance(d2, ToolCall)
        self.assertEqual(d2.tool_name, ToolName.EDIT_FILE)

        d3 = adapter.decide("step 3")
        self.assertIsInstance(d3, ModelCompletion)
        self.assertEqual(d3.message, "Done!")

        self.assertEqual(adapter.total_model_calls, 3)

    def test_mock_mode_exhaustion_raises(self):
        adapter = ModelAdapter(mock_mode=True, mock_responses=['{"action": "complete", "message": "done"}'])
        adapter.decide("step 1")
        with self.assertRaises(ModelAPIError) as cm:
            adapter.decide("step 2")
        self.assertIn("Mock responses exhausted", str(cm.exception))

    def test_mock_mode_reset_responses(self):
        adapter = ModelAdapter(mock_mode=True, mock_responses=["first"])
        self.assertEqual(adapter.call_model("p"), "first")
        adapter.set_mock_responses(["new_1", "new_2"])
        self.assertEqual(adapter.call_model("p"), "new_1")
        self.assertEqual(adapter.call_model("p"), "new_2")


class TestModelAdapterHTTP(unittest.TestCase):
    """Unit tests for ModelAdapter production HTTP caller (with mocked network)."""

    def test_http_200_valid_response(self):
        fake_api_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"tool_name": "git_status", "tool_args": {}}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 25,
                "total_tokens": 145,
            },
        }
        fake_body = json.dumps(fake_api_response).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adapter = ModelAdapter(api_key="sk-test-secret-12345", model="test-model", base_url="https://api.test.com/v1")
            content = adapter.call_model("Hello model")
            decision = adapter.parse_decision(content)

            self.assertIsInstance(decision, ToolCall)
            self.assertEqual(decision.tool_name, ToolName.GIT_STATUS)
            self.assertEqual(adapter.last_token_usage["total_tokens"], 145)
            self.assertEqual(adapter.total_model_calls, 1)

    def test_http_401_authentication_failure(self):
        http_err = urllib.error.HTTPError(
            url="https://api.test.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},  # type: ignore
            fp=BytesIO(b'{"error": {"message": "Invalid API key"}}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            adapter = ModelAdapter(api_key="sk-secret-key-to-protect", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")

            err_msg = str(cm.exception)
            self.assertIn("Authentication failed", err_msg)
            # Crucial security check: secret key must never appear in exception message
            self.assertNotIn("sk-secret-key-to-protect", err_msg)

    def test_http_429_rate_limit(self):
        http_err = urllib.error.HTTPError(
            url="https://api.test.com/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},  # type: ignore
            fp=BytesIO(b"Rate limit exceeded"),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            adapter = ModelAdapter(api_key="sk-key", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Rate limit exceeded (HTTP 429)", str(cm.exception))
            self.assertTrue(cm.exception.is_rate_limit)
            self.assertEqual(cm.exception.status_code, 429)

    def test_http_500_server_error(self):
        http_err = urllib.error.HTTPError(
            url="https://api.test.com/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},  # type: ignore
            fp=BytesIO(b"Server error"),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            adapter = ModelAdapter(api_key="sk-key", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Model server error (HTTP 500)", str(cm.exception))

    def test_connection_failure(self):
        url_err = urllib.error.URLError("Temporary failure in name resolution")
        with patch("urllib.request.urlopen", side_effect=url_err):
            adapter = ModelAdapter(api_key="sk-key", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Connection failure", str(cm.exception))

    def test_malformed_json_in_api_response(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"{not-valid-json"
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adapter = ModelAdapter(api_key="sk-key", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Malformed JSON in model API response", str(cm.exception))

    def test_missing_choices_in_api_response(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"unexpected": 123}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adapter = ModelAdapter(api_key="sk-key", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Missing or invalid 'choices'", str(cm.exception))

    def test_timeout_propagates_to_urlopen_and_raises_model_api_error_without_leaking_key(self):
        # M-2: timeout reaches urlopen(), socket.timeout converted to ModelAPIError, API key not leaked
        secret_key = "super-secret-sk-abcdef123456789"
        with patch("urllib.request.urlopen", side_effect=socket.timeout("The read operation timed out")) as mock_urlopen:
            adapter = ModelAdapter(api_key=secret_key, base_url="https://api.test.com/v1", timeout=37.5)
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("test prompt")

            # 1. Configured timeout reaches urlopen()
            self.assertEqual(mock_urlopen.call_count, 1)
            _, kwargs = mock_urlopen.call_args
            self.assertEqual(kwargs.get("timeout"), 37.5)

            # 2. Timeout is converted to ModelAPIError
            err_msg = str(cm.exception)
            self.assertIn("Connection failure", err_msg)

            # 3. Secret API key does not appear in resulting error
            self.assertNotIn(secret_key, err_msg)
            self.assertNotIn(secret_key, repr(cm.exception))

    def test_http_200_null_content_with_tool_calls_parsed_end_to_end(self):
        fake_api_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_openrouter_123",
                                "type": "function",
                                "function": {
                                    "name": "edit_file",
                                    "arguments": json.dumps({
                                        "path": "calculator.py",
                                        "search_block": "return a - b\n",
                                        "replace_block": "return a + b\n",
                                    }),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 30,
                "total_tokens": 110,
            },
        }
        fake_body = json.dumps(fake_api_response).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adapter = ModelAdapter(api_key="sk-test", base_url="https://api.test.com/v1")
            decision = adapter.decide("fix bug")

            self.assertIsInstance(decision, ToolCall)
            self.assertEqual(decision.tool_name, ToolName.EDIT_FILE)
            self.assertEqual(decision.call_id, "call_openrouter_123")
            self.assertEqual(decision.tool_args["path"], "calculator.py")
            self.assertEqual(adapter.last_token_usage["total_tokens"], 110)

    def test_http_200_null_content_without_tool_calls_raises_model_api_error(self):
        fake_api_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                    }
                }
            ],
        }
        fake_body = json.dumps(fake_api_response).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            adapter = ModelAdapter(api_key="sk-test", base_url="https://api.test.com/v1")
            with self.assertRaises(ModelAPIError) as cm:
                adapter.call_model("prompt")
            self.assertIn("Null 'content' in API response message", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
