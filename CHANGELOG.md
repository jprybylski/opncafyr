# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
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
