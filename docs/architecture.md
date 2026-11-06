# Architecture

This page summarises the layering in `docs/spec.md` and the trust boundaries the code
enforces. The spec remains the authority on design intent.

## Layers

| Layer | Package | Knows about | Must not |
|---|---|---|---|
| Installer | `installer/`, `runtime/pins.toml` | OS, runtimes, pinned packages, checksums | touch study workspaces or machine-wide PATH |
| Core controller | `opncafyr.core` | processes, jobs, files, manifests, routes, security | import adapters/workflows; contain NCA or document layout logic |
| Tool adapters | `opncafyr.adapters.<tool>` | one tool's CLI/API, its input/output contracts | share mutable scientific state with other adapters |
| Workflow packs | `opncafyr.workflows.nca` | the clin pharm user journey (import → clean → map → analyte → run → publish) | bypass adapters to call tools directly |
| Tools | opnca-r, deckifyr, quartifyr/reportifyr | their own calculations and rendering | navigate the workspace |

A future tool is added by shipping `adapters/<tool>/descriptor.yaml` (validated against
`schemas/tool-descriptor-v1.schema.json`), a health check and an adapter that implements
`core.adapter.Adapter`. A future workflow (e.g. a non-NCA analysis) is a new package under
`workflows/` that composes existing adapters. Neither requires changes to `core/`.

A workflow package exposes `workflow()` returning a `core.registry.WorkflowPack`: its
dataset importers (`core.datasets.Importer`, one per file format) and preview annotators
(`core.datasets.DatasetAnnotator`). Core finds packs by package scan
(`load_workflows()`), as it finds web adapters, and never imports a workflow by name. A
new file format is a new importer in a pack.

The registry (`core/registry.py`) reads descriptors only from the installed
`opncafyr.adapters` package, never from a workspace or the working directory. A descriptor
names its format (`schema_version: "1.0"`), the adapter's own `version`, its capabilities,
the runtime executables it needs (symbolic names from `runtime-v1`, never paths), a health
probe, an optional web app, job contracts (each schema must be bundled) and optional
requirements (an executable that enables some capabilities, e.g. LibreOffice for
previews). Descriptors ship with the launcher, so there are no migrations: an unknown
format or any invalid field stops startup with an error naming the file and field.
`core/health.py` runs each probe once in the background at startup; the command is built
by core from the probe kind (`python_import`, `r_package`, `version_flag`) and a
pattern-checked target. `/api/tools` and `opncafyr doctor` report each tool as
`checking`, `ok` or `unavailable` with a short reason, plus the features its missing
optional requirements disable.

## Process model

```
launcher (uvicorn, 127.0.0.1:<ephemeral>)
 ├── child web apps on private loopback ports, proxied under /apps/<tool>/
 │     opnca-r (plumber2)        deckifyr serve (FastAPI)
 └── jobs (argv arrays, app-controlled PATH, cwd = approved workspace dir)
       Rscript --no-init-file …  (cleaning, snapshot, opnca replay, report render)
       python -m deckifyr --json …  (validate, build, preview)
```

`opncafyr launch` binds its own socket on `127.0.0.1:0` and hands it to uvicorn, so the
ephemeral port is known before anything listens; `--host` accepts only `127.0.0.1` (not
`0.0.0.0`, `::1` or a LAN address). `/health` reports status, version, instance id and
uptime and nothing else; the browser opens only after it answers. The launcher logs to
`logs/launcher.log` in the app folder as JSON lines (`core/logs.py`, rotated at 5 MB),
uvicorn's own loggers included.

The controller owns child lifetimes (FastAPI lifespan), the single-instance lock, idle
shutdown and the job state machine `queued → preparing → running → reviewing →
completed` (+ `failed`, `cancelled`). The job manager (`core/jobmanager.py`) runs each job
on a worker thread with a required timeout and a per-pool concurrency limit (default 1).
Outputs go to `<target>.staging/` and are renamed into place only after a clean exit and
a passing review. Opening a workspace marks jobs left behind by a dead launcher `failed`
and kills their recorded process tree. The single-instance lock (`core/instance.py`) is an
OS file lock in the app state dir, plus a state record (`instance-v1`) naming the port and
instance id; a second launch reopens the browser on the healthy instance and never kills
a process because a record names it. The lifecycle (`core/lifecycle.py`) stops the
launcher after the idle timeout only when no page has sent a visible heartbeat, no job is
live and no child reports unsaved edits or busy work (an optional adapter `activity()`
hook). An explicit Quit keeps live jobs running by default and asks before saving
unsaved edits. Raw output stays in the job's log files; messages
and the API carry text written for a person.

## Trust boundaries

