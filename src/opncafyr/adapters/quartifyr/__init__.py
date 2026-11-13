"""quartifyr adapter: report project setup, preflight and render commands (#30, #32).

quartifyr (with reportifyr) renders a DOCX in two passes. Quarto writes the shell, then
reportifyr fills in the ``{rpfy}:`` tables and figures from ``OUTPUTS/`` and their
sidecars. This module only builds commands and reads results; the report workflow
(``workflows/nca/publish/report.py``) runs them as jobs.

The call contract is quartifyr 0.3.3's (issue #32 comments, docs/offline.md):
- **Setup.** A fixed R program (``r/quartifyr_setup.R``) calls
  ``initialize_report_project_offline()`` and the bundled-extension installers. pyro is
  never used and nothing reaches the network.
- **Preflight.** ``scripts/check.R --project --shell --json``; ``check-result-v1`` on stdout.
- **Render.** ``scripts/render.R ... --json --offline --quarto-bin <pinned>``.
  - The result is ``render-result-v1``; extra keys are tolerated.
  - Success means exit 0 *and* ``status == "ok"``, or ``status == "warning"`` where every
    warning is quartifyr 0.3.4's "a table is still too wide at the smallest font" (and there
    is no error, unfilled placeholder or missing metadata). ``--strict`` is not passed: it
    would refuse that one warning, and real opnca-r tables (a summary of 22 columns) can't
    fit a portrait page at 7 pt, so every default report would fail. ``parse_render`` is the
    gate instead, and the warning is recorded in the build (#62). The DOCX is only taken
    from a render copy when the gate passes, so nothing incomplete looks complete.
  - Empty or non-JSON stdout is a failure with no result.
  - ``--recalculate-fields`` and ``--resolve-same-page-crossrefs`` are never passed
    (docs/spikes/libreoffice.md).
- **Environment.**
  - The tools Python env is ``QUARTIFYR_PYTHON_ENV``.
  - uv runs offline and never adopts a project or config (``UV_*``).
  - Quarto's Deno cache and uv's cache go to the app root.
  - A throwaway ``HOME`` holds the git identity reportifyr asks for; the user's git
    config is never read or changed.
  - The pinned uv comes first on ``PATH``.

Executables come from the installed runtime only, never from workspace content.
``template/`` holds the bundled report sources (ADR 0007); see its README.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from opncafyr.core.jobmanager import CommandSpec
from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import Runtime

SETUP_PROGRAM = "quartifyr_setup.R"
REPORT_QMD = "report.qmd"
QUARTO_YML = "_quarto.yml"
REFERENCE_DOCX = "org-reference.docx"
FOOTNOTES = "report/standard_footnotes.yaml"
CONFIG = "report/config.yaml"
STYLE = "style.yaml"  # optional, from an imported report template (#70)
INIT_MARKER = ".report_init.json"
EXTENSIONS = "_extensions"
OUTPUTS = "OUTPUTS"
STATUSES = ("draft", "final")
# The source files of a report project the launcher writes and hashes. ``_extensions``
# is hashed as a tree; quartifyr's own output folders are not inputs.
SOURCES = (REPORT_QMD, QUARTO_YML, REFERENCE_DOCX, FOOTNOTES, CONFIG, INIT_MARKER, STYLE)
GIT_IDENTITY = "[user]\n\tname = opncafyr\n\temail = opncafyr@localhost.invalid\n"


def template() -> Traversable:
    """The bundled report template folder (owner-maintained, from quartifyr's demo-report)."""
    return resources.files(__name__).joinpath("template")


def standard_footnotes() -> Traversable:
    """The bundled ``standard_footnotes.yaml``: abbreviations and footnote types."""
    return template().joinpath("standard_footnotes.yaml")


def setup_program() -> str:
    return resources.files("opncafyr.r").joinpath(SETUP_PROGRAM).read_text("utf-8")


# --- environment ---------------------------------------------------------------------------


def tools_env(runtime: Runtime) -> Path:
    """The tools Python environment (a venv) that quartifyr and reportifyr use.

    The runtime names its interpreter ``deckifyr_python``, after its first user; one tools
    env serves deckifyr, quartifyr and reportifyr (docs/dependency-graph.md).
    """
    return runtime.executable("deckifyr_python").parent.parent


def job_env(runtime: Runtime, home: Path, cache: Path) -> dict[str, str]:
    """Variables for every quartifyr process: offline uv, app-owned caches, a git identity.

    ``home`` is a throwaway folder (the job's tmp); ``cache`` is app-owned.
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text(GIT_IDENTITY, "utf-8", newline="\n")
    return {
        "QUARTIFYR_PYTHON_ENV": str(tools_env(runtime)),
        "UV_OFFLINE": "1",
        "UV_NO_INDEX": "1",
        "UV_NO_SYNC": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_PROJECT": "1",
        "UV_NO_CONFIG": "1",
        "UV_CACHE_DIR": str(cache / "uv"),
        "DENO_DIR": str(cache / "deno"),
        "HOME": str(home),
    }


def _command(
    runtime: Runtime, script: Path, args: Iterable[str], home: Path, cache: Path
) -> CommandSpec:
    spec = rscript_command(runtime, script, args=list(args), env=job_env(runtime, home, cache))
    # The pinned uv first, so pyro or reportifyr never find another one; then Rscript and
    # Quarto (quartifyr's ``quarto`` R package looks it up on PATH too).
    dirs = [runtime.executable("uv").parent, *spec.path_dirs, runtime.executable("quarto").parent]
    return CommandSpec(spec.argv, spec.env, list(dict.fromkeys(dirs)))


def package_dir(runtime: Runtime) -> Path:
    return runtime.require_r_library() / "quartifyr"


def setup_command(
    runtime: Runtime, program: Path, project: Path, home: Path, cache: Path
) -> CommandSpec:
    """Lay out a report project in ``project`` (an empty folder) offline."""
    spec = _command(runtime, program, [], home, cache)
    spec.env["QUARTIFYR_SETUP_DIR"] = str(project)
    return spec


def check_command(runtime: Runtime, project: Path, home: Path, cache: Path) -> CommandSpec:
    script = package_dir(runtime) / "scripts" / "check.R"
    args = ["--project", str(project), "--shell", str(project / REPORT_QMD), "--json"]
    return _command(runtime, script, args, home, cache)


def render_command(
    runtime: Runtime,
    project: Path,
    status: str,
    quarto_timeout_s: int,
    home: Path,
    cache: Path,
) -> CommandSpec:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    script = package_dir(runtime) / "scripts" / "render.R"
    args = [
        "--project",
        str(project),
        "--shell",
        str(project / REPORT_QMD),
        "--status",
        status,
        "--json",
        "--offline",
        "--quarto-bin",
        str(runtime.executable("quarto")),
        "--reference-doc",
        str(project / REFERENCE_DOCX),
        "--quarto-timeout",
        str(int(quarto_timeout_s)),
    ]
    if (project / STYLE).is_file():  # an imported template's style (#70, ADR 0011)
        # quartifyr deep-merges --override over --style, and needs the merged style to be
        # complete; a team's style.yaml is usually a partial org look. So the base is
        # quartifyr's own default style and the project's file is the override.
        default = package_dir(runtime) / "python" / "styles" / "default.yaml"
        if default.is_file():
            args += ["--style", str(default), "--override", str(project / STYLE)]
        else:
            args += ["--style", str(project / STYLE)]
    return _command(runtime, script, args, home, cache)


def reference_docx(runtime: Runtime) -> Path:
    """quartifyr's bundled reference DOCX (ADR 0007 §1)."""
    return package_dir(runtime) / "templates" / REFERENCE_DOCX


# --- the report source ---------------------------------------------------------------------


@dataclass(frozen=True)
class ReportItem:
    """One published table or figure, as the report shows it."""

    file_name: str  # under OUTPUTS/<tables|figures>/
    kind: str  # "table" | "figure"
    title: str
    analyte: str
    run_id: str


_SAFE = re.compile(r"[^\w .,:;()/%+\-\u2013]", re.UNICODE)
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~^$@:])")


