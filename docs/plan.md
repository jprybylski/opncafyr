# Plan of action

**Updated:** 2026-11-04. **Repo:** jprybylski/opncafyr (private).
**Spec:** [spec.md](spec.md). **Upstream changes:** [upstream.md](upstream.md).

## Goal

Ship a no-admin, per-user workspace for Windows and macOS. A user installs it once, clicks
an icon, prepares a CSV, runs **one analyte at a time** in opnca-r, and publishes chosen
completed runs to a DOCX report or PPTX deck. Every step leaves a replayable, hashed
record. The core is tool-agnostic so other tools can be added later.

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
| 0. Installation proof | Owner decisions, dependency pins, clean-account install spikes | No elevation; opnca-r loads and calculates; Quarto renders; failures give redacted diagnostics |
| 1. Reusable shell | Controller, security, jobs, registry, proxy, workspace picker, home UI, installer v1 | Second click reuses the instance; stale state recovers; a job survives tab close; no data leaves the machine |
| 2. Analysis path | Import, cleaning, mapping, validation, analyte snapshot, run bundle, opnca adapter | A two-analyte fixture yields two distinct scripts, snapshots, manifests and outputs, with no cross-analyte rows |
| 3. Publishing path | Sidecar validation, publication curator, quartifyr and deckifyr adapters | Report and deck use only the selected completed run hashes; missing metadata is caught before render; subpath behaviour passes |
| 4. Release hardening | Prebuilt binaries, updater/rollback, install matrix, diagnostics, signing, guide, second-tool proof | Standard-user install and upgrade pass without admin; an interrupted upgrade leaves the old version runnable |

## Critical path

```
#1 platform ─┬─► #4 binaries decision ──────────────────────────────► #34 binaries ─► #37 matrix
             └─► #5 pins ─► #6/#7 install spikes ─► #19 installer v1 ─┘
#9 controller ─► #11 security ─► #15 proxy ─► #16 base-path check ◄── opnca-r#24, deckifyr#82
#2 data conventions ─► #20 fixture ─► #21 import ─► #22 clean / #23 map ─► #24 checks
   ─► #25 snapshot ─► #26 bundle ─► #27 opnca launch ◄── opnca-r#25
   ─► #28 durable save ◄── opnca-r#26/#27 ─► #29 replay check
   ─► #31 curator ─► #32 quartifyr ◄── quartifyr#69-71
                  └► #33 deckifyr  ◄── deckifyr#82-86
```

The three riskiest boundaries come straight from the spec's conclusion. They are
scheduled first.

1. **Native R install** (#4, #6, #7, then opnca-r#28): opnca-r is source-only today, and
   building it needs Rust plus a matching linker. On top of that, the v0.2.4 tarball does
   **not** bundle vendored crates, so it also needs network access to crates.io. The
   release target is prebuilt binaries.
2. **Child apps behind a prefix** (#15, #16, then opnca-r#24 and deckifyr#82): both apps
   use root-relative URLs today. opnca-r also rejects proxied Host headers.
3. **Durable opnca-r export** (#27, #28, #29, then opnca-r#25, #26, #27): export is all
   browser downloads today. The generated script reads a placeholder `your_data.csv`.

## Sequencing

**Now (in parallel)**
- **Decisions #1–#4.** The owner needs representative, deidentified datasets and target
  machines to decide these.
- **Gate 1 shell work (#9–#18).** None of it depends on upstream changes. The scaffold
  already implements part of #9, #11 and #13. Each of those issues has a comment saying
  what is left.
- **Upstream work.** Start with opnca-r#25, #26 and #24, which Gate 2 needs, and
  deckifyr#82, which Gate 3 needs.

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
- Starts once #29 proves runs can be replayed. Until quartifyr#69 lands, the quartifyr
  adapter wraps `render_report()` with `options(warn = 2)`, a `.rpfy-logs` scan and a
  leftover-`{rpfy}:` scan.

**Gate 4**
- Closes the release. #41 adds a toy second adapter to prove that `core/` stays
  tool-agnostic.

## Risks

| Risk | Effect | Mitigation | Issues |
|---|---|---|---|
| opnca-r source build fails on user machines (Rust, Rtools, linker, R ABI) | Install blocked for the target users | Prebuilt binaries are the release target; the source build is a tested fallback only | #4, #6, #7, #34, opnca-r#28, opnca-r#23 |
| Child apps break behind `/apps/<tool>/` | Embedded editors don't work | Configurable base path upstream; a proxy rewrite only as a tested temporary measure | #15, #16, opnca-r#24, deckifyr#82 |
| No durable opnca-r export | Runs aren't reproducible | Temporary replay through the public API, checked against UI results | #27, #28, #29, opnca-r#25, opnca-r#26 |
| Partial report renders exit 0 | Silent incomplete deliverables | Adapter treats warnings as errors and scans logs and placeholders; upstream job contract | #32, quartifyr#69, quartifyr#70 |
| Unsaved deck edits lost on stop | Data loss | Dirty state on health check; save or refuse before stop | #14, #33, deckifyr#83 |
| Shared-origin localStorage leaks a mapping between analytes | Wrong column mapping | Scope or disable it upstream; the launcher always passes an explicit mapping | #27, opnca-r#30 |
| BLQ semantics lost on hand-off | Wrong NCA | opnca-r app maps LLOQ/BLQ; mapping is passed explicitly | #23, opnca-r#29 |
| LibreOffice can't be packaged per user | No slide previews or field recalculation | Mark the features unavailable; never required for a build | #8, deckifyr#84 |
| Organisational application controls block user-local binaries | Install fails despite no-admin design | Detect it in preflight; signing and notarization; clear diagnostics | #19, #38, #39 |
| Scientific rules decided ad hoc in code | Inconsistent analyses | `decision-needed` issue #2 must be answered first; checks are configurable but never silent | #2, #22, #24 |

## Decision log

| Decision | Status | Issue |
|---|---|---|
| Python/FastAPI loopback controller, browser UI | Accepted | [ADR 0001](decisions/0001-python-fastapi-controller.md) |
| Tool-agnostic core; trusted descriptors and adapters; workflow packs | Accepted | [ADR 0002](decisions/0002-tool-agnostic-core.md) |
| Workspace metadata folder is `.opncafyr/`, with a versioned schema | Accepted | spec allowed a rename |
| No reportifyr changes; sidecar validation lives in the launcher | Accepted | #30 |
| Supported platforms, R minor, offline need, idle timeout | Open | #1 |
| Data conventions and warning-vs-blocking checks | Open | #2 |
| Report/deck templates; identifiable-data policy | Open | #3 |
| Binaries first vs. compiler-backed first install | Open (spec recommends binaries) | #4 |
| Home UI frontend stack | Open | #18 |

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
| #36 | Offline installer bundle (if required by the platform decision) | #1, #34 | [opnca-r#23](https://github.com/mantra-opnca/opnca-r/issues/23) |
| #37 | Automated clean install and upgrade test matrix on standard-user VMs | #19, #35, #34 | — |
| #38 | Redacted diagnostic and support bundle export | #11, #3 | — |
| #39 | Code signing (Windows) and notarization (macOS) for installer and launcher | #19 | — |
| #40 | User guide with research-use notice | #32, #33 | — |
| #41 | "Adding a tool" guide plus a toy second adapter that proves the core is tool-agnostic | #13, #15, #12 | — |
