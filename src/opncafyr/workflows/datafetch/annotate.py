"""Label files in the dataset scan that datum fetched (#83, #85).

The annotation names the dataset, when the file was applied and whether it still has the
bytes that were applied, plus the last check's result for that dataset. It reads
``.opncafyr/datafetch/state.json``, ``.data.yaml`` (targets) and the lockfile (each
file's SHA-256, recorded by datum) and never runs datum. A ``fail``-policy dataset whose
last check found an upstream change is marked "changed upstream" (D8); that never blocks
an import, the user decides.
"""

from __future__ import annotations

from typing import Any

from opncafyr.adapters.datum import LOCK_FILE, state
from opncafyr.adapters.datum import config as cfg
from opncafyr.adapters.datum.staging import SnapshotError, read_lock, recorded_files
from opncafyr.core.datasets import DatasetPreview

CHANGED = ("updated", "stale", "fail")


class FetchedAnnotator:
    id = "datafetch"

    def __init__(self) -> None:
        self._app: Any = None

    def bind(self, app: Any) -> None:
        self._app = app

    def annotate(self, preview: DatasetPreview) -> dict[str, Any]:
        ws = getattr(getattr(self._app, "state", None), "workspace", None)
        if ws is None or not preview.path:
            return {}
        root = ws.root
        doc = state.read(root)
        if not doc["datasets"]:
            return {}
        try:
            targets = {d.get("id"): d.get("target") for d in cfg.load(root).datasets}
            items = read_lock(root / LOCK_FILE)
            for dataset, entry in doc["datasets"].items():
                target = targets.get(dataset)
                if not isinstance(target, str):
                    continue
                for rel, sha in recorded_files(target, items.get(dataset)):
                    if rel == preview.path:
                        return self._found(doc, dataset, entry, sha, preview)
        except (cfg.ConfigError, SnapshotError):
            return {}
        return {}

    @staticmethod
    def _found(
        doc: dict[str, Any],
        dataset: str,
        entry: dict[str, Any],
        sha: str | None,
        preview: DatasetPreview,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"dataset": dataset, "fetched_at": entry["applied_at"]}
        if sha is not None:
            out["matches_fetched"] = sha == preview.sha256
        check = doc.get("last_check") or {}
        for r in check.get("results", []):
            if r["id"] == dataset:
                out["last_check"] = {"at": check["at"], "status": r["status"]}
                out["changed_upstream"] = r["status"] in CHANGED
        return out
