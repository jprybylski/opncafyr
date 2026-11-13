# Spike #67: slow use on Windows

- Date: 2026-11-13. Build: `main` at 3332763 (offline bundle from installer CI run
  43363881455), plus this branch's fixes.
- Machine: Windows 11 Pro build 26300, i9-10850K (20 threads), 64 GB, NVMe, Windows
  Defender real-time on (no other security product). The same machine's Docker Desktop
  (WSL2) ran the Linux image `ghcr.io/jprybylski/opncafyr:edge` (linux-x86_64) for a
  **same-hardware** comparison.
- The affected PC could not be measured. Its slowness is in **use**, not in install or
  startup, and the same product in Docker on a weaker cloud VM felt much faster.

**Result.** The mechanism is that on Windows each file open, folder walk and process start
costs 10–50 times more than in the Linux container on the same hardware (table 1). The
app did a lot of these on every poll and click, and in two places that work grew without
bound:

1. **Version history kept polling after the user left it.** Tool pages stay loaded in
   hidden iframes, and a hidden iframe still reports `document.visibilityState ===
   "visible"`. So once History was opened, gitneighbr's page kept asking for `status`,
   `changes` and `identity` every 2 s for the rest of the session. Each `status` took
   **0.92 s on Windows vs 0.22 s in the container** (R plus several Git process starts at
   35 ms each, vs 0.7 ms). On a slower PC with a heavier endpoint agent, that is a
   continuous background load of Git and scanning that slows everything else.
2. **Lists re-read the whole study.** The publish list re-read and re-validated every
   run's manifest three times and re-hashed every file of every completed run on each
   request. The runs/publications lists (one is polled every 15 s) re-read and
   re-validated every manifest.

Both are fixed (below). This also explains why the container felt faster on weaker
hardware: the per-file and per-process costs that dominate on Windows are tiny there.

## Method

`scripts/bench/interact.py` serves the real controller on a loopback port and drives it
over HTTP the way the pages do, with the synthetic `tests/fixtures/two_analyte.csv`
study: polls, views, cleaning, checks, a run, opnca-r through the proxy, save and replay,
publishing, report and deck, version history. It repeats each operation (cold first, then
median and p95) and repeats polls and views after 30 more completed runs. For each
operation it also counts files opened, folders listed, stats, path resolves and processes
started (`tests/perf_helpers.py`). It records counts, durations and coarse system facts
only: no paths, file names or data.

```sh
# Windows, with the installed launcher's Python; scripts/ and tests/ side by side
%LOCALAPPDATA%\opncafyr\versions\<id>\launcher\Scripts\python.exe scripts\bench\interact.py <empty dir>
# Container
docker run --rm --network none -v <repo>:/src:ro -e OPNCAFYR_APP_ROOT=/tmp/app -e HOME=/tmp \
  --entrypoint python ghcr.io/jprybylski/opncafyr:edge /src/scripts/bench/interact.py /tmp/run
```

The browser check for the hidden-frame fix used headless Chromium against the Windows
launcher through an SSH tunnel, counting gitneighbr `status` requests.

## 1. Host costs (same machine)

| Operation | Windows native | Linux container |
|---|---:|---:|
| Create 500 small files | 165 ms | 15–20 ms |
| Open and read 500 small files | 300 ms | 6 ms |
| Stat 500 files | 19 ms | 1.5 ms |
| Delete 500 files | 68 ms | 4.5 ms |
| Resolve 500 paths (`realpath`) | 68 ms | — |
| Start `git --version` (warm) | 34 ms | 0.7 ms |
| Start `Rscript` (warm) | 137 ms | 150 ms |

R's own startup is not slower on Windows. File and Git process costs are.

## 2. Before and after (Windows native, medians, 30 extra completed runs)

| Operation | How often | Before | After | Container (before) |
|---|---|---:|---:|---:|
| gitneighbr status polls with another view shown (per 20 s, browser) | every 2 s after History was opened once | 5 | **0** | — |
| gitneighbr `status` (one request) | each poll while History is shown | 916 ms | 916 ms ¹ | 220 ms |
| `GET /api/nca/publish/runs` | opening Publish | 803 ms, 587 opens | **84 ms, 0 opens** | 277 ms |
| `GET /api/workspace/artifacts` | poll, every 15 s | 99 ms, 45 opens | **37 ms, 0 opens** | 80 ms |
| `GET /api/nca/runs` | views | 84 ms | **30 ms** | 68 ms |
| `GET /api/nca/publications` | views | 33 ms | **24 ms** | 22 ms |
| `POST` publication | click | 136 ms | 124 ms | 56 ms |
| report render (job) | click | 12.7 s | 12.7 s | 9.2 s |
| open run in opnca-r | click | 2.6 s | 2.6 s | 1.5 s |

¹ Upstream: [gitneighbr#50](https://github.com/jprybylski/gitneighbr/issues/50), fewer Git
processes per poll while History is shown (tracked in #82).

The first look at the publish list in a session still hashes each completed run once
(589 ms for 32 runs here). After that, a run is hashed again only when one of its
files changed size or time.

## Fixes

- **Hidden tool frames report themselves hidden** (`web/static/js/toolview.js`): when a
  kept tool frame is out of sight, its document's `visibilityState` is `hidden` and it
  gets a `visibilitychange` event, as a background browser tab does. This is generic: no
  tool is named. UI test: `tests/ui/test_home_page.py::test_a_tool_out_of_sight_says_it_is_hidden`.
- **Validated documents are read once while unchanged** (`core.manifest.read_document`):
  keyed by size and modification time (and, on POSIX, file ID and ctime). Every write is
  an atomic rename, and an ordinary edit changes the time, so a changed document is
  always read again. Used for run manifests, the run and publication lists, and
  publication records. Each JSON Schema validator is also built once instead of per call.
- **The publish list reuses a full hash check while nothing changed**
  (`core.runs.verify_run(reuse=True)`). Only the list uses this. Publishing, report and
  deck builds, sealing and **Check files** hash every file, and verification reads the
  manifest afresh. Run folders are walked with `os.scandir` (one call per folder, not
  several per file).
- Count-bound tests (`tests/test_io_counts.py`) fail on the old code and pass on the new.
  They also check that a changed run is shown as unpublishable, and that an evasive edit
  (same size, restored time) is still refused when publishing and by **Check files**.

## Ruled out

- Disk latency: NVMe, local paths, and the same pattern on the same disk from WSL2.
- Launcher startup and the per-request floor: loopback HTTP is about 1.4 ms on both.
- R startup: equal on both (table 1).
- Network: everything is loopback, and the install was offline.

## For the affected PC

Install the fixed build. If it is still slow, the cause is per-file and per-process
scanning. IT can use Defender's performance analyzer (`New-MpPerformanceRecording`,
then `Get-MpPerformanceReport -TopProcesses -TopFiles`) or their endpoint vendor's
equivalent while History or Publish is in use. Any exclusion must be narrow and
reviewed, for example the app's own runtime folder under `%LOCALAPPDATA%\opncafyr\versions`,
never the study folder or broad patterns. opncafyr doesn't change security settings.

## Still open

- Upstream issue for gitneighbr to cut the Git processes per status poll:
  [gitneighbr#50](https://github.com/jprybylski/gitneighbr/issues/50), tracked in #82.
- `POST` publication hashes the same outputs several times in one request (124 ms here).
  A smaller gain, left as is.
- Dropped from #67 by the owner: keeping per-step durations in installer reports.
