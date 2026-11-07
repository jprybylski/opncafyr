"""deckifyr adapter: the deck editor under ``/apps/deckifyr/`` and the CLI jobs (#16, #33).

- **Editor.** The web-app part of ``opncafyr.core.adapter``: start, ready and stop for
  ``deckifyr serve``.
- **Jobs.** Commands for ``python -m deckifyr --json init|validate|build`` run from the
  tools environment, never through deckifyr's R wrappers (docs/offline.md R5). They always
  use ``--trusted-binaries-only`` with the pinned Rscript, Quarto and soffice, and read the
  JSON result.
- **The generated deck.** ``nca_results_presentation`` writes the "NCA results" deck for
  a publication (ADR 0007 §2).
- **Preview probe.** LibreOffice previews are offered only when a short conversion
  finishes in time (docs/spikes/libreoffice.md).

deckifyr is pinned in ``runtime/pins.toml`` (0.3.6). Executables come from the installed
runtime, never from workspace content. Deck logic stays in deckifyr. Deck projects are
treated as trusted user-authored projects: Quarto fragments and ``.rds`` flextables may
run R (docs/plan.md, risk table).
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

import httpx
import yaml

from opncafyr.core.adapter import ChildActivity, ChildView, LaunchContext, LaunchSpec
from opncafyr.core.jobmanager import CommandSpec
from opncafyr.core.proctree import DEVNULL, TimeoutExpired, child_env, spawn
from opncafyr.core.runtime import Runtime
from opncafyr.core.supervisor import LaunchError

API_TOKEN_HEADER = "X-Deckifyr-Api-Token"  # noqa: S105 - a name, not a secret
API_TOKEN_ENV = "DECKIFYR_API_TOKEN"  # noqa: S105 - a name, not a secret
DEFAULT_JOB_TIMEOUT = 600
# Pinned tools passed with --trusted-binaries-only, so project files can't name others.
_TRUSTED_BINS = {"rscript": "--rscript-bin", "quarto": "--quarto-bin", "soffice": "--soffice-bin"}


class DeckifyrWebApp:
    """The deck editor for one project, served under ``/apps/deckifyr/``.

    ``session`` keys: ``project`` (absolute deck project folder, resolved by the caller
    with ``resolve_in_root``), optional ``presentation`` (a plain file name inside it) and
    ``job_timeout`` (seconds).
    """

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        python = ctx.runtime.executable("deckifyr_python")
        project = Path(session.get("project") or "")
        if not project.is_absolute() or not project.is_dir():
            raise ValueError("deckifyr session 'project' must be an existing absolute folder.")
        presentation = str(session.get("presentation") or "presentation.yaml")
        if len(PurePath(presentation).parts) != 1 or presentation in {".", ".."}:
            raise ValueError("deckifyr session 'presentation' must be a file name.")
        timeout = int(session.get("job_timeout") or DEFAULT_JOB_TIMEOUT)

        argv = [
            str(python),
            "-I",  # isolated: no PYTHON* variables, user site or cwd on sys.path
            "-m",
            "deckifyr",
            "serve",
            f"--project={project}",
            f"--presentation={presentation}",
            "--host=127.0.0.1",
            f"--port={ctx.port}",  # fixed: with --port 0 the Host check ignores the port
            "--launcher=workspace",
            f"--root-path={ctx.base_path.rstrip('/')}",
            f"--api-token-env={API_TOKEN_ENV}",
            "--trusted-binaries-only",
            f"--job-timeout={timeout}",
        ]
        path_dirs = []
        for name, flag in _TRUSTED_BINS.items():
            if ctx.runtime.has(name):
                exe = ctx.runtime.executable(name)
                argv.append(f"{flag}={exe}")
                path_dirs.append(exe.parent)
        return LaunchSpec(
            argv=argv,
            env={API_TOKEN_ENV: ctx.token},
            path_dirs=path_dirs,
            auth_headers={API_TOKEN_HEADER: ctx.token},
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        resp = client.get(child.context.base_url + "api/health")
        if resp.status_code != 200:
            return False
        health = resp.json()
        if health.get("project_error"):
            code = health["project_error"].get("code", "error")
            raise LaunchError(f"deckifyr could not load the project ({code}).")
        return bool(health.get("project_loaded"))

    def activity(self, child: ChildView, client: httpx.Client) -> ChildActivity:
        resp = client.get(child.context.base_url + "api/health")
        resp.raise_for_status()
        health = resp.json()
        # A health reply without the flag can't promise the edits are saved.
        return ChildActivity(
            unsaved=bool(health.get("dirty", True)),
            busy=int(health.get("active_jobs") or 0) > 0,
        )

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        # By the time the launcher stops, the lifecycle has asked the user about unsaved
        # edits (#14), so save anything edited since rather than lose it.
        client.post(
            child.context.base_url + "api/shutdown",
            headers=child.spec.auth_headers,
            json={"if_dirty": "save", "active_jobs": "cancel"},
        )


def web_app() -> DeckifyrWebApp:
    return DeckifyrWebApp()


# --- CLI jobs ------------------------------------------------------------------------------

PRESENTATION = "presentation.yaml"
FOOTNOTES = "standard_footnotes.yaml"
RESULT_VERSION = 1
OFFLINE_ENV = {"DECKIFYR_OFFLINE": "1", "UV_OFFLINE": "1", "UV_NO_INDEX": "1"}


def cli_command(runtime: Runtime, command: str, *args: str, trusted: bool = True) -> CommandSpec:
    """``python -I -m deckifyr --json <command> <args>`` with the pinned binaries."""
    python = runtime.executable("deckifyr_python")
    argv = [str(python), "-I", "-m", "deckifyr", "--json", command, *args]
    path_dirs = [python.parent]
    if trusted:
        argv.append("--trusted-binaries-only")
        for name, flag in _TRUSTED_BINS.items():
            if runtime.has(name):
                exe = runtime.executable(name)
                argv.append(f"{flag}={exe}")
                path_dirs.append(exe.parent)
    return CommandSpec(argv, dict(OFFLINE_ENV), list(dict.fromkeys(path_dirs)))


def init_command(runtime: Runtime, folder: Path) -> CommandSpec:
    # The bundled minimal deck (never --from-repo, ADR 0007); --force because the job's
    # staging folder already holds its owner marker.
    return cli_command(runtime, "init", str(folder), "--force", trusted=False)


def validate_command(runtime: Runtime, presentation: Path) -> CommandSpec:
    return cli_command(runtime, "validate", str(presentation), "--strict")


def build_command(runtime: Runtime, presentation: Path) -> CommandSpec:
    return cli_command(runtime, "build", str(presentation), "--strict")


@dataclass
class CliResult:
    ok: bool
    message: str
    document: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


ERROR_CODES = {
    "E_SCHEMA_VERSION": "The deck's files are for another deckifyr version.",
    "E_SCHEMA_VALIDATION": "The deck's YAML doesn't match deckifyr's format.",
    "E_UNIT_PARSE": "A size or position in the deck can't be read.",
    "E_UNIT_REQUIRED": "A size or position in the deck has no unit.",
    "E_REFERENCE_NOT_FOUND": "The deck refers to something that doesn't exist.",
    "E_CONTENT_VALIDATION": "A slide's content can't be used.",
    "E_PATH_NOT_FOUND": "A file the deck names is missing.",
    "E_COLOR_RESOLUTION": "A colour in the deck can't be resolved.",
    "E_MISSING_DEPENDENCY": "A tool the deck needs is not installed.",
    "E_TIMEOUT": "A step of the build ran past its time limit.",
    "E_UNTRUSTED_BINARY": "The deck names a program of its own; only the app's tools run.",
    "E_PATH_OUTSIDE_PROJECT": "The deck names a file outside its folder.",
    "E_IO": "deckifyr could not read or write a file.",
}
_ABSOLUTE = re.compile(r"(?<![\w.~])(?:[A-Za-z]:[\\/]|/)[^\s'\"]*")


def plain(text: str, base: Path | None = None) -> str:
    """A tool message without absolute paths (they name the user's folders)."""
    if base is not None:
        text = text.replace(str(base) + "/", "").replace(str(base), ".")
    return _ABSOLUTE.sub("<path>", text)[:1500]


def _doc(text: str) -> dict[str, Any] | None:
    try:
        doc = json.loads(text.strip())
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def parse_cli(code: int, stdout: str, stderr: str, base: Path | None = None) -> CliResult:
    """Decide a CLI run from its exit code and JSON (deckifyr 0.3.6 contract).

    Success is exit 0 with ``{"status": "ok", "result_version": 1}`` on stdout. A deckifyr
    error is JSON on stderr. Usage errors (exit 2), signals and crashes print no JSON:
    those are unknown failures. Result schemas tolerate extra keys, so only the fields
    used here are checked.
    """
    if code == 0:
        doc = _doc(stdout)
        if doc is None or doc.get("status") != "ok" or doc.get("result_version") != RESULT_VERSION:
            return CliResult(False, "deckifyr finished without a result it could be judged by.")
        warnings = [w for w in doc.get("warnings") or [] if isinstance(w, dict)]
        return CliResult(True, "ok", doc, warnings)
    err = _doc(stderr.strip().splitlines()[-1] if stderr.strip() else "") or _doc(stderr)
    if err is None or err.get("status") != "error":
        return CliResult(False, f"deckifyr stopped without a result (exit code {code}).")
    what = ERROR_CODES.get(str(err.get("code")), "deckifyr reported an error.")
    detail = plain(str(err.get("message") or ""), base)
    return CliResult(False, f"{what} {detail}".strip(), err)


# --- the generated deck --------------------------------------------------------------------


@dataclass(frozen=True)
class DeckItem:
    file_name: str  # under OUTPUTS/<tables|figures>/
    kind: str  # "table" | "figure"
    title: str
    analyte: str
    run_id: str


def _slide_id(n: int, item: DeckItem) -> str:
    return f"s{n:02d}-{'table' if item.kind == 'table' else 'figure'}"


def nca_results_presentation(items: list[DeckItem], publication_id: str, label: str | None) -> str:
    """``presentation.yaml`` of the "NCA results" deck: a title slide, one slide per item.

    It uses the layouts of deckifyr's bundled minimal deck (``blank``, ``title-content``).
    Tables and figures are ``{rpfy}:`` references, so their footers come from the sidecars.
    A manifest is always written, because a build is judged by it.
    """
    title = "NCA results"
    subtitle = label or f"Publication {publication_id}"
    slides: list[dict[str, Any]] = [
        {
            "id": "title",
            "layout": "blank",
            "elements": [
                {
                    "id": "deck-title",
                    "type": "text",
                    "value": title,
                    "box": {"x": "0.9in", "y": "2.1in", "width": "11.5in", "height": "1.1in"},
                    "style": "title",
                },
                {
                    "id": "deck-subtitle",
                    "type": "text",
                    "value": f"{subtitle}. Research use only.",
                    "box": {"x": "0.9in", "y": "3.3in", "width": "11.5in", "height": "0.8in"},
                },
            ],
        }
    ]
    for n, item in enumerate(items, start=1):
        ref = f"{{rpfy}}:{item.file_name}"
        alt = f"{item.title} for {item.analyte}, run {item.run_id}."
        content: dict[str, Any] = (
            {"type": "table", "source": ref, "alt_text": alt}
            if item.kind == "table"
            else {"type": "reportifyr", "value": ref, "fit": "contain", "alt_text": alt}
        )
        slides.append(
            {
                "id": _slide_id(n, item),
                "layout": "title-content",
                "elements": {
                    "title": {"value": f"{item.title}: {item.analyte}"},
                    "content": content,
                },
            }
        )
    doc = {
        "deckifyr": "0.1",
        "design": {"base": "design.yaml"},
        "layouts": "layouts.yaml",
        "metadata": {"title": title, "author": "opncafyr", "status": "draft"},
        "build": {
            "strict": True,
            "output": "build/nca-results.pptx",
            "manifest": "build/nca-results.manifest.json",
            "previews": False,
            "reportifyr": {
                "outputs_dir": "OUTPUTS",
                "standard_footnotes": FOOTNOTES,
                "fail_on_missing_metadata": True,
            },
        },
        "slides": slides,
    }
    header = (
        "# The NCA results deck, written by opncafyr for publication "
        f"{publication_id}.\n# Edit it in the deck editor; the launcher asks before it "
        "replaces an edited deck.\n"
    )
    # UTF-8 without a byte-order mark (deckifyr#94); data text is quoted by the dumper.
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)


