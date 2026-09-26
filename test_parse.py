import json
import pydantic
from src.orchestrator.model_adapter import ModelAdapter
from src.common.types import ToolCall

adapter = ModelAdapter(mock_mode=True)
json_str = '{"tool_name": "read_file", "tool_args": {"path": "main.py"}}'
data = json.loads(json_str)
result = adapter.parse_decision(json_str)
try:
    from pydantic import TypeAdapter
    TypeAdapter(ToolCall).validate_python(data)
except Exception as e:
    print(e)
