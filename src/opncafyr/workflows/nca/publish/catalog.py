"""What a completed run can publish (#31, ADR 0007 §3).

The catalog maps a run's saved artifacts (``manifest.artifacts``, paths relative to the run
folder) to publishable tables and figures. ADR 0007 pre-selects four types. Three of them
exist in an opnca-r 0.2.8 save:
- the parameter summary (``results/opnca-summary.csv``);
- the individual parameter listing (``results/opnca-parameters-wide.csv``);
- the individual concentration–time profiles (``results/figures/profiles-linear|log``).

The fourth, a mean concentration–time figure, isn't saved by opnca-r
(mantra-opnca/opnca-r#50), so it is listed as unavailable. The launcher never draws it:
opnca-r is the calculation authority.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

FIGURES = "results/figures/"
_SUMMARY_BY = re.compile(r"^results/opnca-summary-by-([a-z0-9][a-z0-9_-]{0,63})\.csv$")
_FIGURE = re.compile(r"^results/figures/([a-z0-9][a-z0-9_-]{0,63})\.(png|svg)$")

UNAVAILABLE = [
    {
        "artifact": "mean-conc-time",
        "title": "Mean concentration–time (linear and log)",
        "reason": "Not saved by this opnca-r version (mantra-opnca/opnca-r#50).",
    }
]


@dataclass(frozen=True)
class Entry:
    artifact: str  # an id, unique within the run, used in the published file name
    title: str
    kind: str  # "table" | "figure"
    path: str  # relative to the run folder
    sha256: str
    contains_subject_ids: bool
    preselected: bool

    @property
    def extension(self) -> str:
        return PurePosixPath(self.path).suffix.lstrip(".")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry(art: dict[str, Any]) -> Entry | None:
    path, sha = art["path"], art["sha256"]
    if path == "results/opnca-summary.csv":
        return Entry("summary", "Parameter summary", "table", path, sha, False, True)
    if path == "results/opnca-parameters-wide.csv":
        return Entry("listing", "Individual parameter listing", "table", path, sha, True, True)
    if m := _SUMMARY_BY.match(path):
        title = f"Parameter summary by {m.group(1)}"
        return Entry(f"summary-by-{m.group(1)}", title, "table", path, sha, False, False)
    if m := _FIGURE.match(path):
        name = m.group(1)
        if name in ("profiles-linear", "profiles-log"):
            scale = "linear" if name.endswith("linear") else "log"
            title = f"Individual concentration–time profiles ({scale})"
            return Entry(name, title, "figure", path, sha, True, True)
        if name.startswith("boxplot-"):
            title = f"Boxplot of {name.removeprefix('boxplot-')}"
            return Entry(name, title, "figure", path, sha, False, False)
        # A figure this version doesn't know: offered, never pre-selected, and assumed to
        # show subjects.
        return Entry(name, name, "figure", path, sha, True, False)
    return None


def catalog(manifest: dict[str, Any]) -> list[Entry]:
    """The publishable artifacts of a run, pre-selected ones first, in a stable order."""
    entries = [e for art in manifest.get("artifacts", []) if (e := _entry(art))]
    return sorted(entries, key=lambda e: (not e.preselected, e.kind, e.artifact))
