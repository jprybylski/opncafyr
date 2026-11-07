# Architecture

This page summarises the layering in `docs/spec.md` and the trust boundaries the code
enforces. The spec remains the authority on design intent.

## Layers

| Layer | Package | Knows about | Must not |
|---|---|---|---|
| Installer | `installer/` (wrappers), `opncafyr.installer`, `runtime/` | OS, runtimes, pinned packages, checksums, the app root layout | touch study workspaces or machine-wide PATH; import anything outside the standard library |
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
new file format is a new importer in a pack. A pack can also add its own API routes
(`mount(app)`, under `/api/<pack>/`, behind the same security middleware), page assets
(`static`, served at `/static/workflows/<pack>/`) and pages (`views`, listed by
`GET /api/workflows`). The shell adds a sidenav link and an empty view for each and calls
the module's `init(container)`; core never knows what a view does.

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

## Installer (#19)

`installer/install.sh` and `install.ps1` only fetch and verify the pinned Python, then run
`opncafyr.installer` from the payload's wheel. The installer uses the standard library
only (a boundary test checks it), because it runs before any environment exists. Every
file it installs is resolved from the cache, a bundle, an `--assets` folder or a
download, and checked against the pins before anything is staged. Each install goes into
its own `versions/<id>`, which is complete only once `install.json` exists. The
`current` pointer flips atomically, so a failure leaves the previous install active.

A launcher finds its own install from its interpreter's location (`installer.layout`), so
its app root and `runtime.json` are its own even after an update. Details:
[install.md](install.md).

## Process model

