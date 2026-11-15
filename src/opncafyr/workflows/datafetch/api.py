"""Routes for the External data page: ``/api/datafetch/...`` (#83, ADR 0012).

Every route goes through the core's loopback guard and session secret like any other.
Nothing here runs on workspace open or in the background: the page asks for status when
it is shown, and every check or fetch is a job the user starts.

- Config edits (``.data.yaml``) are structural, checked against the managed-mode policy
  and guarded by the file's SHA-256, so a concurrent edit is never overwritten.
- Check and fetch are jobs (``adapters.datum.staging``). Credentials arrive with the
  request, reach only that job's datum process, and are never stored or returned.
- Apply, finish/roll back, delete, undelete, forget and purge change study files in
  process: each holds the workspace lease and refuses while a job runs.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from opncafyr.adapters import datum
from opncafyr.adapters.datum import approvals as appr
from opncafyr.adapters.datum import config as cfg
from opncafyr.adapters.datum import promote, staging, state
from opncafyr.adapters.datum.redact import source_display
from opncafyr.core.jobmanager import JobManagerError
from opncafyr.core.workspace import Workspace

LEASE_HOLDER = "External data"
ID = r"^[A-Za-z0-9_-]{1,64}$"
REVIEW = r"^[0-9a-f]{16}$"
MAX_SECRET = 4096
RESEARCH_NOTE = (
    "Fetching tells you which bytes you have and where they came from. It says nothing "
    "about whether the data is fit for analysis."
)


class SourceForm(BaseModel):
    type: str = Field(pattern=r"^(http|file|git)$")
    url: str | None = Field(default=None, max_length=4096)
    path: str | None = Field(default=None, max_length=4096)
    ref: str | None = Field(default=None, max_length=256)
    headers: dict[str, str] | None = None
    body: str | None = Field(default=None, max_length=65536)


class DatasetForm(BaseModel):
    id: str = Field(pattern=ID)
    desc: str = Field(default="", max_length=500)
    target: str = Field(max_length=1024)
    policy: str | None = Field(default=None, pattern=r"^(fail|update|log)$")
    source: SourceForm


class EditBody(BaseModel):
    dataset: DatasetForm
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ValidateBody(BaseModel):
    dataset: DatasetForm
    replace: str | None = Field(default=None, pattern=ID)


class RemoveBody(BaseModel):
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApproveBody(BaseModel):
    path: str = Field(max_length=4096)


class RunBody(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=200)
    credentials: dict[str, str] = Field(default_factory=dict, max_length=50)


class RecoverBody(BaseModel):
    action: str = Field(pattern=r"^(finish|rollback)$")


def mount(app: FastAPI) -> None:
    approvals = appr.Approvals()

    # --- helpers -----------------------------------------------------------------------

    def workspace() -> Workspace:
        ws = app.state.workspace
        if ws is None:
            raise HTTPException(
                409, {"code": "no_workspace", "message": "Open a study folder first."}
            )
        return ws

    def study() -> Path:
        return workspace().root.resolve()

    def study_key() -> str:
        ws = workspace()
        return appr.study_key(ws.root, str(ws.document.get("created_at", "")))

    def health() -> tuple[bool, bool, str | None]:
        tools = getattr(app.state, "tools", {}) or {}
        if datum.TOOL not in tools:
            return False, False, "datum is not part of this build."
        status = app.state.health.status(datum.TOOL)
        return True, status.state == "ok", status.detail

    def runtime() -> Any:
        installed, ok, detail = health()
        rt = getattr(app.state, "runtime", None)
        if not ok or not datum.available(rt):
            raise HTTPException(
                503,
                {
                    "code": "unavailable",
                    "message": "External data is unavailable: "
                    + (detail or "datum is not installed."),
                },
            )
        return rt

    def fail(exc: Exception) -> HTTPException:
        if isinstance(exc, cfg.ConfigError):
            return HTTPException(exc.status, {"code": exc.code, "message": exc.message})
        if isinstance(exc, promote.PromoteError):
            return HTTPException(exc.status, {"code": exc.code, "message": exc.message})
        if isinstance(exc, datum.DatumError):
            return HTTPException(502, {"code": exc.code, "message": exc.message})
        if isinstance(exc, staging.SnapshotError):
            return HTTPException(409, {"code": "unsafe", "message": exc.message})
        raise exc

    def call(fn, *args, **kw) -> Any:
        try:
            return fn(*args, **kw)
        except (
            cfg.ConfigError,
            promote.PromoteError,
            datum.DatumError,
            staging.SnapshotError,
        ) as exc:
            raise fail(exc) from None

    @contextlib.contextmanager
    def scratch() -> Iterator[Path]:
        folder = Path(tempfile.mkdtemp(prefix="opncafyr-datum-")).resolve()
        try:
            yield folder
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def not_leased() -> None:
        lease = getattr(app.state, "workspace_lease", None)
        if lease is not None and lease.holder is not None:
            raise HTTPException(
                423,
                {
                    "code": "workspace_busy",
                    "message": f"{lease.holder} is changing workspace files. "
                    "Try again in a moment.",
                },
            )

    @contextlib.contextmanager
    def exclusive() -> Iterator[None]:
        """Hold the workspace lease for an in-process change to study files."""
        jobs = getattr(app.state, "jobs", None)
        if jobs is not None and jobs.active():
            raise HTTPException(
                409,
                {
                    "code": "jobs_running",
                    "message": "Wait for the running jobs to finish, then try again.",
                },
            )
        lease = getattr(app.state, "workspace_lease", None)
        if lease is not None and not lease.acquire(LEASE_HOLDER):
            not_leased()
        try:
            yield
        finally:
            if lease is not None:
                lease.release(LEASE_HOLDER)

    def schema_for_check() -> dict[str, Any] | None:
        rt = getattr(app.state, "runtime", None)
        if not datum.available(rt):
            return None
        with scratch() as folder:
            try:
                return datum.config_schema(rt, folder)
            except datum.DatumError:
                return None

    def checked(loaded: cfg.Loaded, root: Path) -> cfg.Checked:
        return cfg.check(loaded.data, root, schema_for_check())

    # --- status ------------------------------------------------------------------------

    @app.get("/api/datafetch/status")
    def status() -> dict[str, Any]:
        installed, available, detail = health()
        out: dict[str, Any] = {
            "workspace_open": app.state.workspace is not None,
            "installed": installed,
            "available": available,
            "detail": None if available else detail,
            "note": RESEARCH_NOTE,
            "config_file": datum.CONFIG_FILE,
            "source_data": datum.SOURCE_DATA,
        }
        if app.state.workspace is None:
            return out
        root = study()
        out["exists"] = cfg.exists(root)
        jobs = getattr(app.state, "jobs", None)
        out["jobs_active"] = bool(jobs is not None and jobs.active())
        j = promote.journal(root)
        out["interrupted"] = (
            {"review_id": j.get("review_id"), "started_at": j.get("started_at")} if j else None
        )
        out["reviews"] = promote.list_reviews(root)
        out["previous"] = promote.previous_size(root)
        if not out["exists"]:
            return out
        try:
            loaded = cfg.load(root)
        except cfg.ConfigError as exc:
            out["config_error"] = exc.message
            return out
        result = checked(loaded, root)
        out["sha256"] = loaded.sha256
        out["has_comments"] = cfg.has_comments(loaded.text)
        out["problems"] = [p.to_dict() for p in result.problems]
        out["credentials"] = result.credentials
        audit: dict[str, dict[str, Any]] = {}
        if available:
            with scratch() as folder:
                try:
                    entries = staging.audit(root, loaded, app.state.runtime, folder)
                    audit = {e["id"]: e for e in entries}
                except (datum.DatumError, staging.SnapshotError) as exc:
                    out["audit_error"] = exc.message
        doc = state.read(root)
        last = doc.get("last_check") or {}
        out["last_check_at"] = last.get("at")
        checks = {r["id"]: r for r in last.get("results", [])}
        outside = {o.dataset: o.path for o in result.outside}
        key = study_key()
        try:
            lock_items = staging.read_lock(root / datum.LOCK_FILE)
        except staging.SnapshotError:
            lock_items = {}
        defaults = (
            loaded.data.get("defaults") if isinstance(loaded.data.get("defaults"), dict) else {}
        )
        datasets = []
        for ds in loaded.datasets:
            did = ds.get("id")
            if not isinstance(did, str):
                continue
            sources = [s for _, s in cfg._sources(ds) if isinstance(s, dict)]
            entry: dict[str, Any] = {
                "id": did,
                "desc": ds.get("desc") or "",
                "types": [str(s.get("type")) for s in sources],
                "sources": [source_display(s) for s in sources],
                "target": ds.get("target"),
                "policy": ds.get("policy") or defaults.get("policy") or "fail",
                "editable": "sources" not in ds,
                "source": _form_source(sources[0]) if len(sources) == 1 else None,
                "audit": audit.get(did),
                "last_check": checks.get(did),
                "problems": [p.to_dict() for p in result.problems if p.dataset == did],
            }
            if did in outside:
                entry["approval"] = {
                    "path": outside[did],
                    "approved": approvals.approved(key, did, outside[did]),
                }
            fetched = doc["datasets"].get(did)
            if fetched:
                entry["fetched_at"] = fetched["fetched_at"]
                entry["matches_fetched"] = _matches(root, fetched["files"])
            target = ds.get("target")
            if isinstance(target, str) and not cfg._target_problem(root, target):
                with contextlib.suppress(staging.SnapshotError):
                    entry["files"] = [
                        rel
                        for rel in staging.tracked_files(root, target, lock_items.get(did))
                        if staging.study_file(root, rel).is_file()
                    ]
            datasets.append(entry)
        out["datasets"] = datasets
        out["orphaned"] = [
            e for e in audit.values() if not e.get("in_config") and e["status"] == "orphaned"
        ]
        return out

    # --- config editing ------------------------------------------------------------------

    @app.post("/api/datafetch/setup")
    def setup() -> dict[str, Any]:
        not_leased()
        root = study()
        call(cfg.scaffold, root)
        return {"created": datum.CONFIG_FILE}

    @app.get("/api/datafetch/types")
    def types() -> dict[str, Any]:
        rt = runtime()
        with scratch() as folder:
            found = call(datum.source_types, rt, folder)
        allowed = [t for t in found if isinstance(t, dict) and t.get("type") in cfg.ALLOWED_TYPES]
        return {"types": allowed, "policies": list(cfg.POLICIES)}

    def candidate(form: DatasetForm, replace: str | None) -> tuple[cfg.Loaded, dict[str, Any]]:
        root = study()
        loaded = call(cfg.load, root)
        entry = cfg.dataset_from_form(form.model_dump())
        data = call(cfg.with_dataset, loaded.data, entry, replace)
        return loaded, data

    def problems_for(data: dict[str, Any], did: str) -> list[dict[str, Any]]:
        result = cfg.check(data, study(), schema_for_check())
        return [p.to_dict() for p in result.problems if p.dataset in (did, None)]

    @app.post("/api/datafetch/validate")
    def validate(body: ValidateBody) -> dict[str, Any]:
        _, data = candidate(body.dataset, body.replace)
        return {"problems": problems_for(data, body.dataset.id)}

    def save(data: dict[str, Any], did: str, expected: str) -> dict[str, Any]:
        problems = problems_for(data, did)
        if problems:
            raise HTTPException(
                400,
                {
                    "code": "invalid",
                    "message": "Fix the problems first.",
                    "problems": problems,
                },
            )
        loaded = call(cfg.save, study(), data, expected)
        return {"sha256": loaded.sha256}

    @app.post("/api/datafetch/datasets")
    def add(body: EditBody) -> dict[str, Any]:
        not_leased()
        _, data = candidate(body.dataset, None)
        return save(data, body.dataset.id, body.expected_sha256)

    @app.put("/api/datafetch/datasets/{dataset_id}")
    def edit(dataset_id: str, body: EditBody) -> dict[str, Any]:
        not_leased()
        _id(dataset_id)
        _, data = candidate(body.dataset, dataset_id)
        return save(data, body.dataset.id, body.expected_sha256)

    @app.post("/api/datafetch/datasets/{dataset_id}/remove")
    def remove(dataset_id: str, body: RemoveBody) -> dict[str, Any]:
        not_leased()
        _id(dataset_id)
        root = study()
        loaded = call(cfg.load, root)
        data = call(cfg.without_dataset, loaded.data, dataset_id)
        saved = call(cfg.save, root, data, body.expected_sha256)
        try:
            in_lock = dataset_id in staging.read_lock(root / datum.LOCK_FILE)
        except staging.SnapshotError:
            in_lock = False
        return {"sha256": saved.sha256, "offer_forget": in_lock}

    @app.post("/api/datafetch/datasets/{dataset_id}/approve")
    def approve(dataset_id: str, body: ApproveBody) -> dict[str, Any]:
        _id(dataset_id)
        root = study()
        loaded = call(cfg.load, root)
        outside = {o.dataset: o.path for o in cfg.check(loaded.data, root).outside}
        if outside.get(dataset_id) != body.path:
            raise HTTPException(
                409,
                {
                    "code": "changed",
                    "message": "This dataset's path changed. Reload the page and look again.",
                },
            )
        approvals.approve(study_key(), dataset_id, body.path)
        return {"approved": True}

    # --- check and fetch -----------------------------------------------------------------

    def prepare(body: RunBody) -> tuple[Path, cfg.Loaded, list[str], dict[str, str]]:
        runtime()
        root = study()
        loaded = call(cfg.load, root)
        result = checked(loaded, root)
        if result.problems:
            raise HTTPException(
                400,
                {
                    "code": "invalid",
                    "message": "Fix the problems in the external data settings first.",
                    "problems": [p.to_dict() for p in result.problems],
                },
            )
        all_ids = [d["id"] for d in loaded.datasets]
        ids = all_ids if body.ids is None else body.ids
        unknown = [i for i in ids if i not in all_ids]
        if unknown or not ids:
            raise HTTPException(
                400, {"code": "unknown", "message": "Choose one or more datasets from the list."}
            )
        key = study_key()
        waiting = [
            o.dataset
            for o in result.outside
            if o.dataset in ids and not approvals.approved(key, o.dataset, o.path)
        ]
        if waiting:
            raise HTTPException(
                409,
                {
                    "code": "approval_needed",
                    "message": "Approve the file sources outside the study first.",
                    "datasets": waiting,
                },
            )
        # Only the chosen datasets' credentials reach datum (the snapshot holds only
        # them); others the page holds for this session are dropped here, and a name the
        # settings never declare is refused.
        needed = set(_names(loaded, root, ids))
        given = body.credentials
        extra = sorted(set(given) - set(result.credentials))
        missing = sorted(n for n in needed if not given.get(n))
        if extra or missing:
            raise HTTPException(
                400,
                {
                    "code": "credentials",
                    "message": "Enter a value for every credential the datasets use, "
                    "and no others.",
                    "missing": missing,
                },
            )
        for value in given.values():
            if len(value) > MAX_SECRET or any(c in value for c in "\r\n\x00"):
                raise HTTPException(
                    400,
                    {
                        "code": "credentials",
                        "message": "A credential can't contain line breaks or be that long.",
                    },
                )
        return root, loaded, ids, {k: given[k] for k in needed}

    def submit(request: Any) -> str:
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            raise HTTPException(
                409, {"code": "jobs_unavailable", "message": "Jobs are not available."}
            )
        try:
            return jobs.submit(request)
        except JobManagerError as exc:
            raise HTTPException(409, {"code": "jobs_unavailable", "message": str(exc)}) from None

    @app.post("/api/datafetch/check")
    def check(body: RunBody) -> dict[str, Any]:
        body.ids = None
        root, loaded, _, creds = prepare(body)
        return {"job_id": submit(staging.check_request(root, loaded, creds))}

    @app.post("/api/datafetch/fetch")
    def fetch(body: RunBody) -> dict[str, Any]:
        root, loaded, ids, creds = prepare(body)
        if promote.journal(root) is not None:
            raise HTTPException(
                409,
                {
                    "code": "interrupted",
                    "message": "An earlier Apply was interrupted. Finish or roll it back first.",
                },
            )
        review_id = staging.new_review_id()
        job_id = submit(staging.fetch_request(root, loaded, ids, review_id, creds))
        return {"job_id": job_id, "review_id": review_id}

    # --- reviews -------------------------------------------------------------------------

    @app.get("/api/datafetch/reviews/{review_id}")
    def review(review_id: str) -> dict[str, Any]:
        _review(review_id)
        doc = call(promote.load_review, study(), review_id)
        return {**doc, "note": RESEARCH_NOTE}

    @app.post("/api/datafetch/reviews/{review_id}/apply")
    def apply(review_id: str) -> dict[str, Any]:
        _review(review_id)
        with exclusive():
            return call(promote.apply, study(), review_id)

    @app.delete("/api/datafetch/reviews/{review_id}")
    def discard(review_id: str) -> dict[str, Any]:
        _review(review_id)
        with exclusive():
            call(promote.discard, study(), review_id)
        return {"discarded": True}

    @app.post("/api/datafetch/recover")
    def recover(body: RecoverBody) -> dict[str, Any]:
        with exclusive():
            fn = promote.finish if body.action == "finish" else promote.rollback
            return call(fn, study())

    # --- delete, undelete, forget, purge ----------------------------------------------------

    def lock_edit(dataset_id: str, command: str) -> bytes:
        rt = runtime()
        root = study()
        loaded = call(cfg.load, root)
        with scratch() as folder:
            return call(staging.lock_edit, root, loaded, rt, folder, command, dataset_id)

    @app.post("/api/datafetch/datasets/{dataset_id}/delete")
    def delete(dataset_id: str) -> dict[str, Any]:
        _id(dataset_id)
        with exclusive():
            root = study()
            loaded = call(cfg.load, root)
            ds = next((d for d in loaded.datasets if d.get("id") == dataset_id), None)
            if ds is None or cfg._target_problem(root, ds.get("target")):
                raise HTTPException(404, {"code": "not_found", "message": "No such dataset."})
            item = call(staging.read_lock, root / datum.LOCK_FILE).get(dataset_id)
            rels = call(staging.tracked_files, root, ds["target"], item)
            new_lock = lock_edit(dataset_id, "delete")
            removed = call(promote.keep_and_remove, root, dataset_id, rels)
            _write_lock(root, new_lock)
            state.forget(root, dataset_id)
        return {"removed": removed}

    @app.post("/api/datafetch/datasets/{dataset_id}/undelete")
    def undelete(dataset_id: str) -> dict[str, Any]:
        _id(dataset_id)
        with exclusive():
            _write_lock(study(), lock_edit(dataset_id, "undelete"))
        return {"undeleted": True}

    @app.post("/api/datafetch/datasets/{dataset_id}/forget")
    def forget(dataset_id: str) -> dict[str, Any]:
        _id(dataset_id)
        with exclusive():
            root = study()
            _write_lock(root, lock_edit(dataset_id, "unlock"))
            state.forget(root, dataset_id)
        return {"forgotten": True}

    @app.post("/api/datafetch/datasets/{dataset_id}/purge")
    def purge(dataset_id: str) -> dict[str, Any]:
        _id(dataset_id)
        with exclusive():
            call(promote.purge, study(), dataset_id)
        return {"purged": True}


# --- module helpers ---------------------------------------------------------------------------


def _id(value: str) -> None:
    if not cfg.ID_RE.match(value):
        raise HTTPException(404, {"code": "not_found", "message": "No such dataset."})


def _review(value: str) -> None:
    if not staging.REVIEW_ID_RE.match(value):
        raise HTTPException(404, {"code": "not_found", "message": "No such review."})


def _write_lock(root: Path, data: bytes) -> None:
    from opncafyr.core.manifest import atomic_write_bytes

    atomic_write_bytes(root / datum.LOCK_FILE, data)


def _names(loaded: cfg.Loaded, root: Path, ids: list[str]) -> list[str]:
    """The ${NAME} credentials the datasets in ``ids`` use."""
    subset = {**loaded.data, "datasets": [d for d in loaded.datasets if d.get("id") in ids]}
    return cfg.check(subset, root).credentials


def _matches(root: Path, files: list[dict[str, Any]]) -> bool:
    for f in files:
        path = root / f["path"]
        if not path.is_file():
            return False
        if path.stat().st_size != f["size"] or state.fingerprint(path)[1] != f["sha256"]:
            return False
    return True


def _form_source(source: dict[str, Any]) -> dict[str, Any]:
    """The editable fields of one source, for the edit form."""
    keep = ("type", "url", "path", "ref", "headers", "body")
    return {k: source[k] for k in keep if k in source}
