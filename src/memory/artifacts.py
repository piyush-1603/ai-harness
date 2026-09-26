import hashlib
import os
import re
from pathlib import Path
from typing import Union

class ArtifactStore:
    """
    Deterministic, filesystem-backed storage for large observation artifacts.
    """

    def __init__(self, base_dir: Union[str, Path] = ".harness"):
        self.base_dir = Path(base_dir)

    def _get_dir(self, task_id: str) -> Path:
        """Get and ensure existence of the artifact directory for a task."""
        if not re.match(r'^[\w-]+$', task_id):
            raise ValueError(f"Invalid task_id: {task_id}")
        d = self.base_dir / "artifacts" / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _validate_ref(self, ref: str) -> None:
        """Validate artifact reference format."""
        if not re.match(r'^artifact_[a-f0-9]{16,64}$', ref):
            raise ValueError(f"Invalid artifact reference: {ref}")

    def put_text(self, task_id: str, content: str, kind: str = "output") -> str:
        """
        Store text deterministically. Returns the opaque artifact reference.
        Identical content yields the same reference.
        """
        content_bytes = content.encode('utf-8')
        h = hashlib.sha256(content_bytes).hexdigest()
        ref = f"artifact_{h}"
        
        target_dir = self._get_dir(task_id)
        target_path = target_dir / ref
        
        if not target_path.exists():
            tmp_path = target_path.with_suffix('.tmp')
            with open(tmp_path, 'wb') as f:
                f.write(content_bytes)
            os.replace(tmp_path, target_path)
            
        return ref

    def get_text(self, task_id: str, ref: str) -> str:
        """Retrieve the original text of an artifact."""
        self._validate_ref(ref)
        target_path = self._get_dir(task_id) / ref
        if not target_path.is_file():
            raise FileNotFoundError(f"Artifact {ref} not found for task {task_id}")
        with open(target_path, 'r', encoding='utf-8') as f:
            return f.read()

    def exists(self, task_id: str, ref: str) -> bool:
        """Check if an artifact reference exists for a task."""
        self._validate_ref(ref)
        target_path = self._get_dir(task_id) / ref
        return target_path.is_file()
