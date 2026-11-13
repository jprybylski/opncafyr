"""Routes for standards bundles (#70, ADR 0011).

- ``GET /api/standards``: component types, imported snapshots (with how each reference
  resolves), installed files and selected templates; and whether Git sources can be fetched.
- ``POST /api/standards/fetch {repo, ref?, subdir?, template?, token?}``: fetch a Git source
  as a job (``core.gitfetch``). Returns ``job_id`` and ``fetch_id``; nothing is installed.
- ``POST /api/standards/inspect {source, template?, expect?}``: verify a local folder or
  archive (``source: {kind: "path", path}``) or a fetched source (``{kind: "fetched", id}``)
  and return the preview, with a ``preview_id`` when it passed every check.
- ``POST /api/standards/install {preview_id, components?, variant?, choices?, select?}``.
- ``PUT /api/standards/selected {type, snapshot, component}``: the template new report or deck
  setups use (``snapshot: null`` for the bundled one).

Every route needs an open study. None of them starts an analysis, adopts a profile or
changes a run; the token sent to ``fetch`` is never stored or returned.
"""

from __future__ import annotations

import secrets as _secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from opncafyr.core import gitfetch
from opncafyr.core.jobmanager import JobManagerError
from opncafyr.core.standards import NAME_RE, Standards, StandardsError

STATUS = {
    "no_workspace": 409,
    "not_found": 404,
    "bad_source": 400,
    "bad_token": 400,
    "bad_archive": 400,
    "unsafe_path": 400,
    "missing_file": 400,
    "too_large": 413,
    "empty": 400,
    "unknown_type": 400,
    "state_invalid": 409,
    "snapshot_changed": 409,
    "preview_expired": 409,
    "needs_choice": 409,
    "unresolved": 409,
    "not_selectable": 400,
    "standards_changed": 409,
    "exists": 409,
    "git_unavailable": 501,
    "jobs_unavailable": 409,
}
NAME = NAME_RE.pattern


class SourceBody(BaseModel):
    kind: str = Field(pattern=r"^(path|fetched)$")
    path: str | None = Field(default=None, max_length=4096)
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class TemplateBody(BaseModel):
    tool: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    type: str | None = Field(default=None, pattern=NAME)


class ExpectBody(BaseModel):
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class InspectBody(BaseModel):
    source: SourceBody
    template: TemplateBody | None = None
    expect: ExpectBody | None = None


class FetchBody(BaseModel):
    repo: str = Field(min_length=3, max_length=300)
    ref: str | None = Field(default=None, max_length=200)
    subdir: str | None = Field(default=None, max_length=300)
    template: TemplateBody | None = None
    token: str | None = Field(default=None, max_length=1024)


class InstallBody(BaseModel):
    preview_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    components: list[str] | None = Field(default=None, max_length=100)
    variant: str | None = Field(default=None, pattern=NAME)
    choices: dict[str, str] = Field(default_factory=dict, max_length=200)
    select: list[str] = Field(default_factory=list, max_length=20)


class SelectBody(BaseModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    snapshot: str | None = Field(default=None, max_length=200)
    component: str | None = Field(default=None, pattern=NAME)


def mount_standards_api(app: FastAPI, service: Standards) -> None:
    app.state.standards = service

    def root() -> Path:
        ws = app.state.workspace
        if ws is None:
            raise HTTPException(
                409, {"code": "no_workspace", "message": "Open a study folder first."}
            )
        return ws.root

    def call(fn, *args, **kw) -> Any:
        try:
            return fn(*args, **kw)
        except StandardsError as exc:
            raise HTTPException(
                STATUS.get(exc.code, 400), {"code": exc.code, "message": exc.message, **exc.extra}
            ) from None
        except gitfetch.FetchError as exc:
            raise HTTPException(
                STATUS.get(exc.code, 400), {"code": exc.code, "message": exc.message}
            ) from None

    @app.get("/api/standards")
    def listing() -> dict[str, Any]:
        ws = app.state.workspace
        out: dict[str, Any] = {
            "workspace_open": ws is not None,
            "git_available": gitfetch.available(getattr(app.state, "runtime", None)),
            "types": service.types_json(),
        }
        if ws is not None:
            out.update(call(service.listing, ws.root))
        return out

    @app.post("/api/standards/inspect")
    def inspect(body: InspectBody) -> dict[str, Any]:
        where = root()
        return call(
            service.inspect,
            where,
            body.source.model_dump(exclude_none=True),
            body.template.model_dump() if body.template else None,
            body.expect.model_dump(exclude_none=True) if body.expect else None,
        )

    @app.post("/api/standards/fetch")
    def fetch(body: FetchBody) -> dict[str, Any]:
        where = root()
        if not gitfetch.available(getattr(app.state, "runtime", None)):
            raise HTTPException(
                501,
                {
                    "code": "git_unavailable",
                    "message": "Git isn't installed with this opncafyr, so Git sources can't "
                    "be fetched. Import a local copy (folder or archive) instead.",
                },
            )
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            raise HTTPException(
                409, {"code": "jobs_unavailable", "message": "Jobs are not available."}
            )
        source = call(gitfetch.resolve_source, body.repo, body.ref, body.subdir)
        fetch_id = _secrets.token_hex(8)
        template = body.template
        suffixes: tuple[str, ...] = ()
        if template is not None:
            kind = next((t for t in service.types.values() if t.native == template.tool), None)
            if kind is None:
                raise HTTPException(400, {"code": "unknown_type", "message": "No such template."})
            suffixes = kind.suffixes
        request = call(
            gitfetch.fetch_request,
            source,
            body.token or None,
            fetch_id=fetch_id,
            mode="template" if template else "bundle",
            suffixes=suffixes,
            tool=template.tool if template else None,
            native_type=template.type if template else None,
        )
        try:
            job_id = jobs.submit(request)
        except JobManagerError as exc:
            raise HTTPException(409, {"code": "jobs_unavailable", "message": str(exc)}) from None
        call(service.fetched_dir, where, fetch_id)  # the id is valid for inspect
        return {"job_id": job_id, "fetch_id": fetch_id, "url": source.url, "ref": source.ref}

    @app.post("/api/standards/install")
    def install(body: InstallBody) -> dict[str, Any]:
        where = root()
        for value in body.choices.values():
            if value not in ("keep", "copy"):
                raise HTTPException(422, {"code": "bad_choice", "message": "Choose keep or copy."})
        return call(
            service.install,
            where,
            body.preview_id,
            body.components,
            body.variant,
            body.choices,
            body.select,
        )

    @app.put("/api/standards/selected")
    def select(body: SelectBody) -> dict[str, Any]:
        where = root()
        if (body.snapshot is None) != (body.component is None):
            raise HTTPException(
                422,
                {"code": "bad_choice", "message": "Give a snapshot and a component, or neither."},
            )
        return call(service.select, where, body.type, body.snapshot, body.component)
