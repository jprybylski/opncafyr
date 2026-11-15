"""Per-machine approvals for ``file`` sources outside the study (#83, D3).

A shared ``.data.yaml`` must not quietly copy something like ``~/.ssh/id_ed25519`` into a
study that might later be sent with version history. So a ``file`` source outside the
study runs only after the user approves that exact path for that dataset in that study,
on this computer. Approvals live in the launcher's state folder, never in the study; the
key is a hash of the study (its folder and creation time), the dataset id and the path, so
a copied study, a renamed dataset or a changed path needs approval again.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opncafyr.core.manifest import atomic_write_json
from opncafyr.paths import state_dir

FILE_NAME = "approvals.json"
_lock = threading.Lock()


def study_key(root: Path, created_at: str) -> str:
    text = f"{Path(root).resolve()}\n{created_at}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key(study: str, dataset: str, path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return f"{study}:{dataset}:{digest}"


class Approvals:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_dir() / "datafetch" / FILE_NAME

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"schema_version": 1, "approved": {}}
        if not isinstance(data, dict) or not isinstance(data.get("approved"), dict):
            return {"schema_version": 1, "approved": {}}
        return data

    def approved(self, study: str, dataset: str, path: str) -> bool:
        with _lock:
            return _key(study, dataset, path) in self._read()["approved"]

    def approve(self, study: str, dataset: str, path: str) -> None:
        with _lock:
            data = self._read()
            data["approved"][_key(study, dataset, path)] = datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            atomic_write_json(self.path, data)

    def revoke(self, study: str, dataset: str, path: str) -> None:
        with _lock:
            data = self._read()
            if data["approved"].pop(_key(study, dataset, path), None) is not None:
                atomic_write_json(self.path, data)
