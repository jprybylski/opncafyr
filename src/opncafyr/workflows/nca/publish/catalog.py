"""What a completed run can publish (#31, #59, ADR 0007 §3).

The catalog maps a run's saved artifacts (``manifest.artifacts``, paths relative to the run
folder) to publishable tables and figures. ADR 0007 pre-selects four types:
- the parameter summary (``results/opnca-summary.csv``);
- the individual parameter listing (``results/opnca-parameters-wide.csv``);
- the individual concentration–time profiles (``results/figures/profiles-linear|log``);
- the mean concentration–time profiles (``results/figures/mean-linear|log``).

**Described runs (#59).** opnca-r 0.3.1 describes every file it saves in ``artifacts.json``
schema 2, and the run manifest keeps those fields. The catalog of such a run comes from
them: opnca-r's title, ``identifies_subjects`` as ``contains_subject_ids``, and notes from the
caption, footnote and the record block ``settings`` points to. A table or figure opnca-r
adds later is offered without launcher changes. The ids stay the launcher's, because
published file names and pre-selection use them. Two rules are the launcher's own:
- a CSV table that ``analysis.R`` doesn't recompute (``replay.reproduced: false``, the
  bioequivalence tables) isn't offered: the replay never checked it (``missing()`` says so);
- a formatted flextable (``.rds``, ``derived_from`` a CSV) is offered next to its CSV, never
  pre-selected, and only when its CSV is.

**Older runs.** A run saved before opnca-r 0.3.1 has no ``identifies_subjects``, and keeps
the path rules of ``_path_entry``.

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

from opncafyr.workflows.nca.publish.sidecars import FIGURE_EXTENSIONS

FIGURES = "results/figures/"
_SUMMARY_BY = re.compile(r"^results/opnca-summary-by-([a-z0-9][a-z0-9_-]{0,63})\.csv$")
_FIGURE = re.compile(r"^results/figures/([a-z0-9][a-z0-9_-]{0,63})\.(png|svg)$")

MEAN_TABLE = "results/opnca-mean-profiles.csv"
MEAN_FIGURES = ("mean-linear", "mean-log")
_STATISTIC = {"arithmetic": "Arithmetic mean", "geometric": "Geometric mean"}
_ERROR = {"sd": " ± SD", "se": " ± SE", "none": ""}
_GEOMETRIC_ERROR = {"sd": " with geometric SD", "se": " with geometric SE", "none": ""}

# ADR 0007's pre-selected artifacts, by the launcher's id.
PRESELECTED = frozenset(
    {"summary", "listing", "profiles-linear", "profiles-log", "mean-linear", "mean-log"}
)
# opnca-r's ids for the tables the launcher named before #59; their published names stay.
_TABLE_IDS = {
    "opnca-summary": "summary",
    "opnca-parameters-wide": "listing",
    "opnca-mean-profiles": "mean-profiles",
}
FORMATTED = "-formatted"
_ARTIFACT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")  # publication-v1's artifact
TABLE_FORMATS = frozenset({"csv", "rds"})


@dataclass(frozen=True)
class Entry:
    artifact: str  # an id, unique within the run, used in the published file name
    title: str
    kind: str  # "table" | "figure"
    path: str  # relative to the run folder
    sha256: str
    contains_subject_ids: bool
    preselected: bool
    notes: tuple[str, ...] = ()  # how the file was made, for its sidecar's footnotes

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


def _path_entry(art: dict[str, Any], note: str | None) -> Entry | None:
    """A run saved before opnca-r 0.3.1: the entry from the file's path alone."""
    path, sha = art["path"], art["sha256"]
    notes = (note,) if note else ()
    if path == "results/opnca-summary.csv":
        return Entry("summary", "Parameter summary", "table", path, sha, False, True)
    if path == "results/opnca-parameters-wide.csv":
        return Entry("listing", "Individual parameter listing", "table", path, sha, True, True)
    if path == MEAN_TABLE:
        title = "Mean concentration–time table"
        return Entry("mean-profiles", title, "table", path, sha, False, False, notes)
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
            return Entry(name, title, "figure", path, sha, False, True, notes)
        if name.startswith("boxplot-"):
            title = f"Boxplot of {name.removeprefix('boxplot-')}"
            return Entry(name, title, "figure", path, sha, False, False)
        # A figure this version doesn't know: offered, never pre-selected, and assumed to
        # show subjects.
        return Entry(name, name, "figure", path, sha, True, False)
    return None


