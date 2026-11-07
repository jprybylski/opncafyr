"""The report (DOCX) workflow: set up, bind, check and render a quartifyr project (#32).

``report/`` is a quartifyr project bound to one publication (``projects.py``). Every step
is a job, so it is tracked, can be cancelled, and has a time limit that kills the whole
process tree:
- **Setup** (``report.setup``). The adapter's fixed R program lays out the project
  offline in the job's staging folder. The review then writes the bundled template
  (ADR 0007), the publication's ``report.qmd`` and its ``OUTPUTS/`` copy, and binds it.
  The folder appears only when all of that worked.
- **Check** (``report.check``). The launcher's sidecar checks (#30) and quartifyr's
  ``check.R`` run on a staging copy assembled from the project plus the publication's
  files (checked against their hashes). Their findings are kept as ``preflight-v1``.
- **Render** (``report.render``). The same staging copy, with every input hashed first,
  is rendered with ``--strict --offline``. Only the DOCX that quartifyr's result lists
  is copied out, after re-hashing it, to ``report/builds/<build-id>/`` with a
  ``publish-build-v1`` manifest. A failed, partial, timed-out or cancelled render
  leaves no build.

Editing (#52). The launcher's project-file editor opens ``report.qmd`` and the report
settings (``adapters/quartifyr/editor.py``). The staging copy is taken under the
project's editor lock, so a save never lands half-way through it, and an edit made while
a job runs can't change that job's inputs. Check, render and re-binding wait while a page
reports unsaved report text. A render's status comes from the request or else from the
saved settings; with neither it asks. The status lists each build's ``stale_inputs``: the
inputs that changed since, so the screen marks it out of date. Builds are never rewritten.

Renders share the job manager's default pool, so one project never renders twice at once.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from opncafyr import __version__
from opncafyr.adapters import quartifyr as Q
from opncafyr.adapters.quartifyr import editor as QE
from opncafyr.core import runs as runs_core
from opncafyr.core.adapter import SettingsInvalid
from opncafyr.core.jobmanager import (
    STAGING_SUFFIX,
    JobContext,
    JobManager,
    JobManagerError,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.manifest import atomic_write_json, atomic_write_text
from opncafyr.core.runtime import RuntimeUnavailable
from opncafyr.paths import WORKSPACE_META_DIR, app_root
from opncafyr.workflows.nca.publish import projects, sidecars
from opncafyr.workflows.nca.publish.curator import PublishError

if TYPE_CHECKING:
    from opncafyr.workflows.nca.api import Context

TOOL = "report"
PROJECT = "report"
EDITOR_TOOL = "quartifyr"  # the descriptor whose project editor opens report/
CHECKS_DIR = f"{WORKSPACE_META_DIR}/publish-checks"
SETUP_TIMEOUT_S = 600.0
CHECK_TIMEOUT_S = 300.0
RENDER_TIMEOUT_S = 1800.0
GENERATED = (Q.REPORT_QMD, Q.QUARTO_YML)
# quartifyr's own outputs and logs are never inputs to a new render.
_OUTPUT_FOLDERS = ("report/draft", "report/final", "report/shell")


def cache_dir() -> Path:
    """App-owned caches (uv, Deno): never in the study folder or the user's home."""
    return app_root() / "cache"


def _skip(rel: str) -> bool:
    if projects.under(rel, projects.BUILDS, projects.OUTPUTS, ".quarto", ".rpfy-logs"):
        return True
    if projects.under(rel, "report/.previous", "report/.failed") or rel.startswith(
        "report/.staging"
    ):
        return True
    return projects.under(rel, *_OUTPUT_FOLDERS) and not rel.endswith("readme.txt")


def _stdout(ctx: JobContext) -> str:
    try:
        return (ctx.job_dir / "stdout.log").read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _relative(text: str, base: Path) -> str:
    return text.replace(str(base) + "/", "").replace(str(base), ".")


