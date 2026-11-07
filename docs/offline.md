# Offline operation: network touchpoints and how each is closed (#44)

- Date: 2026-11-06
- Decision: offline installation and operation are a supported v1 use case (owner,
  2026-11-04; ADR 0003). Most installs are online. Online, a network call is fine when it
  is an explicit download of a pinned, checksummed file. Nothing may *require* the network.
- Pins: `runtime/pins.toml`, `runtime/r/renv.lock`, `runtime/r/binaries.toml`, `uv.lock`
  and `runtime/python/tools/uv.lock` (#5). Dependency graph: [dependency-graph.md](dependency-graph.md).
- Check: the `offline` CI job (below) installs from local files and runs the flow with the
  network cut, and fails on any connection attempt.

This page lists every point where installing or running opncafyr can reach the network,
what each fetches, and how it is closed. Sources: the #44 issue table, a read of every tool
at its pinned release, and traced runs (`strace` in a Linux network namespace, WSL Ubuntu
24.04, 2026-11-06). Rows marked **traced** were observed, not only read.

## Install time

| # | Where | What it fetches | Resolution | Issue |
|---|---|---|---|---|
| I1 | uv | The uv binary from GitHub releases | Pinned per platform (`[uv.assets]`); the installer extracts the verified archive (`installer/steps/runtimes.py`). Never the `curl \| sh` installer. | #5, #19 |
| I2 | uv-managed Python | python-build-standalone from GitHub when uv needs an interpreter | Pinned (`[python.assets]`). The installer extracts the archive itself, never through `uv python install` (S3); `UV_PYTHON_DOWNLOADS=never` everywhere after that. | #5, #19, #36 |
| I3 | Launcher Python packages | PyPI, through `uv sync` | `uv.lock` pins every wheel by hash. Offline: a wheelhouse of the locked files plus the requirements exported from the lock (`uv export --frozen`, with hashes), installed with `uv pip install --require-hashes` and `UV_OFFLINE=1`, `UV_NO_INDEX=1`, `UV_FIND_LINKS=<wheelhouse>`. **Traced:** `uv sync --locked` does *not* work this way: it fetches each locked file by its recorded PyPI URL and falls back only to uv's cache, never to `UV_FIND_LINKS` ("Network connectivity is disabled, but the requested data wasn't found in the cache"). The launcher itself comes from its built wheel, so no build backend is fetched. | #36 |
| I4 | Tools Python environment (deckifyr `[web,preview]`, python-docx, PyYAML, Pillow) | The deckifyr wheel from GitHub releases; the rest from PyPI | `runtime/python/tools/uv.lock` pins the wheel URL and every dependency by hash. Same offline path as I3; the exported `deckifyr @ <url>` line becomes `deckifyr==0.3.6` with its hash, so the wheel comes from the wheelhouse. | #5, #36 |
| I5 | R itself | CRAN installer (`.exe` on Windows, `.pkg` on macOS) | Pinned (`[r.assets]`). The installer runs or unpacks the local file; rig is not used (its user mode downloads its own unpinned installer). On macOS the `.pkg` payload is unpacked and relocated per user (#7). | #5, #6, #7 |
| I6 | R packages (126 from CRAN) | P3M or CRAN through `renv::restore()` / `install.packages()` | `renv.lock` against the dated P3M snapshot; `binaries.toml` pins each platform binary and the source tarball by hash. The installer puts the files in a local CRAN-like repository and restores with `options(repos = <file://repo>, renv.config.repos.override = <same>)`, so a missing file fails instead of falling back to a remote. The source index must list every package (S1). On macOS the installer uses base R's `install.packages()` of the same files instead of `renv::restore()`, which needs the Command Line Tools (#7). **Traced** in the CI job (Linux, from the pinned source tarballs). On Linux, renv's cellar lookup takes a source `.tar.gz` for a binary (Linux binaries use the same extension) and then fell back to downloading opnca-r and quartifyr from GitHub (traced: 48 DNS lookups by `curl` for `api.github.com`), so the CI job installs the four cellar packages with `R CMD INSTALL` after the restore. macOS (`.tgz`) and Windows (`.zip`) aren't affected; the container (#42) is. | #5, #34, #36, #42 |
| I7 | renv itself | `renv/activate.R` downloads renv when it isn't installed | Install renv from the same local repository first; never use renv's bootstrap or project autoloader (`RENV_CONFIG_AUTOLOADER_ENABLED=FALSE`). | #34 |
| I8 | opnca-r | Private GitHub release (binary per platform) | Selected from `manifest.json` by fields and verified against the pins (`opncafyr.adapters.opnca.release`, no network). Installed from the renv cellar. | #4, #34 |
| I9 | opnca-r source build (repair path) | crates.io through Cargo | The `-vendored` tarball builds with `--offline` (opnca-r#23). **Traced**: built in the CI job with the network cut. | #4, opnca-r#23 |
| I10 | Rust toolchain (repair path) | `rustup-init` downloads the toolchain from static.rust-lang.org | Offline: the pinned standalone archive (`[rust.standalone]`) and its `install.sh`, never rustup. Normal installs have no Rust (ADR 0004). | #4, #5 |
| I11 | Rtools (repair path, Windows) | CRAN | Pinned installer (`[rtools]`). | #5, #6 |
| I12 | quartifyr, reportifyr, pyro | GitHub or `a2-ai.r-universe.dev` (`Additional_repositories`) | Pinned release tarballs in the renv cellar. r-universe keeps only the latest build, so it is never configured. | #5 |
| I13 | Quarto | GitHub releases | Pinned (`[quarto.assets]`), extracted under the app root. | #5 |
| I14 | Quarto extensions `quartifyr`, `A2-ai/quarto-plus` | `quarto add` from GitHub | Both ship inside the pinned quartifyr tarball. `quartifyr::install_quarto_extensions()` and `install_quartifyr_extension()` copy them into a report project. **Traced**: no attempts. `quarto add` is never run. | #32 |
| I15 | Report and deck templates | `from_repo` git clones | Bundled templates only (ADR 0007): `from_dir` for quartifyr, the bundled minimal project for `deckifyr init`. | #32, #33 |
| I16 | macOS `gdtools` binary | P3M's macOS build links XQuartz (`/opt/X11`, an admin install) | Pinned CRAN's self-contained build instead (`origin = "cran"` in `binaries.toml`). CRAN keeps only current binaries, so the offline bundle must keep its own copy. | #5, #36 |
| I17 | LibreOffice (optional) | Vendor download | Optional pinned per-user download (`[optional.libreoffice]`, archive URLs), an optional component of offline bundles. Needs no network at install or run time. See [spikes/libreoffice.md](spikes/libreoffice.md). | #8, #36 |
| I18 | Linux system libraries (CI and container only) | apt | Not part of a desktop install. The CI job installs them online before cutting the network; the container image (#42) bakes them in. | #42 |
| I19 | R `openssl` 2.5.0 built from source (Linux CI and container only) | Its `tools/cacert.R` tries to download `https://curl.se/ca/cacert.pem` during the build, and uses only the system CA bundle when that fails | **Traced** (2 DNS lookups by R running `--file=tools/cacert.R`). Non-fatal and can't be switched off, so it is the one entry in `scripts/offline/allowed_attempts.toml`; still printed and recorded by the check. Desktop installs use the pinned binary, which doesn't run it. | #42, #44 |

**Installer v1 (#19)** implements the install-time rows ([install.md](install.md)):
- **Python packages (I3, I4).** One exact wheel per package and platform comes from
  `runtime/python/wheels.toml`, which `scripts/pin_wheels.py` generates from both locks and
  `verify_pins.py` keeps current. The wheels are installed with
  `uv pip install --no-index --require-hashes --no-deps`, then `uv pip check`. No
  `uv export` runs at install time.
- **R packages (I6).** The R library is installed with base R `install.packages()` from a
  local repository of the verified binaries, on both OSes. renv never runs at install
  time, so it makes no crandb lookups (S1) and needs no Command Line Tools on macOS. The
  four tool packages come from the cellar files, and every version is checked against
  `renv.lock`.
- **Child processes.** Every installer child gets an allow-listed environment with the
  settings below. In bundle mode it also gets a closed proxy (`HTTPS_PROXY=http://127.0.0.1:9`),
  so a stray attempt fails at once.

## Project setup and run time

| # | Where | What it fetches | Resolution | Issue |
|---|---|---|---|---|
| R1 | pyro `initialize_python()`, called by `reportifyr::initialize_report_project()` and by `quartifyr::initialize_quartifyr_project()` without `python_env` | `curl` of the uv installer into `~/.local/bin` when uv is missing or the wrong version; `uv lock` and `uv sync` from PyPI; possibly a Python download | **Traced.** With the app's uv first on `PATH`, pyro used it ("Using existing uv 0.13.7") and never curled. With the network cut and no offline settings, uv made 20 DNS lookups and the init failed. With `UV_OFFLINE=1`, `UV_NO_INDEX=1`, `UV_FIND_LINKS=<wheelhouse>` it worked with no attempts. **Adapter design (#32):** don't call it. Lay out the project with reportifyr's own helpers (`create_report_directories`, `create_outputs_directories`, `copy_footnotes`, `copy_config`, `create_init_file`) and hand both tools the tools environment (R2). Traced: no attempts. These are reportifyr internals (`:::`), so the adapter pins reportifyr 0.4.0 and tests them; reportifyr is external, so this is an adapter workaround, not an upstream issue. | #32 |
| R2 | reportifyr pass 2: `uv run -m reportipyr.cli …` (no `--no-sync`) with `VIRTUAL_ENV=<venv_dir>/.venv` | In a project with a `pyproject.toml`, `uv run` re-locks and syncs whenever the lock is stale | **Traced** (stale lock, network cut): 26 DNS lookups by uv and a failed render without `--offline`; with `--offline` (quartifyr#76 sets `UV_OFFLINE`, `UV_NO_INDEX`, `UV_NO_SYNC`, `UV_PYTHON_DOWNLOADS=never`) the render was `ok` with no attempts. A current lock made no attempt either way, but only because uv's cache was fresh. **Adapter:** always pass `--offline`; give report projects no `pyproject.toml`; set `options(venv_dir = <tools env parent>)` through an adapter-owned `R_PROFILE_USER` and `QUARTIFYR_PYTHON_ENV=<tools env>`. With `UV_NO_PROJECT=1` (and `UV_NO_CONFIG=1`), `uv run` uses the tools environment as is. Without it, uv adopts any `pyproject.toml` in a *parent* of the report project, such as a Python project the user keeps study folders in: in the CI job the repository's own `pyproject.toml` made reportifyr's `validate-docx` fail; with `UV_NO_PROJECT=1` the same render was `ok`. Traced: no attempts. | #32, quartifyr#76 |
| R3 | quartifyr render (Quarto, both filters) | None observed | Pass `--quarto-bin <pinned quarto>` and `--offline`. | #32 |
| R4 | reportifyr metadata (`write_object_metadata()` and friends) | None, but it needs a **global git identity** (gert `git_config_global()`, level `global` only; `XDG_CONFIG_HOME` and `GIT_CONFIG_GLOBAL` are not accepted) and stops without one | Must work without prompting and without changing the user's git config. Either the curator writes the sidecars itself (#30, #31), or the adapter runs reportifyr with `HOME` pointing at an app-owned folder holding a `.gitconfig`. The CI job does the latter. | #31, #32 |
| R5 | deckifyr CLI (`init`, `build`) | None (**traced**: no attempts, no child processes for the minimal deck) | Run `python -m deckifyr` from the tools environment, never through its R wrappers. Always `--trusted-binaries-only` with absolute `--quarto-bin`, `--rscript-bin`, `--soffice-bin`. | #33 |
| R6 | deckifyr R wrappers (`deck_build()` etc.) | `uv run -m deckifyr …` without `--no-sync`: a re-lock and sync on every call, unless offline mode is on | Not used by the launcher (R5). Since 0.3.6, `DECKIFYR_OFFLINE=1` makes them run `uv run --no-sync` with no index or Python downloads. | #33, [deckifyr#93](https://github.com/jprybylski/deckifyr/issues/93) |
| R7 | `deckifyr init --from-repo` | `git clone` | Not used (bundled templates, I15). Since 0.3.6 it is refused when `DECKIFYR_OFFLINE` or `UV_OFFLINE` is set. | #33, [deckifyr#93](https://github.com/jprybylski/deckifyr/issues/93) |
| R8 | quartifyr `--recalculate-fields`, `--resolve-same-page-crossrefs` | None, but `soffice` is found on `PATH` only | Not network. It breaks the rule that executables come from the pinned runtime, so the adapter controls `PATH` until there is an override. Upstream: `--soffice-bin`. | #8, #32, [quartifyr#83](https://github.com/jprybylski/quartifyr/issues/83) |
| R9 | opnca-r calculation | None (**traced**: `opnca::opnca()` on its theophylline sample, no attempts) | — | #27 |
| R10 | opnca-r and deckifyr web apps | None found: opnca-r bundles plotly and its font (`inst/www/vendor/NOTICE.md`), deckifyr's built frontend loads only local assets | **Traced** for opnca-r: `scripts/serve.R` started, served `api/v1/health` and the page on loopback, no attempts. Both are covered by the proxy harness (#16). | #16 |
| R11 | Launcher home page | None: static files, system fonts, no third-party JavaScript (ADR 0006) | Keep it that way (CLAUDE.md). | #18 |
| R12 | Report project `.Rprofile` and `renv/activate.R` (as in quartifyr's demo project) | renv bootstrap; `a2-ai.r-universe.dev` and CRAN repos | Report projects the launcher creates have neither. The adapter sets `R_PROFILE_USER` itself, so a project `.Rprofile` doesn't run and can't add repositories. | #32 |
| R13 | uv with a warm cache | Nothing until the cache's HTTP freshness runs out; then index revalidation | **Traced**: `uv run` in a locked project made no attempt with a fresh cache. Don't rely on it: set the offline variables for every tool process. | #32, #33 |
| R14 | Update checks, telemetry | None in any tool; none planned | The updater (#35) accepts a local bundle. | #35 |
| R15 | Launcher R programs: cleaning (#22) and later the format check and replay jobs | None: base R and jsonlite only, no package installs, no repositories | Run as `Rscript --vanilla` from the pinned runtime (`core/rscript.py`), so a study folder's `.Rprofile` or `.Renviron` never runs. Not traced yet; the offline CI job reaches them in #51. | #22, #51 |

## Offline settings the launcher applies

The installer and the launcher give every tool process these settings in offline mode.
Most are harmless online too; the launcher sets the uv ones always, because nothing at run
time should resolve or sync.

| Setting | Why |
|---|---|
| App-owned `uv` first on the child `PATH` | pyro finds it and never installs uv (R1) |
| `UV_OFFLINE=1`, `UV_NO_INDEX=1`, `UV_NO_SYNC=1`, `UV_PYTHON_DOWNLOADS=never` | No resolution, index or Python download at run time (R2, R13) |
| `UV_FIND_LINKS=<wheelhouse>` (install only) | Offline install from the bundle's wheels (I3, I4) |
| `UV_PYTHON_INSTALL_MIRROR=file://<bundle>/python` (install only, if uv installs Python) | Python from the bundle (I2) |
| `DENO_DIR=<app root>/cache/deno` | Quarto's Deno cache stays in the app root (S6) |
| `options(repos = <file://local repo>, renv.config.repos.override = <same>)`, `RENV_PATHS_CELLAR`, `RENV_CONFIG_AUTOLOADER_ENABLED=FALSE` | R packages only from local files (I6, I7, I12) |
| `R_PROFILE_USER=<adapter file>`, `R_ENVIRON_USER=/dev/null` (or an adapter file) | No user or project profile adds repositories or bootstraps renv (R12) |
| `QUARTIFYR_PYTHON_ENV=<tools env>`, `options(venv_dir = <tools env parent>)` | quartifyr and reportifyr use the tools environment; pyro never runs (R1, R2) |
| `UV_NO_PROJECT=1`, `UV_NO_CONFIG=1` (run time) | `uv run` never adopts a `pyproject.toml` or `uv.toml` from a folder above the study (R2) |
| quartifyr `render.R --offline --strict --quarto-bin <pinned>` | quartifyr#76's offline render (R2, R3) |
| deckifyr `--trusted-binaries-only` plus absolute binaries | No project-chosen executables (R5) |
| Unset `NOT_CRAN` for source builds | Cargo stays `--offline` with the vendored crates (I9) |

## Network-blocked CI job

`.github/workflows/ci.yml`, job `offline` (Ubuntu 24.04). Linux isn't a supported desktop
platform; it is the cheapest place to enforce "no network" for the whole stack, and the
container (#42) will reuse it. Pins for its Linux runtimes are in `[ci.linux-x86_64]`.

1. **Online phase.** Install the pinned R `.deb` and the system libraries with apt (I18),
   download the opnca-r vendored tarball with the release token, then
   `scripts/offline/fetch.py` fetches and hash-checks everything else into one folder:
   runtimes, the cellar, a CRAN-like source repository for `renv.lock`, and the wheelhouse.
2. **Offline phase.** `scripts/offline/offline_phase.sh` runs inside `sudo unshare --net`
   (dropping back to the runner user), under `strace -f -e trace=%network,%process`. It
   installs uv, Python, Quarto and Rust from the archives, both Python environments from the
   wheelhouse with every hash enforced (I3), and the R library with `renv::restore()` from
   the local repository, then the cellar packages with `R CMD INSTALL` (opnca-r built from
   the vendored tarball). Then it runs:
   the launcher's dataset scan, preview and import of `tests/fixtures/two_analyte.csv`
   through its API; one `opnca::opnca()` calculation (Cmax checked against the input); a
   quartifyr render (`--offline --strict`) of a two-artifact report; and a deckifyr build.
3. **Check.** `scripts/offline/check_trace.py` fails the job on any connection attempt to
   a non-loopback address or to port 53 (a DNS lookup), naming the program and its command
   line. The only tolerated attempt is I19, listed with its reason in
   `scripts/offline/allowed_attempts.toml`; it is still printed and written to the report.
   Tested in `tests/test_offline_trace.py`.

First green run (2026-11-06, run 42264332087 on a temporary branch): 23.5 minutes in all;
the R library (126 packages from source plus the four cellar packages, opnca-r's Rust
build included) takes about 20 of them. The check found no blocked attempt and four
allowed lookups (I19). Same result in WSL Ubuntu 24.04 under `unshare -rn`.

Without the `OPNCA_R_RELEASE_TOKEN` secret, opnca-r is left out and the calculation step
is skipped with a warning. The two-analyte fixture goes only as far as import: cleaning
(#22), the analyte snapshot (#25) and the opnca, quartifyr and deckifyr adapters (#27, #32,
#33) don't exist yet. The follow-up issue (#51) routes the job through the adapters as they
land.

Desktop platforms are covered by the install spikes' offline variants (#6, #7) and, later,
network-disabled VMs in the install matrix (#37).

## Internal-mirror mode

Many firewalled sites allow internal mirrors (Artifactory, Nexus, an internal CRAN-like
repository, a PyPI proxy) instead of carrying a bundle. Every pin is a content hash, so a
mirror changes only where a file comes from, never what is accepted.

| Pinned files | Mirror type | How the installer uses it |
|---|---|---|
| uv, Python, R installers, Quarto, Rtools, Rust, LibreOffice, opnca-r, deckifyr, quartifyr, reportifyr, pyro (GitHub releases, CRAN, static.rust-lang.org, cdn.posit.co) | Generic remote or raw repository | Replace each URL's origin by a configured prefix (`mirror.toml`: origin → mirror base); same file name, same SHA-256 |
| R packages (`binaries.toml`) | CRAN-like remote (P3M snapshot or Artifactory/Nexus "CRAN" remote) | Same prefix rewrite of each pinned file URL. The binary paths (`bin/macosx/…/contrib/4.6`, `bin/windows/contrib/4.6`) must be proxied too, not only `src/contrib` |
| Python wheels (`uv.lock`, tools `uv.lock`) | PyPI remote | Don't point `uv sync` at the mirror: the locks record PyPI as their registry. Download the locked files (`pip download --require-hashes` with `--index-url <mirror>`, or a prefix rewrite of `files.pythonhosted.org`) into a wheelhouse, then take the offline path |

**Implemented (#36):** `install --mirror mirror.toml` and `bundle --mirror mirror.toml`
([install.md](install.md#internal-mirror-mode)). Both take the one install path:
- every pinned URL is rewritten by its longest prefix;
- files are fetched with the system curl and checked against the pins;
- a URL with no configured mirror is refused, never fetched from the origin;
- no tool resolves against an index at install or run time.

The wheels are fetched by their pinned `files.pythonhosted.org` URLs (`wheels.toml`), so a
plain prefix rewrite covers them; no `pip download` is needed. The private opnca-r release
must be uploaded to the mirror by the site, since there is no GitHub token behind the
firewall.

## Offline bundles (#36)

`install.sh bundle` / `install.bat bundle` (and `opncafyr bundle`) write a bundle with
every pinned file for a platform, the installer, a manifest, `SHA256SUMS`, an SPDX SBOM
and a licence inventory ([install.md](install.md#offline-installation-for-it-administrators-36)).
`install --from-bundle` installs from it and never touches the network. The bundle keeps
its own copy of CRAN-only binaries such as macOS `gdtools` (I16).

The `Installer` workflow builds the bundle twice per platform and compares the manifests.
It then installs, updates and repairs from it as a standard user with the network
blocked: `sandbox-exec` on macOS, a per-user firewall rule on Windows. Any logged attempt
fails the job.

## Found by the install spikes (#6, #7)

Both spikes ran the full install and a calculation, a report and a deck as a standard user
with the network blocked (Windows: per-user outbound firewall rule with drop logging;
macOS: `sandbox-exec` denying non-loopback traffic, in a VM and on GitHub's arm64 and
Intel runners). With the fixes below, the final offline runs made **no** connection
attempts. Details: [spikes/windows-install.md](spikes/windows-install.md),
[spikes/macos-install.md](spikes/macos-install.md).

| # | Touchpoint | Seen on | Resolution | Issue |
|---|---|---|---|---|
| S1 | renv looks up any package missing from the local repository's **source** index at `crandb.r-pkg.org`, sending the package name (including opnca) to a third party | Windows (≈130 blocked attempts, ≈90 s of retries); macOS (one, for opnca) | The local repository's source index lists every package, using the binary entry where there's no source tarball. With it, zero attempts on both. | #19, #36 |
| S2 | renv downloads `"Source": "GitHub"` lock records from GitHub before it looks in the cellar (fails for private opnca-r and rolls back the restore) | Windows, online | The four tool packages are locked as `"Source": "Cellar"`; `verify_pins.py` enforces it. | #5 |
| S3 | `uv python install` from `UV_PYTHON_INSTALL_MIRROR` | Windows, macOS | Works offline with a `file://` mirror, but on Windows its minor-version junction is an "untrusted mount point" for a standard user, and without `--no-bin` it writes `~/.local/bin/python3.13`. The installer extracts the pinned archive itself (I2). | #19 |
| S4 | pyro's project setup for reportifyr (`uv lock`/`uv sync`, `curl` of uv when missing) | Windows, macOS | Works offline only with our uv first on `PATH`, the `UV_*` offline variables and the wheelhouse, and fails when the project carries a foreign `uv.lock`. The adapter creates the project `.venv` from the wheelhouse and writes reportifyr's init marker itself (temporary) until quartifyr offers it (R1). | #32, [quartifyr#85](https://github.com/jprybylski/quartifyr/issues/85) |
| S5 | rig's user mode downloads its own copy of the R installer | Windows | rig is not used; R's own installer (Windows) or the expanded `.pkg` (macOS) from the pinned file (I5). | #19 |
| S6 | Quarto's Deno cache in `~/Library/Caches/deno` | macOS | Not network, but outside the app root: set `DENO_DIR`. | #27, #32 |
| S7 | Windows DNS Client service resolves names on behalf of processes | Windows | Lookups happen but nothing connects; the drop log records no connection. No action. | — |
| S8 | Rtools45's post-install step runs MSYS2's first-run keyring setup, which tries `keyserver.ubuntu.com:443` (repair path only) | Windows | Harmless when blocked (nothing uses pacman); the installer can skip Rtools' post-install run. | #34 |

Not network, but found the same way and needed by the installer: on macOS
`renv::restore()` needs the Command Line Tools (`R CMD config CC`), so the R library is
installed with base R's `install.packages()` and checked against `renv.lock` (I6); R and
`TMPDIR` must be at paths without a space. LibreOffice (I17) needed no network on either
platform (#8).

## Open items

- Upstream, filed with this audit: [quartifyr#83](https://github.com/jprybylski/quartifyr/issues/83) `--soffice-bin` (R8); [deckifyr#93](https://github.com/jprybylski/deckifyr/issues/93) offline mode for
  the R wrappers (R6) and `--from-repo` (R7).
- #51: route the offline CI job through the adapters (#22, #25, #27, #32, #33).
- #51: the Gate 2 end-to-end run from an offline bundle, once the analysis path exists. The
  installer's report smoke tests (opnca-r calculation, Quarto DOCX, deckifyr build) are the
  offline proof until then.
