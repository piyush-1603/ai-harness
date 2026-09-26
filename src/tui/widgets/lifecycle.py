from textual.widgets import Static
from textual.reactive import reactive

class LifecycleWidget(Static):
    """A minimal widget displaying the lifecycle phase."""

    active_phase = reactive("PLAN")

    def render(self) -> str:
        phases = ["PLAN", "EXECUTE", "VERIFY", "RECOVER"]
        result = []
        for p in phases:
            if p == self.active_phase:
                result.append(f"[b #4ea8de]● {p}[/]")
            else:
                result.append(f"○ {p}")
        return "    ".join(result)
