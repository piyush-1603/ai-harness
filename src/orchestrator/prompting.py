from typing import Optional
from jinja2 import StrictUndefined, Template

# Default templates matching mini-swe-agent's configuration pattern
DEFAULT_SYSTEM_TEMPLATE = """
You are an autonomous AI coding agent. You can explore a repository, edit files, and run tests.
Your goal is to fix the issue described by the user and ensure all tests pass.
"""

DEFAULT_INSTANCE_TEMPLATE = """
Issue: {{ issue }}

Hypothesis: {{ scratchpad_state.hypothesis }}

{% if scratchpad_state.files_touched %}
Files Touched: {{ scratchpad_state.files_touched | join(', ') }}
{% endif %}

{% if scratchpad_state.attempt_history %}
Attempt History:
{% for attempt in scratchpad_state.attempt_history[-5:] %}
- {{ attempt }}
{% endfor %}
{% endif %}

Last Observation:
{{ last_observation }}

{% if recovery_hint %}
Recovery Strategy Hint:
{{ recovery_hint }}
{% endif %}

What is your next action?
"""

class PromptBuilder:
    """
    Builds system prompt and per-turn user prompt using Jinja2 templates.
    Adopts the system_template / instance_template pattern from mini-swe-agent.
    """
    def __init__(self, system_template: str = DEFAULT_SYSTEM_TEMPLATE, instance_template: str = DEFAULT_INSTANCE_TEMPLATE):
        self.system_template = system_template
        self.instance_template = instance_template

    def build_system_prompt(self) -> str:
        return Template(self.system_template, undefined=StrictUndefined).render()

    def build_turn_prompt(self, issue: str, scratchpad_state, last_observation: str, recovery_hint: Optional[str] = None) -> str:
        template = Template(self.instance_template, undefined=StrictUndefined)
        return template.render(
            issue=issue,
            scratchpad_state=scratchpad_state,
            last_observation=last_observation,
            recovery_hint=recovery_hint
        )
