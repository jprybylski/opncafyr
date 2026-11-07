"""What a completed run can publish (#31, ADR 0007 §3).

The catalog maps a run's saved artifacts (``manifest.artifacts``, paths relative to the run
folder) to publishable tables and figures. ADR 0007 pre-selects four types:
- the parameter summary (``results/opnca-summary.csv``);
- the individual parameter listing (``results/opnca-parameters-wide.csv``);
- the individual concentration–time profiles (``results/figures/profiles-linear|log``);
- the mean concentration–time profiles (``results/figures/mean-linear|log``).

opnca-r saves the mean figures and their table (``results/opnca-mean-profiles.csv``) since
0.2.9 (mantra-opnca/opnca-r#50), with the choices behind them in the analysis record's
``mean_profiles`` block. A run saved without them (the user left them out in opnca-r, or an
older opnca-r saved it) says so through ``missing()``. The launcher never draws them: opnca-r
is the calculation authority.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

FIGURES = "results/figures/"
_SUMMARY_BY = re.compile(r"^results/opnca-summary-by-([a-z0-9][a-z0-9_-]{0,63})\.csv$")
_FIGURE = re.compile(r"^results/figures/([a-z0-9][a-z0-9_-]{0,63})\.(png|svg)$")

MEAN_TABLE = "results/opnca-mean-profiles.csv"
MEAN_FIGURES = ("mean-linear", "mean-log")
_STATISTIC = {"arithmetic": "Arithmetic mean", "geometric": "Geometric mean"}
_ERROR = {"sd": " ± SD", "se": " ± SE", "none": ""}
_GEOMETRIC_ERROR = {"sd": " with geometric SD", "se": " with geometric SE", "none": ""}


@dataclass(frozen=True)
class Entry:
    artifact: str  # an id, unique within the run, used in the published file name
    title: str
    kind: str  # "table" | "figure"
    path: str  # relative to the run folder
    sha256: str
    contains_subject_ids: bool
    preselected: bool
    note: str | None = None  # how the file was made, for its sidecar's footnote

    @property
    def extension(self) -> str:
        return PurePosixPath(self.path).suffix.lstrip(".")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def mean_note(manifest: dict[str, Any]) -> str | None:
    """The mean profiles' choices in words, from the analysis record's ``mean_profiles``
    block (kept in ``nca_settings``), or None when the run has none."""
    mean = (manifest.get("nca_settings") or {}).get("mean_profiles")
    if not isinstance(mean, dict):
        return None
    statistic = str(mean.get("statistic", ""))
    error = str(mean.get("error_measure", ""))
    errors = _GEOMETRIC_ERROR if statistic == "geometric" else _ERROR
    what = _STATISTIC.get(statistic, f"{statistic or 'Unstated'} mean") + errors.get(error, "")
    basis = str(mean.get("time_basis", "unstated"))
    column = mean.get("time_column")
    when = f"{basis} time" + (f" ({column})" if column else "")
    group = mean.get("grouping")
    by = f", by {group}" if group and group != "None" else ""
    return (
        f"{what} at each {when}{by}, of the observed concentrations after the run's "
        "BLQ rules and exclusions."
    )


def _entry(art: dict[str, Any], note: str | None) -> Entry | None:
    path, sha = art["path"], art["sha256"]
    if path == "results/opnca-summary.csv":
        return Entry("summary", "Parameter summary", "table", path, sha, False, True)
    if path == "results/opnca-parameters-wide.csv":
        return Entry("listing", "Individual parameter listing", "table", path, sha, True, True)
    if path == MEAN_TABLE:
        title = "Mean concentration–time table"
        return Entry("mean-profiles", title, "table", path, sha, False, False, note)
    if m := _SUMMARY_BY.match(path):
        title = f"Parameter summary by {m.group(1)}"
        return Entry(f"summary-by-{m.group(1)}", title, "table", path, sha, False, False)
    if m := _FIGURE.match(path):
        name = m.group(1)
        if name in ("profiles-linear", "profiles-log"):
            scale = "linear" if name.endswith("linear") else "log"
            title = f"Individual concentration–time profiles ({scale})"
            return Entry(name, title, "figure", path, sha, True, True)
        if name in MEAN_FIGURES:
            scale = "linear" if name.endswith("linear") else "log"
            title = f"Mean concentration–time profiles ({scale})"
            return Entry(name, title, "figure", path, sha, False, True, note)
        if name.startswith("boxplot-"):
            title = f"Boxplot of {name.removeprefix('boxplot-')}"
            return Entry(name, title, "figure", path, sha, False, False)
        # A figure this version doesn't know: offered, never pre-selected, and assumed to
        # show subjects.
        return Entry(name, name, "figure", path, sha, True, False)
    return None


def catalog(manifest: dict[str, Any]) -> list[Entry]:
    """The publishable artifacts of a run, pre-selected ones first, in a stable order."""
    note = mean_note(manifest)
    entries = [e for art in manifest.get("artifacts", []) if (e := _entry(art, note))]
    return sorted(entries, key=lambda e: (not e.preselected, e.kind, e.artifact))


def missing(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """ADR 0007 defaults this run can't offer, with the reason."""
    saved = {a["path"] for a in manifest.get("artifacts", [])}
    if any(p.startswith(f"{FIGURES}{n}.") for n in MEAN_FIGURES for p in saved):
        return []
    return [
        {
            "artifact": "mean-conc-time",
            "title": "Mean concentration–time (linear and log)",
            "reason": (
                "This run was saved without the mean figures. Make a new run and keep them "
                "ticked in opnca-r's Save box (opnca-r 0.2.9 or later)."
            ),
        }
    ]
