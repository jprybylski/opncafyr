"""The report's contribution to the launcher's project-file editor (#52).

Offered files: ``report.qmd`` first, then ``report/config.yaml`` and
``report/standard_footnotes.yaml`` (the two bounded exceptions, #64), then any other
``.qmd``/``.rmd``/``.md`` file in the top of the report project. Nothing else in a subfolder
is offered, so ``builds/``, ``OUTPUTS/``, ``_extensions/`` and quartifyr's own outputs
(``report/draft`` and the rest) stay out of reach, as does ``_quarto.yml``. A save of either
YAML file is checked in plain words (``config_yaml.py``); the report's Check repeats the check
and Render refuses what would break it.

Report projects are trusted, user-authored projects (docs/plan.md, risk table), so the
source mode may change any part of these files, chunks and shortcodes included. Renders
still run only the pinned binaries, on a launcher staging copy.

Settings (``report-settings-v1``, bundled here) live in the project as
``opncafyr-settings.json``: the document status and the reference DOCX. The status has
no default; a render asks until it is chosen. Nothing edits the QMD front matter.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from opncafyr.adapters.quartifyr import CONFIG, FOOTNOTES, config_yaml
from opncafyr.core.adapter import EditableFile, SettingsField, SettingsInvalid

FILE_PROFILE = "report-source-v1"
SETTINGS_SCHEMA = "report-settings-v1"
SETTINGS_FILE = "opncafyr-settings.json"
REPORT_QMD = "report.qmd"
SOURCE_SUFFIXES = {".qmd": "qmd", ".rmd": "rmd", ".md": "md"}
MAX_SOURCE_BYTES = 256 * 1024
MAX_YAML_BYTES = 128 * 1024
YAML_FILES = (
    (
        CONFIG,
        "Report settings (config.yaml)",
        "How tables, figures and footnotes are filled in: figure width, footnote style and "
        "table placement. Most reports need no change. Each line is “setting: value”, a "
        "value being true or false, a number or text, as in the bundled file. Check and "
        "Render read this file, and a build records which version it used.",
    ),
    (
        FOOTNOTES,
        "Footnotes (standard_footnotes.yaml)",
        "The abbreviations and footnote wording that a published file's description may "
        "name. Changing it changes which footnotes Check and Render accept. Each entry is a "
        "name followed by its indented text.",
    ),
)
REFERENCE_DOCS = (("bundled", "Installed with quartifyr"),)


@cache
def settings_schema() -> dict[str, Any]:
    text = (
        resources.files(__package__)
        .joinpath("schemas", f"{SETTINGS_SCHEMA}.schema.json")
        .read_text("utf-8")
    )
    schema = json.loads(text)
    Draft202012Validator.check_schema(schema)
    return schema


def _check(document: Any) -> None:
    error = best_match(Draft202012Validator(settings_schema()).iter_errors(document))
    if error is not None:
        where = ".".join(str(p) for p in error.absolute_path) or "settings"
        raise SettingsInvalid(f"{where}: {error.message}")


def read_settings(project: Path) -> tuple[dict[str, Any], str | None]:
    """The saved settings (or the defaults) and the file's SHA-256 (None if not saved).

    Used by the report workflow when it renders. An invalid file raises ``SettingsInvalid``.
    """
    import hashlib

    path = project / SETTINGS_FILE
    if path.is_symlink() or not path.is_file():
        return {"schema_version": 1, "reference_doc": "bundled"}, None
    data = path.read_bytes()
    try:
        document = json.loads(data.decode("utf-8"))
    except ValueError:
        raise SettingsInvalid("the settings file is not JSON") from None
    _check(document)
    return document, hashlib.sha256(data).hexdigest()


class ReportEditor:
    profiles = frozenset({FILE_PROFILE})
    settings_schemas = frozenset({SETTINGS_SCHEMA})

    def title(self, file_profile: str) -> str:
        return "Report source"

    def files(self, project: Path, file_profile: str) -> list[EditableFile]:
        found = []
        for path in project.iterdir():
            syntax = SOURCE_SUFFIXES.get(path.suffix.lower())
            if syntax is None or path.name.startswith(".") or path.is_symlink():
                continue
            if not path.is_file():
                continue
            label = "Report" if path.name == REPORT_QMD else path.name
            found.append(EditableFile(path.name, label, syntax, MAX_SOURCE_BYTES))
        found.sort(key=lambda f: (f.path != REPORT_QMD, f.path.lower()))
        extra = []
        for rel, label, help_ in YAML_FILES:
            path = project / rel
            if path.is_file() and not path.is_symlink() and not path.parent.is_symlink():
                extra.append(EditableFile(rel, label, "yaml", MAX_YAML_BYTES, help_))
        head = [f for f in found if f.path == REPORT_QMD]
        return head + extra + [f for f in found if f.path != REPORT_QMD]

    def check_text(self, file_profile: str, path: str, text: str) -> list[str]:
        # For a source file quartifyr's check (Check on the report screen) is the authority.
        if path == CONFIG:
            problems = config_yaml.check_config(text)
        elif path == FOOTNOTES:
            problems = config_yaml.check_footnotes(text)
        else:
            return []
        return [p.message for p in problems]

    def settings_file(self, settings_schema: str) -> str:
        return SETTINGS_FILE

    def settings_form(self, settings_schema: str) -> list[SettingsField]:
        return [
            SettingsField(
                "status",
                "Document status",
                (("draft", "Draft"), ("final", "Final")),
                help="quartifyr marks a draft report as draft. A render asks until you choose.",
                required=True,
            ),
            SettingsField(
                "reference_doc",
                "Word styles",
                REFERENCE_DOCS,
                help="The reference DOCX that sets the report's styles.",
                required=True,
            ),
        ]

    def settings_values(self, settings_schema: str, document: dict[str, Any] | None) -> dict:
        if document is None:
            return {"status": None, "reference_doc": "bundled"}
        _check(document)
        return {"status": document.get("status"), "reference_doc": document["reference_doc"]}

    def settings_document(self, settings_schema: str, values: Mapping[str, Any]) -> dict:
        unknown = sorted(set(values) - {"status", "reference_doc"})
        if unknown:
            raise SettingsInvalid(f"unknown setting {unknown[0]!r}")
        document: dict[str, Any] = {
            "schema_version": 1,
            "reference_doc": values.get("reference_doc") or "bundled",
        }
        if values.get("status") is not None:
            document["status"] = values["status"]
        _check(document)
        return document
