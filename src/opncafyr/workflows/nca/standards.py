"""The NCA pack's standards types (#70, ADR 0011): study profiles, report and deck templates.

Core verifies, stores and copies a standards bundle; these say what the files must be.

- **Study profile** (``profile``): copied into ``profiles/``, checked exactly like the
  study's own profile files (``study_profile.examine``).
- **Report template** (``report-template``): any of ``org-reference.docx``,
  ``standard_footnotes.yaml``, ``config.yaml`` and quartifyr's ``style.yaml`` (partial: rendered
  as an override of quartifyr's default style); whatever is
  missing keeps the bundled default. Also accepted as a quartifyr template source in its own
  layout (``templates/<type>/`` when typed); there its ``_quarto.yml`` and shell ``.qmd`` are
  not used, because the launcher writes the publication's own.
- **Deck template** (``deck-template``): a deckifyr template source (``presentation.yaml``
  naming its design and layouts files, plus local assets), carried flat or referenced in
  deckifyr's own layout, flat or typed. The launcher's "NCA results" deck needs the layouts
  ``blank`` and ``title-content`` (with ``title`` and ``content``) and a ``title`` text style.

Checks read the files only. None of them runs a tool, and a problem never quotes a value.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from typing import Any

import yaml

from opncafyr.adapters.quartifyr import config_yaml
from opncafyr.core.registry import StandardsCheck, StandardsType
from opncafyr.workflows.nca import study_profile
from opncafyr.workflows.nca.publish import sidecars

REPORT_TEMPLATE = "report-template"
DECK_TEMPLATE = "deck-template"

# Name in a template -> what it is, in the order the preview lists them.
REPORT_FILES = {
    "org-reference.docx": "the Word reference document (styles, headers, page layout)",
    "standard_footnotes.yaml": "the standard footnotes and abbreviations",
    "config.yaml": "the reportifyr settings",
    "style.yaml": "quartifyr's style file",
}
DECK_LAYOUTS = {"blank": (), "title-content": ("title", "content")}
DECK_TEXT_STYLES = ("title",)


def _base(ctx: StandardsCheck) -> str:
    return f"templates/{ctx.native_type}/" if ctx.native_type else ""


# --- study profiles ----------------------------------------------------------------------------


def check_profile(files: Mapping[str, bytes], ctx: StandardsCheck) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    for name, data in sorted(files.items()):
        found = study_profile.examine(data, name, ctx.catalog)["problems"]
        problems.extend(p if p.startswith(name) else f"{name}: {p}" for p in found)
    return problems, []


# --- report templates --------------------------------------------------------------------------


def report_files(files: Mapping[str, Any], native_type: str | None) -> dict[str, str]:
    """``{template file name: its path in files}`` for the report files a template holds."""
    base = f"templates/{native_type}/" if native_type else ""
    return {name: base + name for name in REPORT_FILES if base + name in files}


def _yaml_mapping(data: bytes) -> Any:
    try:
        return yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None


def check_report(files: Mapping[str, bytes], ctx: StandardsCheck) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    used = report_files(files, ctx.native_type)
    if not used:
        return [f"holds none of {', '.join(REPORT_FILES)} (in {_base(ctx) or 'its folder'})"], []
    for name, path in used.items():
        data = files[path]
        if name == "org-reference.docx":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    if "word/document.xml" not in zf.namelist():
                        problems.append(f"{path} is not a Word document")
            except zipfile.BadZipFile:
                problems.append(f"{path} is not a Word document")
        elif name == "standard_footnotes.yaml":
            try:
                sidecars.parse_footnotes(data)
            except sidecars.SidecarError as exc:
                problems.append(f"{path}: {exc}")
        elif name == "config.yaml":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{path} is not UTF-8 text")
                continue
            for found in config_yaml.check_config(text):
                (problems if found.severity == "error" else notes).append(
                    f"{path}: {found.message}"
                )
        elif not isinstance(_yaml_mapping(data), dict):
            problems.append(f"{path} is not a YAML mapping")
    for name, what in REPORT_FILES.items():
        if name not in used:
            notes.append(f"no {name}: the report keeps the bundled {what}")
    unused = sorted(p for p in files if p not in used.values())
    if unused:
        notes.append(
            f"not used: {', '.join(unused[:5])}{' …' if len(unused) > 5 else ''} "
            "(the launcher writes the report's own _quarto.yml and report.qmd)"
        )
    return problems, notes


REPORT_TYPE = StandardsType(
    id=REPORT_TEMPLATE,
    title="Report template",
    format="1.0",
    suffixes=(".docx", ".yaml", ".yml", ".qmd", ".md"),
    check=check_report,
    native="quartifyr",
)


# --- deck templates ----------------------------------------------------------------------------


def deck_names(files: Mapping[str, bytes], native_type: str | None) -> tuple[str, str, str]:
    """``(base folder, design file, layouts file)`` a deck template's presentation names."""
    base = f"templates/{native_type}/" if native_type else ""
    doc = _yaml_mapping(files.get(base + "presentation.yaml", b""))
    design = (doc or {}).get("design") if isinstance(doc, dict) else None
    design_name = design.get("base") if isinstance(design, dict) else None
    layouts_name = doc.get("layouts") if isinstance(doc, dict) else None
    return base, str(design_name or ""), str(layouts_name or "")


