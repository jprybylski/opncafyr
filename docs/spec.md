# Build a reusable local pharmacology workspace

**Status:** Proposed technical specification, 3 November 2026  
**Audience:** Product owner, maintainers of `opnca-r`, `deckifyr`, `quartifyr`, and `reportifyr`, and implementation team  
**Scope:** Windows and macOS, single user, local execution, installation without administrator privileges

**Recommendation.** Ship a small per-user installer and a persistent launcher that starts one loopback web origin, then opens the user's browser to a workspace home page. The installer owns pinned Python, R, Quarto, application packages, and, where source builds remain necessary, a user-local Rust toolchain. The launcher owns process lifetime, selected workspace, jobs, and artifact provenance; each scientific or publishing tool remains responsible for its own calculations and rendering. The first release should favor tested platform-specific R package binaries over compiling `opnca-r` on every user's machine. The current `opnca-r` releases are source archives, and its Rust-backed source install has a genuine compiler and linker dependency. The principal integration work is a durable, per-analyte `opnca-r` save contract: the present app holds one dataset in memory and downloads exports through the browser, which cannot reliably populate a reproducible workspace. This specification keeps the product explicitly research-use until a separate intended-use and validation program is defined. ([opnca-r releases](https://github.com/mantra-opnca/opnca-r/releases), [opnca-r source build](https://github.com/mantra-opnca/opnca-r/blob/main/src/Makevars.win.in), [opnca-r README](https://github.com/mantra-opnca/opnca-r/blob/main/README.md))

## The launcher connects existing tools through files and jobs

The proposed product is a **local workspace application**. A user runs `install.bat` on Windows or `install.sh` on macOS once, without an elevated prompt. They later click a desktop icon, choose an existing study directory or create one, inspect a dataset, preview optional cleaning, select exactly one analyte, and enter `opnca-r` with that filtered input. They can repeat this process for other analytes. Each analysis creates its own executable R script, input snapshot, settings, results, and manifest. A report or deck may intentionally combine selected completed runs, but the scientific scripts never combine analytes implicitly. The home page also offers guided report creation, artifact review, and a managed `deckifyr` editor.

The division of work follows the existing projects. `opnca-r` exposes `opnca_app(data = ..., host = ..., port = ..., browse = ...)`, live calculation, QC, code generation, and browser export. Its `data` argument is a data frame rather than a durable dataset path or analyte selection; upload currently targets a single in-memory dataset with a 5 MiB limit. Export creates CSVs, figures, an analysis record, and an R script as browser downloads; for an uploaded CSV, the generated script contains a placeholder input path. A launcher adapter therefore cannot treat the current downloads as a dependable handoff. ([opnca-r app entry](https://github.com/mantra-opnca/opnca-r/blob/main/R/app.R), [opnca-r server](https://github.com/mantra-opnca/opnca-r/blob/main/R/app-server.R), [opnca-r export](https://github.com/mantra-opnca/opnca-r/blob/main/inst/www/export-view.js), [opnca-r code generation](https://github.com/mantra-opnca/opnca-r/blob/main/inst/www/r-code.js))

`deckifyr` already offers a local FastAPI and React editor through `deckifyr serve`, plus validation, build, preview, and other CLI operations. `quartifyr` coordinates a Quarto DOCX shell and a `reportifyr` filling pass; `reportifyr` expects tables and figures with metadata sidecars under `OUTPUTS/`. This makes `deckifyr` the natural embedded child web app, while the other two are better presented initially as guided launcher forms backed by noninteractive jobs. `deckifyr` build does not inherently require LibreOffice, but slide image previews do. Quarto elements require Quarto. `quartifyr`'s optional LibreOffice field recalculation is off by default because of hangs reported in its implementation. ([deckifyr README](https://github.com/jprybylski/deckifyr/blob/main/README.md), [quartifyr README](https://github.com/jprybylski/quartifyr/blob/main/README.md), [quartifyr render function](https://github.com/jprybylski/quartifyr/blob/main/R/render-report.R), [reportifyr README](https://github.com/A2-ai/reportifyr/blob/main/README.md))

| Product boundary | Responsibility | Explicitly excluded from this layer |
|---|---|---|
| Installer | Acquire and verify pinned runtimes and packages; create icon; repair or roll back installation | Study data and analysis state |
| Launcher/controller | Workspace selection, safe file access, process supervision, job queue, one web origin, provenance, UI | NCA algorithms, DOCX/PPTX layout engines |
| Tool adapters | Translate a versioned job contract into each tool's command/API; normalize artifacts | Shared mutable scientific state |
| `opnca-r` | Data QC, interactive NCA, code and analysis export | Global workspace navigation |
| `quartifyr`/`reportifyr` | DOCX shell, table/figure placement, metadata, final document | Re-running or silently changing NCA |
| `deckifyr` | Deck configuration, browser customization, PPTX build and preview | Source-data cleaning or NCA |

**Reusable framework principle:** the controller knows capabilities and file contracts, not package-specific screens or calculation logic. Tool-specific adapters are independently versioned. A future tool can add a manifest, a health check, and a job or web-app adapter without changing the launcher navigation or workspace schema.

## One local origin owns every app and its lifetime

The launcher should be a Python application in its own `uv` environment, with FastAPI/Starlette as the local controller. It binds only to `127.0.0.1` on a free port, starts its own `/health` endpoint, opens the default browser after health succeeds, and serves home, API, and child applications beneath one origin such as `http://127.0.0.1:<port>/`. FastAPI lifespan hooks can own startup and shutdown of the managed child processes. This choice reuses the Python runtime needed by `deckifyr` and leaves a Tauri shell as a later option if native-window behavior becomes essential. ([FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), [Python browser opening](https://docs.python.org/3/library/webbrowser.html), [Tauri sidecars](https://v2.tauri.app/develop/sidecar/))

```text
Desktop icon
   ↓
Launcher process ───── single-instance lock + health/state record
   │
   ├── FastAPI UI/API on 127.0.0.1:<ephemeral port>
   │      ├── /                         workspace home and guided forms
   │      ├── /api/...                  workspace, jobs, manifests, shutdown
   │      ├── /apps/opnca/...          managed opnca-r process via proxy
   │      └── /apps/deckifyr/...       managed deckifyr process via proxy
   │
   ├── Rscript jobs                    cleaning, NCA replay, report builds
   └── uv/Python jobs                  deck validation, builds, previews
```

The controller must support HTTP and, if a child uses them, WebSocket proxying. Each child gets a private loopback port and only a launcher-approved workspace path. `opnca-r` currently calls root-relative `/api/v1/...` routes and checks Host headers; `deckifyr` has not been demonstrated behind a prefix. **A base-path and proxy compatibility spike is a release dependency, not an assumed working feature.** The preferred fix is a configurable base path in each child; a proxy rewrite can be a temporary adapter if all assets, API calls, redirects, and live connections pass automated checks. ([opnca-r server](https://github.com/mantra-opnca/opnca-r/blob/main/R/app-server.R), [opnca-r frontend](https://github.com/mantra-opnca/opnca-r/blob/main/inst/www/codegen.js), [deckifyr CLI](https://github.com/jprybylski/deckifyr/blob/main/inst/python/deckifyr/cli.py))

**Launch and stop contract.** A second icon click reopens a healthy existing instance; a stale lock is removed only after its recorded PID and health probe fail. The browser sends visible-page heartbeats and offers an explicit **Quit application** action. Browser close triggers a best-effort final signal, but cannot be the sole shutdown mechanism because page-unload events are not reliable. After a configurable idle period with no active browser session and no running job, the launcher stops child apps, closes its listener, and clears its state record. A long report render prevents idle shutdown. If the user explicitly quits during a job, the UI asks whether to keep the job running until completion or cancel it; the default is to keep it running and close when finished. An unsaved editor state blocks automatic idle shutdown. ([MDN page lifecycle](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Deferred_Fetch))

The local URL is still a security boundary. Generate a fresh random session secret at launch, require it for state-changing requests, check Host and Origin, reject wildcard CORS, and bind child processes to loopback only. Canonicalize every requested file path under the selected workspace; pass subprocess arguments as arrays rather than interpolated shell strings; allow commands only from installed, trusted adapters. A workspace file must never be able to specify an arbitrary executable. Avoid putting raw patient or subject values in application logs, telemetry, URLs, or diagnostic bundles. The default behavior sends no dataset to a remote service. These are design requirements based on standard Host and CSRF protections, not claims about the current child apps. ([Starlette middleware](https://www.starlette.io/middleware/), [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html))

The launcher should expose a small persisted job state machine: `queued → preparing → running → reviewing → completed`, with `failed` and `cancelled` terminal states. Only `completed` artifacts can be published to a report or deck. Job events and logs are saved locally, with a progress message written for a person rather than only raw command output. Cancellation terminates the owned process tree and leaves a diagnostic record; an incomplete output directory is never mistaken for a successful run.

## Installation remains per-user even when builds need Rust

The installer is an idempotent bootstrapper, while the persistent application is the launcher. Its target roots are `%LOCALAPPDATA%\<Product>` on Windows and `~/Library/Application Support/<Product>` on macOS. It creates a Windows `.lnk` or a small macOS `.app` wrapper that runs the launcher by absolute path. It uses an application-controlled child-process `PATH`, so the app can find Quarto, R, Cargo, and `uv` without changing the machine-wide PATH or disturbing other projects. If a user wants Quarto on their personal PATH for external use, that can be an explicit optional action. Windows desktop shortcuts can be created through the user's shell; macOS app bundles support Finder icons through `Info.plist` and `CFBundleExecutable`. ([Microsoft shortcut example](https://learn.microsoft.com/en-us/powershell/scripting/samples/creating-.net-and-com-objects--new-object-?view=powershell-7.3), [Apple app bundle layout](https://developer.apple.com/documentation/bundleresources/placing-content-in-a-bundle))

The script should first detect OS, architecture, existing app version, write permissions, disk space, download access, and whether local executables are permitted. It should then download or read from a release bundle **version-pinned assets with checksums**, install into a new staging directory, restore dependencies, run package-specific smoke tests, create the icon, and atomically activate the new version. A failed install retains the previous active version and produces a support bundle with commands, versions, exit codes, and redacted logs. A `repair` mode repeats verification and rebuilds only damaged components; an `uninstall` mode removes app-owned files and the icon while leaving study workspaces untouched. Signed Windows and notarized macOS distributions reduce execution warnings but do not change the no-admin architecture; organizational execution policies may still block them. ([Microsoft SmartScreen](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution))

| Component | Per-user installation contract | Verification |
|---|---|---|
| `uv` and Python | Install a pinned `uv` binary; direct managed Python and cache to app-owned paths; restore `uv.lock` with a pinned Python version and `uv sync --locked`. | Interpreter path/version, lock consistency, import/CLI smoke tests. ([uv installation](https://docs.astral.sh/uv/getting-started/installation/), [uv sync](https://docs.astral.sh/uv/concepts/projects/sync/)) |
| R | Install a pinned version with `rig add` in **per-command user mode**, using the version in `renv.lock`; request `--no-default --without-path` so the user's existing default R is not changed. Never invoke `rig system user-mode`, which is a migration operation. | Absolute `Rscript` path, version, architecture, ability to load the private library. ([rig user mode](https://rig.r-lib.org/admin-vs-user-mode.html), [rig add](https://rig.r-lib.org/reference/add.html)) |
| R packages | Restore `renv.lock` in a private app-version library. Put pinned custom archives in the `renv` cellar; use compatible platform binary archives where native code exists. | `renv::status()`, package load, minimal tool-specific call. ([renv restore](https://rstudio.github.io/renv/reference/restore.html), [renv cellar](https://rstudio.github.io/renv/articles/cellar.html)) |
| Quarto | Extract a pinned Windows ZIP or macOS tarball under the app root and prepend its binary directory to managed jobs' PATH; set `QUARTO_R` and `QUARTO_PYTHON` explicitly for render jobs. | `quarto --version` and small DOCX render. ([Quarto downloads](https://quarto.org/docs/download/), [Quarto project environments](https://quarto.org/docs/projects/environment.html)) |
| Rust build fallback | Install a user-local pinned rustup/Cargo toolchain only when a release requires a source build, then preflight linker and target. On Windows, use the package's documented GNU/Rtools path; on macOS, check required developer tools. | `rustc -vV`, Cargo version/target, R build configuration, compile and load a staged `opnca-r` package. ([Rustup installation](https://rust-lang.github.io/rustup/installation/), [opnca-r Windows CI](https://github.com/mantra-opnca/opnca-r/blob/main/.github/workflows/R-CMD-check.yaml)) |
| LibreOffice | Optional capability for deck slide previews and optional DOCX field recalculation; either bundle a supported per-user distribution after a packaging spike or label those features unavailable. | Preview smoke test if enabled. ([deckifyr README](https://github.com/jprybylski/deckifyr/blob/main/README.md), [quartifyr render](https://github.com/jprybylski/quartifyr/blob/main/R/render-report.R)) |

**The source archive distinction is crucial.** The current release assets for `opnca-r`, `deckifyr`, and `quartifyr` are `.tar.gz` R source packages, not Windows or macOS R binaries. `renv` identifies `.zip` as a Windows binary and `.tgz` as a macOS binary. `opnca-r` declares Cargo, `rustc >= 1.87.0`, and `xz`; its makefiles call Cargo during `R CMD INSTALL`. Its included `vendor.tar.xz` makes crate sources available for an offline Cargo build but does not remove compilation. Its Windows CI selects the `x86_64-pc-windows-gnu` Rust target with the Rtools MinGW linker. The release process should build and test binaries across the supported OS/architecture/R-minor matrix and ship their hashes in an installation manifest. Rust compilation remains a documented fallback until that matrix exists. ([renv cellar](https://rstudio.github.io/renv/articles/cellar.html), [opnca-r DESCRIPTION](https://github.com/mantra-opnca/opnca-r/blob/main/DESCRIPTION), [opnca-r Makevars](https://github.com/mantra-opnca/opnca-r/blob/main/src/Makevars.in), [opnca-r release build](https://github.com/mantra-opnca/opnca-r/blob/main/tools/build_cran_package.sh), [opnca-r CI](https://github.com/mantra-opnca/opnca-r/blob/main/.github/workflows/R-CMD-check.yaml))

A **no-admin** promise is conditional on a supported machine that allows user-owned binaries to run. `rig` documents a user-local Rtools install on Windows, but Rust, Rtools, linker, and R ABI must be tested as one package-specific build path; installing Rust alone is insufficient. macOS source builds can require Xcode Command Line Tools and other compilers. Given the user's prior `opnca-r` installation failures, the first milestone must be an isolated installation spike on clean standard-user Windows and macOS accounts, with the exact tarballs and target R version. The success criterion is not a successful `cargo --version`; it is install, load, run a small known input, and then render an artifact without elevation. ([rig Rtools](https://rig.r-lib.org/reference/rtools.html), [Rust installation requirements](https://rust-lang.org/tools/install/), [R macOS tools](https://mac.r-project.org/tools/index.html))

An **online installer** can fetch pinned releases and verify hashes. An **offline installer** is a separate distribution containing all Python wheels, R dependencies and binaries, R itself, Quarto, and any Rust/toolchain assets required by its selected build mode. A few custom tarballs alone do not make the product offline-capable. This is a proposed packaging rule, not a capability verified in the present repositories.

## Each analyte run produces a replayable artifact set

The home page begins with a native folder picker or a small native picker helper; browser-only directory access varies by browser and should not be relied on for a consistent Windows/macOS experience. The user chooses a working directory, and the launcher creates a versioned metadata folder inside it after showing the path and permissions. The first release should import CSV because that is what the current `opnca-r` browser upload supports; XLSX, SAS transport, and domain-specific formats should be added through import adapters after representative, deidentified fixtures are agreed. A directory scan should be shallow by default and display filename, size, columns, row count, and candidate analytes without recursively reading unrelated files.

The cleaning screen presents a preview and a stepwise, saved recipe. `janitor::clean_names()` can make column names unique and machine-friendly, and `janitor::get_dupes()` can surface candidate duplicate keys. Neither function decides concentration units, BLQ policy, time derivation, or which row to exclude. Those remain explicit mapping and review decisions. The original source file is never rewritten. Cleaning can rename columns, parse types, recode routes, derive fields, and flag issues, but each operation must show before/after row counts and preserve a rejected-row log. The app must never silently drop rows, change BLQ to zero, replace actual time with nominal time, infer dose units, or resolve duplicates automatically. ([janitor `clean_names`](https://sfirke.github.io/janitor/reference/clean_names.html), [janitor `get_dupes`](https://sfirke.github.io/janitor/reference/get_dupes.html), [opnca data formats](https://github.com/mantra-opnca/opnca/blob/main/docs/guide/03_data_formats.md))

The analysis setup maps source columns to profile ID, actual time, concentration, dose, route, and relevant optional fields such as nominal time, BLQ/LLOQ, exclusion, tau, and analyte. It checks empty selections, invalid types, duplicate profile/time/occasion keys, mixed undeclared concentration units, inconsistent dose or route within profile, and unrecognized routes. It then filters by the **exact value of the designated analyte column**, makes a read-only input snapshot, and starts one `opnca-r` session. If the user selects a second analyte later, the launcher creates a new run and new script. Changes to cleaning, mapping, exclusions, or terminal rules also create a new run; completed runs are immutable. The scientific handling of flagged records and warning versus blocking checks must be decided against representative data before implementation. ([opnca data formats](https://github.com/mantra-opnca/opnca/blob/main/docs/guide/03_data_formats.md))

The desired workspace layout is a **proposed contract**. Installation runtimes stay outside this directory; the study folder holds only data, recipes, and outputs. The hidden workspace metadata folder may be renamed during implementation, but its schema and migration policy must be versioned.

```text
Study folder/
  source-data/                         optional immutable copies or references
  .clinpharm-workspace/
    workspace.json                     schema version, source references, UI preferences
    recipes/<recipe-id>.yaml           explicit cleaning and mapping steps
    runs/<run-id>/
      input/source-fingerprint.json    source path, size, SHA-256; no row data in manifest
      input/analysis.csv               frozen, one-analyte analysis input
      analysis.R                       replayable script using relative input path
      settings.json                    analyte, mapping, rules, exclusions
      analysis-record.json             opnca export record
      results/                          native opnca CSVs and figures
      artifacts/                        curated table/figure plus metadata JSON
      logs/                              job and validation diagnostics
      manifest.json                    state, versions, hashes, lineage, warnings
    publications/<publication-id>.json selected completed run IDs and artifact hashes
  OUTPUTS/
    figures/                           reportifyr-compatible published copies + sidecars
    tables/                            reportifyr-compatible published copies + sidecars
  report/                               quartifyr/Quarto source and output
  deck/                                 deckifyr YAML, assets, and output PPTX
```

The run manifest should be JSON Schema validated and written atomically. Minimum fields are `schema_version`, `run_id`, `created_at`, `state`, `source` path and SHA-256, `analysis_input` SHA-256 and row count, `analyte` column/raw value/display label, cleaning recipe hash, field mapping and units, NCA settings/exclusions, tool and runtime versions, generated script path/hash, warnings, and artifact entries with path/media type/hash. The publication manifest records exact run IDs and artifact hashes, never a moving `latest` directory. This extends information `opnca-r` already places in its browser-created analysis record, while making the provenance durable and available to downstream jobs. ([opnca-r export implementation](https://github.com/mantra-opnca/opnca-r/blob/main/inst/www/export-view.js))

```json
{
  "schema_version": "1.0",
  "run_id": "20261103T142512Z-7c4e",
  "state": "completed",
  "analyte": {"column": "analyte", "value": "Example A"},
  "analysis_input": {"path": "input/analysis.csv", "sha256": "<digest>", "rows": 128},
  "script": {"path": "analysis.R", "sha256": "<digest>"},
  "tools": {"opnca_r": "<pinned version>", "R": "<pinned version>"},
  "artifacts": [{"path": "artifacts/table-1.csv", "sha256": "<digest>", "kind": "table"}]
}
```

The `opnca-r` adapter must launch from a file-backed, one-analyte snapshot and support **server-side save to an approved run directory**. Its output includes a runnable `analysis.R` with the snapshot's relative path and pinned settings, a machine-readable analysis record, native result tables and figures, and an artifact index. The save operation should write to a staging subdirectory, validate hashes and expected files, then publish atomically. If changing `opnca-r` takes longer than the launcher MVP, a temporary adapter may derive a script and export from its public R APIs, but that path must be tested against the UI's calculations and should never scrape browser downloads. `opnca-r` remains the calculation authority; the launcher does not implement NCA itself. ([opnca-r app entry](https://github.com/mantra-opnca/opnca-r/blob/main/R/app.R), [opnca-r export](https://github.com/mantra-opnca/opnca-r/blob/main/inst/www/export-view.js))

Publishing is a separate, reviewable action. The user chooses one or more **completed** runs, sees their analytes and hashes, and approves which figures and tables are copied into `OUTPUTS/` with the metadata JSON expected by `reportifyr`. The convenience report screen initializes or opens a `quartifyr` project, exposes a small set of approved report settings, shows missing placeholder/metadata checks, runs `quartifyr::render_report()` with the project root as working directory, and opens the resulting DOCX. The deck screen initializes or opens a `deckifyr` project, references the same published artifact set, starts its editor under the launcher origin, then runs `validate` and `build` to produce the PPTX. A report or deck build manifest records the exact run IDs and source artifact hashes used. ([reportifyr README](https://github.com/A2-ai/reportifyr/blob/main/README.md), [quartifyr render](https://github.com/jprybylski/quartifyr/blob/main/R/render-report.R), [deckifyr README](https://github.com/jprybylski/deckifyr/blob/main/README.md))

## Stable adapters make delivery and reuse testable

Each installed tool is registered by a trusted, versioned descriptor. The descriptor identifies runtime, capability, fixed executable, health probe, web path where applicable, input schema, and output schema. The controller exposes `prepare(workspace)`, `validate(input)`, `run(job)`, `status(job)`, `cancel(job)`, `artifacts(job)`, and `shutdown()` to its own UI. Adapters translate those calls into absolute-path process invocations or narrowly scoped local HTTP calls. Workspace files contain data and options only; they cannot add commands or override executable paths. A possible descriptor shape is:

```yaml
id: deckifyr
version: 1
runtime: python
capabilities: [edit_deck, validate_deck, build_deck]
web_app:
  route: /apps/deckifyr/
  readiness: /health
jobs:
  validate: {input_schema: deck-project-v1, output_schema: validation-v1}
  build: {input_schema: deck-project-v1, output_schema: deck-build-v1}
```

The release should proceed through gated increments rather than treating all tools as one integration task. A planning estimate is intentionally omitted until platform support and representative inputs are known.

| Gate | Deliverable | Acceptance evidence |
|---|---|
| 0. Installation proof | Exact dependency graph and clean-account install spike on each target OS/architecture, including the current `opnca-r` archive and Rust fallback. | No elevation; `opnca-r` loads and calculates; Quarto renders; failures yield useful redacted diagnostics. |
| 1. Reusable shell | Versioned installer, icon, workspace picker, single-instance loopback controller, idle and quit behavior, job manager, adapter registry. | Second click reuses instance; stale state recovers; active job survives tab closure; no data leaves machine by default. |
| 2. Analysis path | CSV import, previewable cleaning, explicit mapping, analyte selection, `opnca-r` integration, durable run bundle. | A two-analyte fixture creates two distinct scripts, input snapshots, manifests, and outputs with no cross-analyte rows. |
| 3. Publishing path | `OUTPUTS/` curator, report convenience form, `quartifyr`/`reportifyr` job, `deckifyr` editor and build. | Report and deck resolve only selected completed run hashes; missing metadata is caught before render; subpath behavior passes. |
| 4. Release hardening | Prebuilt R binaries, updater/rollback, clean install matrix, diagnostic export, user guide. | Standard-user Windows/macOS installation and upgrade pass without admin, and an interrupted upgrade leaves the old version runnable. |

The most valuable **upstream issue proposals** are concrete and independently reviewable; no issues have been filed by this specification.

| Repository | Proposed issue and completion criterion |
|---|---|
| [`mantra-opnca/opnca-r`](https://github.com/mantra-opnca/opnca-r) | **Add a file-backed launcher entry point.** Accept snapshot path, explicit analyte context, and optional output directory; return a health URL and stop handle; leave current interactive API intact. |
| [`mantra-opnca/opnca-r`](https://github.com/mantra-opnca/opnca-r) | **Add atomic server-side run export.** Save executable script with real relative input path, analysis record, tables, figures, versions, and hashes; test replay against UI results. |
| [`mantra-opnca/opnca-r`](https://github.com/mantra-opnca/opnca-r) | **Publish platform R binaries and build diagnostics.** Test the supported R/OS/architecture matrix; include SHA-256 manifest and a documented GNU/Rtools plus Rust source fallback. |
| [`mantra-opnca/opnca-r`](https://github.com/mantra-opnca/opnca-r), [`jprybylski/deckifyr`](https://github.com/jprybylski/deckifyr) | **Support a configurable proxy base path.** Static assets, API, redirects, Host validation, and any live connections work beneath `/apps/<tool>/`; clean shutdown is observable. |
| [`jprybylski/deckifyr`](https://github.com/jprybylski/deckifyr) | **Document launcher mode.** Accept a supplied project and port, suppress auto-browser opening, expose readiness and orderly stop, and provide stable build/preview result metadata. |
| [`jprybylski/quartifyr`](https://github.com/jprybylski/quartifyr) | **Expose a noninteractive report job contract.** Validate project initialization and `report/shell`, return structured artifact paths/warnings/errors, and avoid an ambiguous success after a partial render. |
| [`A2-ai/reportifyr`](https://github.com/A2-ai/reportifyr) | **Define an artifact metadata schema and validation helper.** Let a caller validate table/figure sidecars before `quartifyr` renders; otherwise keep this validation in the launcher adapter. |

The release test suite must include installer rollback, locked-environment restoration, source and binary package loading, proxy routes, job cancellation, idle shutdown, and path/Origin rejection. A scientific fixture should contain two analytes with overlapping subject and time keys, BLQ and exclusion examples, duplicates, mixed units, and a deliberately invalid row. Assertions should check exact analyte filtering; counts before and after cleaning; preserved source bytes; separate R scripts; successful script replay within agreed numerical tolerances; and report/deck lineage to the intended run IDs. An end-to-end DOCX/PPTX success proves artifact assembly, **not** scientific or regulatory validation. The `opnca-r` project currently describes itself as research-use only; intended use, verification, approval, and change control need a separate owner decision before clinical or regulatory claims are made. ([opnca-r README](https://github.com/mantra-opnca/opnca-r/blob/main/README.md))

## Decisions that control the first release

Several inputs remain unknown and should be decided using representative, deidentified datasets and target machines: supported Windows/macOS versions and CPU architectures; the intended R minor version; whether end users need offline installation; source formats beyond CSV; the analyte field and unit conventions; BLQ, exclusion, duplicate, and time rules; report and deck templates; treatment of potentially identifiable data; and the desired idle timeout. The no-admin requirement is fixed, but an organization's application controls can still prevent user-local binaries from running. The most consequential early decision is whether to invest immediately in platform R binaries or accept a compiler-heavy initial install; for nontechnical users with known `opnca-r` installation trouble, **prebuilt binaries should be the release target**, with a checked Rust build path retained for development and exceptions.

## Conclusion

The reusable part of this product is the local controller, installer, workspace manifest, process supervision, and adapter contract. The clin pharm experience becomes a set of adapters and guided screens on top of that foundation. The implementation risk sits at the boundaries: native R package installation, embedded child-app routing, and a durable `opnca-r` export. Proving those boundaries first makes the one-click experience credible without changing the scientific engines or claiming validation they do not currently have.