1. **Browser ↔ controller.** Anyone on the machine can connect to 127.0.0.1, including
   other users on a shared host and any web page the user visits. Implemented in
   `core/security.py` and `core/bootstrap.py`:
   - Host must be our loopback authority (400 otherwise); a browser-sent `Origin` must be
     ours; `Sec-Fetch-Site`, when sent, must be `same-origin` or `none`. No CORS.
   - Every request except `/health` and `/open` needs the per-launch session cookie
     (`HttpOnly; SameSite=Strict; Path=/`, named after the port). A process that can
     merely connect gets neither the home page, its secret, nor any API data.
   - The browser gets the cookie only from `/open?token=…`. The launcher, and a second
     launch that reopens it, mint a one-time token: its SHA-256 is a file name in
     `state/open-tokens/` (user-only), valid for 120 s and consumed by the first use.
     `/open` sets the cookie and redirects to `/`. A page without a session shows
     "Open opncafyr from its shortcut". The secret is never in a URL, a file or
     `instance.json`.
   - State-changing requests also need the secret in `x-opncafyr-session` (the home page
     reads it from a meta tag). Under `/apps/`, where child pages can't add a header,
     they need our `Origin` instead.

   The home page (ADR 0006) is static HTML, ES modules and one CSS
   file in `web/static/`, served under `/static/`; `/` sends a `self`-only
   Content-Security-Policy, so the page runs no inline script and requests nothing from
   another origin. Its data comes from JSON APIs (`/api/tools`, `/api/apps`, `/api/jobs`,
   `/api/workspace/artifacts`, a read-only runs and publications listing in
   `core/artifacts.py`), and its `api.js` adds the session header to every
   state-changing request.
2. **Controller ↔ child apps.** Each child listens on its own loopback port with a
   per-child API token (`core/supervisor.py`). The proxy (`core/proxy.py`) rewrites
   `Host` to the child's port, drops `Origin`, `Referer`, `Cookie` and the session header,
   adds the child's token, and strips `Set-Cookie` and CORS headers from responses.
   It adds `frame-ancestors 'self'`: the home page embeds child apps in a same-origin
   iframe (`#/apps/<id>`, #50), and nothing else may frame them. Each proxied request
   counts as an open page for idle shutdown. The children themselves reject a foreign
   Host, cross-site requests and a missing token, so their ports are safe even though
   the browser can reach them directly. Child pages share the launcher's origin by
   design, so a child's own scripts are trusted like ours.
3. **Controller ↔ workspace.** Every path goes through `core/workspace.resolve_in_root`.
   Workspace files hold data and options only. The workspace root itself comes from the
   native picker the controller runs (`core/picker.py`): `/api/workspace/*` accepts only
   a folder picked in this session or one on the recent list (paths only, in the app
   state dir).
4. **Controller ↔ tools.** Only installed descriptors are trusted; executables come from
   the pinned runtime record (`runtime-v1`, `core/runtime.py`). Every process starts
   through `proctree.spawn`: an argv list whose first item is an absolute runtime path,
   never a shell, with an allow-listed environment and a launcher-owned cwd and TMPDIR.
   A boundary test allows `subprocess` only in `core/proctree.py` and `core/picker.py`
   (fixed system dialogs) and forbids `shell=True`, `os.system`/`popen`/`exec*`/`spawn*`
   and `asyncio.create_subprocess_*`. Children never see the session secret.
5. **Machine ↔ network.** No dataset leaves the machine by default. Subject IDs and other
   cell values never reach a log (ADR 0007): `core/redaction.py` filters every launcher
   log record (table-like arguments become a row count; registered identifier values
   and `token=` query values become `‹redacted›`) and every line of child output copied
   into `logs/children/`. Workflows register identifier values when they read a dataset.
   Job logs stay in the study workspace with the run; the diagnostic bundle (#38) must
   redact them.

## Workspace contract

```
Study folder/
  source-data/                  optional immutable copies or references
  .opncafyr/
    workspace.json              schemas/workspace-v1 (core/workspace.py, migrations in
                                core/migrations/); `sources` lists imported files
    backups/                    copies made before a schema migration
    recipes/<recipe-id>.yaml    schemas/recipe-v1
    runs/<run-id>/
      input/source-fingerprint.json
      input/analysis.csv        frozen, one analyte
      analysis.R                replayable, relative input path
      settings.json  analysis-record.json  results/  artifacts/  logs/
      manifest.json             schemas/run-manifest-v1
    publications/<id>.json      schemas/publication-v1
    jobs/<job-id>/              schemas/job-v1 state.json, events.jsonl, stdout/stderr
                                logs, diagnostic.json (core/jobmanager.py)
  OUTPUTS/{figures,tables}/     reportifyr-compatible copies + *_metadata.json sidecars
  report/                       quartifyr / Quarto project
  deck/                         deckifyr project
```

## Datasets (#21)

`/api/datasets` (`core/datasets_api.py`, `core/datasets.py`) lists importable files
**directly** in the study folder and in `source-data/`. Nothing deeper, nothing under a
dot-folder and no other file type is listed or read. A preview may name only a file the
scan could list; every path goes through `resolve_in_root`. Files are opened read-only
(`rb`), never locked or written. Results are cached in memory by path, mtime and size.

A preview gives columns, inferred types (with up to three counterexamples), missing
counts, exact or estimated row count, the first rows and the annotations. Files over
50 MB are sampled (first 100 000 rows) and labelled so; the SHA-256 always covers the
whole file. The NCA pack's CSV importer (`workflows/nca/importers/csv.py`) sniffs the
delimiter, strips a UTF-8 BOM and treats `"", NA, ., NaN, NULL` as missing; its analyte
annotator lists exact raw values of the analyte code columns of ADR 0005 Q1.

`POST /api/datasets/import` re-hashes the file and records `{id, path, sha256,
size_bytes}` in `workspace.json` `sources` (the id a recipe's `source_id` names). The same
bytes at the same path keep their entry; changed bytes get a new one. Data values travel
only to the page on this origin and are never logged.
