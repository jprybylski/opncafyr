"""Publishing routes, mounted by the NCA pack's ``api.mount``.

Sidecars (#30):
- ``GET /api/nca/outputs/check``: every artifact under the study's ``OUTPUTS/`` and the
  findings for its sidecar (``preflight-v1``), checked against the bundled
  ``standard_footnotes.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from opncafyr.adapters import quartifyr
from opncafyr.workflows.nca.publish import sidecars

if TYPE_CHECKING:
    from opncafyr.workflows.nca.api import Context


def bundled_footnotes() -> sidecars.Footnotes:
    return sidecars.load_footnotes(quartifyr.standard_footnotes())


def mount(app: FastAPI, ctx: Context) -> None:
    @app.get("/api/nca/outputs/check")
    def check_outputs() -> dict[str, Any]:
        root = ctx.workspace().root
        files, findings = sidecars.check_outputs(root, bundled_footnotes())
        return {
            "files": files,
            "blocking": sidecars.blocking(findings),
            "report": sidecars.preflight(findings),
        }
