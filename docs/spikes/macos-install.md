# Spike #7: clean standard-user install on macOS

- Date: 2026-11-06
- Machines:
  - **arm64 VM:** tart 2.41.1, `ghcr.io/cirruslabs/macos-sequoia-base@sha256:eee659985b01e4df2a6c0754bd8d25e54969933c43fdcd4edef8bea337b3d354`,
    macOS 15.7.7 (24G720). A new standard user `spike` (not in `admin`). Gatekeeper
    turned on, the Command Line Tools (CLT) moved aside, no R anywhere. (SIP is off in
    tart images; see "Limits".)
  - **GitHub Actions:** `macos-15` (arm64, macOS 15.8.2) and `macos-15-intel` (x86_64,
    macOS 15.7.9), workflow `.github/workflows/install-spike.yml`. The job hides the
    preinstalled `R.framework`, R and Quarto, creates a standard user `spike` and runs
    the spike as that user. The runners keep Xcode, so they say nothing about the CLT.
- Pins: `runtime/pins.toml` (#5): uv 0.13.7, Python 3.13.18 (python-build-standalone
  20261026), CRAN R 4.6.2 `.pkg`, the 130-package `runtime/r/renv.lock` (P3M snapshot
  2026-11-06), opnca-r 0.2.7 macOS binaries, quartifyr 0.3.2, reportifyr 0.4.0, pyro 0.1.1,
  deckifyr 0.3.5 in `runtime/python/tools`, Quarto 1.11.9.
- Script: `scripts/spikes/macos-install.sh` (asset list from
  `scripts/spikes/spike_assets.py` + `assets_tsv.py`; offline profile
  `scripts/spikes/nonet.sb`).

**Result: a standard user can install the whole stack on macOS without admin rights,
without the Command Line Tools, and with no network once the pinned files are local.**
On arm64 (VM and CI) and Intel (CI), online and offline:
- opnca-r loads and reproduces the reference NCA values exactly;
- its web app answers `/api/v1/health` behind its base path;
- deckifyr builds a PPTX;
- quartifyr + reportifyr render a DOCX with status `ok`.

