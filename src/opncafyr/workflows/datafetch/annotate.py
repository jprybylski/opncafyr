"""Label files in the dataset scan that datum fetched (#83).

The annotation names the dataset, when the file was applied and whether it still has the
bytes that were applied, plus the last check's result for that dataset. It reads only
``.opncafyr/datafetch/state.json`` and never runs datum. A ``fail``-policy dataset whose
last check found an upstream change is marked "changed upstream" (D8); that never blocks
an import, the user decides.
"""

from __future__ import annotations

from typing import Any

from opncafyr.adapters.datum import state
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
        doc = state.read(ws.root)
        for dataset, entry in doc["datasets"].items():
            for f in entry["files"]:
                if f["path"] != preview.path:
                    continue
                out: dict[str, Any] = {
                    "dataset": dataset,
                    "fetched_at": entry["fetched_at"],
                    "matches_fetched": f["sha256"] == preview.sha256,
                }
                check = doc.get("last_check") or {}
                for r in check.get("results", []):
                    if r["id"] == dataset:
                        out["last_check"] = {"at": check["at"], "status": r["status"]}
                        out["changed_upstream"] = r["status"] in CHANGED
                return out
        return {}
