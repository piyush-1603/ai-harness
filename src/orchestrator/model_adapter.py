"""ModelAdapter module for Track A.

Manages communication with foundation models via OpenAI-compatible endpoints,
enforces API key security, supports deterministic offline mock mode, and
parses model responses into strongly-typed canonical ToolCall objects or
ModelCompletion decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Union
import urllib.error
import urllib.request

from src.common.types import ToolCall, ToolName

logger = logging.getLogger(__name__)


class ModelAPIError(Exception):
    """Raised when an API call fails or encounters HTTP/connection errors."""
    pass


class ModelFormatError(Exception):
    """Raised when the model's raw output does not contain a valid bracketed JSON block."""
    pass

class ModelParseError(Exception):
    """Raised when model output cannot be parsed into a valid ToolCall or ModelCompletion."""
    pass


@dataclass
class ModelCompletion:
    """Represents a completion / final answer decision from the model."""
    message: str


class ModelAdapter:
    """
    Adapter for foundation model communication.
    
    In production mode:
      - Reads AI_API_KEY, AI_MODEL, AI_BASE_URL.
      - Dispatches requests via standard library urllib.request to OpenAI-compatible endpoint.
      - Never leaks credentials in logs or exceptions.
      - Handles network/HTTP/rate-limit errors explicitly without silent fallback.

    In mock mode:
      - Explicitly enabled via `mock_mode=True`.
      - Yields deterministic pre-configured responses for unit and integration testing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        mock_mode: bool = False,
        mock_responses: Optional[List[str]] = None,
    ) -> None:
        env_mock = os.environ.get("HARNESS_MOCK_MODE", "").lower() in ("true", "1", "yes")
        self.mock_mode = mock_mode or env_mock
        if mock_responses is not None:
            self._mock_responses = list(mock_responses)
        elif self.mock_mode and os.environ.get("HARNESS_MOCK_RESPONSES"):
            try:
                self._mock_responses = json.loads(os.environ["HARNESS_MOCK_RESPONSES"])
            except Exception:
                self._mock_responses = [os.environ["HARNESS_MOCK_RESPONSES"]]
        elif self.mock_mode:
            self._mock_responses = [json.dumps({"action": "complete", "message": "Task completed in mock mode."})]
        else:
            self._mock_responses = []
        self._mock_index = 0
        self.timeout = float(timeout)

        if not self.mock_mode:
            self.api_key = api_key or os.environ.get("AI_API_KEY")
            self.model = model or os.environ.get("AI_MODEL", "gpt-4o-mini")
            raw_base = base_url if base_url is not None else os.environ.get("AI_BASE_URL", "")
            self.base_url = raw_base.strip()
            if not self.api_key:
                logger.warning("AI_API_KEY is not set. Production model calls will fail.")
        else:
            self.api_key = api_key or "mock-key"
            self.model = model or "mock-model"
            self.base_url = ""

        self.last_token_usage: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.total_model_calls: int = 0

    def set_mock_responses(self, responses: List[str]) -> None:
        """Sets or resets the mock response queue for mock mode."""
        self._mock_responses = list(responses)
        self._mock_index = 0

    def call_model(self, prompt: str) -> str:
        """Sends prompt to the model and returns the raw response string."""
        if self.mock_mode:
            if self._mock_index >= len(self._mock_responses):
                raise ModelAPIError("Mock responses exhausted: no more responses configured.")
            response = self._mock_responses[self._mock_index]
            self._mock_index += 1
            self.total_model_calls += 1
            return response

        if not self.api_key:
            raise ModelAPIError("AI_API_KEY is not set. An API key is required in production mode.")

        endpoint = (
            f"{self.base_url.rstrip('/')}/chat/completions"
            if self.base_url
            else "https://api.openai.com/v1/chat/completions"
        )

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                e.close()
            except Exception:
                pass
            if status in (401, 403):
                raise ModelAPIError("Authentication failed: invalid or unauthorized API key (HTTP 401/403)") from None
            elif status == 429:
                raise ModelAPIError("Rate limit exceeded (HTTP 429)") from None
            elif status >= 500:
                raise ModelAPIError(f"Model server error (HTTP {status})") from None
            else:
                raise ModelAPIError(f"Model API error (HTTP {status})") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            raise ModelAPIError(f"Connection failure to model endpoint: {type(e).__name__}") from None
        except Exception as e:
            raise ModelAPIError(f"Unexpected error communicating with model: {type(e).__name__}") from None

        try:
            res_json = json.loads(body)
        except json.JSONDecodeError as e:
            raise ModelAPIError(f"Malformed JSON in model API response: {e.msg}") from e

        if not isinstance(res_json, dict):
            raise ModelAPIError(f"Unexpected API response structure: expected JSON object, got {type(res_json).__name__}")

        choices = res_json.get("choices")
        if not choices or not isinstance(choices, list):
            raise ModelAPIError("Missing or invalid 'choices' in API response")

        first_choice = choices[0]
        if not isinstance(first_choice, dict) or "message" not in first_choice:
            raise ModelAPIError("Missing 'message' in API response choice")

        msg = first_choice["message"]
        if not isinstance(msg, dict) or "content" not in msg:
            raise ModelAPIError("Missing 'content' in API response message")

        content = msg["content"]
        if content is None:
            raise ModelAPIError("Null 'content' in API response message")

        usage = res_json.get("usage", {})
        if isinstance(usage, dict):
            self.last_token_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }

        self.total_model_calls += 1
        return str(content)

    @classmethod
    def parse_decision(cls, raw_text: str) -> Union[ToolCall, ModelCompletion]:
        """
        Parses raw model output into either a canonical ToolCall or ModelCompletion.
        
        Strict parsing rules:
        - Must contain valid JSON (either bare or within ```json ... ```).
        - If multiple ambiguous JSON blocks or tool calls are detected, raises ModelParseError.
        - Completion must have "action": "complete", "status": "completed", "completed": true,
          or "tool_name": "complete".
        - Tool call must have valid "tool_name" belonging to canonical ToolName enum,
          and "tool_args" must be a dictionary.
        """
        if not raw_text or not raw_text.strip():
            raise ModelParseError("Empty response from model.")

        text = raw_text.strip()

        # Check for markdown code blocks containing json
        code_block_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
        code_blocks = code_block_pattern.findall(text)

        candidate_json_strs: List[str] = []
        if code_blocks:
            for cb in code_blocks:
                stripped_cb = cb.strip()
                if stripped_cb:
                    candidate_json_strs.append(stripped_cb)
            if len(candidate_json_strs) > 1:
                raise ModelParseError("Ambiguous response: multiple JSON blocks detected in response.")

        if candidate_json_strs:
            target_str = candidate_json_strs[0]
        else:
            target_str = text

        # If it starts with '[', it's a list/array
        if target_str.lstrip().startswith("["):
            raise ModelParseError("Unexpected response structure: expected JSON object, got list.")

        # Find first '{' and last '}'
        start = target_str.find("{")
        if start == -1:
            raise ModelParseError("No valid JSON object found in model response.")

        end = target_str.rfind("}")
        if end == -1 or end < start:
            raise ModelParseError("Malformed JSON in model response: unclosed '{'.")

        if target_str[:start].find("}") != -1:
            raise ModelParseError("Malformed JSON in model response: unexpected '}' before '{'.")

        if target_str[end + 1:].find("{") != -1:
            raise ModelParseError("Ambiguous response: multiple tool calls detected.")

        json_str = target_str[start : end + 1]

        # 1. Attempt normal json.loads parsing first.
        # If the candidate is valid JSON representing exactly one root object,
        # we accept it without running raw-text regex ambiguity heuristics on string contents.
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            # Check if failure is due to multiple top-level JSON objects
            try:
                decoder = json.JSONDecoder()
                _, first_end = decoder.raw_decode(json_str)
                remaining = json_str[first_end:].strip()
                if "{" in remaining:
                    raise ModelParseError("Ambiguous response: multiple tool calls detected.")
            except ModelParseError:
                raise
            except Exception:
                pass
            raise ModelParseError(f"Malformed JSON in model response: {e.msg}") from e

        if not isinstance(data, dict):
            raise ModelParseError(f"Unexpected response structure: expected JSON object, got {type(data).__name__}.")

        # Check for completion signals
        has_completion_action = (
            data.get("action") in ("complete", "finish", "done")
            or data.get("status") in ("completed", "done", "finished")
            or data.get("completed") is True
        )
        tool_name_raw = data.get("tool_name")
        tool_name_val = str(tool_name_raw).strip().lower() if tool_name_raw is not None else ""
        has_tool_name_complete = tool_name_val in ("complete", "finish")
        has_other_tool_name = bool(tool_name_val and not has_tool_name_complete)

        # M-1: Reject contradictory completion + tool signals
        if has_completion_action and has_other_tool_name:
            raise ModelParseError("Contradictory response: contains both completion signal and tool_name.")

        if has_completion_action or has_tool_name_complete:
            tool_args = data.get("tool_args") or {}
            completion_msg = str(
                data.get("message")
                or (tool_args.get("message") if isinstance(tool_args, dict) else None)
                or "Task completed."
            )
            return ModelCompletion(message=completion_msg)

        # Must be a tool call
        if "tool_name" not in data:
            raise ModelParseError("Missing 'tool_name' in model response.")

        raw_name = data["tool_name"]
        if not isinstance(raw_name, str):
            raise ModelParseError(f"Invalid 'tool_name' type: expected string, got {type(raw_name).__name__}.")

        try:
            canonical_tool = ToolName(raw_name.strip().lower())
        except ValueError:
            raise ModelParseError(f"Unknown tool name: '{raw_name}'.")

        if "tool_args" not in data:
            raise ModelParseError(f"Missing 'tool_args' for tool '{canonical_tool.value}'.")

        args = data["tool_args"]
        if not isinstance(args, dict):
            raise ModelParseError(
                f"Invalid 'tool_args' for tool '{canonical_tool.value}': expected dictionary, got {type(args).__name__}."
            )

        # H-2: Deterministic call_id derived from tool_name and tool_args
        if data.get("call_id"):
            call_id = str(data["call_id"])
        else:
            args_repr = json.dumps(args, sort_keys=True, separators=(",", ":"))
            content_sig = f"{canonical_tool.value}:{args_repr}"
            digest = hashlib.sha256(content_sig.encode("utf-8")).hexdigest()[:12]
            call_id = f"call_{digest}"

        return ToolCall(tool_name=canonical_tool, tool_args=args, call_id=call_id)


    def _extract_json_block(self, text: str) -> str:
        # Try to find a code block first
        code_block_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
        code_blocks = code_block_pattern.findall(text)
        
        if code_blocks:
            for cb in code_blocks:
                stripped = cb.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    return stripped
        
        # Fallback to regex finding the outermost {}
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
            
        raise ModelFormatError("No valid bracketed JSON block found in the output.")

    def decide(self, prompt: str) -> Union[ToolCall, ModelCompletion]:
        """Calls the model and returns a validated ToolCall or ModelCompletion."""
        raw_text = self.call_model(prompt)
        
        import pydantic
        
        try:
            json_str = self._extract_json_block(raw_text)
            data = json.loads(json_str)
            
            result = self.parse_decision(json_str)
            
            # Validate against Pydantic ToolCall schema if it's a ToolCall
            if isinstance(result, ToolCall):
                from pydantic import TypeAdapter
                # Validate the resulting dict which includes the auto-generated call_id
                TypeAdapter(ToolCall).validate_python(
                    {"tool_name": result.tool_name, "tool_args": result.tool_args, "call_id": result.call_id}
                )
                
            return result

        except (json.JSONDecodeError, pydantic.ValidationError, ModelFormatError, ModelParseError) as e:
            error_details = str(e)
            return ToolCall(
                tool_name=ToolName.RUN_BASH,
                tool_args={"command": f"echo 'Invalid format: {error_details}. You must output exactly one valid JSON object without markdown wrappers.' >&2 && exit 1"},
                call_id="format_error"
            )