Nothing is written under `/Library`, and the user's own R is untouched. Four findings
change how the installer (#19) has to work:

1. **R isn't installed with rig, and can't live under `Application Support`.** rig 0.8
   (on the dev Mac) needs sudo on macOS; rig 0.11 has a user mode, but it downloads its
   own unpinned copy of R (see the Windows spike), so the installer doesn't use it
   either. The spike instead expands CRAN's `.pkg` and relocates
   `R.framework`, editing only text files (recipe below). R must be at a path **without
   a space**: renv runs `R CMD config`, i.e. `make` with `R_HOME/etc/Makeconf`, and R
   refuses a temporary directory that contains a space.
2. **A system-wide CRAN R of the same minor hijacks a relocated R** unless
   `DYLD_LIBRARY_PATH` is set. With only the fallback path, R 4.6.2 relocated on a Mac
   that has CRAN R 4.6.1 reported "R version 4.6.1": dyld loaded the system `libR` from
   its absolute path.
3. **`renv::restore()` doesn't work on a Mac without the CLT.** It always runs
   `R CMD config CC`, even for binaries, which calls `make`. In a GUI session that pops up
   the "install command line developer tools" dialog. Base R's `install.packages()` of
   the exact pinned binaries, plus the three pure-R tarballs from source, needs no
   toolchain, and every version is then checked against `renv.lock`.
4. **The vendored opnca-r source build (repair path) needs the CLT, i.e. admin.** Without
   the CLT it fails at `R CMD config`. With the CLT present it builds offline in about 80 s
   using the pinned Rust standalone toolchain (no rustup, no crates.io). Installing the
   CLT needs an admin password. That confirms the source path is not a standard-user path
   on macOS, which supports ADR 0004 (binaries only for normal installs).

## Results

Seconds per step (`logs/steps.tsv`). "VM" is the final run, without the CLT. CI is the
final run [42267176934](https://github.com/jprybylski/opncafyr/actions/runs/42267176934):
both jobs green, on = online, off = offline.

| Step | What | VM online | VM offline | CI arm64 on / off | CI Intel on / off | Outcome |
|---|---|---|---|---|---|---|
| preflight | not admin; no `R.framework`; Gatekeeper; CLT | 0 | 0 | 0 / 0 | 0 / 0 | ok |
| fetch | 170 files, SHA-256 against the pins (`curl` online; local offline) | 102 | 3 | 47 / 5 | 126 / 23 | ok; no file was quarantined |
| uv | extract the uv tarball | 0 | 0 | 0 / 1 | 1 / 1 | ok |
| python | `uv python install 3.13.18 --no-bin` from a `file://` `UV_PYTHON_INSTALL_MIRROR` | 1 | 1 | 12 / 0 | 70 / 3 | ok |
| tools_env | `uv export` of `runtime/python/tools/uv.lock` → `uv pip install --no-index --require-hashes --no-deps` from the wheelhouse | 1 | 1 | 2 / 2 | 12 / 11 | ok (Intel: pymupdf has no wheel, skipped) |
| r | expand CRAN `.pkg`, relocate `R.framework` | 3 | 3 | 4 / 3 | 14 / 24 | ok; R 4.6.2 on its own `libR`/`libRblas`/`libgfortran` |
| r_repo | local CRAN-like repository (126 binaries) + cellar | 3 | 3 | 3 / 3 | 9 / 15 | ok |
| r_renv_restore | `renv::restore()` (recorded only) | 29 | 19 | 46 / 43 | 132 / 185 | **fails** (see 3) |
| r_install | base-R install of the locked set; versions checked | 8 | 8 | 10 / 12 | 44 / 53 | ok: 130/130; `libR` and `libomp` from the relocated R |
| opnca_calc | `opnca()` on the opnca-r test profile; diff to reference < 1e-12 | 0 | 1 | 0 / 0 | 1 / 1 | ok on both architectures |
| opnca_serve | `scripts/serve.R` with the launcher flags; health; shutdown | 2 | 2 | 2 / 3 | 5 / 6 | ok (see "opnca-r") |
| deckifyr | `deckifyr init` + `build --trusted-binaries-only --rscript-bin` | 1 | 0 | 1 / 0 | 2 / 2 | ok, 2-slide PPTX |
| quarto | extract the Quarto tarball; `--version` | 1 | 1 | 1 / 2 | 12 / 9 | ok |
| report | reportifyr project setup + `render.R --strict --json --offline` | 5 | 5 | 7 / 7 | 49 / 36 | ok, DOCX, `status: ok` |
| quarantine | quarantine the installed runtimes in place, run each | — | 1 | — / 2 | — / 12 | all ran (see below) |
| vendored | build `opnca_0.2.7-vendored.tar.gz` offline | — | 37 (fails: no CLT) | — / 155 (ok, Xcode) | — / 646 (ok, Xcode) | needs the CLT; VM with the CLT restored: ok in 115 s |

Total, excluding the renv attempt: about 2 minutes online on arm64 (mostly downloading
790 MB) and under 30 s offline. On the Intel runner about 4 minutes online and 3 minutes
offline.

Reference values (opnca-r 0.2.7, `tests/testthat/test-basic-nca.R` profile): P1 cmax 10,
tmax 1, AUClast 24.837056812223246, λz 0.346573590279973, t½ 2; P2 cmax 20, AUClast
49.674113624446498. Both architectures match exactly.

Disk use (arm64): R 161 MB, R library 261 MB, Python 70 MB, tools env 288 MB, Quarto
687 MB, uv cache 276 MB (can be dropped after install), downloads 786 MB.

Files written outside the app root and R root: only `~/Library/Caches/deno` (Quarto's
Deno; set `DENO_DIR` to keep it inside the app root). Nothing under `/Library`; no
`~/.local/bin` (`--no-bin`).

## Relocating R without admin (recipe for #19)

1. `pkgutil --expand-full R-4.6.2-<arch>.pkg <tmp>`, then `ditto
   <tmp>/R-fw.pkg/Payload/R.framework <rroot>/4.6.2/R.framework`. The other sub-packages
   (R.app GUI, Tcl/Tk, texinfo) and the postflight script, which writes
   `/usr/local/bin` links and `chown`s to root, are skipped. `<rroot>` must not contain
   a space.
2. In `Resources/bin/R`, `etc/Renviron` and `etc/Makeconf`, replace
   `/Library/Frameworks/R.framework` with the new path. These are text files, so no
   binary changes and CRAN's signatures stay valid (`codesign --verify` passes).
3. In `bin/R`, after `export R_HOME`, add `RHOME="${R_HOME}"; export RHOME`. The
   `Rscript` binary has the framework path compiled in and honours only `RHOME`.
   opnca-r's `configure` and other R-started Rscripts need it.
4. Append to `etc/ldpaths`: `DYLD_LIBRARY_PATH="${R_HOME}/lib…"; export
   DYLD_LIBRARY_PATH`. R's own `bin/R` sources `ldpaths`, which already sets
   `DYLD_FALLBACK_LIBRARY_PATH=${R_HOME}/lib`. That handles a clean Mac: every absolute
   `/Library/Frameworks/R.framework/...` reference (`libR`, `libRblas`, `libgfortran`, and
   `libomp`/`libR` in the data.table and opnca package binaries) falls back to the
   relocated copy. `DYLD_LIBRARY_PATH` also wins over an installed system R (finding 2).
   `exec/R` is signed with `allow-dyld-environment-variables` and
   `disable-library-validation`, so dyld honours both. Because `ldpaths` is sourced
   after `/bin/sh` starts, SIP's stripping of `DYLD_*` doesn't matter. This was checked
   on the developer Mac with SIP on.
5. Callers run a two-line wrapper `<app>/bin/Rscript` (sets `RHOME`, execs
   `R_HOME/bin/Rscript`), so the runtime record (`runtime-v1`) can name one absolute
   executable.
6. Set `TMPDIR` to a space-free directory. macOS's per-user `getconf
   DARWIN_USER_TEMP_DIR` works.

## Online vs offline

Both variants run the same install steps. They differ only in where the files come
from: `--online` downloads what is missing, while offline every file is already local.
Either way each file is checked against its pinned SHA-256 first. The offline run is
the whole script under `sandbox-exec -f scripts/spikes/nonet.sb`, which denies all
outbound network except loopback and unix sockets. Everything passed. Settings that
make it work:
- `UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=<wheelhouse> UV_PYTHON_DOWNLOADS=manual`,
  `UV_PYTHON_INSTALL_MIRROR=file://<dir>` with `<dir>/20261026/<archive>`.
- The tools env can't be restored with `uv sync --locked` offline. deckifyr is pinned by
  URL in that lock, and uv fetches the URL even when the wheel is in `UV_FIND_LINKS`.
  The spike exports the lock, points deckifyr at the verified local wheel and runs
  `uv pip install --require-hashes --no-deps`.
- reportifyr's project setup goes through pyro (`uv lock` + `uv sync` of its
  `reportifyr` group). It works offline because:
  - our uv is first on `PATH`, so pyro reports "Using existing uv 0.13.7" and never curls;
  - pyro's pins (python-docx 1.1.2, PyYAML 6.0.2, Pillow 11.1.0) are in the tools
    wheelhouse.

  It fails if the project already has a `uv.lock` naming other wheels (quartifyr's demo
  ships one), so app-made report projects must not carry a foreign `uv.lock`.
- quartifyr: `render.R --offline` with `QUARTIFYR_PYTHON_ENV=<tools env>`.

## Quarantine and Gatekeeper

The spike installs files with `curl`, `tar`, `ditto` and `pkgutil`, so nothing it writes
is quarantined. To see what a browser-downloaded bundle would do, the spike quarantined
the installed uv, Python, R and Quarto trees in place (`com.apple.quarantine`, agent
"Safari") with Gatekeeper on. Every runtime still ran, both from an SSH session and from
a process started in the user's GUI session (`launchctl asuser`):
- uv is notarized (Developer ID);
- CRAN R is signed by Simon Urbanek (Developer ID);
- Quarto's Deno is signed;
- the python-build-standalone interpreter is only ad-hoc signed.

`spctl -t exec` "rejects" each of them ("does not seem to be an app"), but that
assessment isn't applied when a command-line tool is executed. Recommendation: the
installer verifies each file's hash, then clears the attribute on its own extracted
files (`xattr -dr com.apple.quarantine`). The launcher `.app` wrapper itself is subject
to Gatekeeper and needs signing and notarization (#39).

## opnca-r

- `serve.R` requires an existing `--output-dir` and a shutdown token of at least 16
  characters. With the launcher flags it printed `OPNCA_READY` in 2 s (VM) and answered
  health with `{"ok":true,...}`.
- Twice in four CI jobs (Intel online in an earlier run, arm64 offline in the final run)
  the `POST api/v1/shutdown` response was cut off: a 202 status, then the connection
  closed with 49 body bytes unread (`curl` exit 18). The server exited cleanly. The adapter should treat a 202 followed by a closed connection as a
  stopped server. Filed as [opnca-r#47](https://github.com/mantra-opnca/opnca-r/issues/47).

## Intel (x86_64)

- The x86_64 R installs as `Versions/4.6-x86_64` and uses P3M's `big-sur-x86_64`
  binaries.
- Timings are 3–8 times the arm64 runner's.
- PyMuPDF 2.x (locked at 2.1) has no macOS x86_64 wheel, so on Intel the tools env lacks
  pymupdf, and deckifyr previews can't work. The spike skips the package (`--no-deps`
  keeps `pymupdf-layout` and `pymupdf4llm` installable). Fixed in #5: the tools env now
  pins pymupdf 1.28.2, which has wheels for all three platforms.
- The Intel run couldn't cover LibreOffice: no GUI session for the spike user on a
  runner (see `docs/spikes/libreoffice.md`).

## Failure modes seen (and what the installer must do)

| Failure | Where | Cause | Installer rule |
|---|---|---|---|
| `make: *** No rule to make target '/Users/…/Library/Application'` | renv restore | R_HOME contains a space | R root without spaces |
| `Fatal error: 'R_TempDir' contains space` | any R start | `TMPDIR` under Application Support | space-free `TMPDIR` |
| "R version 4.6.1" from a relocated 4.6.2 | dev Mac with CRAN R | dyld uses the system `libR` | `DYLD_LIBRARY_PATH` in `ldpaths` |
| `error executing R CMD config CC` + CLT dialog | renv restore, no CLT | renv's macOS install scope | base-R install of pinned files; no renv at install time |
| `dependencies … are not available` for pyro/reportifyr/quartifyr | renv restore with the CLT | not investigated (base R installs the same files fine) | as above |
| `Rscript execution error: No such file or directory` | opnca-r `configure` | `Rscript` without `RHOME` | export `RHOME` in `bin/R` |
| `Failed to download deckifyr @ https://…` | `uv sync --locked` offline | URL-pinned wheel | export + `uv pip install` |
| reportifyr init wants lxml 6.1.1 universal2 | demo project's own `uv.lock` | foreign lock in the project | app-created projects only |
| Rust `install.sh: Option 'Support/…' is not recognized` | repair path | prefix with a space | space-free Rust prefix |

## Recommendation (feeds #4, #19, #34)

- **#4 / ADR 0004 stands, and gets firmer for macOS.** Binaries are the only
  standard-user path. The vendored source build needs the CLT, and installing the CLT
  needs admin. Keep the source path for repair and exception only (and for CI).
- **#19 installer v1 on macOS:**
  - bootstrap uv first;
  - fetch and verify every pinned file;
  - install Python from a local mirror;
  - restore the tools env from the wheelhouse;
  - relocate CRAN's `R.framework` into a space-free R root (recipe above);
  - install the R library with base R from a local repository, check it against
    `renv.lock`, and write `runtime.json` with absolute paths.

  The app data can stay in `~/Library/Application Support/opncafyr`, but R and `TMPDIR`
  must not contain a space. Options for the R root: `~/Library/opncafyr` or
  `~/.opncafyr`. Owner's choice; the spike used `~/.opncafyr-spike/r`.
- **#5 pins:** done: pymupdf 1.28.2 in the tools env. The tools env keeps the
  export-and-install route (the same on Windows and in the offline CI job).
- **Launcher (#27):** set `DENO_DIR` for Quarto, `TMPDIR`, `RHOME` (through the
  wrapper), `QUARTIFYR_PYTHON_ENV` and the `UV_*` offline variables in job environments.

## Limits

- SIP is disabled in tart's base image and on GitHub runners. The `DYLD_LIBRARY_PATH`
  and fallback behaviour was confirmed with SIP on, on the developer Mac (macOS 26.6),
  where the relocated R loaded its own libraries. The full chain hasn't been run with
  SIP on and no R installed.
- macOS 15 only (the minimum in ADR 0003). macOS 26 was used only for the relocation
  check above.
- The CI runners have Xcode. The no-CLT results come from the VM only.

## How to reproduce

```sh
# Asset list for the platform, then pre-fetch the private opnca-r files
uv run python scripts/spikes/spike_assets.py --platform macos-arm64 > a.json
mkdir -p assets && uv run python scripts/spikes/assets_tsv.py a.json --platform macos-arm64 > assets/assets.tsv
gh release download v0.2.7 -R mantra-opnca/opnca-r -D assets \
  -p manifest.json -p 'opnca_0.2.7_R4.6_macos-arm64.tgz' -p opnca_0.2.7-vendored.tar.gz
curl -fsSL -o assets/quartifyr-v0.3.2-src.tar.gz \
  https://github.com/jprybylski/quartifyr/archive/refs/tags/v0.3.2.tar.gz
# As a standard user (no sudo):
scripts/spikes/macos-install.sh --assets assets --online            # online
cp ~/Library/Application\ Support/opncafyr/spike/downloads/* assets/  # keep the downloads
sandbox-exec -f scripts/spikes/nonet.sb \
  scripts/spikes/macos-install.sh --assets assets --quarantine-test --vendored   # offline
```
