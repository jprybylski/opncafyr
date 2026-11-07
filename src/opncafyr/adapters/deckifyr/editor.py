"""The deck's contribution to the launcher's project-file editor (#52).

deckifyr places a Quarto fragment on a slide with a ``type: quarto`` element whose
``source`` is a ``.qmd`` file in the deck project (deckifyr spec §8.1). deckifyr's own
editor can choose an existing fragment but can't edit its text. The launcher's editor
fills that gap:

- **Files.** Every ``.qmd`` file in ``deck/``, except build output (``build/``,
  ``builds/``, ``previews/``), the publication copy (``OUTPUTS/``), ``_extensions/`` and
  hidden folders. ``presentation.yaml`` is left to deckifyr's editor, which keeps the deck
  in memory while it runs.
- **New fragments** go in ``fragments/``. A new fragment then appears in deckifyr's
  editor, where a ``quarto`` element can use it.
- **Warnings.** deckifyr's strict check rejects a fragment that has a slide break
  (``---``, ``***`` or ``___``) or more than one top-level ``#`` heading, outside its front
  matter and code fences (``renderers/quarto.py``, ``check_fragment_complexity``). A save
  warns about the same things early; deckifyr's Check stays the authority.

Decks are trusted, user-authored projects (docs/plan.md, risk table): a fragment's code
runs only at build time, through the pinned binaries (``--trusted-binaries-only``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opncafyr.core.adapter import EditableFile, NewFileRule, SettingsField, SettingsInvalid

FILE_PROFILE = "deck-fragments-v1"
FRAGMENTS = "fragments"
MAX_FRAGMENT_BYTES = 128 * 1024
# Folders never offered: build output, the publication copy, Quarto extensions.
SKIP_DIRS = {"build", "builds", "previews", "outputs", "_extensions"}
STARTER = (
    "Write the text for one slide element here. Use at most one top-level heading and no\n"
    "slide breaks: a fragment fills one box on a slide.\n"
)

_FENCE = re.compile(r"^(`{3,}|~{3,})")
_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_H1 = re.compile(r"^#\s+\S")


def fragment_warnings(text: str) -> list[str]:
    """deckifyr's fragment limits, checked the same way (outside front matter and fences)."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    fence: str | None = None
    breaks = headings = 0
    for line in lines[start:]:
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence * 3):
                fence = None
            continue
        match = _FENCE.match(stripped)
        if match:
            fence = match.group(1)[0]
            continue
        if _RULE.match(stripped):
            breaks += 1
        elif _H1.match(stripped):
            headings += 1
    out = []
    if breaks:
        out.append(
            "deckifyr will reject this fragment: it has a slide break (---, *** or ___). "
            "A fragment fills one box on one slide."
        )
    if headings > 1:
        out.append(
            "deckifyr will reject this fragment: it has more than one top-level (#) heading."
        )
    return out


class DeckEditor:
    profiles = frozenset({FILE_PROFILE})
    settings_schemas: frozenset[str] = frozenset()

    def title(self, file_profile: str) -> str:
        return "Deck fragments"

    def files(self, project: Path, file_profile: str) -> list[EditableFile]:
        found = []
        for folder, dirs, names in os.walk(project, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS
            )
            for name in sorted(names):
                if name.startswith(".") or not name.lower().endswith(".qmd"):
                    continue
                rel = (Path(folder) / name).relative_to(project).as_posix()
                found.append(EditableFile(rel, rel, "qmd", MAX_FRAGMENT_BYTES))
        return found

    def check_text(self, file_profile: str, path: str, text: str) -> list[str]:
        return fragment_warnings(text)

    def new_file_rules(self, file_profile: str) -> list[NewFileRule]:
        return [
            NewFileRule(FRAGMENTS, ".qmd", "Quarto fragment", "qmd", STARTER, MAX_FRAGMENT_BYTES)
        ]

    # The deck has no editor settings; deckifyr's own editor owns presentation.yaml.
    def settings_file(self, settings_schema: str) -> str:
        raise SettingsInvalid("the deck has no editor settings")

    def settings_form(self, settings_schema: str) -> list[SettingsField]:
        return []

    def settings_values(self, settings_schema: str, document: dict[str, Any] | None) -> dict:
        raise SettingsInvalid("the deck has no editor settings")

    def settings_document(self, settings_schema: str, values: Mapping[str, Any]) -> dict:
        raise SettingsInvalid("the deck has no editor settings")
