# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
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
