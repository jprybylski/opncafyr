"""Publishing routes, mounted by the NCA pack's ``api.mount``.

Sidecars (#30):
- ``GET /api/nca/outputs/check``: every artifact under the study's ``OUTPUTS/`` and the
  findings for its sidecar (``preflight-v1``), checked against the bundled
  ``standard_footnotes.yaml``. A file that no publication lists is a ``not-recorded``
  warning: reports and decks never use it.

Curator (#31):
- ``GET  /api/nca/publish/runs``: every run, whether it can be published (completed,
  replay passed, sealed files intact) and the tables and figures it offers.
- ``POST /api/nca/publications {items: [{run_id, artifact}], dry_run, label}``: with
  ``dry_run`` the plan (names, statuses, sidecar findings); without it the files are copied
  into ``OUTPUTS/`` and the manifest is recorded (201). Refusals are 409 with a code.
- ``GET  /api/nca/publications``; ``GET /api/nca/publications/{id}`` (with ``changed``:
  published files that no longer match).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from opncafyr.adapters import quartifyr
from opncafyr.core.artifacts import list_publications
from opncafyr.workflows.nca.publish import curator, sidecars
from opncafyr.workflows.nca.publish.catalog import UNAVAILABLE

if TYPE_CHECKING:
    from opncafyr.workflows.nca.api import Context

STATUS = {
    "not_found": 404,
    "unknown_artifact": 404,
    "nothing_selected": 422,
    "duplicate": 422,
    "too_many": 422,
    "publish_failed": 500,
}


def failure(exc: curator.PublishError) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(exc.code, 409),
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


def bundled_footnotes() -> sidecars.Footnotes:
    return sidecars.load_footnotes(quartifyr.standard_footnotes())


class PickBody(BaseModel):
    run_id: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
    artifact: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,80}$")


class PublishBody(BaseModel):
    items: list[PickBody] = Field(max_length=curator.MAX_ITEMS)
    dry_run: bool = False
    label: str | None = Field(default=None, max_length=200)


def mount(app: FastAPI, ctx: Context) -> None:
    @app.get("/api/nca/outputs/check")
    def check_outputs() -> dict[str, Any]:
        root = ctx.workspace().root
        files, findings = sidecars.check_outputs(root, bundled_footnotes())
        recorded = curator.recorded_paths(root)
        findings += [
            sidecars.Finding(
                "not-recorded",
                "warning",
                rel,
                "No publication lists this file, so reports and decks won't use it.",
            )
            for rel in files
            if rel not in recorded
        ]
        return {
            "files": files,
            "blocking": sidecars.blocking(findings),
            "report": sidecars.preflight(findings),
        }

    @app.get("/api/nca/publish/runs")
    def publish_runs() -> dict[str, Any]:
        root = ctx.workspace().root
        return {"runs": curator.publishable_runs(root), "unavailable": UNAVAILABLE}

    @app.post("/api/nca/publications")
    def create_publication(body: PublishBody) -> Any:
        root = ctx.workspace().root
        picks = [curator.Pick(i.run_id, i.artifact) for i in body.items]
        try:
            if body.dry_run:
                return {"plan": curator.plan(root, picks, bundled_footnotes()).to_dict()}
            doc = curator.publish(root, picks, bundled_footnotes(), body.label)
        except curator.PublishError as exc:
            raise failure(exc) from None
        return JSONResponse({"publication": doc}, status_code=201)

    @app.get("/api/nca/publications")
    def publications() -> dict[str, Any]:
        root = ctx.workspace().root
        out = []
        for listed in list_publications(root):
            if listed.get("state") != "recorded":
                out.append(listed)
                continue
            try:
                doc = curator.read_publication(root, listed["publication_id"])
            except curator.PublishError:
                out.append({"publication_id": listed["publication_id"], "state": "incomplete"})
                continue
            out.append(
                {
                    "publication_id": doc["publication_id"],
                    "created_at": doc["created_at"],
                    "label": doc.get("label"),
                    "state": "recorded",
                    "runs": [{"run_id": r["run_id"], "analyte": r["analyte"]} for r in doc["runs"]],
                    "items": len(doc["items"]),
                }
            )
        return {"publications": out}

    @app.get("/api/nca/publications/{publication_id}")
    def publication(publication_id: str) -> dict[str, Any]:
        root = ctx.workspace().root
        try:
            doc = curator.read_publication(root, publication_id)
        except curator.PublishError as exc:
            raise failure(exc) from None
        return {"publication": doc, "changed": curator.verify_publication(root, doc)}
