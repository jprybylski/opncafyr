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

## Process model

```
launcher (uvicorn, 127.0.0.1:<ephemeral>)
 ├── child web apps on private loopback ports, proxied under /apps/<tool>/
 │     opnca-r (plumber2)        deckifyr serve (FastAPI)
 └── jobs (argv arrays, app-controlled PATH, cwd = approved workspace dir)
       Rscript --no-init-file …  (cleaning, snapshot, opnca replay, report render)
       python -m deckifyr --json …  (validate, build, preview)
```

The controller owns child lifetimes (FastAPI lifespan), the single-instance lock, idle
shutdown and the job state machine `queued → preparing → running → reviewing →
completed` (+ `failed`, `cancelled`). The job manager (`core/jobmanager.py`) runs each job
on a worker thread with a required timeout and a per-pool concurrency limit (default 1).
Outputs go to `<target>.staging/` and are renamed into place only after a clean exit and
a passing review. Opening a workspace marks jobs left behind by a dead launcher `failed`
and kills their recorded process tree. The single-instance lock (`core/instance.py`) is an
OS file lock in the app state dir, plus a state record (`instance-v1`) naming the port and
instance id; a second launch reopens the browser on the healthy instance and never kills
a process because a record names it. Raw output stays in the job's log files; messages
and the API carry text written for a person.

## Trust boundaries

1. **Browser ↔ controller.** Host must be our loopback authority; state-changing requests
   need the per-launch session secret and a same-origin `Origin`. No CORS. Implemented in
   `core/security.py`. Under `/apps/` the secret is a `HttpOnly; SameSite=Strict;
   Path=/apps/` cookie named after the port, required on every request, with
   `Sec-Fetch-Site` limited to `same-origin`/`none` and our `Origin` on state-changing
   requests.
2. **Controller ↔ child apps.** Each child listens on its own loopback port with a
   per-child API token (`core/supervisor.py`). The proxy (`core/proxy.py`) rewrites
   `Host` to the child's port, drops `Origin`, `Referer`, `Cookie` and the session header,
   adds the child's token, and strips `Set-Cookie` and CORS headers from responses. The
   children themselves reject a foreign Host, cross-site requests and a missing token, so
   their ports are safe even though the browser can reach them directly. Child pages share
   the launcher's origin by design, so a child's own scripts are trusted like ours.
3. **Controller ↔ workspace.** Every path goes through `core/workspace.resolve_in_root`.
   Workspace files hold data and options only. The workspace root itself comes from the
   native picker the controller runs (`core/picker.py`): `/api/workspace/*` accepts only
   a folder picked in this session or one on the recent list (paths only, in the app
   state dir).
4. **Controller ↔ tools.** Only installed descriptors are trusted; executables come from
   the pinned runtime record (`runtime-v1`, `core/runtime.py`); arguments are arrays
   (`core/proctree.py`); the environment is allow-listed; cwd and TMPDIR are
   launcher-owned. Children never see the session secret.
5. **Machine ↔ network.** No dataset leaves the machine by default. Logs and diagnostic
   bundles are redacted of subject-level values.

## Workspace contract

```
Study folder/
  source-data/                  optional immutable copies or references
  .opncafyr/
    workspace.json              schemas/workspace-v1 (core/workspace.py, migrations in
                                core/migrations/)
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
