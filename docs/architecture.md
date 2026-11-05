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
completed` (+ `failed`, `cancelled`).

## Trust boundaries

1. **Browser ↔ controller.** Host must be our loopback authority; state-changing requests
   need the per-launch session secret and a same-origin `Origin`. No CORS. Implemented in
   `core/security.py`.
2. **Controller ↔ workspace.** Every path goes through `core/workspace.resolve_in_root`.
   Workspace files hold data and options only.
3. **Controller ↔ tools.** Only installed descriptors are trusted; executables come from
   the pinned runtime; arguments are arrays. Children never see the session secret.
4. **Machine ↔ network.** No dataset leaves the machine by default. Logs and diagnostic
   bundles are redacted of subject-level values.

## Workspace contract

```
Study folder/
  source-data/                  optional immutable copies or references
  .opncafyr/
    workspace.json              schemas/workspace-v1
    recipes/<recipe-id>.yaml    schemas/recipe-v1
    runs/<run-id>/
      input/source-fingerprint.json
      input/analysis.csv        frozen, one analyte
      analysis.R                replayable, relative input path
      settings.json  analysis-record.json  results/  artifacts/  logs/
      manifest.json             schemas/run-manifest-v1
    publications/<id>.json      schemas/publication-v1
  OUTPUTS/{figures,tables}/     reportifyr-compatible copies + *_metadata.json sidecars
  report/                       quartifyr / Quarto project
  deck/                         deckifyr project
```
