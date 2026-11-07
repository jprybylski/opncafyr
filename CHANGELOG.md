# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
- quartifyr report adapter: set up, check and render a DOCX from one publication (#32).
  - **Set up report** lays out `report/` offline:
    - quartifyr 0.3.3's `initialize_report_project_offline()` and the bundled extensions
      (no pyro, uv or network);
    - the bundled template (ADR 0007), quartifyr's reference DOCX, a generated
      `report.qmd` (data text reduced to plain words) and a copy of the publication's
      files.

    The project is bound to that publication (`publish-binding-v1`). Binding it to
    another publication asks before replacing an edited `report.qmd`.
  - **Check** runs the sidecar checks (#30) and quartifyr's `check.R` on a staging copy
    that holds exactly the publication's files, each checked against its hash.
  - **Render** runs `render.R --strict --offline` with the pinned Quarto on that copy:
    - success means exit 0 and status `ok`; the DOCX that quartifyr lists is re-hashed and
      kept in `report/builds/<id>/` with `build.json` (new `publish-build-v1`: the
      publication, exact run IDs, every published file and input hash, tool versions);
    - any failure, partial render, timeout (whole process tree killed) or cancel keeps
      nothing;
    - one report job runs at a time;
    - "Download DOCX" serves a build only while it matches its manifest.
  - Every report job runs with offline uv, app-owned uv and Deno caches, a throwaway git
    identity, and the pinned uv first on `PATH` (docs/offline.md R1-R4, R12, S4, S6).
  - The job manager gains `ok_exit_codes` (a check tool may exit 1 to report findings)
    and `diagnose(ctx, code)` (a tool's own JSON error becomes the job's message).
  - Integration tests render a two-analyte publication with real quartifyr and Quarto.
- Publication curator: completed runs' tables and figures copied into `OUTPUTS/` with a
  publication manifest (#31).
  - The Publish page lists every run. Only completed runs whose replay passed and whose
    sealed files still match can be chosen, and the API enforces the same (409
    `not_publishable`).
  - Each run offers what opnca-r saved. Pre-selected:
    - the parameter summary;
    - the individual listing;
    - individual profiles, linear and log.

    Optional: summaries by group and parameter boxplots. One publication can combine
    analytes.
  - ADR 0007's mean concentration–time figure isn't saved by opnca-r 0.2.8. It is listed
    as unavailable (mantra-opnca/opnca-r#50, filed; ADR 0007 amended); the launcher
    doesn't draw it.
  - "Check" shows names, statuses and sidecar findings without writing anything.
    "Publish" then:
    - copies each file byte for byte to
      `OUTPUTS/<tables|figures>/<analyte>_<run-id>_<artifact>.<ext>`;
    - writes its reportifyr sidecar in Python (meta_type `"NA"`; source is the run's
      `analysis.R`);
    - checks both (#30);
    - records `.opncafyr/publications/<id>.json` last.
  - A published file is never replaced. A name holding different bytes refuses the whole
    publication before anything is written, and identical files are reused. Files from an
    interrupted publish show as "not recorded" and are never used.
  - `publication-v1` is finalised in place (nothing wrote it before). It records:
    - exact run IDs, with each run's manifest and replay SHA-256;
    - the SHA-256 of each source artifact, published copy and sidecar;
    - whether each item shows subject IDs.
  - New user guide `docs/publishing.md`, including why the publishing path has no R
    scripts.
- Checks of reportifyr metadata sidecars before anything is published or rendered (#30).
  - reportifyr only logs a missing or broken sidecar, and the report still succeeds. So the
    launcher checks each table and figure in `OUTPUTS/` first
    (`workflows/nca/publish/sidecars.py`):
    - the sidecar exists, is valid JSON, and matches reportifyr 0.4.0's format (new
      schema `reportifyr-sidecar-v1`);
    - its `object_meta.path` and file type match the file;
    - its BLAKE3 `hash` matches the file's bytes;
    - its `meta_type` and abbreviations are defined in `standard_footnotes.yaml`.
  - Findings use the new `preflight-v1` shape (quartifyr's check-result shape) and name
    files and checks only, never data values.
  - The new Publish page lists them ("Check published files"; `GET
    /api/nca/outputs/check`).
  - The report template's `standard_footnotes.yaml` and `config.yaml` (quartifyr v0.3.3
    demo-report, ADR 0007) are bundled in `adapters/quartifyr/template/`.
  - New launcher dependency `blake3` 1.0.11, pinned per platform in
    `runtime/python/wheels.toml`.
- Replay verification of saved runs (#29).
  - A verified save is replayed at once: `analysis.R` runs with the pinned R and opnca-r
    in a copy of the run, and its tables are compared with the saved results. IDs,
    counts, flags and status must be equal; other numbers agree within a relative
    tolerance (default 1e-10, recorded). The report (`replay-v1`, with a readable list
    of differences) is kept in the run's `logs/`.
  - A run can be completed, and so published, only after a passed replay; "Replay again"
    reruns it. The Analysis page shows the result and the differences.
  - Gate 2's exit test runs both analytes of the two-analyte fixture end to end, through
    real opnca-r sessions and saves, to two sealed, replayed runs with no cross-analyte
    rows.
- Durable save of an analysis into its run folder (#28).
  - The launcher watches the run folder for opnca-r's server-side save (never a browser
    download). A save counts only when `COMPLETE` matches `artifacts.json`, every artifact
    re-hashes, nothing is unlisted or stray, and the record and `analysis.R` name the
    snapshot by hash and relative path. A partial or tampered save fails the run.
  - On a complete save the session stops and the run moves to review with its script,
    record, artifacts, opnca-r's effective settings, versions and exclusions. A reviewed
    run is completed, and sealed, only after its replay passes (#29).
  - Points excluded inside opnca-r replace the launcher's exclusion column; until
    mantra-opnca/opnca-r#49 is fixed such a save fails with a message.
- opnca-r sessions on a run (#27).
  - "Open in opnca-r" starts `serve.R` on the run's frozen input with its preset, the run
    folder as output folder and the run id as storage namespace, so nothing from another
    run's session carries over. Opening another run, or stopping the launcher, stops the
    session and its whole process tree. The embedded page reloads with the new session.
  - The adapter adds `--require-explicit-settings` and `--no-guess-roles` (opnca-r
    0.2.8): no default BLQ method, no role guessed from column names.
  - Confirmed against opnca-r 0.2.8 that CONC on BLQ rows doesn't change results (ADR
    0005, "Still to confirm").
- Run bundles and the final run-manifest-v1 (#26).
  - `run-manifest-v1` is finalised in place: the spec's minimum fields are required, a
    reviewing or completed run must name its script, record, export and artifacts, a
    completed run its replay and seal, a failed run its failure. Lineage records the
    recipe hashes and parent run. The spec's example manifest now validates.
  - `core/runs.py` stages and publishes runs, validates every manifest read and write
    (atomic), moves runs through the job states, and seals completed runs: every file
    hashed into the manifest, then read-only. A sealed run can't change; "New run from
    this" (`POST /api/nca/runs/{id}/rerun`) makes a linked one.
  - `opncafyr runs verify <study> [--run ID]` and `GET /api/runs/{id}/verify` report
    changed, missing and added files.
- Frozen one-analyte input snapshots (#25).
  - `POST /api/nca/runs` checks and gates the selection, then writes
    `.opncafyr/runs/<run-id>/` through a staging folder and one rename: the rows whose
    analyte (then specimen) value equals the chosen text exactly, with `NCA_CONC`,
    `NCA_BLQ`, `NCA_MISSING`, `NCA_EXCLUDED` and the exclusion reason added. BLQ text never
    becomes 0; excluded rows stay.
  - `input/source-fingerprint.json`, the opnca-r preset (`input/opnca-mapping.json`) and
    `settings.json` (new schema `run-settings-v1`) are written with it; the input and
    settings are read-only. `run-manifest-v1` gains specimen, units, acknowledgements,
    row accounting and the settings file hash.
  - BLQ rules and method must be chosen (409 `needs_choice` otherwise). Values that differ
    only in spaces or case are other analytes. The Analysis page lists runs.
- Pre-analysis checks (#24).
  - `workflows/nca/checks.py` runs every ADR 0005 Q7 check (21 blocking, 6 warnings, 4
    info) on one exact analyte and specimen selection of the cleaned data. Findings carry
    physical source lines, never values in logs; subject IDs are registered with the log
    redactor.
  - opnca-r's `check_data_format()` runs on the same selection as an R job; its issues are
    added as info.
  - `POST /api/nca/runs` answers 409 with the findings while anything blocks or a
    warning is not acknowledged. The Analysis page lists findings by level, asks for each
    acknowledgement and keeps "Start analysis" disabled until then.
  - Each planted case of the two-analyte fixture gives exactly its expected findings,
    with and without R.
- Column-role mapping (#23).
  - The recipe gains a `mapping` block: the column for each role, the units of time,
    concentration and dose (a unit column or one declared value, never inferred) and what
    each value of the BLQ, missing and exclusion flag columns means. It is hashed with the
    recipe; cleaning results are keyed on the steps alone.
  - Suggestions ported from makecoolplots (`workflows/nca/roles.py`): alias names plus
    value checks, each with a reason. A weak match or two fitting columns is a
    `needs_choice` question with candidates; nominal time is never suggested as actual
    time. Units are normalised by spelling only; unrecognised routes are listed for an
    explicit recode.
  - `GET /api/nca/recipes/{id}/mapping-help` and `PUT .../mapping`; the Analysis page shows
    each suggestion beside its role and fills in nothing until the user picks it.
  - `tests/fixtures/adpc.csv`, copied from makecoolplots with its generator excerpt.
- Cleaning recipes (#22).
  - `recipe-v1` is finalised in place (nothing had written the draft): ordered steps
    `{op, args, note}`, with `clean_names`, `rename`, `trim`, `parse_type`, `recode`,
    `derive`, `derive_elapsed_time`, `convert_units`, `flag`, `exclude`, `filter_rows` and
    `report_dupes`. Row conditions are a fixed shape, never an expression.
  - `workflows/nca/recipe.py` refuses a step that would set a BLQ or blank value to zero,
    derive a constant zero, overwrite a column (so nominal time can't replace actual time)
    or change the launcher's own columns. Recipes are hashed as canonical JSON.
  - Each recipe compiles to a standalone, readable `clean.R` (the fixed library
    `r/nca_clean_lib.R` plus one call per step; recipe text only as escaped string
    literals), run as a job with the pinned R. Outputs: `cleaned.csv`, `rejected.csv`
    (line, step, reason) and `steps.json` (rows in, out and rejected per step, values
    changed, issues with lines, duplicate groups). The job is accepted only if every step
    accounts for its rows and the source is unchanged. Preview runs the same script on the
    first 200 rows.
  - Workflow packs can add routes, page assets and pages (`WorkflowPack.mount`, `static`,
    `views`; `GET /api/workflows`); the NCA pack's Analysis page edits, previews and
    applies recipes. `core/rscript.py` is the one way R is run (`Rscript --vanilla`).
  - The two-analyte fixture's scenario recipes use the `{op, args}` shape; CI runs the
    R-backed tests in `tests/integration/nca` with the proxy harness.
- Offline installation (#36), as a supported v1 use case
  ([docs/install.md](docs/install.md#offline-installation-for-it-administrators-36)).
  - The bundle builder (`install.sh bundle`, `install.bat bundle`, `opncafyr bundle`)
    fetches every pinned file for a platform from its original source and checks each
    against the pins. It writes a bundle containing:
    - the installer;
    - `bundle.json` (schema `bundle-manifest-v1`): every file with its version, SHA-256,
      source URL and licence;
    - `SHA256SUMS`;
    - an SPDX 2.3 SBOM;
    - a licence inventory read from each package's own metadata.

    `--archive` writes a deterministic zip.
  - `install --from-bundle` (folder or zip) never uses the network. A missing or tampered
    file is refused, naming its component, and the previous install stays active. Updates
    from a newer bundle use the same staging and atomic activation.
  - Internal-mirror mode: `--mirror mirror.toml` rewrites each pinned URL to a configured
    mirror prefix, keeps the pinned SHA-256, and never falls back to the origin.
  - Every install, update or repair writes an installation verification report (JSON,
    schema `install-report-v1`, and printable HTML). It records:
    - versions and hashes;
    - the bundle ID and the payload digest;
    - OS and paths;
    - smoke tests, now including an opnca-r calculation with known answers, a Quarto
      DOCX render with an R chunk and a deckifyr build.

    It is for research use and is not a validation claim.
  - The `Installer` workflow builds each platform's bundle twice and compares the
    manifests. It then installs from the bundle with the user's network blocked
    (`sandbox-exec` on macOS, a per-user firewall rule on Windows) and fails on any logged
    connection attempt. It also checks that a tampered bundle is refused, and that an
    update killed mid-way and then completed leaves the right install active. The bundle
    is kept as a workflow artifact.
- Installer v1 (#19): a per-user installer with no administrator rights
  ([docs/install.md](docs/install.md)).
  - `install.sh` (macOS) and `install.bat`/`install.ps1` (Windows) only fetch and verify
    the pinned Python, then run `opncafyr.installer`. The installer uses the standard
    library only, a rule a boundary test checks.
  - Every pinned file is found in the download cache, an `--assets` folder or an offline
    bundle (`--from-bundle`, never the network), or downloaded with the system `curl`. Each
    is checked against `runtime/pins.toml` before anything is staged, and a missing or
    tampered file is named by its component. The private opnca-r release needs
    `GH_TOKEN` or `--assets`.
  - Each install is staged into its own `versions/<id>`:
    - Python and uv;
    - the launcher and tools environments, from hash-pinned wheels
      (`runtime/python/wheels.toml`, made by `scripts/pin_wheels.py`);
    - R: on Windows, R's own `/CURRENTUSER` installer, which leaves the user's own R
      registration as it was; on macOS, CRAN's `R.framework` relocated to
      `~/Library/opncafyr`;
    - the R library, installed with base R from a local repository and checked against
      `renv.lock`;
    - Quarto, and LibreOffice with `--with-libreoffice`.
  - Smoke tests and a schema self-check run before `install.json` is written. Then the
    `current` pointer flips atomically and the icon is created: `~/Applications/opncafyr.app`
    on macOS, Desktop and Start Menu shortcuts on Windows.
  - A failure leaves the previous install active and writes a redacted support bundle.
  - A second run is a no-op. `repair` re-checks and rebuilds; `uninstall` removes the app,
    R and the icon, never a workspace.
  - `scripts/make_installer.py` builds the payload.
  - The new `Installer` workflow runs it all as a standard user on macOS arm64, macOS
    Intel and Windows Server 2025.
  - Launcher: finds its own install and `runtime.json` from its location; `doctor` shows
    the install; `pythonw.exe` (no console) is handled.
  - New schemas `install-v1` and `payload-v1`. `runtime-v1` gains an optional `wheelhouse`.
  - `runtime/pins.toml` names each component's licence, and `verify_pins.py` checks the
    licences and `wheels.toml`.
  - The opnca-r release checks moved to `opncafyr.installer.opnca_release`;
    `opncafyr.adapters.opnca.release` re-exports them.
- LibreOffice decision (#8): an optional, verified per-user download for deckifyr slide
  previews, pinned in `[optional.libreoffice]` (26.2.7.1, archive URLs). quartifyr's
  field recalculation stays off until quartifyr#84/#86. Report:
  `docs/spikes/libreoffice.md`.
- macOS install spike (#7). `scripts/spikes/macos-install.sh` installs the pinned stack
  as a standard user on macOS 15 by relocating CRAN's R, online or offline, and runs the
  same checks; the `install-spike` workflow (manual, or `spike/**` branches) runs it on
  GitHub's arm64 and Intel runners. Report: `docs/spikes/macos-install.md`.
- Windows install spike (#6). `scripts/spikes/windows-install.ps1` installs the pinned
  stack as a standard user on Windows 11, online or from local files, and runs opnca-r,
  its server, a deck build and a report render. It passed with no elevation, no machine
  changes and, offline, no connection attempts. Report: `docs/spikes/windows-install.md`.
- Offline audit (#44). `docs/offline.md` lists every network touchpoint at install and
  run time with its resolution, including those found by the install spikes. A new
  `offline` CI job installs the whole stack from local files
  (`scripts/offline/fetch.py`, `offline_phase.sh`) and runs the dataset import, an
  opnca-r calculation, a quartifyr render and a deckifyr build in a network namespace
  under strace; `scripts/offline/check_trace.py` fails the job on any attempt to leave
  the machine. Linux CI pins are in `runtime/pins.toml` `[ci.linux-x86_64]`.
- Runtime pins and dependency graph (#5).
  - `runtime/pins.toml` pins uv 0.13.7, Python 3.13.18, R 4.6.2, Quarto 1.11.9, opnca-r
    0.2.7, deckifyr 0.3.5, quartifyr 0.3.2, reportifyr 0.4.0, pyro 0.1.1, Rust 1.99.1 and
    Rtools45 by exact URL and SHA-256 for Windows x86_64 and macOS arm64/x86_64.
  - `runtime/r/renv.lock` locks the app-private R library (130 packages from the Posit
    Package Manager snapshot of 2026-11-06 plus the four tool packages from the cellar),
    and `runtime/r/binaries.toml` pins every package's binary per platform and its source
    tarball. macOS gdtools comes from CRAN, because P3M's build links XQuartz.
  - `runtime/python/tools` locks one Python environment for deckifyr (with the web editor
    and previews), quartifyr and reportifyr, so pyro never creates its own.
  - `scripts/verify_pins.py` checks the pins and locks (now a CI step) and, with
    `--network`, downloads and hashes every file. `scripts/lock_r.R` and
    `scripts/pin_r_binaries.py` regenerate them; `scripts/check_r_restore.R` restores the
    lock into an isolated library. `docs/dependency-graph.md` explains what needs what.
- Dataset scan, preview and import (#21).
  - `GET /api/datasets` lists data files directly in the study folder and in
    `source-data/`, with size, rows, columns and candidate analytes. Nothing deeper is
    read.
  - `POST /api/datasets/preview` returns columns, inferred types with up to three
    counterexamples, missing counts, the row count (estimated and labelled for files
    over 50 MB), the first rows, and the size and SHA-256 of the file. Source files are
    opened read-only and never changed.
  - `POST /api/datasets/import` records the file's path, size and SHA-256 in
    `workspace.json` `sources`, giving later recipes a `source_id`.
  - Formats plug in through workflow packs: `core.datasets.Importer` and
    `DatasetAnnotator`, found by `core.registry.load_workflows()`. The NCA pack adds a
    CSV importer (delimiter sniffing, UTF-8 BOM, `"", NA, ., NaN, NULL` as missing,
    readr-like types, ported from makecoolplots) and an analyte annotator that lists
    exact raw values and flags values differing only by spaces or case.
  - A Data view (`#/data`) in the launcher lists the files, previews one and imports it.
- Launcher visual design (#50), approved by the owner on 2026-11-06.
  - Approved design and screenshots: `docs/design/home-v1/`. Teal accent, a dark
    slate top bar and a light sidebar, the reverse of the tools' own layout. Status
    tones (ok, warn, danger, info) match the tools'. System fonts only.
  - `app.css` holds colour, spacing, radius, shadow and type tokens for light and dark,
    with a contrast table. `tests/test_design_tokens.py` checks every listed pair
    against WCAG 2.2 AA.
  - App shell: a top bar with the open study folder, a live-jobs indicator, a theme
    switch and Quit; the research-use notice and page status lines on every view;
    sidebar views Home, Jobs and Settings (`#/`, `#/jobs`, `#/settings`).
  - Home is a dashboard: a study-folder card, tool cards with capabilities and health,
    recent runs and publications with status pills, and an activity card for jobs
    (message, start time and duration, a moving bar while running, Cancel). The Jobs
    view lists every job.
  - Theme: System, Light or Dark, saved as `theme` in `settings-v1` (an optional,
    additive field). The server renders it on `<html data-theme>` (ADR 0006 amended).
    `PUT /api/settings` now takes either setting on its own, and saving one keeps the
    other.
  - Component reference at `static/components.html` (linked from Settings): buttons,
    pills, fields, status lines, empty state, tables, activity items and dialogs, for
    the guided screens (#21–#24) to reuse.
  - Tools' web apps open inside the launcher (owner's choice, option A): `#/apps/<id>`
    shows the app in a same-origin iframe under a tool strip (Home / name, state,
    Reload, Open in new tab). The sidebar folds into an icon rail and lists every web
    app. Each frame stays loaded while other views are shown, so work in a tool
    survives a visit to Home.
  - The proxy adds `Content-Security-Policy: frame-ancestors 'self'` to every `/apps/`
    response, so only the launcher can frame a tool. The "not running" page's home link
    targets the top window.
  - Using a tool now keeps the launcher open: each proxied request counts as an open
    page for idle shutdown, so auto-close no longer stops the app while someone works
    in a tool in another tab.
- Local-origin hardening finished (#11).
  - The browser gets the session cookie only from a one-time link: the launcher (and a
    second launch that reopens it) opens `/open?token=…`. The token is stored as a
    SHA-256 file name in `state/open-tokens/`, lives 120 s, and works once; `/open`
    sets the cookie and redirects to `/`.
  - The cookie (`HttpOnly; SameSite=Strict`) now covers the whole origin, not only
    `/apps/`. Every request except `/health` and `/open` needs it, so another process
    or user on the machine can no longer read the home page, its secret or the API.
    `Sec-Fetch-Site` is checked everywhere. A page without a session explains how to
    reopen opncafyr.
  - `core/redaction.py`: launcher log records and child-app output in
    `logs/children/` are redacted of registered identifier values and `token=` query
    values; table-like log arguments become a row count. Workflows register values
    when they read a dataset (#21, #25).
  - The boundary test now allows `subprocess` only in `core/proctree.py` and
    `core/picker.py`, and forbids `os.exec*`/`spawn*`/`startfile`, `pty` and
    `asyncio.create_subprocess_*` as well as `shell=True` and `os.system`/`popen`.
  - Tests: missing or invalid cookie or secret → 403, foreign Host → 400, cross-site
    Origin or `Sec-Fetch-Site` → 403, no `Access-Control-*` header on any response,
    more path-escape forms, and subject IDs from the two-analyte fixture never reaching
    the launcher log or a child log.
- Controller core finished (#9).
  - `launch --host` accepts only `127.0.0.1` and exits with an error for anything else,
    `::1` included.
  - `/health` adds `uptime_s`; it still carries no paths or data.
  - The launcher log: JSON lines in `logs/launcher.log` (rotated), with uvicorn's
    loggers routed to the same handlers and a filter hook for redaction.
  - Tests: `launch --no-browser` serves `/health`, two launchers in parallel get
    different ports, and the browser opens exactly once, only after `/health` answers.
- Tool registry and finalized `tool-descriptor-v1` (#13).
  - Descriptors now state `schema_version`, the runtime `executables` they need
    (symbolic names, never paths) and a structured `health` probe; `runtime` may be
    `binary`; jobs may set `timeout_s`; `optional_requirements` name an executable, the
    capabilities it enables and a message for when it is missing.
  - An invalid descriptor stops `opncafyr launch` before it binds a port, with an error
    naming the file and field (`DescriptorError`). Job schema names must be bundled
    schemas, so the deckifyr and quartifyr job entries wait for #32 and #33.
  - `core/health.py` probes each tool once at startup in the background (argv only,
    through `proctree.spawn`). `/api/tools` adds `version`, `health` and
    `missing_requirements`; the home page shows them on each tool tile, and
    `opncafyr doctor` prints them.
  - Tests: invalid fields are named, descriptors inside a workspace are ignored, and
    every bundled stub reports its health.
- ADR 0007 records the templates and the identifiable-data policy (#3).
  - v1 ships one generic NCA report (quartifyr 0.3.2 `examples/demo-report` and
    `org-reference.docx`) and deckifyr's bundled default deck, with an auto-built "NCA
    results" deck. Both templates ship with the install and are never fetched.
  - The curator pre-selects all four artifact types.
  - Coded subject IDs may appear in run bundles and published outputs, never in logs,
    URLs, telemetry or diagnostic bundles.
  - A likely direct-identifier column needs an explicit keep-or-exclude choice.
  - Source files are copied into `source-data/` by default, with a reference-only
    option.
- Home page UI shell (#49, ADR 0006). The page is a static shell, plain ES modules and one
  stylesheet in `web/static/`, served from a `/static/` mount. No build step and no
  third-party code.
  - Sections: study folder, tools, runs and publications, jobs, application (idle
    setting and Quit). The research-use-only banner is in the shell header.
  - Tool tiles come from `/api/tools` (now with `display_name`) and child-app state from
    `/api/apps`; core static files never name a tool, and a test enforces it.
  - The job panel polls `/api/jobs` only while the page is visible, announces state
    changes in an `aria-live="polite"` region and has a Cancel button per live job.
  - `api.js` adds `x-opncafyr-session` to every state-changing request.
  - `/` sends a `self`-only Content-Security-Policy; static files send
    `cache-control: no-cache`. Every asset URL is relative.
  - Accessibility (WCAG 2.2 AA target): landmarks, skip link, native controls and
    `<dialog>`, visible focus, states shown as a word and a symbol, light and dark
    themes.
- `GET /api/workspace/artifacts` (`core/artifacts.py`): a read-only list of the runs
  (`.opncafyr/runs/*/manifest.json`) and publications in the open workspace. A run
  without a valid manifest, or whose manifest names another run, shows as `incomplete`.
  Symlinks are skipped and no file paths are returned.
- Browser checks for the home page (`pytest -m ui`, dependency group `ui` with
  Playwright and `axe-playwright-python`): it renders with stub adapters, makes no
  request off its origin, a Tab walk reaches every control in order, and axe finds no
  violations in light or dark mode. A new Ubuntu CI job runs them with Chromium.
- Launcher lifecycle (#14). `core/lifecycle.py` decides when the launcher stops.
  - The home page sends a heartbeat every 15 s while it is visible
    (`POST /api/session/heartbeat`). Closing the tab sends a best-effort `leave` with
    `fetch(..., {keepalive: true})`; it is only a hint.
  - **Idle shutdown** after 30 minutes by default (ADR 0003) with no visible page, no
    live job and no child reporting unsaved edits or busy work. The home page sets it
    (5 minutes to 2 hours, or never) and it is saved in `state/settings.json`
    (`settings-v1`). `opncafyr launch --idle-timeout MINUTES|off` overrides it for one
    launch.
  - **Quit application** (`POST /api/quit`).
    - Live jobs: the default keeps them running, refuses new jobs and stops when they
      finish. `{"jobs": "cancel"}` cancels them. The page asks first, and *Keep the app
      open* (`POST /api/quit/abort`) withdraws a pending quit.
    - Unsaved edits: the quit is refused (409 `needs_choice`) until the user saves in
      the editor or chooses *Save and quit* (`{"unsaved": "save"}`).
  - Stop order: refuse new jobs, stop the children, close the listener, clear the state
    record.
  - Web-app adapters can report `activity()` (`ChildActivity`: unsaved, busy). deckifyr
    reads `dirty` and `active_jobs` from its `api/health`. A probe that fails counts as
    unsaved, so it never lets the launcher stop on its own.
  - `GET /api/lifecycle` reports pages, live jobs, unsaved and busy tools, the timeout
    and the time left before an idle stop.
- Single-instance launcher (#10). `core/instance.py` holds an OS lock on
  `state/instance.lock` (`flock` on macOS/Linux, `msvcrt.locking` on Windows) for the
  life of the launcher and writes `state/instance.json` (`instance-v1`: pid, pid start
  time, port, instance id, start time, version; no session secret, user-only on POSIX).
  - A second launch that finds the lock held probes `/health` on the recorded port and
    checks the instance id, then opens the browser there and exits 0 without binding.
  - A launch that gets the lock treats any old record as stale and starts. It logs
    whether the recorded pid was dead or alive (a reused pid).
  - A lock holder that does not answer is waited for (20 s), then reported with its pid.
    Nothing is ever killed because a record names it.
  - A clean stop removes the record. `/health` now returns `instance_id` (part of #9).
- Workspace selection (#17).
  - `core/picker.py` shows the native folder dialog from the controller: AppleScript
    `choose folder` on macOS, `FolderBrowserDialog` in Windows PowerShell. Both scripts
    are fixed. The prompt and start folder reach them as argv or environment data. Both
    dialogs can create a new folder.
  - `core/workspace.py` adds `inspect_folder`, `init_workspace` and `open_workspace`.
    - Inspecting shows the path, write permission, item count and state, and writes
      nothing.
    - Initializing creates only `.opncafyr/workspace.json`, which now records
      `app_version`.
    - A workspace with a newer schema is refused, with a message naming its version.
  - The recently used list lives in the app state dir (`recent-workspaces-v1`, paths
    only, at most 10).
  - `core/migrations/` holds the ordered `vN → vN+1` steps. A migration runs only when the
    user agrees, backs up to `.opncafyr/backups/` first, and must validate before it
    replaces the file. A failed step leaves the original untouched. The first step
    upgrades a synthetic schema 0.
  - API:
    - `GET /api/workspace`;
    - `POST /api/workspace/pick`;
    - `POST /api/workspace/inspect`;
    - `POST /api/workspace/init`;
    - `POST /api/workspace/open`.

    The browser cannot name an arbitrary folder: only a folder picked in this session or
    one on the recent list is accepted. Opening a workspace runs the job manager's sweep.
    Switching while jobs run is refused.
  - The home page has a minimal *Study folder* section. Manual test steps are in
    `docs/testing/workspace-picker.md`.
- Two-analyte scientific fixture (#20). `tests/fixtures/make_fixtures.py` is seeded and
  stdlib-only, and writes three files:
  - `two_analyte.csv`: PARENT and METAB on a shared subject × time grid;
  - `two_analyte_mixed_units.csv`: the same rows with mixed PARENT units;
  - `two_analyte.expected.json`: row counts, each planted case with its line numbers,
    the full list of blocking and warning findings under a standard setup, and mapping
    scenarios.

  Each blocking and warning check in ADR 0005 Q7 is planted in the data, triggered by a
  scenario, or marked not plantable (`session_exclusion_no_reason`). The planted cases
  include BLQ as a flag and as `<LLOQ` text, exclusions with and without reasons, exact
  and conflicting duplicates, an invalid row, a subject with no dose, and a second
  specimen. `tests/test_fixtures.py` fails when the committed files differ from a fresh
  run. `.gitattributes` keeps their line endings unchanged on Windows checkouts.
- ADR 0006 records the home UI stack (#18). It is a static HTML shell with vanilla JS ES
  modules and one CSS file, served from `/static/`. There is no build step, no Node and no
  third-party runtime JS. The page uses WCAG 2.2 AA landmarks and controls, and an axe
  check runs in CI. The shell itself is built in #49.
- ADR 0005 records the data conventions (#2):
  - the analyte is filtered on the exact code value, with a separate specimen role;
  - units come from a column or a user declaration, never inferred;
  - BLQ and LLOQ are built by explicit recipe steps, and all three BLQ rules are always
    chosen by the user;
  - every exclusion has a reason;
  - duplicates, undosed profiles and nominal time used as actual time are blocking;
  - a table sets each check's level (warning or blocking) with a snake_case id for #24.
- Job manager (#12) in `core/jobmanager.py`.
  - Each job lives in `.opncafyr/jobs/<job-id>/`:
    - `state.json` (new schema `job-v1`, written atomically);
    - `events.jsonl` (messages written for a person);
    - `stdout.log` and `stderr.log`;
    - `diagnostic.json` when the job fails or is cancelled.
  - Every job must set a timeout. Each pool has a concurrency limit, default 1.
  - Outputs are written to `<target>.staging/`. They are renamed into place only after a
    clean exit and a passing review, and an existing target is never replaced.
  - Cancel kills the whole process tree.
  - Opening a workspace marks jobs left by a dead launcher `failed`, kills their recorded
    tree, and does not promote their staging.
  - `GET /api/jobs`, `GET /api/jobs/{id}?after=N` and `POST /api/jobs/{id}/cancel`.
    Jobs are submitted only from trusted Python code. Stopping the controller cancels
    running jobs; since #14 an explicit Quit keeps them running by default.
  - `proctree.kill_recorded_tree()`.
  - `OwnedProcess.kill_tree()` no longer walks a pid that has already been reaped.
- Proxy compatibility harness (#16) in `tests/integration/proxy/`, run with
  `pytest -m integration`. It runs opnca-r 0.2.7 and deckifyr 0.3.5 from their pinned
  releases behind the proxy and checks:
  - that every page request stays under the prefix (Playwright);
  - an API round trip for each app: opnca-r schema plus calculate, and deckifyr save,
    build and PPTX download;
  - the attacks reproduced in the upstream reviews, both through the proxy and straight to
    the child port;
  - that an orderly stop and a tree kill both leave no processes behind.

  Results are in `docs/spikes/basepath.md`. CI runs the harness in a new macOS 15 job.
  The opnca-r half needs the `OPNCA_R_RELEASE_TOKEN` secret and is skipped with a
  warning without it.
- `opncafyr.adapters.opnca.release` selects an opnca-r asset from `manifest.json` by its
  fields and verifies it against `runtime/pins.toml`, with no network. #34 reuses it.
- Reverse proxy for child apps under `/apps/<tool>/` (#15).
  - Request and response bodies are streamed, with a per-descriptor body limit (413).
  - `/apps/<tool>` redirects to `/apps/<tool>/` with a 308.
  - Before forwarding, the proxy rewrites `Host` to the child's port, drops `Origin`,
    `Cookie` and the session header, and injects the child's token. It strips
    `Set-Cookie` and CORS headers from responses.
  - A crashed child gets a 502 page with a restart button.
  - `GET /api/apps` reports each app's state.
  - The home page sets a `HttpOnly; SameSite=Strict; Path=/apps/` session cookie. It is
    required on every proxied request, along with a same-origin `Sec-Fetch-Site` and our
    own `Origin` on state-changing requests.
- opnca and deckifyr web adapters (start, ready, stop):
  - opnca-r 0.2.7 runs `scripts/serve.R` with `--base-path`, the API and shutdown tokens,
    `--lock-dataset`, `--no-overwrite` and `--require-blq-rules`;
  - deckifyr 0.3.5 runs `serve` with `--root-path`, its API token, a fixed port and
    `--trusted-binaries-only`.
- Child process supervision (#12, #9). `core/proctree.py` spawns argv arrays only, with an
  allow-listed environment (it always drops `OPNCAFYR_*` and the variables that widen
  opnca-r's Host check), and kills whole process trees: freeze-and-walk on POSIX, a Job
  Object on Windows. `core/supervisor.py` starts one child per tool:
  - with a per-child token, a fixed loopback port (retried after a port race) and a
    launcher-owned cwd and TMPDIR;
  - waits for the adapter's readiness probe;
  - stops through the adapter's orderly stop, then a tree kill.
- `core/runtime.py` and `schemas/runtime-v1.schema.json`: the installed runtime record that
  names the absolute paths of the pinned executables.
- Descriptors can set `web_app.max_body_bytes`. The registry finds web-app adapters by
  package scan, and a test checks that `core/` never imports adapters or workflows.
- Project scaffold: loopback FastAPI controller with `/health`, Host/Origin and
  session-secret checks, workspace path canonicalization, job state machine, trusted tool
  descriptor registry, JSON Schemas (tool descriptor, workspace, run manifest,
  publication, recipe), adapter stubs for opnca, deckifyr and quartifyr, installer stubs,
  CI, and the plan of action.

### Changed
- quartifyr is pinned at 0.3.3, which releases quartifyr#81–#86: staged renders,
  `--soffice-bin`, the LibreOffice fixes for macOS and Windows, and
  `initialize_report_project_offline()` for one Python environment serving both passes.
  It was pinned within the release's 24-hour refresh window; re-verify the hashes after
  2026-11-08T00:29Z. The adapter workarounds and the LibreOffice recalculation switch are
  unchanged for now.
- deckifyr is pinned at 0.3.6, which releases deckifyr#93 (offline mode for the R
  wrappers, `init --from-repo` refused offline) and #94 (project files that start with a
  UTF-8 BOM, and no locale-encoding fallback on Windows). Its dependencies are unchanged.
- opnca-r is pinned at 0.2.8 (PR #48), which releases opnca-r#45 (macOS binaries built
  for R's own minimum: `macos_min` 13.0 on arm64, 11.0 on x86_64), #46 (save lock,
  `INCOMPLETE_SAVE`, `--require-explicit-settings`, `--no-guess-roles`,
  `effective_settings` in the record) and #47 (the shutdown reply always arrives whole).
  Its dependencies are unchanged. The macOS install spike now fails on a truncated
  shutdown reply. Our macOS minimum stays 15 and the adapter flags are unchanged for now.
- The README says opncafyr and opnca are under development and that public installation
  is not yet supported.
- Recorded the v1 platform matrix (ADR 0003, #1): Windows 11 x86_64, macOS arm64/x86_64
  (macOS 15 until opnca-r#45), R 4.6.2, a 30-minute configurable idle timeout and no
  native Linux. Also recorded the opnca-r install path (ADR 0004, #4): prebuilt binaries,
  with the vendored source tarball as the repair/exception fallback.
  `runtime/pins.toml` now has the platform table and the R pin.
- Reviewed the merged opnca-r (#23-#30, v0.2.6) and quartifyr (#69-#72) work against the
  issues we filed. `docs/upstream.md` now has a status column and the follow-ups
  (opnca-r #40-#43, quartifyr #75-#78). `docs/plan.md` risks are updated, and
  `runtime/pins.toml` pins opnca-r 0.2.6 assets and the quartifyr commit and tarball.
- Reviewed deckifyr 0.3.4 (#82-#86) and filed follow-ups #88-#91. `runtime/pins.toml` now
  pins the deckifyr 0.3.4 wheel, sdist and R tarball by SHA-256.
- Reviewed quartifyr 0.3.2 (#75-#78) and filed follow-ups #81-#82. `runtime/pins.toml`
  now pins the quartifyr 0.3.2 tag commit, tarball and `SHA256SUMS` by SHA-256.
- Reviewed deckifyr 0.3.5 (#88-#91) and pinned its wheel, sdist and R tarball by SHA-256.
  `upstream-blocked` removed from #33.
- Reviewed opnca-r 0.2.7 (#40-#43) and filed follow-ups #45-#46. `runtime/pins.toml` now
  pins the 0.2.7 binaries, vendored tarball, `manifest.json` and `SHA256SUMS` by SHA-256.
  `upstream-blocked` removed from #27.

### Fixed
- R jobs in a study folder whose path has a space (for example "PK Study") stopped at
  once with "'R_TempDir' contains space". Their temp folder was inside the study folder.
  Such a job now gets a private temp folder in the system temp folder instead, which is
  removed with the job (#12). This affected cleaning, the data-format check, replay and
  report jobs.

