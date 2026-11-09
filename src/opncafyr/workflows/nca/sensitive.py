"""Identifier values in a study folder, for the support bundle's redaction (#38).

The bundle never includes data, but free text in it (job messages, log lines) could quote
a subject ID. This hook finds every value of the columns mapped to ``SUBJECT_ID`` and
``SECONDARY_ID`` in each recipe's cleaned output, so the bundle can mask them even when
no study is open in the launcher (``opncafyr doctor --bundle --workspace …``). It reads
files only, and skips anything it can't read.
"""

from __future__ import annotations

import csv
from pathlib import Path

from opncafyr.workflows.nca import mapping as mappings
from opncafyr.workflows.nca import recipe as recipes
from opncafyr.workflows.nca.cleaning import CLEANED_DIR

ID_ROLES = ("SUBJECT_ID", "SECONDARY_ID")
# prepared.csv: the event stage's observations (#58). records.csv holds line numbers only.
CSV_FILES = ("cleaned.csv", "rejected.csv", "prepared.csv")


def _column_values(path: Path, columns: list[str]) -> set[str]:
    values: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                values.update(v for c in columns if (v := (row.get(c) or "").strip()))
    except (OSError, UnicodeDecodeError, csv.Error):
        pass
    return values


def sensitive_values(workspace: Path) -> set[str]:
    values: set[str] = set()
    folder = workspace / recipes.RECIPES_DIR
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            doc = recipes.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001, S112 - an unreadable recipe adds nothing
            continue
        columns = [c for r in ID_ROLES if (c := mappings.role(doc.get("mapping"), r))]
        if not columns or not doc.get("id"):
            continue
        for cleaned in (workspace / CLEANED_DIR).glob(f"{doc['id']}-*"):
            for name in CSV_FILES:
                values |= _column_values(cleaned / name, columns)
    return values
