"""Project-file editor API (#52), for tools whose descriptor declares ``project_editor``.

Paths and text travel in JSON bodies, never in URLs, so no document content reaches an
access log. The security middleware already applies the Host, Origin and session-secret
checks to every route here. ``core.projectfiles`` holds the file rules.

- ``GET  /api/tools/{tool}/project``: title, the files offered (no text) and the settings.
- ``POST /api/tools/{tool}/project/read`` ``{path}``: one file's text and revision.
- ``POST /api/tools/{tool}/project/save`` ``{path, text, expected_sha256}``: 409
  ``conflict`` (with ``current_sha256``) when the file changed since it was opened.
- ``POST /api/tools/{tool}/project/settings`` ``{values, expected_sha256}``.
- ``POST /api/tools/{tool}/project/create`` ``{rule, name}``: a new file from one of the
  adapter's rules (listed as ``new_files``); 409 ``exists`` rather than overwrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from opncafyr.core.projectfiles import ProjectFileError, ProjectFiles

TOOL_ID = r"^[a-z][a-z0-9_-]*$"
# A hard limit on the request; each file's own cap is checked on its encoded bytes.
MAX_TEXT_CHARS = 2_000_000


class ReadBody(BaseModel):
    path: str = Field(min_length=1, max_length=240)


class SaveBody(ReadBody):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CreateBody(BaseModel):
    rule: int = Field(ge=0, le=31)
    name: str = Field(min_length=1, max_length=80)


class SettingsBody(BaseModel):
    values: dict[str, Any]
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def mount_project_files_api(app: FastAPI, service: ProjectFiles) -> None:
    def root() -> Path:
        workspace = getattr(app.state, "workspace", None)
        if workspace is None:
            raise HTTPException(
                409, {"code": "no_workspace", "message": "Open a study folder first."}
            )
        return workspace.root

    def run(fn):
        try:
            return fn()
        except ProjectFileError as exc:
            raise HTTPException(exc.status, exc.detail()) from None

    tool_param = PathParam(pattern=TOOL_ID, max_length=64)

    @app.get("/api/tools/{tool}/project")
    def project(tool: str = tool_param) -> dict[str, Any]:
        return run(lambda: service.overview(root(), tool))

    @app.post("/api/tools/{tool}/project/read")
    def read(body: ReadBody, tool: str = tool_param) -> dict[str, Any]:
        return run(lambda: service.read(root(), tool, body.path))

    @app.post("/api/tools/{tool}/project/save")
    def save(body: SaveBody, tool: str = tool_param) -> dict[str, Any]:
        return run(lambda: service.save(root(), tool, body.path, body.text, body.expected_sha256))

    @app.post("/api/tools/{tool}/project/settings")
    def settings(body: SettingsBody, tool: str = tool_param) -> dict[str, Any]:
        return run(lambda: service.save_settings(root(), tool, body.values, body.expected_sha256))

    @app.post("/api/tools/{tool}/project/create")
    def create(body: CreateBody, tool: str = tool_param) -> dict[str, Any]:
        return run(lambda: service.create(root(), tool, body.rule, body.name))
