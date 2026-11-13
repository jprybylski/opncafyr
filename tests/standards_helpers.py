"""Standards bundles for tests (#70): written into a temporary folder, hashed as they go.

Built from the files the app ships (an example profile and tutorial, the bundled report
template) plus a small deckifyr template and a minimal Word document, so every bundle here
passes the same checks a real one does.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import yaml

from opncafyr.adapters import quartifyr as Q
from opncafyr.core.guidance import shipped_files
from opncafyr.workflows.nca import study_profile as sp

PROFILE = shipped_files(sp.EXAMPLES)["dose-escalation.yaml"]
TUTORIAL = shipped_files(("opncafyr.workflows.nca", "tutorials"))["first-analysis.yaml"]
FOOTNOTES = Q.standard_footnotes().read_bytes()
CONFIG = Q.template().joinpath("config.yaml").read_bytes()

DESIGN = b"""\
deckifyr: "0.1"
slide:
  width: 13.333in
  height: 7.5in
  background: "#FFFFFF"
  background_image: logo.png
fonts:
  body: Arial
  heading: Arial
text_styles:
  title:
    font: heading
    size: 30pt
    bold: true
  body:
    font: body
    size: 16pt
  footnote:
    font: body
    size: 8pt
"""

LAYOUTS = b"""\
deckifyr: "0.1"
layouts:
  title-content:
    elements:
      title:
        type: text
        box: {x: 0.7in, y: 0.35in, width: 11.9in, height: 0.65in}
        style: title
        required: true
      content:
        type: slot
        box: {x: 0.7in, y: 1.25in, width: 11.9in, height: 5.35in}
      footnotes:
        type: footnotes
        box: {x: 0.7in, y: 6.72in, width: 11.9in, height: 0.45in}
        style: footnote
  blank:
    elements: {}
"""

PRESENTATION = b"""\
deckifyr: "0.1"
design:
  base: house-design.yaml
layouts: house-layouts.yaml
metadata:
  title: House deck
build:
  output: build/house.pptx
  manifest: build/house.manifest.json
slides: []
"""

LOGO = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def docx() -> bytes:
    """A minimal Word document: enough for the template check (a zip with document.xml)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def deck_files(prefix: str = "") -> dict[str, bytes]:
    return {
        f"{prefix}presentation.yaml": PRESENTATION,
        f"{prefix}house-design.yaml": DESIGN,
        f"{prefix}house-layouts.yaml": LAYOUTS,
        f"{prefix}logo.png": LOGO,
    }


def report_files(prefix: str = "") -> dict[str, bytes]:
    return {
        f"{prefix}org-reference.docx": docx(),
        f"{prefix}standard_footnotes.yaml": FOOTNOTES,
        f"{prefix}config.yaml": CONFIG,
    }


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bundle(
    folder: Path,
    components: list[dict[str, Any]],
    *,
    bundle_id: str = "house",
    version: str = "1.0.0",
    title: str = "House standards",
    extra: dict[str, Any] | None = None,
    tamper: dict[str, bytes] | None = None,
) -> Path:
    """Write a bundle: each component is ``{id, type, title, files: {path: bytes}}`` (or a
    ``ref``). ``tamper`` overwrites files after they were hashed."""
    folder.mkdir(parents=True, exist_ok=True)
    listed = []
    for comp in components:
        entry = {k: v for k, v in comp.items() if k != "files"}
        if "files" in comp:
            entry["files"] = []
            for path, data in comp["files"].items():
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                entry["files"].append({"path": path, "sha256": sha(data)})
        listed.append(entry)
    doc = {
        "schema_version": "1.0",
        "id": bundle_id,
        "version": version,
        "title": title,
        "components": listed,
        **(extra or {}),
    }
    (folder / "standards.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    for path, data in (tamper or {}).items():
        (folder / path).write_bytes(data)
    return folder


def mixed_bundle(folder: Path, **kw: Any) -> Path:
    """A profile, its tutorial, a report template and a deck template, linked."""
    return write_bundle(
        folder,
        [
            {
                "id": "dose-escalation",
                "type": "profile",
                "title": "Dose escalation",
                "files": {"profiles/dose-escalation.yaml": PROFILE},
                "links": ["first-analysis", "house-report", "house-deck"],
            },
            {
                "id": "first-analysis",
                "type": "tutorial",
                "title": "First analysis",
                "files": {"tutorials/first-analysis.yaml": TUTORIAL},
            },
            {
                "id": "house-report",
                "type": "report-template",
                "title": "House report",
                "files": report_files("report/"),
            },
            {
                "id": "house-deck",
                "type": "deck-template",
                "title": "House deck",
                "files": deck_files("deck/"),
            },
        ],
        **kw,
    )
