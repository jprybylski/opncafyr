# Plan of action

**Updated:** 2026-11-06. **Repo:** jprybylski/opncafyr (private).
**Spec:** [spec.md](spec.md). **Upstream changes:** [upstream.md](upstream.md).

## Goal

Ship a no-admin, per-user workspace for Windows and macOS. A user installs it once, clicks
an icon, prepares a CSV, runs **one analyte at a time** in opnca-r, and publishes chosen
completed runs to a DOCX report or PPTX deck. Every step leaves a replayable, hashed
record. The core is tool-agnostic so other tools can be added later.

**Offline installation is a supported v1 use case** (owner decision, 2026-11-04).
Most installs will be online, but firewalled environments are common, especially GxP
cloud analytical systems. Offline isn't trivial, so each gate checks it rather than
leaving it to the final release.

## How the work is organised

- **Milestones** are the spec's five gates. A gate closes only when its exit evidence is
  shown, not when its issues are closed.
- **Labels:** `area:*` for the part of the system, `adapter:*` for a tool adapter,
  `spike` for time-boxed investigations that end in a written result, `decision-needed`
  for owner decisions, and `upstream-blocked` when a change in another repo is required.
- **Upstream work** is tracked in the tool's own repo (14 new issues plus comments on
  opnca-r #23 and #24). See [upstream.md](upstream.md).

## Gates and exit evidence