def _prepare(exc: Exception) -> JobPrepareError:
    if isinstance(exc, PublishError):
        return JobPrepareError(exc.message)
    if isinstance(exc, RuntimeUnavailable):
        return JobPrepareError(f"quartifyr is not available: {exc}")
    return JobPrepareError(str(exc))


# --- the report source ---------------------------------------------------------------------


def write_sources(runtime: Any, project: Path, publication: dict[str, Any]) -> dict[str, str]:
    """Write the bundled template files and this publication's ``report.qmd``.

    Returns the hashes of the generated, user-editable files (``GENERATED``).
    """
    for name in ("standard_footnotes.yaml", "config.yaml"):
        target = project / "report" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Q.template().joinpath(name).read_bytes())
    shutil.copyfile(Q.reference_docx(runtime), project / Q.REFERENCE_DOCX)
    write_report_qmd(project, publication)
    return {rel: runs_core.sha256_file(project / rel) for rel in GENERATED}


def write_report_qmd(project: Path, publication: dict[str, Any]) -> None:
    runs = {r["run_id"]: r for r in publication["runs"]}

    def analyte(run_id: str) -> str:
        a = runs[run_id]["analyte"]
        return a.get("label") or a["value"]

    items = [
        Q.ReportItem(
            file_name=Path(i["published_path"]).name,
            kind=i["kind"],
            title=i.get("title") or i["artifact"],
            analyte=analyte(i["run_id"]),
            run_id=i["run_id"],
        )
        for i in publication["items"]
    ]
    rows = [{"run_id": r, "analyte": analyte(r)} for r in runs]
    text = Q.report_source(items, publication["publication_id"], publication.get("label"), rows)
    atomic_write_text(project / Q.REPORT_QMD, text + "\n")
    atomic_write_text(project / Q.QUARTO_YML, Q.QUARTO_PROJECT)


# --- the service ---------------------------------------------------------------------------


