"""The report and deck templates a study selected from imported standards (#70, ADR 0011).

A study uses the bundled templates (ADR 0007) until it selects an imported one. The selection
applies when a report or deck project is **set up**; an existing project keeps the template it
was set up from (recorded in its binding), and its status says when the selection differs.
Every use re-hashes the snapshot's files first (``core.standards.material``).

Footnotes: the curator and both projects use the selected report template's
``standard_footnotes.yaml`` when it has one, else the bundled file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opncafyr.adapters import quartifyr as Q
from opncafyr.core import standards as S
from opncafyr.workflows.nca.publish import sidecars
from opncafyr.workflows.nca.publish.curator import PublishError
from opncafyr.workflows.nca.standards import DECK_TEMPLATE, REPORT_TEMPLATE, report_files


def selected(root: Path, type_id: str) -> S.Material | None:
    """The selected template, verified; ``PublishError`` if it changed or can't be read."""
    try:
        return S.material(root, type_id)
    except S.StandardsError as exc:
        raise PublishError("standards_changed", exc.message) from None


def report_material(root: Path) -> S.Material | None:
    return selected(root, REPORT_TEMPLATE)


def deck_material(root: Path) -> S.Material | None:
    return selected(root, DECK_TEMPLATE)


def report_file(material: S.Material | None, name: str) -> Path | None:
    """Where the selected report template keeps ``name`` (one of ``REPORT_FILES``), if it does."""
    if material is None:
        return None
    rel = report_files(material.files, material.native_type).get(name)
    return material.files[rel] if rel is not None else None


def footnotes_bytes(root: Path) -> bytes:
    found = report_file(report_material(root), "standard_footnotes.yaml")
    return found.read_bytes() if found is not None else Q.standard_footnotes().read_bytes()


def footnotes_for(root: Path) -> sidecars.Footnotes:
    """The footnote keys published files are checked against for this study."""
    return sidecars.parse_footnotes(footnotes_bytes(root))


def deck_layout(material: S.Material) -> tuple[str, str, str]:
    """``(base folder, design file, layouts file)`` of a selected deck template."""
    base = f"templates/{material.native_type}/" if material.native_type else ""
    doc = yaml.safe_load(material.files[base + "presentation.yaml"].read_text("utf-8"))
    return base, str(doc["design"]["base"]), str(doc["layouts"])


def summary(root: Path, type_id: str, binding: dict[str, Any] | None) -> dict[str, Any]:
    """What a Report or Deck card says about templates: selected now, and set up from."""
    out: dict[str, Any] = {"type": type_id, "selected": None, "problem": None}
    try:
        found = S.material(root, type_id)
    except S.StandardsError as exc:
        out["problem"] = exc.message
        found = None
    if found is not None:
        out["selected"] = found.provenance
    used = (binding or {}).get("standards")
    out["project"] = used
    if binding is not None:
        now = found.provenance if found is not None else None
        same = (now or {}).get("component_sha256") == (used or {}).get("component_sha256")
        out["differs"] = not same and (now is not None or used is not None)
    return out