def safe_text(text: str, limit: int = 120) -> str:
    """Workspace text reduced to plain words for the report source.

    Analyte names come from the data. In a ``.qmd`` they could otherwise open a code chunk
    or a shortcode, which Quarto would run. Only letters, digits, spaces and a little
    punctuation are kept; the rest becomes a space.
    """
    cleaned = re.sub(r"\s+", " ", _SAFE.sub(" ", text)).strip()
    return cleaned[:limit].strip() or "unnamed"


def _md(text: str) -> str:
    """``safe_text`` escaped for Markdown body text."""
    return _MD_SPECIAL.sub(r"\\\1", safe_text(text))


def report_source(
    items: list[ReportItem], publication_id: str, label: str | None, runs: list[dict]
) -> str:
    """``report.qmd`` for a publication: one captioned table or figure per item.

    Every ``{rpfy}:`` placeholder is followed by a paragraph: reportifyr fails on a
    placeholder that ends its section (quartifyr's ``placeholder-last-in-section``).
    """
    subtitle = safe_text(label) if label else f"Publication {publication_id}"
    lines = [
        "---",
        'title: "Noncompartmental analysis results"',
        f'subtitle: "{subtitle}"',
        'document-status: "draft"',
        f'date: "{date.today().isoformat()}"',
        "format: docx",
        "filters:",
        "  - quarto-plus",
        "  - quartifyr",
        "---",
        "",
        '::: {custom-style="Heading 1"}',
        "Table of Contents",
        ":::",
        "",
        "::: .toc",
        ":::",
        "",
        "{{< pagebreak >}}",
        "",
        "{{< body-start >}}",
        "",
        "# Introduction",
        "",
        f"This report was assembled by opncafyr from publication {publication_id}. It shows "
        "the tables and figures saved by opnca-r for the runs below, each run holding one "
        "analyte. Research use only: it has not been validated for clinical or regulatory "
        "decisions.",
        "",
        "| Run | Analyte |",
        "|---|---|",
        *(f"| {r['run_id']} | {_md(r['analyte'])} |" for r in runs),
        "",
        "# Results",
        "",
    ]
    counts = {"table": 0, "figure": 0}
    by_analyte: dict[str, list[ReportItem]] = {}
    for item in items:
        by_analyte.setdefault(item.analyte, []).append(item)
    for analyte, group in by_analyte.items():
        lines += [f"## {_md(analyte)}", ""]
        for item in group:
            counts[item.kind] += 1
            n = counts[item.kind]
            ref, shortcode = (
                ("Tbl", "tbl_caption") if item.kind == "table" else ("Fig", "fig_caption")
            )
            caption = safe_text(f"{item.title}, {analyte}")
            lines += [
                f'{{{{< {shortcode} "{ref}{n}" "{caption}" >}}}}',
                "",
                f"{{rpfy}}:{item.file_name}",
                "",
                f"From run {item.run_id}.",
                "",
            ]
    return "\n".join(lines)