# --- previews ------------------------------------------------------------------------------


def preview_probe(runtime: Runtime, timeout_s: float = 60.0) -> tuple[bool, str]:
    """Can the pinned LibreOffice convert a file headless, in time? (spikes/libreoffice.md)

    "Installed" isn't enough: without a desktop session (or, on Windows, the VC++
    runtime) soffice hangs or fails to start. The profile folder is passed as a file URI.
    """
    if not runtime.has("soffice"):
        return False, "LibreOffice is not installed."
    soffice = runtime.executable("soffice")
    with tempfile.TemporaryDirectory(prefix="opncafyr-soffice-") as tmp:
        base = Path(tmp)
        (base / "probe.txt").write_text("opncafyr preview check\n", "utf-8")
        argv = [
            str(soffice),
            f"-env:UserInstallation={(base / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(base / "out"),
            str(base / "probe.txt"),
        ]
        env = child_env({"TMPDIR": tmp, "TMP": tmp, "TEMP": tmp}, [soffice.parent])
        try:
            proc = spawn(argv, env=env, cwd=base, stdout=DEVNULL, stderr=DEVNULL)
        except (OSError, ValueError) as exc:
            return False, f"LibreOffice could not start ({type(exc).__name__})."
        try:
            code = proc.wait(timeout_s)
        except TimeoutExpired:
            proc.kill_tree()
            return False, f"LibreOffice did not convert a test file within {timeout_s:g} seconds."
        finally:
            proc.kill_tree()
        if code != 0 or not (base / "out" / "probe.pdf").is_file():
            return False, f"LibreOffice could not convert a test file (exit code {code})."
    return True, "LibreOffice converted a test file."
