from textual.widgets import Static
from textual.reactive import reactive

class LifecycleWidget(Static):
    """A minimal widget displaying the lifecycle phase."""

    active_phase = reactive("PLAN")
    task_status = reactive("RUNNING")

    def render(self) -> str:
        if self.task_status == "DONE":
            return "[b #52b788]● TASK COMPLETED[/]"

        phases = ["PLAN", "EXECUTE", "VERIFY", "RECOVER"]
        result = []

        accent = "#4ea8de"
        if self.task_status == "FAILED":
            accent = "#e63946"
        elif self.task_status == "BLOCKED":
            accent = "#ffb703"

        for p in phases:
            if p == self.active_phase:
                result.append(f"[b {accent}]● {p}[/]")
            else:
                result.append(f"○ {p}")
        return "    ".join(result)
