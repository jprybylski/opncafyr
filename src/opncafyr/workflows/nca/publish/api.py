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

Report (#32, ``report.py``):
- ``GET  /api/nca/report``: the project, its publication, running jobs, the last check's
  findings, every build and each build's ``stale_inputs`` (#52).
- ``POST /api/nca/report {publication_id, source}``: set up ``report/`` (a job) or bind
  it to another publication (409 ``needs_choice`` if ``report.qmd`` was edited).
- ``POST /api/nca/report/check``; ``POST /api/nca/report/render {status?}``: jobs. Without
  a status the render uses the saved report settings, or asks (#52). Both are refused
  while the report editor holds unsaved text. The editor itself is the launcher's
  ``/api/tools/quartifyr/project`` (``core/projectfiles_api.py``).
- ``GET  /api/nca/report/builds/{id}/docx``: the DOCX, if it still matches its manifest.

Deck (#33, ``deck.py``):
- ``GET  /api/nca/deck``; ``POST /api/nca/deck {publication_id, source}`` (set up or bind).
- ``POST /api/nca/deck/editor`` starts the editor under ``/apps/deckifyr/``; ``DELETE``
  stops it (saving unsaved edits).
- ``POST /api/nca/deck/check``; ``POST /api/nca/deck/build {previews}``: jobs, refused
  while the editor has unsaved edits. ``POST /api/nca/deck/previews`` runs the LibreOffice
  probe.
- ``GET  /api/nca/deck/builds/{id}/pptx``: the PPTX, if it still matches its manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from opncafyr.adapters import quartifyr
from opncafyr.core.artifacts import list_publications
from opncafyr.workflows.nca.publish import curator, deck, report, sidecars, templates

if TYPE_CHECKING:
    from opncafyr.workflows.nca.api import Context

STATUS = {
    "not_found": 404,
    "unknown_artifact": 404,
    "nothing_selected": 422,
    "duplicate": 422,
    "too_many": 422,
    "publish_failed": 500,
    "bad_status": 422,
    "editor_failed": 503,
    "standards_changed": 409,
}


def failure(exc: curator.PublishError) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(exc.code, 409),
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


def bundled_footnotes() -> sidecars.Footnotes:
    """The footnote keys of the bundled template (ADR 0007)."""
    return sidecars.load_footnotes(quartifyr.standard_footnotes())


def footnotes(root: Path) -> sidecars.Footnotes:
    """The footnote keys for this study: the selected report template's, else bundled (#70)."""
    return templates.footnotes_for(root)


class PickBody(BaseModel):
    run_id: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
    artifact: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,80}$")


class PublishBody(BaseModel):
    items: list[PickBody] = Field(max_length=curator.MAX_ITEMS)
    dry_run: bool = False
    label: str | None = Field(default=None, max_length=200)
    # The recorded publication this one revises (#62); the earlier one stays as it is.
    revises: str | None = Field(default=None, pattern=r"^pub-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
    # The study profile publication step this is for (#68), chosen by the user.
    profile_step: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")


def mount(app: FastAPI, ctx: Context) -> None:
    @app.get("/api/nca/outputs/check")
    def check_outputs() -> dict[str, Any]:
        root = ctx.workspace().root
        try:
            notes = footnotes(root)
        except curator.PublishError as exc:
            raise failure(exc) from None
        files, findings = sidecars.check_outputs(root, notes)
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
        return {"runs": curator.publishable_runs(root)}

    @app.post("/api/nca/publications")
    def create_publication(body: PublishBody) -> Any:
        root = ctx.workspace().root
        picks = [curator.Pick(i.run_id, i.artifact) for i in body.items]
        context = ctx.profile_context(
            body.profile_step, "publication", {i.run_id for i in body.items}
        )
        try:
            if body.dry_run:
                return {"plan": curator.plan(root, picks, footnotes(root)).to_dict()}
            doc = curator.publish(root, picks, footnotes(root), body.label, body.revises, context)
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

    report.mount(app, ctx, failure)
    deck.mount(app, ctx, failure)
