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
`schemas/tool-descriptor-v1.schema.json`), a health check declared in it, and an adapter
package: a `core.adapter.WebAppAdapter` from `web_app()` for a web app, and
`CommandSpec` builders that a workflow submits as `JobRequest`s for jobs. A future
workflow (e.g. a non-NCA analysis) is a new package under `workflows/` that composes
existing adapters. Neither requires changes to `core/`. [adding-a-tool.md](adding-a-tool.md)
is the guide. The toy `adapters/example_echo/` (#41) is a whole second tool, with a job and
a web app, added without any core change; release wheels leave it out.

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
probe, an optional web app, job contracts (each schema is bundled in the adapter's own
`schemas/` folder or with the launcher, never both; a `JobRequest` can have the job
manager check an output document against the contract before the job completes) and optional
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

   A child that changes workspace files itself declares `web_app.guard_mutations`
   (#56; today gitneighbr, ADR 0008). The proxy then refuses its state-changing
   requests (423, `core/proxy.py` `mutation_refusal`) while a job runs, while another
   child reports unsaved or busy work, or while a page holds unsaved editor text.
   Otherwise the request holds the workspace lease (`core/workspace_lease.py`) until
   its reply has been sent, and jobs and project-file saves refuse to start
   meanwhile. A child started with a `workspace_root` session key serves that folder
   only, and `core/workspace_api.py` stops it when another workspace opens
   (`Supervisor.stop_bound_elsewhere`).
3. **Controller ↔ workspace.** Every path goes through `core/workspace.resolve_in_root`.
   Workspace files hold data and options only. The project-file editor (#52) writes only
   files its tool's adapter lists, never through a link, outside `OUTPUTS/` and hidden
   folders, and only against the revision the user opened. The workspace root itself comes from the
   native picker the controller runs (`core/picker.py`): `/api/workspace/*` accepts only
   a folder picked in this session or one on the recent list (paths only, in the app
   state dir).

   In cloud container mode (#42, ADR 0009) the browser is on another computer and
   reaches the container through an SSH forward or an IDE proxy. `core/remote.py` turns
   explicit `launch --container` options (never environment variables) into extra
   Host/Origin entries and a URL prefix. `core/security.py` adds them to the allow-lists,
   scopes the cookie to the prefix, and `BasePathMiddleware` accepts the prefix whether
   or not the proxy strips it. Every check above still applies. The port is published
   to the VM's loopback only; behind an IDE proxy the IDE's login is the boundary, so v1
   assumes one user per VM. `--workspace` fixes the one study folder (no picker), and
   `core/storage.py` refuses object-storage FUSE mounts, which fake atomic rename.
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
   Job logs stay in the study workspace with the run; the support bundle (#38) never
   includes them, only their sizes.

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
    report.json  deck.json      schemas/publish-binding-v1: the project's publication (#32, #33)
    publish-checks/<id>/        preflight.json (schemas/preflight-v1) of a report check
    jobs/<job-id>/              schemas/job-v1 state.json, events.jsonl, stdout/stderr
                                logs, diagnostic.json (core/jobmanager.py)
  OUTPUTS/{figures,tables}/     published copies <analyte>_<run-id>_<artifact>.<ext> +
                                <name>_<ext>_metadata.json sidecars (#30, #31)
  report/                       quartifyr project bound to one publication (#32):
                                report.qmd, _quarto.yml, report/, _extensions/, OUTPUTS/ copy
    builds/<build-id>/          report-<draft|final>.docx, render-result.json, build.json
                                (schemas/publish-build-v1)
  deck/                         deckifyr project bound to one publication (#33):
                                presentation.yaml, design.yaml, layouts.yaml,
                                standard_footnotes.yaml, OUTPUTS/ copy; build/ (editor drafts)
    builds/<build-id>/          the PPTX, deck-manifest.json, previews/, build-result.json,
                                build.json (schemas/publish-build-v1)
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

### Event records (#58)

An optional `events` block (recipe `schema_version` 1.1; ADR 0005 Q9) adds a fixed stage
after the steps for NONMEM-style files with separate dose and observation records. It
names the subject, time, record-kind, amount, endpoint and route columns, plus optional
DV, MDV, occasion, dose-unit and time-unit columns. It gives the meaning of every
record-kind, endpoint and MDV value, and holds the user's choices: dosing interval,
same-time doses, predose samples, samples at a dose time, and unmatched observations.

The compiler appends the fixed library `r/nca_events_lib.R` and the block as string
literals to the same `clean.R`, so the script still replays the whole preparation offline.
Recipes without events compile byte for byte as before.

The stage puts every record in one partition: selected, `predose_set_aside`,
`outside_interval`, `dose`, `other_endpoint`, `other_record`, `rejected` (only by the
`reject` choice; appended to `rejected.csv` as step n+1) or `unresolved` (with a check
id). It writes three files:

- `prepared.csv`: the selected PK observations, raw columns unchanged, plus the
  reserved `opncafyr_dose`, `_dose_unit`, `_route`, `_dose_time`, `_time_after_dose`,
  `_dose_number` and `_dose_lines` columns;
- `records.csv`: line, partition, category and dose lines; no values;
- `events.json`: the counts and the unresolved groups.

`cleaned.csv` stays every row after the steps, which the event page reads for the code
counts. The job's review also checks that the partitions add up to the rows after the
steps, that `records.csv` lists each line once, and that `prepared.csv` and
`rejected.csv` agree with the counts. Mapping, checks, the analyte list, opnca-r's format
check and the snapshot read `prepared.csv` for an event recipe (`cleaning.analysis_table`).

`workflows/nca/events.py` suggests the layout, columns and EVID meanings with reasons; it
never applies them. It also turns `events.json` into blocking findings, global to every
analyte, each naming the choice or recipe fix that settles it. Routes:
`GET/PUT /api/nca/recipes/{id}/events-help` and `.../events`. A steps save keeps the
saved block.

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
and a column used for two roles. With an events block it also blocks unless actual time,
dose, route and subject use the prepared columns and the event subject
(`event_mapping_mismatch`), and while TAU or infusion duration is mapped
(`event_role_unsupported`). The prepared columns are suggested with that reason.

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
registered with the log redactor (for event records also those of `cleaned.csv`, since a
subject with dose records only is not in the prepared rows). For an event recipe, every
unresolved record in `events.json` blocks every analyte, an analyte column that keeps two
endpoint codes blocks (`analyte_endpoints_mixed`), and a mapped nominal time is a warning
(`event_nominal_basis`).

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
  method the user chose, and the unit labels), `settings.json` (`run-settings-v1`:
  analyte, specimen, mapping, units, BLQ choices, acknowledged warnings, row accounting)
  and `manifest.json` (`preparing`).

The unit labels (`analysis_units`, #57) are the units of the frozen rows:
`units.analysis_labels` takes each role's declared unit, or the one unit its column holds
across the selected rows, in the spelling table's form. A missing, unknown or mixed unit
refuses the run (`units_unresolved`; the checks block it first). opnca-r 0.3.1 locks the
preset's labels in its page and refuses a request with others (opnca-r#56). It uses them
for every result, table, axis, `analysis.R` and `effective_settings.units`. Labels are
metadata; nothing is converted. The manifest and settings record them next to the
declared `units`.

For an event recipe the run also keeps `input/event-records.csv` (the cleaning result's
`records.csv`) and `input/prepare.R` (its `clean.R`). `settings.json` and `manifest.json`
are then `schema_version` 1.1: settings gain a `preparation` block (the events block,
its hash, the partition counts and both files' hashes) and the manifest's lineage gains
`events_sha256`. Row accounting adds `rows_rejected_by_events` and the `events_*`
partitions. Other runs are unchanged.

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
folder (opnca-r 0.3.1, `--no-overwrite`). The launcher never reads a browser download: the
Analysis page polls `POST /api/nca/runs/{id}/save-status` for open runs. When `COMPLETE`
appears the session is stopped, then `adapters/opnca/export.py` checks the save: `COMPLETE`
matches `artifacts.json`, every listed file re-hashes, `results/` holds nothing unlisted,
the record names the snapshot's SHA-256 and `input/analysis.csv`, and nothing else sits at
the top of the folder. A lock or staging folder with a live session means "saving"; any
other partial output is a failed save, never a complete one.

A schema-2 `artifacts.json` must also validate against the schema opnca-r installs in the
app's R library (#59).

A complete save moves the run to `reviewing` with its script, record, artifacts (path,
media type, bytes, SHA-256, and opnca-r's schema-2 description of each file), opnca-r's settings and effective settings, the opnca-r and R
versions, the mean-profile choices when the save has the mean figures, and the
exclusions. The launcher's exclusions are the snapshot's `NCA_EXCLUDED` rows; points
excluded in the session are added to them (opnca-r#49, 0.2.9), carry no reason and add a
warning. A save whose record no longer maps `excluded` to `NCA_EXCLUDED` while the run has
launcher exclusions fails, because those rows would have been dropped. So does a save whose
record's `effective_settings.units` are not the run's `analysis_units` with
`units_source: "preset"` (#57); a run made before 0.3.1 has no labels, and opnca-r won't
open it (`run_outdated`). A failed save moves the run to `failed`; "New run
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
(default 1e-10, recorded). A table that opnca-r's `artifacts.json` (schema 2) marks
`replay.reproduced: false`, such as the bioequivalence tables, is not compared. It is
listed in `not_reproduced` with opnca-r's reason. It writes `replay.json` (`replay-v1`, with a readable list of
differences) and the replayed tables to `runs/<id>/logs/replay-<n>/`. The job fails only
if R fails; a difference is a finished replay with `status: failed`, kept with the run.

The newest finished replay is recorded in the manifest's `replay`. Completing a run needs
a passed replay, so every sealed run holds its own replay evidence, and only completed
runs can be published. Against opnca-r's own `analysis/calculate` response the replay
matches to 1e-10; since opnca-r 0.3.0 that API no longer rounds numbers.

## Publishing (#30, #31)

`workflows/nca/publish/` turns completed runs into `OUTPUTS/` (user guide:
[publishing.md](publishing.md)).

- **Eligibility.** `curator.py` publishes only runs whose manifest is `completed`, whose
  replay passed, and whose sealed files `verify_run` still finds intact. The API enforces
  this (409 `not_publishable`).
- **Catalog.** `catalog.py` maps a run's saved artifacts to publishable tables and figures.
  For a run saved by opnca-r 0.3.1, titles, "shows subject IDs" and notes come from
  opnca-r's description kept in the manifest (#59). Tables the replay didn't check aren't
  offered, and formatted `.rds` tables are offered next to their CSV. Older runs use path
  rules. The four ADR 0007 types are pre-selected, including the mean concentration–time figures
  that opnca-r saves since 0.2.9 (mantra-opnca/opnca-r#50). Their sidecar notes state the
  record's choices (statistic, error, time basis). A run saved without them says so.
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

## Reports (#32)

`workflows/nca/publish/report.py` runs quartifyr as jobs. The quartifyr adapter
(`adapters/quartifyr/`) only builds the commands and reads the results.

- **Setup** (`report.setup`): `r/quartifyr_setup.R` calls
  `initialize_report_project_offline()` and the bundled-extension installers. The
  review then writes the template, `report.qmd` and the `OUTPUTS/` copy, and the binding.
  `report/` appears only when all of that worked.
- **Check** (`report.check`): the sidecar checks plus `check.R --json`, run on a staging
  copy. `check.R` exits 1 when it finds an error, so the job accepts exit codes 0 and 1
  (`JobRequest.ok_exit_codes`).
- **Render** (`report.render`):
  `render.R --offline --json --quarto-bin <pinned> --reference-doc <abs>` (no `--strict`: `parse_render` is the gate and accepts a `warning` only when it is quartifyr's "table too wide for the page" one, #62).
  - The job runs in `report/builds/<id>.staging/work`, because Quarto refuses a project
    inside a hidden folder.
  - Inputs are hashed before the run.
  - Success means exit 0 and `status: ok`. The DOCX the result lists is re-hashed, then
    copied out, and the work copy is removed before promotion.
  - On a non-zero exit, `JobRequest.diagnose` turns quartifyr's JSON error (`error_class`,
    `errors`, missing metadata) into the job's message, without absolute paths.
- **Environment**:
  - `QUARTIFYR_PYTHON_ENV` = the tools env;
  - `UV_OFFLINE`, `UV_NO_INDEX`, `UV_NO_SYNC`, `UV_NO_PROJECT`, `UV_NO_CONFIG`,
    `UV_PYTHON_DOWNLOADS=never`;
  - `UV_CACHE_DIR` and `DENO_DIR` under `<app root>/cache`;
  - a throwaway `HOME` holding a git identity;
  - the pinned uv first on `PATH`.
- **Limits.** Time limits come from the quartifyr descriptor's `check_report` and
  `render_report` contracts; the job manager kills the whole tree. Report jobs refuse to
  start while another one runs.

## Decks (#33)

`workflows/nca/publish/deck.py` runs deckifyr. The deckifyr adapter
(`adapters/deckifyr/`) builds the commands, reads the results, writes the "NCA results"
`presentation.yaml` and runs the LibreOffice preview probe.

- **Setup** (`deck.init`): `python -I -m deckifyr --json init <staging> --force`, using
  the bundled minimal deck. The review writes the generated deck, the footnotes, the
  `OUTPUTS/` copy and the binding.
- **Editor**: `supervisor.start("deckifyr", {project, presentation, job_timeout})`.
  - The editor's own job limit is kept below the launcher's build limit.
  - Opening it restores `deck/OUTPUTS/` to the publication's files.
  - Rebinding is refused while it runs.
  - Check and build are refused while the editor reports unsaved edits.
- **Check and build** (`deck.validate`, `deck.build`) run `--json validate|build --strict
  --trusted-binaries-only` with the pinned `--rscript-bin`, `--quarto-bin` and
  `--soffice-bin`.
  - Both work on a staging copy: the project minus `build/`, `builds/` and `OUTPUTS/`, plus
    the publication's files checked against their hashes.
  - A build runs in `deck/builds/<id>.staging/work`. It needs `build.manifest`, and its
    `build.previews` follows the request; only the staging copy is changed.
  - It counts only when the exit code is 0, `status` is `ok`, and both the manifest's and
    the result's PPTX SHA-256 match the file. Previews come from the manifest's list.
  - Non-zero exits are explained from deckifyr's stderr JSON (`code`, `message`), without
    absolute paths. Exit 2, signals and non-JSON output are unknown failures.
- **Shell**: `js/tools.js` refreshes the app list when a workflow emits `app-restarted`,
  so the embedded editor shows as soon as it is ready.

## Project-file editor (#52)

A generic launcher capability: a tool opts in, and the launcher's own page edits the
tool's project sources. Core knows files, revisions and locks, never what the files mean.

- **Opting in.** The descriptor declares `project_editor: {capability, project_root,
  file_profile, settings_schema?}` (an optional key of `tool-descriptor-v1`). The values
  are ids, never globs, commands or paths. The adapter package's `project_editor()`
  returns a `core.adapter.ProjectEditor`, found by package scan like `web_app()`.
  Startup stops unless the adapter bundles the named profile and settings schema
  (`registry.check_project_editors`).
- **The adapter decides** which files to offer (`files()`), with a label, a syntax and a
  byte cap; advisory warnings for saved text (`check_text()`); and its settings: the
  file, the form fields and the versioned document (`settings_*`).
- **Core enforces** (`core/projectfiles.py`), whatever the adapter lists:
  - paths are relative, without `..`, backslashes, hidden parts or an `OUTPUTS` part,
    inside the workspace, with every part `lstat`-ed so no link is followed;
  - the file is on the adapter's current list and is a regular file;
  - UTF-8 only, a kept BOM, CRLF converted back on save, files mixing line ends
    read-only, the byte cap on read and save;
  - a save sends the SHA-256 it opened; under the project's lock the file is hashed
    again (409 `conflict` with `current_sha256` on a mismatch), written through a
    synced temp file and a rename that keeps its permissions, then read back and
    hashed. Logs carry the tool, the relative path and byte counts, never text.
- **API** (`core/projectfiles_api.py`): `GET /api/tools/<id>/project`, then
  `POST …/read`, `…/save` and `…/settings`. Paths and text travel in JSON bodies only.
  `/api/tools` lists `project_editor: {title}` so the tool tile offers **Edit project
  files**.
- **Page** (`web/static/js/editor.js`, `#/tools/<id>/files[?path=<file>]`):
  - **source mode**: line numbers, find, the browser's undo, Save (Ctrl/Cmd+S) and Discard;
  - **visual mode** for Markdown, Quarto and R Markdown: `mdblocks.js` cuts the file into
    exact slices. Only plain headings, paragraphs and lists are editable; front matter,
    chunks, fences, divs, shortcodes, braces, inline code, citations, maths, tables, HTML
    and anything unknown are locked. An edit is accepted only if every locked slice is
    byte-for-byte unchanged;
  - **preview** built from DOM nodes: no `innerHTML`, inert links, nothing run.

  A conflict keeps the user's text in the editor and shows the disk version next to it.
- **New files** (optional, `SupportsNewFiles.new_file_rules()`). Each rule gives a folder,
  a suffix, a label and starter text. The user types only a name: letters, digits, `-`
  and `_`, up to 64. Core then:
  - checks the path with the same rules as for editing, and creates the rule's folder
    if it is missing (never through a link);
  - writes a synced temp file and hard-links it into place, so an existing file is
    never replaced (409 `exists`). Without hard links it falls back to an exclusive
    create;
  - removes the file again if the adapter doesn't then list it.
- **Unsaved text** (`unsaved.js`) goes with every heartbeat (`unsaved: [tool ids]`), and
  the page keeps beating while hidden. The lifecycle counts it like a child's unsaved
  edits (blocks idle shutdown for up to 15 minutes without a heartbeat) and
  `Lifecycle.page_unsaved(tool)` lets a workflow wait. Quit asks: **Save and quit** saves
  the page's editors first, and **Discard and quit** (`unsaved: "discard"`) drops them.
- **Snapshots.** A workflow that copies a project for a job holds
  `app.state.project_files.lock(root, tool)` while copying, so a save never lands
  half-way through, and a later edit can't change that job's inputs.
- **Trust.** Report and deck projects are trusted, user-authored projects (owner
  decision, docs/plan.md risk table), so source mode may change chunks and shortcodes;
  they run only through the pinned binaries on a launcher staging copy.

**The report** (quartifyr adapter, `adapters/quartifyr/editor.py`) offers `report.qmd`
and any other `.qmd`/`.rmd`/`.md` file at the top of `report/` (nothing in subfolders,
so not `builds/`, `OUTPUTS/`, `_extensions/` or quartifyr's outputs), up to 256 KiB.
Its settings (`report-settings-v1`, bundled with the adapter) are kept in
`report/opncafyr-settings.json`: the document status and the reference DOCX. The report
workflow:
- renders with the request's status, else the saved one, else asks;
- refuses check, render and re-binding while the report editor has unsaved text;
- records `status`, `status_from`, `reference_doc` and `settings_sha256` in
  `build.json`, with the settings file as an input;
- lists each build's `stale_inputs` in its status, so the screen marks a build out of
  date without rewriting it.

**The deck** (deckifyr adapter, `adapters/deckifyr/editor.py`) offers its Quarto
fragments. deckifyr places one on a slide with a `type: quarto` element whose `source` is
a `.qmd` file in the project (deckifyr spec §8.1). deckifyr's own editor can choose an
existing fragment but can't edit its text.
- Every `.qmd` in `deck/` is offered, except under `build/`, `builds/`, `previews/`,
  `OUTPUTS/`, `_extensions/` and hidden folders. `presentation.yaml` stays with deckifyr's
  editor, which holds the deck in memory while it runs.
- **New fragment** creates `deck/fragments/<name>.qmd` from short starter text. A
  `quarto` element in deckifyr's editor can then use it.
- `check_text()` warns about what deckifyr's strict check rejects: a slide break
  (`---`, `***`, `___`) or more than one top-level `#` heading, outside front matter and
  code fences. `deckifyr validate --strict` stays the authority.
- The deck workflow copies `deck/` under the editor lock, and refuses check, build and
  re-binding while a page reports unsaved fragment text, as it already does for unsaved
  edits in deckifyr's editor.

The test-only `toy_notes`
tool (`tests/toy_notes.py`) shows a second tool using the editor with no change to core
or the generic page.

## Support bundle (#38)

**Settings → Support → Create support bundle…** and `opncafyr doctor --bundle` collect
what support needs to diagnose a problem into one zip, `<app root>/logs/support/
support-<time>.zip` (`core/support.py`, `core/support_api.py`). It is saved on this
machine only; nothing is uploaded. The page previews every entry, read-only, before
anything is written, and Save writes exactly the previewed bytes. `doctor --preview`
prints it without saving, and `doctor --bundle` asks first (`--yes` to skip;
`--workspace DIR` adds a study folder's jobs).

| Entry | Contents |
|---|---|
| `README.txt` | What is in the bundle and what isn't; the research-use notice |
| `system.json` | opncafyr, Python, OS, architecture, free disk, active install id |
| `install.json` | the active install's record (version, mode, options, components and versions, smoke results); whether its installer payload still matches its hashes and the record (`installer.payload.load`); missing runtime files (`quick_check`); installed and incomplete ids; the latest installation report; the names of `install-failure-*.zip` bundles and of the latest install's step logs |
| `runtime.json` | each runtime executable's file name and whether it exists |
| `tools.json` | each tool's version, capabilities, health and web-app state |
| `jobs.json` | for the open study: its folder name, counts of what `.opncafyr/` holds, and the 20 most recent jobs (kind, pool, state, reason, exit code, times, time limit, the short message, `diagnostic.json` flags, and the sizes of `stdout.log`/`stderr.log`) |
| `logs/launcher.log` | the last 500 records of the launcher log |
| `logs/install/installer.log` | the end of the latest install, update or repair log |
| `logs/children/<tool>.log` | the last 100 lines of each child app's log |

Never included: dataset content, snapshots, results, job `stdout.log`/`stderr.log`
content, job labels and outputs (they hold file names), file listings of the study, the
recent-workspaces list and the environment.

Every free-text value (job messages, log lines) is scrubbed:
- secrets and tokens are masked (`installer.support.redact`, `core.redaction`);
- the study folder's path becomes `<study:NAME>`, the home folder `~`, the user name
  `<user>`;
- identifier values become `‹redacted›`: those registered in the launcher, plus those
  each workflow pack's `sensitive_values(workspace)` hook finds. The NCA pack returns
  every value of the columns mapped to `SUBJECT_ID` and `SECONDARY_ID` in its cleaned
  output, so the CLI masks them even without the study open;
- every decimal number becomes `‹n›`, so a concentration quoted in an error can't get
  through. Dotted versions (`4.6.2`) and ids are kept.

Structured fields (versions, ids, states, exit codes, times) are copied unchanged.
Installer logs hold no study data, so they get only the path and secret rules.
`tests/test_support_bundle.py` and `tests/integration/nca/test_support_bundle_r.py` build
a bundle after running the two-analyte fixture and check that none of its subject IDs or
concentrations is in it.
