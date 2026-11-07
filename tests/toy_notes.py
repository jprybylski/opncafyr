"""The test-only ``toy_notes`` tool (#52): its descriptor and a naive project editor.

The editor deliberately lists every ``.md`` path it finds, links and outputs included, so
the tests show that core's own rules, not the adapter's care, keep the editor safe.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opncafyr.core.adapter import EditableFile, SettingsField, SettingsInvalid
from opncafyr.core.registry import load_descriptors

ROOT = Path(__file__).with_name("toy_adapters")
AUDIENCES = (("internal", "Internal"), ("external", "External"))


class ToyNotesEditor:
    profiles = frozenset({"notes-md-v1"})
    settings_schemas = frozenset({"notes-settings-v1"})

    def __init__(self, max_bytes: int = 4096, extra: tuple[str, ...] = ()) -> None:
        self.max_bytes = max_bytes
        self.extra = extra  # paths to offer whether or not they exist or are safe

    def title(self, file_profile: str) -> str:
        return "Notes"

    def files(self, project: Path, file_profile: str) -> list[EditableFile]:
        found = sorted(p.relative_to(project).as_posix() for p in project.rglob("*.md"))
        return [EditableFile(p, p, "md", self.max_bytes) for p in [*found, *self.extra]]

    def check_text(self, file_profile: str, path: str, text: str) -> list[str]:
        return ["The note still has a TODO."] if "TODO" in text else []

    def settings_file(self, settings_schema: str) -> str:
        return "notes-settings.json"

    def settings_form(self, settings_schema: str) -> list[SettingsField]:
        return [SettingsField("audience", "Audience", AUDIENCES, required=True)]

    def settings_values(self, settings_schema: str, document: dict[str, Any] | None) -> dict:
        if document is None:
            return {"audience": None}
        if document.get("version") != 1:
            raise SettingsInvalid("unknown version")
        return {"audience": document.get("audience")}

    def settings_document(self, settings_schema: str, values: Mapping[str, Any]) -> dict:
        if values.get("audience") not in {a for a, _ in AUDIENCES}:
            raise SettingsInvalid("audience must be internal or external")
        return {"version": 1, "audience": values["audience"]}


def toy_tools() -> dict:
    return load_descriptors(ROOT)