def check_deck(files: Mapping[str, bytes], ctx: StandardsCheck) -> tuple[list[str], list[str]]:
    base, design_name, layouts_name = deck_names(files, ctx.native_type)
    where = base + "presentation.yaml"
    if where not in files:
        return [f"has no {where} (a deckifyr template source names its design and layouts)"], []
    if not isinstance(_yaml_mapping(files[where]), dict):
        return [f"{where} is not a YAML mapping"], []
    problems: list[str] = []
    docs: dict[str, Any] = {}
    for label, name in (("design.base", design_name), ("layouts", layouts_name)):
        path = base + name
        if not name or "/" in name or name.startswith(".") or path not in files:
            problems.append(f"{where}: {label} must name a file beside it in the template")
            continue
        docs[label] = _yaml_mapping(files[path])
        if not isinstance(docs[label], dict):
            problems.append(f"{path} is not a YAML mapping")
    if problems:
        return problems, []
    layouts = docs["layouts"].get("layouts")
    layouts = layouts if isinstance(layouts, dict) else {}
    for layout, elements in DECK_LAYOUTS.items():
        found = layouts.get(layout)
        if not isinstance(found, dict):
            problems.append(
                f"{base}{layouts_name} has no {layout} layout (the NCA results deck uses it)"
            )
            continue
        have = found.get("elements") if isinstance(found.get("elements"), dict) else {}
        for element in elements:
            if element not in have:
                problems.append(f"{base}{layouts_name}: layout {layout} has no {element} element")
    styles = docs["design.base"].get("text_styles")
    styles = styles if isinstance(styles, dict) else {}
    for style in DECK_TEXT_STYLES:
        if style not in styles:
            problems.append(f"{base}{design_name} has no {style} text style")
    slide = docs["design.base"].get("slide")
    image = slide.get("background_image") if isinstance(slide, dict) else None
    if isinstance(image, str) and image and "://" not in image and base + image not in files:
        problems.append(f"{base}{design_name}: its background image is not in the template")
    notes = [
        "the launcher writes the deck's own presentation.yaml with the template's design "
        "and layouts"
    ]
    return problems, notes


DECK_TYPE = StandardsType(
    id=DECK_TEMPLATE,
    title="Deck template",
    format="1.0",
    suffixes=(".yaml", ".yml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".emf", ".md"),
    check=check_deck,
    native="deckifyr",
)

PROFILE_TYPE = StandardsType(
    id="profile",
    title="Study profile",
    format="1.0",
    suffixes=(".yaml",),
    check=check_profile,
    install_to=study_profile.PROFILES_DIR,
)

TYPES = (PROFILE_TYPE, REPORT_TYPE, DECK_TYPE)