QUARTO_PROJECT = "project:\n  type: default\n  output-dir: report/shell\n"


# --- results -------------------------------------------------------------------------------


@dataclass
class RenderResult:
    ok: bool
    message: str
    document: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


def _json(text: str) -> dict[str, Any] | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


ABSOLUTE_PATH = re.compile(r"(?<![\w.~])(?:[A-Za-z]:[\\/]|/)[^\s'\"]*")


def _plain(messages: Iterable[Any], project: Path | None) -> str:
    """Tool messages without absolute paths (they name the user's folders)."""
    out = []
    for m in messages:
        text = str(m)
        if project is not None:
            text = text.replace(str(project), "<report>")
        out.append(ABSOLUTE_PATH.sub("<path>", text))
    return " ".join(out)[:2000]


# quartifyr 0.3.4's warning for a table that does not fit its page at the smallest font. It
# does not mean the report is incomplete, so it alone does not fail a render.
TABLE_TOO_WIDE = "still too wide at the minimum font size"


def only_wide_tables(doc: Mapping[str, Any]) -> bool:
    """A ``warning`` result whose warnings are all the wide-table one, with nothing else wrong."""
    warnings = [str(w) for w in doc.get("warnings") or []]
    return bool(
        doc.get("status") == "warning"
        and warnings
        and all(TABLE_TOO_WIDE in w for w in warnings)
        and not doc.get("errors")
        and not doc.get("unfilled_placeholders")
        and not doc.get("missing_metadata")
    )


