"""Support bundle routes for the home page (#38).

- ``GET /api/support/preview`` builds a bundle in memory and returns every entry's text,
  so the user sees exactly what would be saved. Nothing is written.
- ``POST /api/support/bundle`` with ``{"preview_id": …}`` saves that same preview, byte for
  byte, to ``<app root>/logs/support/``.
- ``GET /api/support/bundles`` lists saved bundles; ``GET /api/support/bundles/{name}``
  downloads one. Only bundle file names are accepted.

Like every route, these need the session cookie; saving needs the session header too. The
bundle never leaves the machine unless the user sends it.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from opncafyr.core import support
from opncafyr.paths import app_root

PREVIEWS_KEPT = 3
PREVIEW_TTL_S = 600


class SaveBody(BaseModel):
    preview_id: str


def mount_support_api(app: FastAPI, packs: list[Any]) -> None:
    previews: OrderedDict[str, tuple[float, list[support.Entry]]] = OrderedDict()
    lock = threading.Lock()

    def entries() -> list[support.Entry]:
        ws = app.state.workspace
        root = ws.root if ws is not None else None
        sup = app.state.supervisor
        return support.build(
            app_root=app_root(),
            tools=app.state.tools,
            health=app.state.health,
            runtime=getattr(app.state, "runtime", None),
            apps={tool: str(sup.state(tool)) for tool in sup.tools},
            workspace=root,
            sensitive=support.sensitive_values(packs, root),
        )

    @app.get("/api/support/preview")
    def preview() -> dict[str, Any]:
        built = entries()
        preview_id = secrets.token_hex(8)
        now = time.monotonic()
        with lock:
            for key, (at, _) in list(previews.items()):
                if now - at > PREVIEW_TTL_S:
                    del previews[key]
            previews[preview_id] = (now, built)
            while len(previews) > PREVIEWS_KEPT:
                previews.popitem(last=False)
        return {
            "preview_id": preview_id,
            "entries": [
                {"name": e.name, "bytes": len(e.text.encode("utf-8")), "text": e.text}
                for e in built
            ],
        }

    @app.post("/api/support/bundle")
    def save(body: SaveBody) -> dict[str, Any]:
        with lock:
            found = previews.pop(body.preview_id, None)
        if found is None or time.monotonic() - found[0] > PREVIEW_TTL_S:
            raise HTTPException(
                404,
                {"code": "preview_expired", "message": "Preview the bundle again, then save."},
            )
        path = support.write(found[1], app_root())
        return {"name": path.name, "bytes": path.stat().st_size, "folder": "logs/support"}

    @app.get("/api/support/bundles")
    def bundles() -> dict[str, Any]:
        return {"bundles": support.saved_bundles(app_root())}

    @app.get("/api/support/bundles/{name}")
    def download(name: str) -> FileResponse:
        path = support.bundle_path(app_root(), name)
        if path is None:
            raise HTTPException(404, {"code": "not_found", "message": "There is no such bundle."})
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
            headers={"cache-control": "no-store"},
        )
