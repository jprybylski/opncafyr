"""Plain-language checks for the report's two YAML files (#64).

``report/config.yaml`` holds the settings reportifyr and quartifyr read (figure width, footnote
style, table placement). ``report/standard_footnotes.yaml`` holds the abbreviations and footnote
wording. The project-file editor offers both as bounded exceptions to "report sources only"
(``editor.py``); this module says what is wrong with a text in words a person can act on.

- **Blocking** (an *error*): the file is not valid YAML, is not a list of ``setting: value``
  lines, or changes ``report_dir_name`` / ``outputs_dir_name``, which the launcher needs as
  the template has them (it copies the publication's files into that folder).
- **Warning**: a setting the bundled template doesn't have (usually a typo), or one whose
  kind differs from the template's (``use_artifact_size: "false"`` is text, not ``false``).

The bundled template is the reference for the setting names and kinds. Nothing here writes a
file or runs a tool, and the text of a value is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

import yaml

from opncafyr.adapters.quartifyr import template

# The two settings that name folders. The launcher builds the project around them.
PATH_SETTINGS = {"report_dir_name": "report", "outputs_dir_name": "OUTPUTS"}


@dataclass(frozen=True)
class Problem:
    severity: str  # "error" | "warning"
    message: str


@cache
def _reference() -> dict[str, Any]:
    doc = yaml.safe_load(template().joinpath("config.yaml").read_text("utf-8"))
    return doc if isinstance(doc, dict) else {}


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if value is None:
        return "nothing"
    return "something else"


def parse(text: str, what: str) -> tuple[Any, Problem | None]:
    """The YAML document of ``text``, or a problem that names the line."""
    try:
        return yaml.safe_load(text), None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        reason = str(getattr(exc, "problem", "") or "it could not be read")
        return None, Problem("error", f"{what} is not valid YAML{where}: {reason}.")


def check_config(text: str) -> list[Problem]:
    """Problems in a ``config.yaml`` text, errors first."""
    doc, bad = parse(text, "The settings file")
    if bad is not None:
        return [bad]
    if doc is None:
        return []  # an empty file means every setting keeps its default
    if not isinstance(doc, dict):
        return [Problem("error", "The settings file should be lines of “setting: value”.")]
    reference = _reference()
    errors: list[Problem] = []
    warnings: list[Problem] = []
    for key, value in doc.items():
        name = str(key)
        if name in PATH_SETTINGS:
            if value != PATH_SETTINGS[name]:
                errors.append(
                    Problem(
                        "error",
                        f"{name} must stay “{PATH_SETTINGS[name]}”: the app builds the report "
                        "project around it.",
                    )
                )
        elif key not in reference:
            warnings.append(
                Problem(
                    "warning",
                    f"“{name}” is not one of the settings in the bundled template. Check the "
                    "spelling; a setting the tools don't know is ignored.",
                )
            )
        elif _kind(value) != _kind(reference[key]):
            warnings.append(
                Problem(
                    "warning",
                    f"{name} should be {_kind(reference[key])}, not {_kind(value)}.",
                )
            )
    return errors + warnings


def check_footnotes(text: str) -> list[Problem]:
    """Problems in a ``standard_footnotes.yaml`` text (its structure is checked by Check)."""
    doc, bad = parse(text, "The footnotes file")
    if bad is not None:
        return [bad]
    if doc is not None and not isinstance(doc, dict):
        return [Problem("error", "The footnotes file should be a set of named entries.")]
    return []