def launcher_id(opnca_id: str) -> str:
    """The launcher's artifact id for one of opnca-r's ids."""
    if opnca_id in _TABLE_IDS:
        return _TABLE_IDS[opnca_id]
    if opnca_id.startswith("opnca-summary-by-"):
        return opnca_id.removeprefix("opnca-")
    return opnca_id.removeprefix("figure-")


def _not_reproduced(art: dict[str, Any]) -> bool:
    replay = art.get("replay")
    return isinstance(replay, dict) and replay.get("reproduced") is False


def _offered_kind(art: dict[str, Any]) -> bool:
    kind, fmt = art.get("kind"), str(art.get("format", "")).lower()
    if kind == "table":
        return fmt in TABLE_FORMATS
    return kind == "figure" and fmt in FIGURE_EXTENSIONS


# Words for the analysis-record blocks a ``settings`` pointer can name.
_SETTINGS_WORDS = {"/mean_profiles": mean_note}


def _described(manifest: dict[str, Any]) -> list[Entry]:
    """The entries of a run whose artifacts carry opnca-r's schema-2 description (#59)."""
    arts = [a for a in manifest.get("artifacts", []) if _offered_kind(a) and a.get("id")]
    csv = {a["id"]: a for a in arts if a["kind"] == "table" and a.get("format") == "csv"}
    shown: list[tuple[str, dict[str, Any]]] = []
    for art in arts:
        if art["kind"] == "table":
            source = csv.get(art.get("derived_from", "")) if art.get("format") != "csv" else art
            if source is None or _not_reproduced(source):
                continue
            if source is not art:
                shown.append((launcher_id(source["id"]) + FORMATTED, art))
                continue
        shown.append((launcher_id(art["id"]), art))
    taken: dict[str, int] = {}
    for name, _ in shown:
        taken[name] = taken.get(name, 0) + 1
    entries = []
    for name, art in shown:
        if taken[name] > 1:  # the same figure saved in two formats
            name = f"{name}-{str(art.get('format', '')).lower()}"
        if not _ARTIFACT_ID.match(name):
            continue
        words = _SETTINGS_WORDS.get(art.get("settings") or "")
        notes = [art.get("caption"), art.get("footnote"), words(manifest) if words else None]
        entries.append(
            Entry(
                artifact=name,
                title=str(art.get("title") or name),
                kind=art["kind"],
                path=art["path"],
                sha256=art["sha256"],
                # Missing only if the listing were incomplete: the cautious reading.
                contains_subject_ids=art.get("identifies_subjects") is not False,
                preselected=name in PRESELECTED,
                notes=tuple(n for n in notes if isinstance(n, str) and n.strip()),
            )
        )
    return entries


def described(manifest: dict[str, Any]) -> bool:
    """Whether the run's artifacts carry opnca-r's own description (opnca-r 0.3.1+)."""
    return any("identifies_subjects" in a for a in manifest.get("artifacts", []))


def catalog(manifest: dict[str, Any]) -> list[Entry]:
    """The publishable artifacts of a run, pre-selected ones first, in a stable order."""
    if described(manifest):
        entries = _described(manifest)
    else:
        note = mean_note(manifest)
        entries = [e for art in manifest.get("artifacts", []) if (e := _path_entry(art, note))]
    return sorted(entries, key=lambda e: (not e.preselected, e.kind, e.artifact))


def missing(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """ADR 0007 defaults this run can't offer, and tables the replay didn't check, with
    the reason."""
    out = []
    saved = {a["path"] for a in manifest.get("artifacts", [])}
    if not any(p.startswith(f"{FIGURES}{n}.") for n in MEAN_FIGURES for p in saved):
        out.append(
            {
                "artifact": "mean-conc-time",
                "title": "Mean concentration–time (linear and log)",
                "reason": (
                    "This run was saved without the mean figures. Make a new run and keep "
                    "them ticked in opnca-r's Save box (opnca-r 0.2.9 or later)."
                ),
            }
        )
    if described(manifest):
        for art in manifest.get("artifacts", []):
            if art.get("kind") == "table" and art.get("format") == "csv" and _not_reproduced(art):
                why = str(art["replay"].get("reason") or "no reason given")
                out.append(
                    {
                        "artifact": launcher_id(str(art.get("id", ""))),
                        "title": str(art.get("title") or art["path"]),
                        "reason": (
                            "analysis.R doesn't recompute this table, so the replay didn't "
                            f"check it and it can't be published (opnca-r: {why})."
                        ),
                    }
                )
    return out