| Gate | Delivers | Exit evidence |
|---|---|---|
| 0. Installation proof | Owner decisions, dependency pins, clean-account install spikes | No elevation; opnca-r loads and calculates; Quarto renders; failures give redacted diagnostics; the same works with networking disabled from pre-fetched assets, and every network touchpoint is listed (#44) |
| 1. Reusable shell | Controller, security, jobs, registry, proxy, workspace picker, home UI, installer v1 | Second click reuses the instance; stale state recovers; a job survives tab close; no data leaves the machine; installer v1 installs both online and from an offline bundle (#36) |
| 2. Analysis path | Import, cleaning, mapping, validation, analyte snapshot, run bundle, opnca adapter | A two-analyte fixture yields two distinct scripts, snapshots, manifests and outputs, with no cross-analyte rows |
| 3. Publishing path | Sidecar validation, publication curator, quartifyr and deckifyr adapters | Report and deck use only the selected completed run hashes; missing metadata is caught before render; subpath behaviour passes |
| 4. Release hardening | Prebuilt binaries, updater/rollback, install matrix, diagnostics, signing, guide, second-tool proof | Standard-user install and upgrade pass without admin; an interrupted upgrade leaves the old version runnable |

## Critical path

```
#1 platform ─┬─► #4 binaries decision ──────────────────────────────► #34 binaries ─► #37 matrix
             └─► #5 pins ─► #6/#7 install spikes ─► #19 installer v1 ─┘
#9 controller ─► #11 security ─► #15 proxy ─► #16 base-path check ◄── opnca-r#24, deckifyr#82
#2 data conventions ─► #20 fixture ─► #21 import ─► #22 clean / #23 map ─► #24 checks
   ─► #25 snapshot ─► #26 bundle ─► #27 opnca launch (opnca-r#24/#25/#40 done)
   ─► #28 durable save (opnca-r#26/#27/#41 done) ─► #29 replay check
   ─► #31 curator ─► #32 quartifyr (quartifyr#69-72, #75-78 done in 0.3.2; #81-82 follow-ups)
                  └► #33 deckifyr  (deckifyr#82-86, #88-91 done in 0.3.5)
```

The three riskiest boundaries come straight from the spec's conclusion. They are
scheduled first.

1. **Native R install** (#4, #6, #7, #34): since v0.2.6 (2026-11-05), opnca-r ships
   R 4.6 binaries for macOS arm64/x86_64 and Windows x86_64, plus a vendored source
   tarball that builds offline. Each release has a SHA-256 `manifest.json`. Since v0.2.7
   the 1.87 MSRV is checked in CI. Clean-account installs and a renv cellar restore are
   ours to prove (#6, #7, #34). Since v0.2.8 the macOS binaries load on macOS 13 (arm64) and
   11 (x86_64) (opnca-r#45); our minimum stays macOS 15 until the rest of the stack is rechecked.
2. **Child apps behind a prefix** (#15, #16): opnca-r has supported a base path and
   allowed hosts since v0.2.5. deckifyr has supported a path prefix since v0.3.4, and
   rejects foreign Hosts and cross-site requests (with an optional launcher token) since
   v0.3.5. opnca-r has done the same since v0.2.7 (opnca-r#40).
3. **Durable opnca-r export** (#27, #28, #29): `opnca_serve()`, atomic server-side save
   and replayable `analysis.R` landed in v0.2.5. Since v0.2.7, `--no-overwrite` makes a saved
   run immutable (opnca-r#41).

## Sequencing

**Now (in parallel)**
- **Gate 0 install proof is done (#5–#8).** Every runtime and package is pinned with a
  SHA-256 ([dependency-graph.md](dependency-graph.md)). As a standard user with no admin
  prompt, Windows 11 and macOS 15 (arm64 and Intel) installed the stack, ran opnca-r with
  known answers, rendered a DOCX and built a PPTX, online and with the network blocked
  ([spikes/windows-install.md](spikes/windows-install.md),
  [spikes/macos-install.md](spikes/macos-install.md)). LibreOffice is an optional
  per-user download for slide previews ([spikes/libreoffice.md](spikes/libreoffice.md)).
  The redacted-diagnostics part of the exit evidence moved to the installer (#19): a
  failed install writes a redacted support bundle.
- **Installer v1 (#19) is done** ([install.md](install.md)). It installs per user, with
  no admin rights, from the pins, online or from local files. Each install is staged into
  its own `versions/<id>` and activated atomically, with an icon, repair and uninstall.
  The `Installer` workflow proves it as a new standard user on macOS arm64 and Intel and
  on Windows Server 2025: install, second run, launch, a failure after staging, and
  uninstall. Clean Windows 11 VMs remain #37.
- **Offline installation (#36) is done** ([install.md](install.md#offline-installation-for-it-administrators-36)).
  The bundle builder, `--from-bundle` (no network at all), mirror mode and the installation
  verification report are in place. CI installs, updates and rejects a tampered bundle
  with the user's network blocked, and any logged attempt fails the job. After the bundle
  install it also runs the Gate 2 flow end to end (#51). Attaching CI-built bundles to
  releases waits on the licensing review (#43).
- **Offline audit (#44)** is done: [offline.md](offline.md) lists every network
  touchpoint and its resolution. The `offline` CI job installs from local files and, with
  the network cut, runs the Gate 2 flow through the launcher's APIs and adapters (#51):
  import, clean, map, one opnca-r run per analyte, replay, publish, report and deck.
- **All Gate 0 decisions are made:** #1, #2, #3 and #4 (ADR 0003, ADR 0005, ADR 0007,
  ADR 0004).
- **Gate 1 shell work (#9–#18).** None of it depends on upstream changes. The scaffold
  covered part of it; the controller core (#9), local-origin hardening (#11), the job
  manager (#12) and the tool registry (#13) are done. Jobs run as `JobRequest`s through
  the job manager; the old `core.adapter.Adapter` Protocol was removed (#55).
- **Upstream work.** opnca-r#23–#30, quartifyr#69–#72 and #75–#78 (0.3.2), and
  deckifyr#82–#86 (0.3.4) landed on 2026-11-05. The review follow-ups are in
  [upstream.md](upstream.md). opnca-r#40–#43 shipped in 0.2.7, opnca-r#45–#47 in 0.2.8,
  opnca-r#49–#50 in 0.2.9 and deckifyr#88–#91 in 0.3.5, so no opncafyr issue is blocked upstream. The proxy slice (#15/#16) is done: both
  children run behind `/apps/<tool>/` with no rewriting ([spike result](spikes/basepath.md)).
- **Container image, Phase A (#42).** A pinned Linux image for users on cloud VMs that
  can run Docker. opnca-r is compiled once, when the image is built. The same image gives
  CI and the Gate 2/3 integration tests a working runtime while the native install spikes
  are still open. Phase B (cloud access through an SSH tunnel or IDE proxy) follows #11,
  #15 and #16.

**After #1 and #5**
- Install spikes #6 and #7 on clean standard-user accounts. They succeed only when an
  artifact renders, not when `cargo --version` works.

**Now that #2 is decided (ADR 0005)**
- The fixture generator (#20) and the import/clean/map/validate chain (#21–#24) can start.
  They use ADR 0005's rules and check ids. This chain can be built and tested before
  opnca-r changes land.

**opnca adapter (#27, #28)**
- Use opnca-r 0.2.9 directly: `scripts/serve.R` with the launcher flags (#27) and the
  server-side export (#28). The earlier temporary paths are no longer needed.
- Never read browser downloads. #29 checks the saved `analysis.R` against the UI's
  results.
- Gate 2's exit evidence is automated (2026-11-07):
  `tests/integration/nca/test_replay_r.py::test_gate_2_two_analytes_two_independent_runs`
  takes the two-analyte fixture through cleaning, mapping, checks, two snapshots, two
  opnca-r sessions and saves, two replays and two sealed runs, with no cross-analyte rows.
  It runs with the CI proxy harness job. Points excluded inside an opnca-r session are
  added to the launcher's exclusions since opnca-r 0.2.9
  ([opnca-r#49](https://github.com/mantra-opnca/opnca-r/issues/49)), and the replay
  reproduces them.

**Gate 3**
- Starts once #29 proves runs can be replayed. The quartifyr adapter (#32) calls quartifyr
  0.3.3's `render.R --strict --json --offline` and `check.R --json` in a staging copy of
  the project that holds only the bound publication's files. It keeps only the DOCX
  listed in the result, after re-hashing it. Quarto refuses a project inside a hidden
  folder, so the render copy lives in the build's staging folder rather than
  `.opncafyr/jobs/`.

**Gate 4**
- Closes the release. #41 is done (2026-11-07): the toy `example_echo` adapter runs a
  job and a web app with no change under `core/`, and
  [adding-a-tool.md](adding-a-tool.md) is the guide.
- #38 is done (2026-11-07): Settings → Support and `opncafyr doctor --bundle` save a
  redacted support bundle locally after a preview
  ([architecture.md](architecture.md#support-bundle-38)). A test runs the two-analyte
  fixture and checks that none of its subject IDs or concentrations reach the bundle.
- #40 is done (2026-11-07): [user-guide/](user-guide/index.md), shown inside the app
  under **Guide**, with a two-analyte walkthrough and screenshots from the fixture flow.
  opnca-r's `analysis.R` research-use line is opnca-r#53.
- #34 is done (2026-11-07): opnca-r installs from its verified binary with base R, never
  touching the user's R library (ADR 0004 amendment), and `repair --build-from-source`
  builds the vendored tarball. CI's `source-build-*` jobs prove both on macOS (offline) and
  Windows as a standard user. #37's matrix can build on them.

## Risks

| Risk | Effect | Mitigation | Issues |
|---|---|---|---|
| opnca-r source build fails on user machines (Rust, Rtools, linker, R ABI) | Install blocked for the target users | Prebuilt binaries are the release target; the source build is a tested fallback only. Users on cloud VMs that can run Docker use the container image instead (#42) | #4, #6, #7, #34, #42, opnca-r#28, opnca-r#23 |
| Child apps break behind `/apps/<tool>/` | Embedded editors don't work | Configurable base path upstream; a proxy rewrite only as a tested temporary measure | #15, #16, opnca-r#24, deckifyr#82 |
| Saved opnca-r run replaced or half-written | Runs aren't reproducible or immutable | Upstream atomic save (done); only `COMPLETE` plus re-verified `artifacts.json` counts; launch with `--no-overwrite`, one folder per session | #27, #28, #29, opnca-r#26, opnca-r#41 |
| A cross-site page drives a child's loopback port directly | Exclusions, results or decks changed without the session secret | Upstream Host allow-list, API token injected by the proxy, and rejection of cross-site requests; negative test in the harness | #16, #27, #33, opnca-r#40, deckifyr#88 |
| A deck project file names an executable or a path outside the project | Breaks the rule that workspace files never name an executable | Pass `--trusted-binaries-only` with absolute pinned `--rscript-bin`/`--quarto-bin`/`--soffice-bin` (done upstream in 0.3.5) | #33, deckifyr#89 |
| A deck project's Quarto fragments (code chunks, filters) or reportifyr `.rds` files run code | Workspace files execute code despite the trusted-binaries rule | **Decided (owner, 2026-11-07):** deck and report projects are trusted, user-authored projects. Quarto blocks (see #52) and flextable `.rds` files, possibly made by other tools, are allowed. Code runs only through the pinned binaries (`--trusted-binaries-only`), with an allow-listed environment and launcher-owned staging copies. The generated NCA results deck contains no executable fragments, and data text is quoted or reduced to plain words before it goes into a deck or report source | #33, #52 |
| Partial or stale report renders look complete | Silent incomplete deliverables | `render.R --strict --json` (done upstream); render in staging; trust only hashes in the result | #32, quartifyr#69, quartifyr#77, quartifyr#81 |
| Unsaved deck edits lost on stop | Data loss | `POST /api/shutdown` refuses while there are unsaved edits (done); the launcher's idle timer and Quit check deckifyr's `dirty` flag and ask before saving (done, #14); SIGTERM only as a fallback | #14, #33, deckifyr#83 |
| Cloud users reach the app through an SSH tunnel or IDE proxy (different Host/Origin, URL prefix) | App unreachable, or security checks loosened ad hoc | Explicit base-path and allow-list settings with tested presets; the session token is always required; the port is published to VM loopback only | #42, #15, #16, opnca-r#24, deckifyr#82 |
| Shared-origin localStorage leaks a mapping between analytes | Wrong column mapping | Scope or disable it upstream; the launcher always passes an explicit mapping | #27, opnca-r#30 |
| BLQ semantics lost or defaulted on hand-off | Wrong NCA | opnca-r maps LLOQ/BLQ (done); the launcher asks for all three BLQ rules and passes a full preset; upstream `--require-blq-rules` (done); the adapter also presets `blq_method`; upstream `--require-explicit-settings` (opnca-r#46, done) | #23, #27, opnca-r#29, opnca-r#42 |
| LibreOffice can't be packaged per user | No slide previews or field recalculation | Mark the features unavailable; never required for a build | #8, deckifyr#84 |
| Organisational application controls block user-local binaries | Install fails despite no-admin design | Detect it in preflight; signing and notarization; clear diagnostics | #19, #38, #39 |
| Scientific rules decided ad hoc in code | Inconsistent analyses | `decision-needed` issue #2 must be answered first; checks are configurable but never silent | #2, #22, #24 |
| Tools reach the network during install or use (pyro uv install and sync, `quarto add`, renv bootstrap, crates.io) | Install or reports fail in firewalled environments | Offline audit with a network-blocked CI job; app-owned uv plus offline environment settings; bundled wheels, extensions and packages; upstream fixes | #44, #36, opnca-r#23, quartifyr#72, quartifyr#76, quartifyr#82 |

## Decision log

| Decision | Status | Issue |
|---|---|---|
| Python/FastAPI loopback controller, browser UI | Accepted | [ADR 0001](decisions/0001-python-fastapi-controller.md) |
| Tool-agnostic core; trusted descriptors and adapters; workflow packs | Accepted | [ADR 0002](decisions/0002-tool-agnostic-core.md) |
| Workspace metadata folder is `.opncafyr/`, with a versioned schema | Accepted | spec allowed a rename |
| No reportifyr changes; sidecar validation lives in the launcher | Accepted | #30 |
| Offline installation supported in v1 alongside the default online install (native and container) | **Accepted** (owner, 2026-11-04) | #1, #36, #44, [offline.md](offline.md) |
| v1 matrix: Windows 11 x86_64, macOS arm64/x86_64 (macOS 15 until opnca-r#45), R 4.6.2; 30-min configurable idle timeout; no native Linux | **Accepted** (owner, 2026-11-06) | [ADR 0003](decisions/0003-platform-matrix.md), #1 |
| Data conventions and warning-vs-blocking checks: exact-code analyte filter plus specimen role, declared units, explicit BLQ/LLOQ steps and BLQ rules, reasons for every exclusion, duplicates and undosed profiles blocking | **Accepted** (owner, 2026-11-05); provisional until checked on sponsor data | [ADR 0005](decisions/0005-data-conventions.md), #2 |
| Generic quartifyr NCA report and deckifyr bundled default deck (both shipped, never fetched); all four artifact types pre-selected; coded subject IDs allowed in outputs, never in logs/URLs/diagnostics; direct identifiers need an explicit choice; source copied by default | **Accepted** (owner, 2026-11-06) | [ADR 0007](decisions/0007-templates-and-identifiable-data.md), #3 |
| opnca-r from prebuilt binaries; vendored source tarball only as a repair/exception fallback (option C) | **Accepted** (owner, 2026-11-06) | [ADR 0004](decisions/0004-opnca-r-binaries-with-vendored-fallback.md), #4 |
| LibreOffice: optional verified per-user download for deckifyr previews (optional in offline bundles); quartifyr field recalculation off until quartifyr#84/#86 | From spike #8 (2026-11-06) | [spikes/libreoffice.md](spikes/libreoffice.md), #8 |
| Home UI: static HTML + vanilla JS ES modules, no build step, no third-party runtime JS; axe check in CI | **Accepted** (owner, 2026-11-05) | [ADR 0006](decisions/0006-home-ui-stack.md), #18; shell build #49 |
| Cloud container mode in v1: supported environments, Docker vs Podman, storage types | Open | #42, #1 |
| Redistributing image archives: audience, opncafyr license, GPL source obligations, PPM binaries | Open (needs legal) | #43 |

## Extending to other tools

To add a tool ([adding-a-tool.md](adding-a-tool.md)):
1. Ship `src/opncafyr/adapters/<tool>/descriptor.yaml`. It must validate against
   `tool-descriptor-v1`.
2. Declare its health check in the descriptor.
3. Add the adapter: `web_app()` returning a `core.adapter.WebAppAdapter` for a web app,
   and `CommandSpec` builders that a workflow submits as `JobRequest`s for jobs.
4. If the tool has a web app, make sure it works under a base path (see #16 for the
   automated check).

A new user journey, such as a non-NCA analysis, goes in a new package under
`src/opncafyr/workflows/` and reuses existing adapters. Neither step changes `core/`. The
toy `example_echo` adapter (#41) proves it: a job and a web app, added with no change
under `core/`, and left out of release wheels.

## Issue map

### Gate 0 – Installation proof

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #1 | Decide the supported platform, architecture, R minor, offline, and idle-timeout matrix | — | — |
| #2 | Decide data conventions: analyte field, units, BLQ, exclusions, duplicates, time, and blocking checks | — | — |
| #3 | Decide report/deck templates and the potentially-identifiable-data policy (decided: ADR 0007) | — | — |
| #4 | Decide whether v1 ships prebuilt opnca-r binaries or a compiler-backed source install | #1 | [opnca-r#28](https://github.com/mantra-opnca/opnca-r/issues/28), [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
| #5 | Produce the dependency graph and populate runtime/pins.toml with versions and checksums | #1 | [deckifyr#86](https://github.com/jprybylski/deckifyr/issues/86), [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
| #6 | Spike: clean standard-user install on Windows through to a rendered artifact | #5 | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23), [opnca-r#28](https://github.com/mantra-opnca/opnca-r/issues/28) |
| #7 | Spike: clean standard-user install on macOS through to a rendered artifact | #5 | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23), [opnca-r#28](https://github.com/mantra-opnca/opnca-r/issues/28) |
| #8 | Spike: per-user LibreOffice packaging for deck previews and field recalculation | #5 | [deckifyr#84](https://github.com/jprybylski/deckifyr/issues/84) |
| #42 | Cloud container mode: pinned Docker image for users on cloud compute (plus dev/CI reference runtime) | #11, #15, #16 (Phase B) | [opnca-r#24](https://github.com/mantra-opnca/opnca-r/issues/24), [deckifyr#82](https://github.com/jprybylski/deckifyr/issues/82), [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
| #44 | Offline-first audit: inventory and eliminate every network touchpoint at install and run time | — | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23), [quartifyr#72](https://github.com/jprybylski/quartifyr/issues/72) |

### Gate 1 – Reusable shell

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #9 | Controller core: loopback ephemeral port, /health, and browser open after health | — | — |
| #10 | Single-instance lock and state record with stale-lock recovery | #9 | — |
| #11 | Harden the local origin: session secret, Host/Origin checks, path canonicalization, argv-only subprocesses, log redaction | #9 | — |
| #12 | Job manager: persisted state machine, process-tree cancellation, human-readable progress | #9, #11 | — |
| #13 | Adapter registry and versioned tool descriptor schema | #9 | — |
| #14 | Lifecycle: page heartbeats, explicit Quit, idle shutdown, quit-during-job, unsaved-editor block | #9, #10, #12 | [deckifyr#83](https://github.com/jprybylski/deckifyr/issues/83) |
| #15 | Reverse proxy for managed child apps under /apps/<tool>/ | #9, #11, #13 | — |
| #16 | Spike + automated check: opnca-r and deckifyr behind the /apps/<tool>/ proxy | #15 | [opnca-r#24](https://github.com/mantra-opnca/opnca-r/issues/24), [deckifyr#82](https://github.com/jprybylski/deckifyr/issues/82) |
| #17 | Workspace selection: native folder picker, .opncafyr/workspace.json, schema migrations | #9, #11 | — |
| #18 | Home page UI shell and frontend stack decision (decided: ADR 0006) | #9, #17 | — |
| #49 | Home page UI shell (ADR 0006) | #12, #14, #17 | — |
| #19 | Installer v1: per-user staging, atomic activation, desktop icon, repair and uninstall | #5, #6, #7, #9 | — |
| #36 | Offline installation (supported v1 use case): air-gapped bundles, bundle builder, and internal-mirror mode | #44, #5, #19 | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23), [opnca-r#28](https://github.com/mantra-opnca/opnca-r/issues/28), [deckifyr#86](https://github.com/jprybylski/deckifyr/issues/86), [quartifyr#72](https://github.com/jprybylski/quartifyr/issues/72) |

### Gate 2 – Analysis path

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #20 | Seeded generator for the two-analyte scientific test fixture | #2 | — |
| #21 | CSV import and shallow workspace scan with column/type/analyte preview | #17, #20 | — |
| #22 | Cleaning recipes: versioned YAML schema, R executor, preview, row accounting, rejected-row log | #21, #2, #12 | — |
| #23 | Column-role mapping with suggestions ported from makecoolplots and explicit user choice | #21, #2 | [opnca-r#29](https://github.com/mantra-opnca/opnca-r/issues/29) |
| #24 | Pre-analysis validation checks with warning/blocking levels | #23, #2 | — |
| #25 | Exact-value analyte filter and frozen one-analyte input snapshot | #22, #24 | — |
| #26 | Run bundle layout and run-manifest-v1: atomic writes, schema validation, immutability | #25, #12 | — |
| #27 | opnca adapter: launch an opnca-r session from a one-analyte snapshot | #25, #15, #13 | [opnca-r#25](https://github.com/mantra-opnca/opnca-r/issues/25), [opnca-r#24](https://github.com/mantra-opnca/opnca-r/issues/24), [opnca-r#30](https://github.com/mantra-opnca/opnca-r/issues/30), [opnca-r#29](https://github.com/mantra-opnca/opnca-r/issues/29) |
| #28 | opnca adapter: durable, atomic save of an analysis into the run directory | #27, #26 | [opnca-r#26](https://github.com/mantra-opnca/opnca-r/issues/26), [opnca-r#27](https://github.com/mantra-opnca/opnca-r/issues/27), [opnca-r#49](https://github.com/mantra-opnca/opnca-r/issues/49) |
| #29 | Script replay verification job: re-run analysis.R and compare to the UI results within tolerance | #28 | [opnca-r#26](https://github.com/mantra-opnca/opnca-r/issues/26) |

### Gate 3 – Publishing path

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #30 | Validate reportifyr artifact metadata sidecars in the launcher | #26 | [quartifyr#70](https://github.com/jprybylski/quartifyr/issues/70) |
| #31 | Publication curator: approve artifacts from completed runs into OUTPUTS/ with a publication manifest | #29, #30, #3 | — |
| #32 | quartifyr adapter: report project setup, preflight, render job, and build manifest | #31, #12 | [quartifyr#69](https://github.com/jprybylski/quartifyr/issues/69), [quartifyr#70](https://github.com/jprybylski/quartifyr/issues/70), [quartifyr#71](https://github.com/jprybylski/quartifyr/issues/71) |
| #33 | deckifyr adapter: deck project, managed editor under /apps/deckifyr/, validate/build jobs, build manifest | #31, #15, #16, #14 | [deckifyr#82](https://github.com/jprybylski/deckifyr/issues/82), [deckifyr#83](https://github.com/jprybylski/deckifyr/issues/83), [deckifyr#84](https://github.com/jprybylski/deckifyr/issues/84), [deckifyr#85](https://github.com/jprybylski/deckifyr/issues/85), [deckifyr#86](https://github.com/jprybylski/deckifyr/issues/86) |

### Gate 4 – Release hardening

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #34 | Install opnca-r from prebuilt platform binaries via the renv cellar | #4, #19 | [opnca-r#28](https://github.com/mantra-opnca/opnca-r/issues/28), [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
| #35 | Updater with rollback: an interrupted upgrade leaves the old version runnable | #19 | — |
| #37 | Automated clean install and upgrade test matrix on standard-user VMs | #19, #35, #34 | — |
| #38 | Redacted diagnostic and support bundle export | #11, #3 | — |
| #39 | Code signing (Windows) and notarization (macOS) for installer and launcher | #19 | — |
| #40 | User guide with research-use notice | #32, #33 | — |
| #41 | "Adding a tool" guide plus a toy second adapter that proves the core is tool-agnostic | #13, #15, #12 | — |
| #43 | Attach offline Docker image archives to each release (pending a redistribution/licensing review) | #42 | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
