import hashlib
import os
import re
from pathlib import Path
from typing import Union

class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactStore:
    """
    Deterministic, filesystem-backed storage for large observation artifacts.
    """

    def __init__(self, base_dir: Union[str, Path] = ".harness"):
        self.base_dir = Path(base_dir)
        self.artifact_root = (self.base_dir / "artifacts").resolve()

    def _validate_task_id(self, task_id: str) -> None:
        """Validate task_id format."""
        if not re.match(r'^[A-Za-z0-9_-]+$', task_id):
            raise ValueError(f"Invalid task_id: {task_id}")

    def _validate_ref(self, ref: str) -> None:
        """Validate artifact reference format."""
        if not re.match(r'^artifact_[a-f0-9]{64}$', ref):
            raise ValueError(f"Invalid artifact reference: {ref}")

    def _get_task_dir(self, task_id: str, create: bool = False) -> Path:
        """Get the artifact directory for a task."""
        self._validate_task_id(task_id)
        d = self.base_dir / "artifacts" / task_id
        if create:
            d.mkdir(parents=True, exist_ok=True)
        if d.exists():
            resolved_dir = d.resolve()
            if not resolved_dir.is_relative_to(self.artifact_root):
                raise ValueError("Unsafe path resolution: directory escapes artifact root")
        return d

    def put_text(self, task_id: str, content: str, kind: str = "output") -> str:
        """
        Store text deterministically. Returns the opaque artifact reference.
        Identical content yields the same reference.
        """
        content_bytes = content.encode('utf-8')
        h = hashlib.sha256(content_bytes).hexdigest()
        ref = f"artifact_{h}"

        target_dir = self._get_task_dir(task_id, create=True)
        target_path = target_dir / ref

        if target_path.is_file():
            # If target exists and is identical, do not overwrite to preserve mtime
            return ref

        import tempfile
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_dir,
                prefix=f".{ref}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content_bytes)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, target_path)
        except Exception:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

        return ref

    def get_text(self, task_id: str, ref: str) -> str:
        """Retrieve the original text of an artifact."""
        self._validate_ref(ref)
        target_dir = self._get_task_dir(task_id, create=False)
        target_path = target_dir / ref
        if not target_path.exists():
            raise FileNotFoundError(f"Artifact {ref} not found for task {task_id}")

        resolved_path = target_path.resolve()
        if not resolved_path.is_relative_to(self.artifact_root):
            raise ValueError("Unsafe path resolution: artifact escapes root")

        if not resolved_path.is_file():
            raise FileNotFoundError(f"Artifact {ref} is not a file")

        with open(resolved_path, 'rb') as f:
            content_bytes = f.read()

        expected_hash = ref[9:]
        if hashlib.sha256(content_bytes).hexdigest() != expected_hash:
            raise ArtifactIntegrityError(f"Integrity check failed for artifact {ref}")

        return content_bytes.decode('utf-8')

    def exists(self, task_id: str, ref: str) -> bool:
        """Check if an artifact reference exists for a task."""
        self._validate_ref(ref)
        target_dir = self._get_task_dir(task_id, create=False)
        target_path = target_dir / ref

        if not target_path.exists():
            return False

        resolved_path = target_path.resolve()
        if not resolved_path.is_relative_to(self.artifact_root):
            raise ValueError("Unsafe path resolution: artifact escapes root")

        return resolved_path.is_file()
