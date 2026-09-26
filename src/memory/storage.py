"""Disk persistence for TaskState and append-only event logs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Union

from src.memory.models import Event, TaskState


class TaskStorage:
    """
    Handles JSON file persistence of TaskState snapshots under .harness/state/<task_id>.json
    and append-only event logs under .harness/events/<task_id>.jsonl.
    """

    def __init__(self, base_dir: Union[Path, str] = ".harness") -> None:
        self.base_dir = Path(base_dir)
        self.state_dir = self.base_dir / "state"
        self.events_dir = self.base_dir / "events"

    def get_state_path(self, task_id: str) -> Path:
        """Return the target path for a task's JSON state snapshot."""
        return self.state_dir / f"{task_id}.json"

    def get_events_path(self, task_id: str) -> Path:
        """Return the target path for a task's JSONL event log."""
        return self.events_dir / f"{task_id}.jsonl"

    def save_state(self, state: TaskState) -> Path:
        """
        Persist TaskState to disk atomically as JSON.
        Ensures directory exists and writes via temporary file to prevent corruption.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.get_state_path(state.task_id)
        temp_path = self.state_dir / f"{state.task_id}.tmp"

        json_str = state.to_json(indent=2)
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())

        temp_path.replace(target_path)
        return target_path

    def load_state(self, task_id: str) -> TaskState:
        """
        Load and deserialize TaskState from disk.
        Raises FileNotFoundError if the task file does not exist.
        """
        target_path = self.get_state_path(task_id)
        if not target_path.is_file():
            raise FileNotFoundError(
                f"Task state file not found for task_id '{task_id}' at {target_path}"
            )

        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()

        return TaskState.from_json(content)

    def append_event(self, task_id: str, event: Event) -> None:
        """
        Append an event record to the canonical event log (.harness/events/<task_id>.jsonl).
        """
        self.events_dir.mkdir(parents=True, exist_ok=True)
        event_line = json.dumps(event.to_dict()) + "\n"

        events_path = self.get_events_path(task_id)
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(event_line)
            f.flush()

    def load_events(self, task_id: str) -> list[Event]:
        """
        Read and deserialize all events from the canonical event log for a given task_id.
        Returns an empty list when it does not exist.
        """
        events_path = self.get_events_path(task_id)
        if not events_path.is_file():
            return []

        events: list[Event] = []
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(Event.from_dict(json.loads(line)))

        return events

    def task_exists(self, task_id: str) -> bool:
        """Check if a task state file exists."""
        return self.get_state_path(task_id).is_file()

    def list_tasks(self) -> list[str]:
        """List all task IDs present in the state directory."""
        if not self.state_dir.is_dir():
            return []
        return [
            p.stem
            for p in self.state_dir.glob("*.json")
            if not p.name.endswith(".events.json") and not p.name.endswith(".tmp")
        ]
