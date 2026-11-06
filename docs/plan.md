# Plan of action

**Updated:** 2026-11-04. **Repo:** jprybylski/opncafyr (private).
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
   ─► #25 snapshot ─► #26 bundle ─► #27 opnca launch ◄── opnca-r#40 (#25 done)
   ─► #28 durable save (opnca-r#26/#27 done) ─► #29 replay check
   ─► #31 curator ─► #32 quartifyr (quartifyr#69-72, #75-78 done in 0.3.2; #81-82 follow-ups)
                  └► #33 deckifyr  (deckifyr#82-86, #88-91 done in 0.3.5)
```

The three riskiest boundaries come straight from the spec's conclusion. They are
scheduled first.

1. **Native R install** (#4, #6, #7, #34): since v0.2.6 (2026-11-05), opnca-r ships
   R 4.6 binaries for macOS arm64/x86_64 and Windows x86_64, plus a vendored source
   tarball that builds offline. Each release has a SHA-256 `manifest.json`. Still
   unverified upstream: clean-account installs, renv cellar restore, and the 1.87 MSRV
   (opnca-r#43).
2. **Child apps behind a prefix** (#15, #16): opnca-r has supported a base path and
   allowed hosts since v0.2.5. deckifyr has supported a path prefix since v0.3.4, and
   rejects foreign Hosts and cross-site requests (with an optional launcher token) since
   v0.3.5. The opnca-r child port still accepts cross-site POSTs (opnca-r#40).
3. **Durable opnca-r export** (#27, #28, #29): `opnca_serve()`, atomic server-side save
   and replayable `analysis.R` landed in v0.2.5. A saved run can still be overwritten from
   the UI (opnca-r#41).

## Sequencing

**Now (in parallel)**
- **Offline audit (#44).** Map every point where install or runtime reaches the network.
  The biggest known one: quartifyr and reportifyr project setup goes through pyro, which
  can `curl` uv and run `uv lock`/`uv sync`. opnca-r#23 (vendored crates) and
  quartifyr#72 (offline project setup) are done, and quartifyr#76 (offline-safe render)
  shipped in 0.3.2 as an opt-in `--offline` flag, which our adapter must always pass.
- **Decisions #1–#4.** The owner needs representative, deidentified datasets and target
  machines to decide these.
- **Gate 1 shell work (#9–#18).** None of it depends on upstream changes. The scaffold
  already implements part of #9, #11 and #13. Each of those issues has a comment saying
  what is left.
- **Upstream work.** opnca-r#23–#30, quartifyr#69–#72 and #75–#78 (0.3.2), and
  deckifyr#82–#86 (0.3.4) landed on 2026-11-05. The review follow-ups are in
  [upstream.md](upstream.md). Next: opnca-r#40 (security; blocks #27). deckifyr#88–#91
  shipped in 0.3.5.
- **Container image, Phase A (#42).** A pinned Linux image for users on cloud VMs that
  can run Docker. opnca-r is compiled once, when the image is built. The same image gives
  CI and the Gate 2/3 integration tests a working runtime while the native install spikes
  are still open. Phase B (cloud access through an SSH tunnel or IDE proxy) follows #11,
  #15 and #16.

**After #1 and #5**
- Install spikes #6 and #7 on clean standard-user accounts. They succeed only when an
  artifact renders, not when `cargo --version` works.

**After #2**
- The fixture generator (#20) and the import/clean/map/validate chain (#21–#24). This
  chain can be built and tested before opnca-r changes land.

**opnca adapter (#27, #28)**
- Start on the **temporary paths**: launch with an RDS file through the existing private
  entry point, and save by replaying through the public `opnca()` API.
- Switch to the upstream entry point and server export when they ship.
- The temporary paths never read browser downloads, and #29 checks them against the UI's
  results.

**Gate 3**
- Starts once #29 proves runs can be replayed. The quartifyr adapter calls quartifyr
  0.3.2's `render.R --strict --json --offline` and `check.R --json` (quartifyr#69/#78),
  in a staging copy of the project, until quartifyr#81 lands.

**Gate 4**
- Closes the release. #41 adds a toy second adapter to prove that `core/` stays
  tool-agnostic.

## Risks

| Risk | Effect | Mitigation | Issues |
|---|---|---|---|
| opnca-r source build fails on user machines (Rust, Rtools, linker, R ABI) | Install blocked for the target users | Prebuilt binaries are the release target; the source build is a tested fallback only. Users on cloud VMs that can run Docker use the container image instead (#42) | #4, #6, #7, #34, #42, opnca-r#28, opnca-r#23 |
| Child apps break behind `/apps/<tool>/` | Embedded editors don't work | Configurable base path upstream; a proxy rewrite only as a tested temporary measure | #15, #16, opnca-r#24, deckifyr#82 |
| Saved opnca-r run replaced or half-written | Runs aren't reproducible or immutable | Upstream atomic save (done); only `COMPLETE` plus re-verified `artifacts.json` counts; block `overwrite` until upstream adds `--no-overwrite` | #27, #28, #29, opnca-r#26, opnca-r#41 |
| A cross-site page drives a child's loopback port directly | Exclusions, results or decks changed without the session secret | Upstream Host allow-list, API token injected by the proxy, and rejection of cross-site requests; negative test in the harness | #16, #27, #33, opnca-r#40, deckifyr#88 |
| A deck project file names an executable or a path outside the project | Breaks the rule that workspace files never name an executable | Pass `--trusted-binaries-only` with absolute pinned `--rscript-bin`/`--quarto-bin`/`--soffice-bin` (done upstream in 0.3.5) | #33, deckifyr#89 |
| A deck project's Quarto fragments (code chunks, filters) or reportifyr `.rds` files run code | Workspace files execute code despite the trusted-binaries rule | Open decision: the launcher writes every fragment itself, and/or ask deckifyr for a mode that refuses executable fragments | #33 |
| Partial or stale report renders look complete | Silent incomplete deliverables | `render.R --strict --json` (done upstream); render in staging; trust only hashes in the result | #32, quartifyr#69, quartifyr#77, quartifyr#81 |
| Unsaved deck edits lost on stop | Data loss | `POST /api/shutdown` refuses while there are unsaved edits (done); SIGTERM only as a fallback | #14, #33, deckifyr#83 |
| Cloud users reach the app through an SSH tunnel or IDE proxy (different Host/Origin, URL prefix) | App unreachable, or security checks loosened ad hoc | Explicit base-path and allow-list settings with tested presets; the session token is always required; the port is published to VM loopback only | #42, #15, #16, opnca-r#24, deckifyr#82 |
| Shared-origin localStorage leaks a mapping between analytes | Wrong column mapping | Scope or disable it upstream; the launcher always passes an explicit mapping | #27, opnca-r#30 |
| BLQ semantics lost or defaulted on hand-off | Wrong NCA | opnca-r maps LLOQ/BLQ (done); the launcher asks for all three BLQ rules and passes a full preset; upstream `--require-blq-rules` | #23, #27, opnca-r#29, opnca-r#42 |
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
| Offline installation supported in v1 alongside the default online install (native and container) | **Accepted** (owner, 2026-11-04) | #1, #36, #44 |
| Supported platforms, R minor, idle timeout | Open | #1 |
| Data conventions and warning-vs-blocking checks | Open | #2 |
| Report/deck templates; identifiable-data policy | Open | #3 |
| Binaries first vs. compiler-backed first install | Open (spec recommends binaries) | #4 |
| Home UI frontend stack | Open | #18 |
| Cloud container mode in v1: supported environments, Docker vs Podman, storage types | Open | #42, #1 |
| Redistributing image archives: audience, opncafyr license, GPL source obligations, PPM binaries | Open (needs legal) | #43 |

## Extending to other tools

To add a tool:
1. Ship `src/opncafyr/adapters/<tool>/descriptor.yaml`. It must validate against
   `tool-descriptor-v1`.
2. Add a health check.
3. Add an adapter that implements `core.adapter.Adapter`.
4. If the tool has a web app, make sure it works under a base path (see #16 for the
   automated check).

A new user journey, such as a non-NCA analysis, goes in a new package under
`src/opncafyr/workflows/` and reuses existing adapters. Neither step changes `core/`. #41
makes this concrete with a guide and a toy adapter.

## Issue map

### Gate 0 – Installation proof

| # | Issue | Depends on | Upstream |
|---|---|---|---|
| #1 | Decide the supported platform, architecture, R minor, offline, and idle-timeout matrix | — | — |
| #2 | Decide data conventions: analyte field, units, BLQ, exclusions, duplicates, time, and blocking checks | — | — |
| #3 | Decide report/deck templates and the potentially-identifiable-data policy | — | — |
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
| #18 | Home page UI shell and frontend stack decision | #9, #17 | — |
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
| #28 | opnca adapter: durable, atomic save of an analysis into the run directory | #27, #26 | [opnca-r#26](https://github.com/mantra-opnca/opnca-r/issues/26), [opnca-r#27](https://github.com/mantra-opnca/opnca-r/issues/27) |
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
