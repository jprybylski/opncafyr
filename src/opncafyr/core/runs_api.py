"""Run integrity check for the page (#26): ``GET /api/runs/{run_id}/verify``.

Tool-agnostic: it recomputes the hashes a run's manifest lists (``core.runs.verify_run``)
and returns paths only, never contents.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from opncafyr.core.runs import RunError, run_dir, verify_run


def mount_runs_api(app: FastAPI) -> None:
    @app.get("/api/runs/{run_id}/verify")
    def verify(run_id: str) -> dict[str, Any]:
        ws = app.state.workspace
        if ws is None:
            raise HTTPException(
                409, {"code": "no_workspace", "message": "Open a study folder first."}
            )
        try:
            return {"report": verify_run(run_dir(ws.root, run_id))}
        except RunError as exc:
            raise HTTPException(404, {"code": exc.code, "message": exc.message}) from None