```
launcher (uvicorn, 127.0.0.1:<ephemeral>)
 ├── child web apps on private loopback ports, proxied under /apps/<tool>/
 │     opnca-r (plumber2)        deckifyr serve (FastAPI)
 └── jobs (argv arrays, app-controlled PATH, cwd = approved workspace dir)
       Rscript --vanilla …  (cleaning, opnca replay, report render; core/rscript.py)
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
   A boundary test allows `subprocess` only in `core/proctree.py`, `core/picker.py`
   (fixed system dialogs) and `installer/proc.py` (the installer's equivalent of
   proctree, under the same rules), and forbids `shell=True`, `os.system`/`popen`/`exec*`/`spawn*`
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
    cleaned/<recipe-id>-<key>/  clean.R, cleaned.csv, rejected.csv, steps.json (#22)
    runs/<run-id>/              one analyte per run (core/runs.py, #25, #26)
      input/source-fingerprint.json  input/opnca-mapping.json
      input/analysis.csv        frozen, one analyte, read-only
      settings.json             schemas/run-settings-v1, read-only
      analysis.R  analysis-record.json  artifacts.json  COMPLETE  results/
                                written by opnca-r's server-side save (#28)
      logs/                     launcher logs, such as the replay report (#29)
      manifest.json             schemas/run-manifest-v1, sealed when completed
    publications/<id>.json      schemas/publication-v1: exact run IDs and every hash (#31)
    jobs/<job-id>/              schemas/job-v1 state.json, events.jsonl, stdout/stderr
                                logs, diagnostic.json (core/jobmanager.py)
  OUTPUTS/{figures,tables}/     published copies <analyte>_<run-id>_<artifact>.<ext> +
                                <name>_<ext>_metadata.json sidecars (#30, #31)
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

## Cleaning (#22)

A recipe (`.opncafyr/recipes/<id>.yaml`, `recipe-v1`) is an ordered list of steps
`{op, args, note}` for one imported source. Steps are data: `clean_names`, `rename`, `trim`,
`parse_type`, `recode`, `derive` (copy, concat or constant), `derive_elapsed_time`,
`convert_units` (fixed factor tables only), `flag`, `exclude` (rows stay, marked, with a
reason), `filter_rows` (rows move to the rejected-row log, with a reason) and
`report_dupes` (reports, removes nothing). Row conditions are a fixed shape (`equals`,
`in`, `is_blank`, `all`), never an expression. `workflows/nca/recipe.py` adds the rules a
schema can't: derived values always go to new columns and `rename` never reuses a name
the data has had (so nominal time can't replace actual time, even through a swap); no step
writes a numeric zero for a BLQ-looking or blank value; the launcher's columns
(`opncafyr_line`, `opncafyr_excluded`, `opncafyr_exclusion_reason`) can't be changed.

`clean_compile.py` turns a recipe into a standalone `clean.R`: the fixed library
`r/nca_clean_lib.R` inlined, then one call per step. Recipe text appears only as escaped
ASCII string literals (`r_string`). R reads every column as text, keeps each row's
physical line as `opncafyr_line`, and refuses ragged rows or fields spanning lines. Each
run writes `.opncafyr/cleaned/<recipe-id>-<key>/` with `clean.R`, `meta.json`,
`cleaned.csv`, `rejected.csv` (line, step, reason; no values) and `steps.json` (rows in,
out and rejected per step, values changed, columns added, issues with lines, duplicate
groups). The key covers the recipe, source and script hashes and the sample size, so an
old result is never reused for a changed recipe. The job's review checks the files,
`rows_in == rows_out + rows_rejected` for every step, and that the source is unchanged.
A preview is the same script on the first 200 rows. Routes: `/api/nca/recipes...`
(`workflows/nca/api.py`); page: Analysis (`workflows/nca/web/`).

## Column mapping (#23)

The recipe's `mapping` block names the column for each role (`SUBJECT_ID`, `ANALYTE`,
`ACTUAL_TIME`, `CONC`, `DOSE` and `ROUTE` are required; specimen, nominal time, units,
LLOQ, the BLQ/missing/excluded flags, exclusion reason, tau, infusion duration, occasion
are optional), the units of time, concentration and dose (a per-row column or one
declared value) and what each value of a flag column means. It is part of the recipe
hash; the cleaning output is keyed on the steps alone, so a mapping change never re-runs
cleaning.

`workflows/nca/roles.py` suggests a column per role from the cleaned data, ported from
makecoolplots: names (exact alias 0.75, a word inside the name 0.40, capped at 0.55)
plus value checks, each with a reason. Below 0.6, or when two columns fit, the role comes
back as `needs_choice` with the candidates; nominal-time names never suggest actual time.
Nothing is saved until the user picks it. `units.py` normalises spelling only (ADR 0005
Q2); a unit in a header is a hint to confirm. `routes.py` reads routes exactly as opnca-r
does and lists any other value for an explicit recode. `mapping.problems()` blocks on an
unmapped required role, a missing or unknown unit, a flag column without a value table
and a column used for two roles.

## Pre-analysis checks (#24)

`workflows/nca/checks.py` runs every check of ADR 0005 Q7 on the full cleaned data for one
selection: the exact analyte value, then the exact specimen value. A finding is
`{check, level, analyte, scope, subject, lines, message, fix_hint}` plus `column`,
`value`, `values`, `kind` or `role` where they apply; `lines` are physical source lines.
Value checks apply to every selected row (excluded rows still reach opnca-r's
`read.csv`); duplicate keys and profile checks use the rows that are not excluded;
analyte-level checks count every row. An exclusion is the mapped flag table or a recipe
`exclude` step; its reason is the step's, else the reason column, else the one mapped
reason. The fixture's expected findings are the test oracle.

opnca-r's `check_data_format()` also runs on the selection as an R job
(`r/nca_format_check.R`); its issues are merged as info. Blocking findings make
`POST /api/nca/runs` answer 409 `checks_blocking` with the findings; every warning needs
its own acknowledgement (a key over the check, analyte, subject, lines and recipe hash),
or the answer is 409 `warnings_unacknowledged`. Subject IDs read by the checks are
registered with the log redactor.

## Analyte snapshot (#25)

`POST /api/nca/runs` checks and gates the selection, then `workflows/nca/snapshot.py`
writes the run in `.opncafyr/runs/.<run-id>.staging/` and renames it into place
(`<run-id>` is `YYYYMMDDTHHMMSSZ-<4 hex>`):

- `input/analysis.csv`: the rows whose analyte value equals the chosen text exactly,
  then the specimen value; every cleaned column as written, plus `NCA_CONC` (the
  concentration when it is a number, else empty: never 0), `NCA_BLQ`, `NCA_MISSING`,
  `NCA_EXCLUDED` (TRUE/FALSE) and `NCA_EXCLUSION_REASON`. Excluded rows stay. Headers that
  R's `read.csv` would rename, and subject IDs that R would merge as numbers, are refused.
- `input/source-fingerprint.json` (path, size, SHA-256), `input/opnca-mapping.json` (the
  opnca-r preset: every role it knows, `""` for unmapped optional ones, the BLQ rules and
  method the user chose), `settings.json` (`run-settings-v1`: analyte, specimen, mapping,
  units, BLQ choices, acknowledged warnings, row accounting) and `manifest.json`
  (`preparing`).

The input files and `settings.json` are read-only. Row accounting runs from the source
through the recipe, the analyte and specimen filters to the snapshot; other analytes and
specimens are counted, never rejected.

## Run bundles (#26)

`core/runs.py` is the only code that writes a run manifest. Every write is validated
against `run-manifest-v1` and atomic (temp file, fsync, rename): a crash leaves the old
manifest or the new one. The schema requires the spec's minimum fields (source, recipe,
input, analyte, mapping, units, tools); a `reviewing` or `completed` run must also name its
script, opnca-r record, export and artifacts, a completed one its replay and seal, a failed
one its failure. A run is staged in `.<run-id>.staging/` and renamed into place only with
a valid manifest. Its state follows the job states; staying in a state is allowed, other
changes must be legal transitions.

`seal_run` completes a run: every file except the manifest is listed with its SHA-256
and size in `sealed.files`, the manifest is written, then every file (and, on POSIX, every
folder) is made read-only. A sealed manifest can't be written again (`RunSealedError`,
409 `run_sealed`); a change means a new run (`POST /api/nca/runs/{id}/rerun`, linked by
`lineage.parent_run_id`). `verify_run`, `opncafyr runs verify <study> [--run ID]` and
`GET /api/runs/{id}/verify` recompute the hashes and report changed, missing and added
files.

## opnca-r sessions (#27)

`POST /api/nca/runs/{id}/session` (`workflows/nca/session.py`) starts opnca-r's
`scripts/serve.R` on a run that is `preparing` or `running` and has no opnca-r output
yet: `--data-path` is the run's frozen `input/analysis.csv`, `--output-dir` the run
folder, `--mapping` its preset, `--storage-namespace` the run id, with
`--lock-dataset --no-persist-mapping --no-overwrite --require-blq-rules
--require-explicit-settings --no-guess-roles` and the API and shutdown tokens. The preset
is checked against the snapshot's header before launch. The run moves to `running`.

There is one opnca-r child at a time: opening another run stops the current session
first, and the launcher's stop stops it too; with the single-instance launcher that keeps
one session per output folder. The supervisor kills the whole process tree after the
orderly stop and deletes the child's TMP folder. The page reloads the embedded opnca-r
frame when the session changes (`app-restarted`).

## Saving a run (#28)

The user saves in opnca-r's Export view; its server writes `analysis.R`,
`analysis-record.json`, `results/**`, `artifacts.json` and finally `COMPLETE` into the run
folder (opnca-r 0.2.8, `--no-overwrite`). The launcher never reads a browser download: the
Analysis page polls `POST /api/nca/runs/{id}/save-status` for open runs. When `COMPLETE`
appears the session is stopped, then `adapters/opnca/export.py` checks the save: `COMPLETE`
matches `artifacts.json`, every listed file re-hashes, `results/` holds nothing unlisted,
the record names the snapshot's SHA-256 and `input/analysis.csv`, and nothing else sits at
the top of the folder. A lock or staging folder with a live session means "saving"; any
other partial output is a failed save, never a complete one.

A complete save moves the run to `reviewing` with its script, record, artifacts (path,
media type, bytes, SHA-256), opnca-r's settings and effective settings, the opnca-r and R
versions and the exclusions (the launcher's, and any made in the session, which carry no
reason and add a warning). If the session replaced the launcher's exclusion column with
its own (opnca-r#49), the save fails. A failed save moves the run to `failed`; "New run
from this" starts again. `POST /api/nca/runs/{id}/complete` seals a reviewed run once its
replay has passed (#29).

## Replay verification (#29)

A verified save starts `workflows/nca/replay.py` as a job (`nca.replay`, timeout from the
opnca descriptor's `replay_nca` contract). It copies the run's `analysis.R` and
`input/analysis.csv` into the job's own work folder and runs `Rscript --vanilla
analysis.R` there, so nothing is written into the run while R runs; `analysis.R` checks
the input's MD5 and writes `replay/`. The review compares every table of `results/` with
`replay/` (`replay_compare.py`): same columns and rows in the same order; identifiers,
labels, counts, flags and status equal as text; other numbers within a relative tolerance
(default 1e-10, recorded). It writes `replay.json` (`replay-v1`, with a readable list of
differences) and the replayed tables to `runs/<id>/logs/replay-<n>/`. The job fails only
if R fails; a difference is a finished replay with `status: failed`, kept with the run.

The newest finished replay is recorded in the manifest's `replay`. Completing a run needs
a passed replay, so every sealed run holds its own replay evidence, and only completed
runs can be published. Against opnca-r's own `analysis/calculate` response the replay
matches to the 4 decimal places that API writes numbers with.

## Publishing (#30, #31)

`workflows/nca/publish/` turns completed runs into `OUTPUTS/` (user guide:
[publishing.md](publishing.md)).

- **Eligibility.** `curator.py` publishes only runs whose manifest is `completed`, whose
  replay passed, and whose sealed files `verify_run` still finds intact. The API enforces
  this (409 `not_publishable`).
- **Catalog.** `catalog.py` maps a run's saved artifacts to publishable tables and figures.
  The four ADR 0007 types are pre-selected, but the mean concentration–time figure is
  unavailable until opnca-r saves one (mantra-opnca/opnca-r#50).
- **Publishing.**
  1. Files are copied, through a hidden temp file and a rename, to deterministic names.
  2. A name that holds different bytes refuses the whole publication before anything is
     written.
  3. The launcher writes the reportifyr sidecars itself, in Python (`reportifyr-sidecar-v1`;
     BLAKE3 `object_meta.hash`).
  4. The copies and sidecars are checked on disk.
  5. The publication manifest is written last, with `atomic_write_json`.
- **Sidecar checks.** `sidecars.py` runs before every publish, render and build. It checks
  that the sidecar exists, its JSON and schema, its path, file type and hash, and that its
  `meta_type` and abbreviations are defined in `standard_footnotes.yaml`. It returns
  `preflight-v1` findings that name files and checks, never values.
- **Templates.** The bundled `standard_footnotes.yaml` and `config.yaml` are in
  `adapters/quartifyr/template/`.