class ReportService(projects.ProjectJobs):
    tool = TOOL
    project_name = PROJECT

    def __init__(self, cache: Path | None = None) -> None:
        super().__init__()
        self.cache = cache
        # Set by mount(): the editor's project lock and its unsaved flag (#52).
        self.project_lock: Callable[[Path], Any] = lambda root: contextlib.nullcontext()
        self.unsaved: Callable[[], bool] = lambda: False

    def refuse_if_unsaved(self) -> None:
        if self.unsaved():
            raise PublishError(
                "editor_unsaved",
                "The report editor has unsaved changes. Save or discard them first.",
            )

    def render_settings(self, project: Path, status: str | None) -> tuple[str, dict[str, Any]]:
        """The status to render with and the settings to record (request wins)."""
        try:
            settings, sha = QE.read_settings(project)
        except SettingsInvalid as exc:
            raise PublishError(
                "settings_invalid", f"The report settings are not valid: {exc}"
            ) from None
        chosen = status or settings.get("status")
        if chosen not in Q.STATUSES:
            raise PublishError(
                "needs_choice",
                "Choose whether to render a draft or a final report.",
                options=list(Q.STATUSES),
            )
        recorded = {
            "status": chosen,
            "status_from": "request" if status else "settings",
            "reference_doc": settings["reference_doc"],
            "settings_sha256": sha,
        }
        return chosen, recorded

    def _cache(self) -> Path:
        return self.cache if self.cache is not None else cache_dir()

    # --- set up or re-bind ---------------------------------------------------------------

    def start(
        self,
        manager: JobManager,
        root: Path,
        publication_id: str,
        source: str | None = None,
        timeout_s: float = SETUP_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Set up ``report/`` for a publication, or bind the existing project to it."""
        with self.lock:
            self.refuse_if_busy(manager)
            publication, sha = projects.load_publication(root, publication_id)
            project = self.project(root)
            if project.exists():
                self.refuse_if_unsaved()
                return {"binding": self._rebind(root, project, publication, sha, source)}
            job_id = manager.submit(self._setup(root, publication, sha, timeout_s))
            self.jobs["setup"] = job_id
            return {"job_id": job_id}

    def _setup(
        self, root: Path, publication: dict[str, Any], sha: str, timeout_s: float
    ) -> JobRequest:
        cache = self._cache()

        def build(ctx: JobContext):
            assert ctx.staging is not None  # noqa: S101
            program = ctx.tmpdir / Q.SETUP_PROGRAM
            program.write_text(Q.setup_program(), "utf-8", newline="\n")
            try:
                return Q.setup_command(
                    ctx.runtime, program, ctx.staging, ctx.tmpdir / "home", cache
                )
            except RuntimeUnavailable as exc:
                raise _prepare(exc) from None

        def review(ctx: JobContext) -> ReviewResult:
            project = ctx.staging
            assert project is not None  # noqa: S101
            needed = (Q.INIT_MARKER, Q.FOOTNOTES, f"{Q.EXTENSIONS}/quartifyr")
            missing = [p for p in needed if not (project / p).exists()]
            if missing:
                return ReviewResult(False, f"quartifyr did not create {', '.join(missing)}.")
            generated = write_sources(ctx.runtime, project, publication)
            projects.sync_outputs(root, project, publication)
            projects.write_binding(
                root,
                projects.binding(TOOL, PROJECT, publication["publication_id"], sha, generated),
            )
            return ReviewResult(True)

        return JobRequest(
            kind="report.setup",
            label="Set up report",
            build=build,
            review=review,
            timeout_s=timeout_s,
            output=PROJECT,
        )

    def _rebind(
        self,
        root: Path,
        project: Path,
        publication: dict[str, Any],
        sha: str,
        source: str | None,
    ) -> dict[str, Any]:
        current = projects.read_binding(root, TOOL)
        generated = current["generated"] if current else {}
        changed = projects.edited(project, generated) if current else list(GENERATED)
        if changed and source not in ("replace", "keep"):
            raise PublishError(
                "needs_choice",
                f"{', '.join(changed)} changed since the launcher wrote it. Replace it with "
                "the new publication's report, or keep your version (its placeholders may "
                "then name files the new publication doesn't have).",
                edited=changed,
                options=["replace", "keep"],
            )
        if source == "keep" and changed:
            new_generated = dict(generated)
        else:
            with self.project_lock(root):
                write_report_qmd(project, publication)
            new_generated = {rel: runs_core.sha256_file(project / rel) for rel in GENERATED}
        projects.sync_outputs(root, project, publication)
        doc = projects.binding(TOOL, PROJECT, publication["publication_id"], sha, new_generated)
        projects.write_binding(root, doc)
        return doc

    # --- check and render ------------------------------------------------------------------

    def _assemble(self, root: Path, publication: dict[str, Any], dest: Path) -> list[str]:
        with self.project_lock(root):
            projects.copy_project(self.project(root), dest, _skip)
        return projects.copy_publication(root, publication, dest)

    def check(self, manager: JobManager, root: Path, timeout_s: float = CHECK_TIMEOUT_S) -> str:
        with self.lock:
            self.refuse_if_busy(manager)
            self.refuse_if_unsaved()
            publication, _ = self.bound(root)
            output = f"{CHECKS_DIR}/report-{projects.new_build_id()}"
            job_id = manager.submit(self._check(root, publication, output, timeout_s))
            self.jobs["check"] = job_id
            return job_id

    def _check(
        self, root: Path, publication: dict[str, Any], output: str, timeout_s: float
    ) -> JobRequest:
        cache = self._cache()

        def build(ctx: JobContext):
            try:
                listed = self._assemble(root, publication, ctx.cwd)
                launcher = _sidecar_findings(ctx.cwd, listed)
                (ctx.tmpdir / "launcher.json").write_text(json.dumps(launcher), "utf-8")
                return Q.check_command(ctx.runtime, ctx.cwd, ctx.tmpdir / "home", cache)
            except (PublishError, RuntimeUnavailable) as exc:
                raise _prepare(exc) from None

        def review(ctx: JobContext) -> ReviewResult:
            assert ctx.staging is not None  # noqa: S101
            found = Q.parse_check(_stdout(ctx))
            if found is None:
                return ReviewResult(False, "quartifyr's check printed no result.")
            for f in found:
                f["path"] = _relative(f["path"], ctx.cwd) if f["path"] else None
                f["message"] = _relative(f["message"], ctx.cwd)
            launcher = json.loads((ctx.tmpdir / "launcher.json").read_text("utf-8"))
            doc = {"schema_version": 1, "findings": launcher + found}
            atomic_write_json(ctx.staging / "preflight.json", doc, "preflight-v1")
            return ReviewResult(True)

        return JobRequest(
            kind="report.check",
            label="Check report",
            build=build,
            review=review,
            timeout_s=timeout_s,
            output=output,
            ok_exit_codes=(0, 1),  # check.R exits 1 when it found an error
        )

    def render(
        self,
        manager: JobManager,
        root: Path,
        status: str | None = None,
        timeout_s: float = RENDER_TIMEOUT_S,
    ) -> dict[str, Any]:
        if status is not None and status not in Q.STATUSES:
            raise PublishError("bad_status", "Choose draft or final.")
        with self.lock:
            self.refuse_if_busy(manager)
            self.refuse_if_unsaved()
            publication, sha = self.bound(root)
            self.render_settings(self.project(root), status)  # ask now, not in the job
            build_id = projects.new_build_id()
            request = self._render(root, publication, sha, status, build_id, timeout_s)
            job_id = manager.submit(request)
            self.jobs["render"] = job_id
            return {"job_id": job_id, "build_id": build_id}

    def _render(
        self,
        root: Path,
        publication: dict[str, Any],
        sha: str,
        requested: str | None,
        build_id: str,
        timeout_s: float,
    ) -> JobRequest:
        cache = self._cache()
        # Decided from the staging copy, so the status, the settings hash and the inputs
        # all come from the same snapshot.
        picked: dict[str, Any] = {}
        quarto_timeout = int(max(60, min(900, timeout_s - 300)))

        output = f"{PROJECT}/{projects.BUILDS}/{build_id}"

        def build(ctx: JobContext):
            try:
                hidden = [p for p in ctx.cwd.parts if p.startswith(".") and p not in (".", "..")]
                if hidden:
                    raise PublishError(
                        "hidden_folder",
                        "Quarto can't render a project inside a hidden folder "
                        f"('{hidden[0]}'). Move the study folder out of it.",
                    )
                listed = self._assemble(root, publication, ctx.cwd)
                status, recorded = self.render_settings(ctx.cwd, requested)
                picked.update(status=status, settings=recorded)
                findings = _sidecar_findings(ctx.cwd, listed)
                errors = [f for f in findings if f["severity"] == "error"]
                if errors:
                    raise PublishError(
                        "sidecars_invalid",
                        f"{len(errors)} metadata problem(s), such as: {errors[0]['message']} "
                        "Run Check for the full list.",
                    )
                missing = [
                    p
                    for p in (Q.REPORT_QMD, Q.QUARTO_YML, Q.INIT_MARKER)
                    if not (ctx.cwd / p).is_file()
                ]
                if missing:
                    raise PublishError(
                        "no_project", f"The report project has no {', '.join(missing)}."
                    )
                roles = [
                    ("shell_qmd", Q.REPORT_QMD),
                    ("quarto_yml", Q.QUARTO_YML),
                    ("reference_doc", Q.REFERENCE_DOCX),
                    ("standard_footnotes", Q.FOOTNOTES),
                    ("config", Q.CONFIG),
                    ("init_marker", Q.INIT_MARKER),
                    ("settings", QE.SETTINGS_FILE),
                    ("extensions", Q.EXTENSIONS),
                    *projects.output_roles(publication),
                ]
                inputs = projects.hash_inputs(ctx.cwd, roles)
                (ctx.tmpdir / "inputs.json").write_text(json.dumps(inputs), "utf-8")
                return Q.render_command(
                    ctx.runtime, ctx.cwd, status, quarto_timeout, ctx.tmpdir / "home", cache
                )
            except (PublishError, RuntimeUnavailable) as exc:
                raise _prepare(exc) from None

        def diagnose(ctx: JobContext, code: int) -> str | None:
            return Q.parse_render(code, _stdout(ctx), None, ctx.cwd).message

        def review(ctx: JobContext) -> ReviewResult:
            staging = ctx.staging
            assert staging is not None  # noqa: S101
            status = picked["status"]
            result = Q.parse_render(0, _stdout(ctx), Q.render_schema(ctx.runtime), ctx.cwd)
            if not result.ok or result.document is None:
                return ReviewResult(False, result.message)
            entry = next((a for a in result.artifacts if a.get("role") == status), None)
            if entry is None:
                return ReviewResult(False, f"quartifyr's result lists no {status} DOCX.")
            docx = Path(str(entry.get("path", "")))
            try:
                docx.resolve().relative_to(ctx.cwd.resolve())
            except ValueError:
                return ReviewResult(False, "quartifyr's DOCX is outside the render folder.")
            if not docx.is_file() or runs_core.sha256_file(docx) != entry.get("sha256"):
                return ReviewResult(False, "The DOCX doesn't match the hash quartifyr reported.")
            name = f"report-{status}.docx"
            shutil.copyfile(docx, staging / name)
            out_sha = runs_core.sha256_file(staging / name)
            if out_sha != entry["sha256"]:
                return ReviewResult(False, "The DOCX changed while it was copied.")
            text = _relative(json.dumps(result.document, indent=2, sort_keys=True), ctx.cwd)
            (staging / "render-result.json").write_text(text + "\n", "utf-8")
            versions = result.document.get("versions") or {}
            tools = {"opncafyr": __version__}
            tools.update(
                {k: str(v) for k, v in versions.items() if isinstance(v, str | int | float)}
            )
            doc = projects.build_manifest(
                build_id=build_id,
                tool=TOOL,
                publication=publication,
                publication_sha=sha,
                inputs=json.loads((ctx.tmpdir / "inputs.json").read_text("utf-8")),
                tools=tools,
                settings=picked["settings"],
                outputs=[
                    {
                        "role": status,
                        "path": name,
                        "sha256": out_sha,
                        "bytes": (staging / name).stat().st_size,
                    }
                ],
                warnings=[
                    {"code": None, "message": str(w)} for w in result.document.get("warnings") or []
                ],
                tool_result={
                    "path": "render-result.json",
                    "sha256": runs_core.sha256_file(staging / "render-result.json"),
                },
            )
            atomic_write_json(staging / "build.json", doc, projects.BUILD_SCHEMA)
            shutil.rmtree(ctx.cwd)  # the render copy; only the DOCX and records are kept
            return ReviewResult(True)

        # Quarto refuses a project inside a hidden folder, so the render copy can't live in
        # the job's own folder (.opncafyr/jobs/). It goes in the build's staging folder,
        # which never looks complete and is removed before the build is promoted.
        return JobRequest(
            kind="report.render",
            label="Render report",
            build=build,
            review=review,
            diagnose=diagnose,
            timeout_s=timeout_s,
            output=output,
            cwd=f"{output}{STAGING_SUFFIX}/work",
        )

    # --- status ----------------------------------------------------------------------------

    def status(self, manager: JobManager | None, root: Path) -> dict[str, Any]:
        out = self.base_status(manager, root)
        project = self.project(root)
        current: dict[str, str | None] = {}

        def now(rel: str) -> str | None:
            if rel not in current:
                found = projects.hash_inputs(project, [("input", rel)]) if project.is_dir() else []
                current[rel] = found[0]["sha256"] if found else None
            return current[rel]

        # build id -> inputs that changed since (the build itself is never rewritten).
        out["stale_inputs"] = {}
        for build in out["builds"]:
            recorded = {i["path"]: i["sha256"] for i in build["inputs"]}
            if build["settings"].get("settings_sha256") is None:
                # Built before the settings existed (or without them): saving them later
                # changes what a new render would use.
                recorded.setdefault(QE.SETTINGS_FILE, None)  # type: ignore[arg-type]
            stale = sorted(p for p, sha in recorded.items() if now(p) != sha)
            out["stale_inputs"][build["build_id"]] = stale
        return out


def _sidecar_findings(project: Path, listed: list[str]) -> list[dict[str, Any]]:
    try:
        footnotes = sidecars.load_footnotes(project / Q.FOOTNOTES)
    except sidecars.SidecarError as exc:
        return [
            {
                "check": "footnotes-yaml",
                "severity": "error",
                "path": Q.FOOTNOTES,
                "message": str(exc),
            }
        ]
    return [f.to_dict() for f in sidecars.check_outputs(project, footnotes, listed)[1]]


# --- routes --------------------------------------------------------------------------------


class ReportBody(BaseModel):
    publication_id: str = Field(pattern=r"^pub-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
    source: str | None = Field(default=None, pattern=r"^(replace|keep)$")


class RenderBody(BaseModel):
    # Omitted: use the saved report settings (#52); with neither, the render asks.
    status: str | None = Field(default=None, pattern=r"^(draft|final)$")


def mount(app: FastAPI, ctx: Context, failure) -> ReportService:
    service = ReportService()
    app.state.report = service

    def project_lock(root: Path):
        files = getattr(app.state, "project_files", None)
        return files.lock(root, EDITOR_TOOL) if files is not None else contextlib.nullcontext()

    def unsaved() -> bool:
        life = getattr(app.state, "lifecycle", None)
        return life is not None and life.page_unsaved(EDITOR_TOOL)

    service.project_lock = project_lock
    service.unsaved = unsaved

    def manager() -> JobManager:
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            raise HTTPException(
                409, {"code": "jobs_unavailable", "message": "Jobs are not available."}
            )
        return jobs

    def timeout(capability: str, default: float) -> float:
        tool = (getattr(app.state, "tools", None) or {}).get("quartifyr")
        contract = tool.jobs.get(capability) if tool is not None else None
        return float(contract.timeout_s) if contract is not None and contract.timeout_s else default

    def run(fn):
        try:
            return fn()
        except PublishError as exc:
            raise failure(exc) from None
        except JobManagerError as exc:
            raise HTTPException(409, {"code": "jobs_unavailable", "message": str(exc)}) from None

    @app.get("/api/nca/report")
    def report_status() -> dict[str, Any]:
        return service.status(getattr(app.state, "jobs", None), ctx.workspace().root)

    @app.post("/api/nca/report")
    def report_start(body: ReportBody) -> dict[str, Any]:
        root = ctx.workspace().root
        return run(
            lambda: service.start(
                manager(), root, body.publication_id, body.source, SETUP_TIMEOUT_S
            )
        )

    @app.post("/api/nca/report/check")
    def report_check() -> dict[str, Any]:
        root = ctx.workspace().root
        return {
            "job_id": run(
                lambda: service.check(manager(), root, timeout("check_report", CHECK_TIMEOUT_S))
            )
        }

    @app.post("/api/nca/report/render")
    def report_render(body: RenderBody) -> dict[str, Any]:
        root = ctx.workspace().root
        return run(
            lambda: service.render(
                manager(), root, body.status, timeout("render_report", RENDER_TIMEOUT_S)
            )
        )

    @app.get("/api/nca/report/builds/{build_id}/docx")
    def report_docx(build_id: str) -> FileResponse:
        root = ctx.workspace().root
        doc = projects.read_build(root, PROJECT, build_id)
        if doc is None:
            raise HTTPException(404, {"code": "not_found", "message": "There is no such build."})
        role = doc["settings"].get("status", "draft")
        path = projects.build_output(root, PROJECT, build_id, role)
        if path is None:
            raise HTTPException(
                409,
                {
                    "code": "build_changed",
                    "message": "The DOCX no longer matches its build manifest.",
                },
            )
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"report-{build_id}-{role}.docx",
        )

    return service
