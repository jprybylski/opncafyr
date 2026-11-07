"""The deck (PPTX) workflow: set up, edit, validate and build a deckifyr project (#33).

``deck/`` is a deckifyr project bound to one publication (``projects.py``):
- **Setup** (``deck.init``). ``deckifyr init`` copies its bundled minimal deck into the
  job's staging folder (never ``--from-repo``, ADR 0007). The review writes the "NCA
  results" deck for the publication, the bundled ``standard_footnotes.yaml`` and the
  ``OUTPUTS/`` copy, then binds it.
- **Editor.** ``deckifyr serve`` on the project, under ``/apps/deckifyr/``. Quit, idle
  and stop save unsaved edits (``if_dirty: save``, #14). Builds started inside the editor
  are drafts in ``deck/build/``; the launcher doesn't track them.
- **Check** (``deck.validate``) and **Build** (``deck.build``). These run on a staging copy
  of ``deck/`` holding exactly the publication's files, each checked against its hash.
  - They refuse to start while the editor has unsaved edits.
  - A build counts only when deckifyr exits 0 with an ``ok`` result, its manifest exists,
    and the manifest's PPTX SHA-256 matches the file.
  - Previews are taken from the manifest's list and hashed by the launcher.
  - Everything goes to ``deck/builds/<build-id>/`` with a ``publish-build-v1`` manifest.

Decks are trusted user-authored projects: Quarto fragments and ``.rds`` flextables may
run R, through the pinned binaries only (``--trusted-binaries-only``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from opncafyr import __version__
from opncafyr.adapters import deckifyr as D
from opncafyr.adapters import quartifyr as Q
from opncafyr.core import runs as runs_core
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
from opncafyr.core.runtime import Runtime, RuntimeUnavailable
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.workflows.nca.publish import projects, sidecars
from opncafyr.workflows.nca.publish.curator import PublishError
from opncafyr.workflows.nca.publish.report import CHECKS_DIR

if TYPE_CHECKING:
    from opncafyr.workflows.nca.api import Context

TOOL = "deck"
PROJECT = "deck"
APP = "deckifyr"
SETUP_TIMEOUT_S = 300.0
CHECK_TIMEOUT_S = 300.0
BUILD_TIMEOUT_S = 1200.0
GENERATED = (D.PRESENTATION,)
PPTX_MEDIA = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _skip(rel: str) -> bool:
    return projects.under(rel, "build", projects.BUILDS, projects.OUTPUTS, "previews")


def _log(ctx: JobContext, name: str) -> str:
    try:
        return (ctx.job_dir / name).read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _prepare(exc: Exception) -> JobPrepareError:
    if isinstance(exc, PublishError):
        return JobPrepareError(exc.message)
    if isinstance(exc, RuntimeUnavailable):
        return JobPrepareError(f"deckifyr is not available: {exc}")
    return JobPrepareError(str(exc))


def deck_items(publication: dict[str, Any]) -> list[D.DeckItem]:
    runs = {r["run_id"]: r["analyte"] for r in publication["runs"]}
    return [
        D.DeckItem(
            file_name=Path(i["published_path"]).name,
            kind=i["kind"],
            title=i.get("title") or i["artifact"],
            analyte=runs[i["run_id"]].get("label") or runs[i["run_id"]]["value"],
            run_id=i["run_id"],
        )
        for i in publication["items"]
    ]


def write_presentation(project: Path, publication: dict[str, Any]) -> None:
    text = D.nca_results_presentation(
        deck_items(publication), publication["publication_id"], publication.get("label")
    )
    atomic_write_text(project / D.PRESENTATION, text)


def read_presentation(project: Path) -> dict[str, Any]:
    path = project / D.PRESENTATION
    try:
        doc = yaml.safe_load(path.read_text("utf-8-sig"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise PublishError("deck_invalid", "presentation.yaml can't be read as YAML.") from None
    if not isinstance(doc, dict):
        raise PublishError("deck_invalid", "presentation.yaml is not a YAML mapping.")
    return doc


def _inside(project: Path, rel: Any, what: str) -> str:
    """A project-relative path from the deck's YAML, refused if it leaves the project."""
    if not isinstance(rel, str) or not rel.strip():
        raise PublishError("deck_invalid", f"presentation.yaml has no {what}.")
    try:
        resolve_in_root(project, rel)
    except WorkspaceAccessError:
        raise PublishError(
            "deck_invalid", f"The deck's {what} is outside the deck folder."
        ) from None
    return rel