ERROR_CLASSES = {
    "quartifyr_quarto_timeout": "Quarto took longer than its time limit.",
    "quartifyr_render_incomplete": "The render left placeholders unfilled or metadata missing.",
    "quartifyr_pass2_failed": "reportifyr could not fill in the tables and figures.",
    "quartifyr_offline_failure": "A step tried to reach the network.",
    "quartifyr_python_env_invalid": "The tools Python environment is not usable.",
}


def parse_render(
    code: int, stdout: str, schema: Mapping[str, Any] | None, project: Path | None
) -> RenderResult:
    """Decide a render from its exit code and JSON result (quartifyr 0.3.3 contract)."""
    doc = _json(stdout.strip()) if stdout.strip() else None
    if doc is None:
        return RenderResult(False, "quartifyr printed no result; R or the render did not finish.")
    if schema is not None:
        from jsonschema import Draft7Validator

        errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            return RenderResult(False, "quartifyr's result does not match its schema.", doc)
    status = doc.get("status")
    if code != 0 or (status != "ok" and not only_wide_tables(doc)):
        why = ERROR_CLASSES.get(str(doc.get("error_class")), "")
        detail = _plain(doc.get("errors") or [], project)
        missing = doc.get("missing_metadata") or []
        unfilled = doc.get("unfilled_placeholders") or []
        parts = [p for p in (why, detail) if p]
        # Without --strict a warning other than the wide-table one still fails the render
        # here. Say what it was, not just "status warning" (#62).
        warned = _plain(doc.get("warnings") or [], project)
        if warned:
            parts.append(warned)
        if missing:
            parts.append(f"Missing metadata: {', '.join(map(str, missing))}.")
        if unfilled:
            # Each entry is "{rpfy}:<file name>". Name the first few: which file is the clue (#62).
            names = [str(u).removeprefix("{rpfy}:") for u in unfilled]
            more = f" and {len(names) - 5} more" if len(names) > 5 else ""
            parts.append(f"Unfilled placeholders: {len(unfilled)} ({', '.join(names[:5])}{more}).")
        return RenderResult(False, " ".join(parts) or f"The render failed (status {status}).", doc)
    return RenderResult(True, "Rendered.", doc, list(doc.get("artifacts") or []))


def parse_check(stdout: str) -> list[dict[str, Any]] | None:
    """quartifyr's ``check-result-v1`` findings, or None when there is no result."""
    doc = _json(stdout.strip()) if stdout.strip() else None
    if doc is None or not isinstance(doc.get("findings"), list):
        return None
    out = []
    for f in doc["findings"]:
        if not isinstance(f, dict):
            continue
        check = re.sub(r"[^a-z0-9-]", "-", str(f.get("check", "check")).lower()) or "check"
        if not check[0].isalpha():
            check = f"q-{check}"
        out.append(
            {
                "check": check,
                "severity": "error" if f.get("severity") == "error" else "warning",
                "path": f.get("path") if isinstance(f.get("path"), str) else None,
                "message": str(f.get("message", "")),
            }
        )
    return out


def render_schema(runtime: Runtime) -> dict[str, Any] | None:
    path = package_dir(runtime) / "schemas" / "render-result-v1.schema.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


# --- project-file editor (#52) -------------------------------------------------------------


def project_editor():
    """The report's file policy and settings for the launcher's editor (``editor.py``)."""
    from opncafyr.adapters.quartifyr.editor import ReportEditor

    return ReportEditor()
