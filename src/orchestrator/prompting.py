from typing import Optional

class PromptBuilder:
    """
    Builds system prompt and per-turn user prompt.
    """
    def build_system_prompt(self) -> str:
        return (
            "You are an autonomous AI coding agent. "
            "You can explore a repository, edit files, and run tests. "
            "Your goal is to fix the issue described by the user and ensure all tests pass."
        )

    def build_turn_prompt(self, issue: str, scratchpad_state, last_observation: str, recovery_hint: Optional[str] = None) -> str:
        prompt = f"Issue: {issue}\n\n"
        prompt += f"Hypothesis: {scratchpad_state.hypothesis}\n"
        if scratchpad_state.files_touched:
            prompt += f"Files Touched: {', '.join(scratchpad_state.files_touched)}\n"
        
        if scratchpad_state.attempt_history:
            prompt += "\nAttempt History:\n"
            for attempt in scratchpad_state.attempt_history[-5:]: # Keep last 5 for context limits
                prompt += f"- {attempt}\n"
            
        prompt += f"\nLast Observation:\n{last_observation}\n"
        
        if recovery_hint:
            prompt += f"\nRecovery Strategy Hint:\n{recovery_hint}\n"
            
        prompt += "\nWhat is your next action?"
        return prompt