def footnotes_path(doc: dict[str, Any]) -> str:
    reportifyr = ((doc.get("build") or {}).get("reportifyr")) or {}
    return reportifyr.get("standard_footnotes") or D.FOOTNOTES


class DeckService(projects.ProjectJobs):
    tool = TOOL
    project_name = PROJECT

    def __init__(self) -> None:
        super().__init__()
        self.preview: tuple[bool, str] | None = None

    # --- editor ----------------------------------------------------------------------------

    @staticmethod
    def editor_running(supervisor: Any) -> bool:
        from opncafyr.core.supervisor import ChildState

        return (
            supervisor is not None
            and APP in supervisor.tools
            and (supervisor.state(APP) in (ChildState.RUNNING, ChildState.STARTING))
        )

    def refuse_if_unsaved(self, supervisor: Any) -> None:
        if not self.editor_running(supervisor):
            return
        activity = supervisor.activity().get(APP)
        if activity is not None and activity.unsaved:
            raise PublishError(
                "editor_unsaved",
                "The deck editor has unsaved edits. Save them there first (or close the "
                "editor, which saves them).",
            )

    def open_editor(self, supervisor: Any, root: Path, job_timeout: float) -> dict[str, Any]:
        if supervisor is None or APP not in supervisor.tools:
            raise PublishError("editor_unavailable", "The deck editor is not installed.")
        with self.lock:
            publication, _ = self.bound(root)
            project = self.project(root)
            if projects.outputs_match(project, publication):
                projects.sync_outputs(root, project, publication)
            from opncafyr.core.supervisor import LaunchError

            try:
                supervisor.start(
                    APP,
                    {
                        "project": str(project),
                        "presentation": D.PRESENTATION,
                        "job_timeout": int(job_timeout),
                    },
                )
            except (LaunchError, ValueError) as exc:
                raise PublishError(
                    "editor_failed", f"The deck editor didn't start: {exc}"
                ) from None
        return {"route": f"/apps/{APP}/"}

    def close_editor(self, supervisor: Any) -> None:
        if self.editor_running(supervisor):
            supervisor.stop(APP)

    # --- set up or re-bind -----------------------------------------------------------------

    def start(
        self,
        manager: JobManager,
        supervisor: Any,
        root: Path,
        publication_id: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self.refuse_if_busy(manager)
            if self.editor_running(supervisor):
                raise PublishError(
                    "editor_open", "Close the deck editor before changing its publication."
                )
            publication, sha = projects.load_publication(root, publication_id)
            project = self.project(root)
            if project.exists():
                return {"binding": self._rebind(root, project, publication, sha, source)}
            job_id = manager.submit(self._setup(root, publication, sha))
            self.jobs["setup"] = job_id
            return {"job_id": job_id}

    def _setup(self, root: Path, publication: dict[str, Any], sha: str) -> JobRequest:
        def build(ctx: JobContext):
            assert ctx.staging is not None  # noqa: S101
            try:
                return D.init_command(ctx.runtime, ctx.staging)
            except RuntimeUnavailable as exc:
                raise _prepare(exc) from None

        def diagnose(ctx: JobContext, code: int) -> str | None:
            return D.parse_cli(code, _log(ctx, "stdout.log"), _log(ctx, "stderr.log")).message

        def review(ctx: JobContext) -> ReviewResult:
            project = ctx.staging
            assert project is not None  # noqa: S101
            result = D.parse_cli(0, _log(ctx, "stdout.log"), "")
            if not result.ok:
                return ReviewResult(False, result.message)
            missing = [n for n in ("design.yaml", "layouts.yaml") if not (project / n).is_file()]
            if missing:
                return ReviewResult(False, f"deckifyr init did not create {', '.join(missing)}.")
            write_presentation(project, publication)
            (project / D.FOOTNOTES).write_bytes(Q.standard_footnotes().read_bytes())
            projects.sync_outputs(root, project, publication)
            generated = {rel: runs_core.sha256_file(project / rel) for rel in GENERATED}
            projects.write_binding(
                root, projects.binding(TOOL, PROJECT, publication["publication_id"], sha, generated)
            )
            return ReviewResult(True)

        return JobRequest(
            kind="deck.init",
            label="Set up deck",
            build=build,
            review=review,
            diagnose=diagnose,
            timeout_s=SETUP_TIMEOUT_S,
            output=PROJECT,
        )

    def _rebind(
        self, root: Path, project: Path, publication: dict[str, Any], sha: str, source: str | None
    ) -> dict[str, Any]:
        current = projects.read_binding(root, TOOL)
        generated = current["generated"] if current else {}
        changed = projects.edited(project, generated) if current else list(GENERATED)
        if changed and source not in ("replace", "keep"):
            raise PublishError(
                "needs_choice",
                "presentation.yaml changed since the launcher wrote it. Replace it with the new "
                "publication's NCA results deck, or keep your version (its slides may then "
                "name files the new publication doesn't have).",
                edited=changed,
                options=["replace", "keep"],
            )
        if source == "keep" and changed:
            new_generated = dict(generated)
        else:
            write_presentation(project, publication)
            new_generated = {rel: runs_core.sha256_file(project / rel) for rel in GENERATED}
        projects.sync_outputs(root, project, publication)
        doc = projects.binding(TOOL, PROJECT, publication["publication_id"], sha, new_generated)
        projects.write_binding(root, doc)
        return doc

    # --- validate and build ----------------------------------------------------------------

    def _assemble(
        self, root: Path, publication: dict[str, Any], dest: Path
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Stage the deck plus the publication's files; return its YAML and sidecar findings."""
        projects.copy_project(self.project(root), dest, _skip)
        listed = projects.copy_publication(root, publication, dest)
        doc = read_presentation(dest)
        rel = _inside(dest, footnotes_path(doc), "standard_footnotes file")
        try:
            footnotes = sidecars.load_footnotes(dest / rel)
        except sidecars.SidecarError as exc:
            return doc, [
                {"check": "footnotes-yaml", "severity": "error", "path": rel, "message": str(exc)}
            ]
        return doc, [f.to_dict() for f in sidecars.check_outputs(dest, footnotes, listed)[1]]

    def check(
        self, manager: JobManager, supervisor: Any, root: Path, timeout_s: float = CHECK_TIMEOUT_S
    ) -> str:
        with self.lock:
            self.refuse_if_busy(manager)
            self.refuse_if_unsaved(supervisor)
            publication, _ = self.bound(root)
            output = f"{CHECKS_DIR}/deck-{projects.new_build_id()}"

            def build(ctx: JobContext):
                try:
                    _, launcher = self._assemble(root, publication, ctx.cwd)
                    (ctx.tmpdir / "launcher.json").write_text(json.dumps(launcher), "utf-8")
                    return D.validate_command(ctx.runtime, ctx.cwd / D.PRESENTATION)
                except (PublishError, RuntimeUnavailable) as exc:
                    raise _prepare(exc) from None

            def diagnose(ctx: JobContext, code: int) -> str | None:
                out = D.parse_cli(code, _log(ctx, "stdout.log"), _log(ctx, "stderr.log"), ctx.cwd)
                return f"The deck did not validate. {out.message}"

            def review(ctx: JobContext) -> ReviewResult:
                assert ctx.staging is not None  # noqa: S101
                result = D.parse_cli(0, _log(ctx, "stdout.log"), "", ctx.cwd)
                if not result.ok:
                    return ReviewResult(False, result.message)
                launcher = json.loads((ctx.tmpdir / "launcher.json").read_text("utf-8"))
                doc = {
                    "schema_version": 1,
                    "findings": launcher + _warning_findings(result.warnings, ctx.cwd),
                }
                atomic_write_json(ctx.staging / "preflight.json", doc, "preflight-v1")
                return ReviewResult(True)

            job_id = manager.submit(
                JobRequest(
                    kind="deck.validate",
                    label="Check deck",
                    build=build,
                    review=review,
                    diagnose=diagnose,
                    timeout_s=timeout_s,
                    output=output,
                )
            )
            self.jobs["check"] = job_id
            return job_id

    def previews(self, runtime: Runtime) -> tuple[bool, str]:
        if self.preview is None or not self.preview[0]:
            self.preview = D.preview_probe(runtime)
        return self.preview

    def build(
        self,
        manager: JobManager,
        supervisor: Any,
        root: Path,
        previews: bool,
        timeout_s: float = BUILD_TIMEOUT_S,
    ) -> dict[str, Any]:
        with self.lock:
            self.refuse_if_busy(manager)
            self.refuse_if_unsaved(supervisor)
            if previews:
                ok, why = self.previews(manager.runtime)
                if not ok:
                    raise PublishError(
                        "previews_unavailable", f"Slide previews are not available: {why}"
                    )
            publication, sha = self.bound(root)
            build_id = projects.new_build_id()
            request = self._build(root, publication, sha, build_id, previews, timeout_s)
            job_id = manager.submit(request)
            self.jobs["build"] = job_id
            return {"job_id": job_id, "build_id": build_id}

    def _build(
        self,
        root: Path,
        publication: dict[str, Any],
        sha: str,
        build_id: str,
        previews: bool,
        timeout_s: float,
    ) -> JobRequest:
        output = f"{PROJECT}/{projects.BUILDS}/{build_id}"

        def build(ctx: JobContext):
            try:
                doc, findings = self._assemble(root, publication, ctx.cwd)
                errors = [f for f in findings if f["severity"] == "error"]
                if errors:
                    raise PublishError(
                        "sidecars_invalid",
                        f"{len(errors)} metadata problem(s), such as: {errors[0]['message']} "
                        "Run Check for the full list.",
                    )
                settings = doc.get("build") or {}
                if not isinstance(settings, dict) or not settings.get("manifest"):
                    raise PublishError(
                        "deck_invalid",
                        "presentation.yaml must set build.manifest: a build is judged by its "
                        "manifest.",
                    )
                _inside(ctx.cwd, settings.get("manifest"), "build manifest path")
                _inside(ctx.cwd, settings.get("output"), "build output path")
                roles = []
                for path in sorted(ctx.cwd.rglob("*")):
                    rel = path.relative_to(ctx.cwd).as_posix()
                    if path.is_file() and not projects.under(rel, projects.OUTPUTS):
                        role = {D.PRESENTATION: "presentation"}.get(rel, "project_file")
                        roles.append((role, rel))
                inputs = projects.hash_inputs(
                    ctx.cwd, [*roles, *projects.output_roles(publication)]
                )
                (ctx.tmpdir / "inputs.json").write_text(json.dumps(inputs), "utf-8")
                if bool(settings.get("previews")) != previews:
                    # Previews follow the request; only this staging copy is changed.
                    doc.setdefault("build", {})["previews"] = previews
                    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)
                    (ctx.cwd / D.PRESENTATION).write_text(text, "utf-8")
                return D.build_command(ctx.runtime, ctx.cwd / D.PRESENTATION)
            except (PublishError, RuntimeUnavailable) as exc:
                raise _prepare(exc) from None

        def diagnose(ctx: JobContext, code: int) -> str | None:
            out = D.parse_cli(code, _log(ctx, "stdout.log"), _log(ctx, "stderr.log"), ctx.cwd)
            return f"The deck did not build. {out.message}"

        def review(ctx: JobContext) -> ReviewResult:
            staging = ctx.staging
            assert staging is not None  # noqa: S101
            result = D.parse_cli(0, _log(ctx, "stdout.log"), "", ctx.cwd)
            if not result.ok or result.document is None:
                return ReviewResult(False, result.message)
            try:
                made = _collect(result.document, ctx.cwd, staging)
            except PublishError as exc:
                return ReviewResult(False, exc.message)
            text = D.plain(json.dumps(result.document, indent=2, sort_keys=True), ctx.cwd)
            (staging / "build-result.json").write_text(text + "\n", "utf-8")
            doc = projects.build_manifest(
                build_id=build_id,
                tool=TOOL,
                publication=publication,
                publication_sha=sha,
                inputs=json.loads((ctx.tmpdir / "inputs.json").read_text("utf-8")),
                tools={"opncafyr": __version__, "deckifyr": made["deckifyr_version"]},
                settings={"previews": previews},
                outputs=made["outputs"],
                warnings=[
                    {
                        "code": w.get("code"),
                        "message": D.plain(str(w.get("message", "")), ctx.cwd),
                        "slide": w.get("slide"),
                        "element": w.get("element"),
                    }
                    for w in result.warnings
                ],
                tool_result={
                    "path": "build-result.json",
                    "sha256": runs_core.sha256_file(staging / "build-result.json"),
                },
            )
            atomic_write_json(staging / "build.json", doc, projects.BUILD_SCHEMA)
            shutil.rmtree(ctx.cwd)  # the build copy; only the outputs and records are kept
            return ReviewResult(True)

        # Like a report render, the build copy lives in the build's staging folder: Quarto
        # fragments can't be rendered inside a hidden folder (.opncafyr/jobs/).
        return JobRequest(
            kind="deck.build",
            label="Build deck",
            build=build,
            review=review,
            diagnose=diagnose,
            timeout_s=timeout_s,
            output=output,
            cwd=f"{output}{STAGING_SUFFIX}/work",
        )

    def status(self, manager: JobManager | None, supervisor: Any, root: Path) -> dict[str, Any]:
        out = self.base_status(manager, root)
        out["editor"] = (
            str(supervisor.state(APP))
            if supervisor is not None and APP in supervisor.tools
            else None
        )
        runtime = manager.runtime if manager is not None else None
        out["previews"] = {
            "installed": bool(runtime is not None and runtime.has("soffice")),
            "checked": self.preview is not None,
            "available": bool(self.preview and self.preview[0]),
            "message": self.preview[1] if self.preview else None,
        }
        return out


def _within(base: Path, value: Any, what: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PublishError("build_invalid", f"deckifyr's result names no {what}.")
    path = Path(value)
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise PublishError(
            "build_invalid", f"deckifyr's {what} is outside the build folder."
        ) from None
    if not path.is_file():
        raise PublishError("build_invalid", f"deckifyr's {what} is missing.")
    return path


def _collect(result: dict[str, Any], work: Path, staging: Path) -> dict[str, Any]:
    """Check a build against its manifest and copy what it lists into ``staging``."""
    pptx = _within(work, result.get("output"), "PPTX")
    manifest_path = _within(work, result.get("manifest"), "build manifest")
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        raise PublishError("build_invalid", "deckifyr's build manifest can't be read.") from None
    sha = runs_core.sha256_file(pptx)
    listed = next((a for a in result.get("artifacts") or [] if a.get("kind") == "pptx"), {})
    if (manifest.get("output") or {}).get("sha256") != sha or listed.get("sha256") != sha:
        raise PublishError(
            "build_invalid", "The PPTX doesn't match the hash in deckifyr's manifest."
        )
    outputs = []

    def keep(source: Path, rel: str, role: str) -> None:
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        outputs.append(
            {
                "role": role,
                "path": rel,
                "sha256": runs_core.sha256_file(target),
                "bytes": target.stat().st_size,
            }
        )

    keep(pptx, pptx.name, "pptx")
    if runs_core.sha256_file(staging / pptx.name) != sha:
        raise PublishError("build_invalid", "The PPTX changed while it was copied.")
    keep(manifest_path, "deck-manifest.json", "deck_manifest")
    # Previews come from the manifest's own list: previews/ can hold stale files.
    for value in manifest.get("previews") or []:
        image = _within(work, value, "preview image")
        keep(image, f"previews/{image.name}", "preview")
    if manifest.get("preview_pdf"):
        pdf = _within(work, manifest["preview_pdf"], "preview PDF")
        keep(pdf, f"previews/{pdf.name}", "preview_pdf")
    return {"outputs": outputs, "deckifyr_version": str(manifest.get("deckifyr_version") or "")}


def _warning_findings(warnings: list[dict[str, Any]], base: Path) -> list[dict[str, Any]]:
    out = []
    for w in warnings:
        code = str(w.get("code") or "w-general").lower().replace("_", "-")
        where = ", ".join(f"{k} {w[k]}" for k in ("slide", "element") if w.get(k))
        message = D.plain(str(w.get("message", "")), base)
        out.append(
            {
                "check": code if code[0].isalpha() else f"w-{code}",
                "severity": "warning",
                "path": D.PRESENTATION,
                "message": f"{message} ({where})" if where else message,
            }
        )
    return out


# --- routes --------------------------------------------------------------------------------


class DeckBody(BaseModel):
    publication_id: str = Field(pattern=r"^pub-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
    source: str | None = Field(default=None, pattern=r"^(replace|keep)$")


class BuildBody(BaseModel):
    previews: bool = False


def mount(app: FastAPI, ctx: Context, failure) -> DeckService:
    service = DeckService()
    app.state.deck = service

    def manager() -> JobManager:
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            raise HTTPException(
                409, {"code": "jobs_unavailable", "message": "Jobs are not available."}
            )
        return jobs

    def supervisor() -> Any:
        return getattr(app.state, "supervisor", None)

    def timeout(capability: str, default: float) -> float:
        tool = (getattr(app.state, "tools", None) or {}).get(APP)
        contract = tool.jobs.get(capability) if tool is not None else None
        return float(contract.timeout_s) if contract is not None and contract.timeout_s else default

    def run(fn):
        try:
            return fn()
        except PublishError as exc:
            raise failure(exc) from None
        except JobManagerError as exc:
            raise HTTPException(409, {"code": "jobs_unavailable", "message": str(exc)}) from None

    @app.get("/api/nca/deck")
    def deck_status() -> dict[str, Any]:
        return service.status(getattr(app.state, "jobs", None), supervisor(), ctx.workspace().root)

    @app.post("/api/nca/deck")
    def deck_start(body: DeckBody) -> dict[str, Any]:
        root = ctx.workspace().root
        return run(
            lambda: service.start(manager(), supervisor(), root, body.publication_id, body.source)
        )

    @app.post("/api/nca/deck/editor")
    def deck_editor() -> dict[str, Any]:
        root = ctx.workspace().root
        # The editor's own jobs stay below the launcher's build limit.
        limit = max(60.0, timeout("build_deck", BUILD_TIMEOUT_S) - 60)
        return run(lambda: service.open_editor(supervisor(), root, limit))

    @app.delete("/api/nca/deck/editor")
    def deck_editor_close() -> dict[str, Any]:
        service.close_editor(supervisor())
        return {"editor": "stopped"}

    @app.post("/api/nca/deck/check")
    def deck_check() -> dict[str, Any]:
        root = ctx.workspace().root
        limit = timeout("validate_deck", CHECK_TIMEOUT_S)
        return {"job_id": run(lambda: service.check(manager(), supervisor(), root, limit))}

    @app.post("/api/nca/deck/previews")
    def deck_previews() -> dict[str, Any]:
        ok, message = service.previews(manager().runtime)
        return {"available": ok, "message": message}

    @app.post("/api/nca/deck/build")
    def deck_build(body: BuildBody) -> dict[str, Any]:
        root = ctx.workspace().root
        limit = timeout("build_deck", BUILD_TIMEOUT_S)
        return run(lambda: service.build(manager(), supervisor(), root, body.previews, limit))

    @app.get("/api/nca/deck/builds/{build_id}/pptx")
    def deck_pptx(build_id: str) -> FileResponse:
        root = ctx.workspace().root
        if projects.read_build(root, PROJECT, build_id) is None:
            raise HTTPException(404, {"code": "not_found", "message": "There is no such build."})
        path = projects.build_output(root, PROJECT, build_id, "pptx")
        if path is None:
            raise HTTPException(
                409,
                {
                    "code": "build_changed",
                    "message": "The PPTX no longer matches its build manifest.",
                },
            )
        return FileResponse(path, media_type=PPTX_MEDIA, filename=f"deck-{build_id}.pptx")

    return service
