# Spike #6: clean standard-user install on Windows 11

- Date: 2026-11-06
- Machine: Windows 11 Pro x86_64 (build 26300), a new local **standard** user
  (`opncafyr-spike`: member of Users only, Medium integrity) created for the spike and
  deleted afterwards. Every step ran as that user over SSH. The machine already had R
  4.1-4.6.1 installed for all users (HKLM default 4.6.1); the new account had nothing of
  its own.
- Pins: `runtime/pins.toml`, `runtime/r/renv.lock` (P3M snapshot 2026-11-06, 130
  packages), `runtime/r/binaries.toml`, `runtime/python/tools/uv.lock` (#5). uv 0.13.7,
  Python 3.13.18 (python-build-standalone 20261026), R 4.6.2, Quarto 1.11.9, opnca-r
  0.2.7 (Windows binary), deckifyr 0.3.5 (wheel), quartifyr 0.3.2, reportifyr 0.4.0,
  pyro 0.1.1.
- Script: `scripts/spikes/windows-install.ps1` (plus `spike_assets.py`, `local_repo.R`,
  `restore_r.R`, `smoke_opnca.R`, `windows-state.ps1`, `windows-offline-firewall.ps1`).
  LibreOffice (#8): `windows-libreoffice.ps1`, `expand_msi.ps1`; summary below.

**Result: it works, online and offline, with no elevation.** On a clean standard account
everything installs under `%LOCALAPPDATA%\opncafyr\…`; opnca-r loads and returns the known
answer on R 4.6.2, opnca-r's server answers its health check, deckifyr builds a PPTX and
quartifyr renders the demo report to DOCX with `--strict` (status `ok`, no warnings).
Machine PATH, HKLM, the user's PATH and the default R are unchanged. With the user's
outbound traffic blocked by the firewall, the same install from the pinned files makes
**zero** connection attempts (firewall drop log), once two renv traps below are fixed. A
full offline install and run takes about **3 minutes**.

Recommendation for #4 and #19: keep the binary path (ADR 0004). Install R with its own
Inno Setup installer in `/CURRENTUSER` mode (not rig), unpack Python ourselves, restore
the R library from a local repository whose source index covers every package, and give
reportifyr its project `.venv` and init marker without pyro.

## Steps

Times are from the final runs. "Online" fetches every pinned file from its URL (opnca-r,
which is private, was copied in); "offline" reads the same files from a folder with the
firewall block on. Every file is checked against its pinned SHA-256 before use.

| Step | What | Online | Offline | Outcome |
|---|---|---|---|---|
| verify | 168 pinned files (494 MB): fetch if `-Online`, check SHA-256 | 459 s (165 downloads, one at a time) | 2 s | ok |
| uv | unzip `uv-x86_64-pc-windows-msvc.zip` | 4 s | 3 s | uv 0.13.7 |
| python | `tar -xzf` the python-build-standalone archive | 3 s | 2 s | 3.13.18, OpenSSL 3.5.9 |
| tools | tools venv: online `uv sync --locked --no-install-project`; offline `uv pip install --offline --no-index --find-links <assets> --require-hashes -r requirements.txt` (exported from the same lock) | 53 s | 8 s | deckifyr 0.3.5 with web + preview |
| r | `R-4.6.2-win.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /NOICONS /DIR=… /MERGETASKS="!desktopicon,!quicklaunchicon,!recordversion,!associate"` | 15 s | 13 s | R 4.6.2 (ucrt) |
| rlib | local CRAN-like repository + `renv::restore()` of `renv.lock` into the private library | 208 s ¹ | 107 s | 130/130 packages at the locked versions |
| opnca | `smoke_opnca.R`: two profiles with a known answer | <1 s | <1 s | Cmax, Tmax, λz = ln 2/2, t½ = 2 h, AUClast exact |
| serve | opnca-r `scripts/serve.R` with the launcher flags; `GET …/api/v1/health` | 9 s | 11 s | health ok; API without token → 403 |
| quarto | unzip Quarto | 14 s | 10 s | 1.11.9 |
| deck | `python -m deckifyr --json build presentation.yaml --trusted-binaries-only --rscript-bin=… --quarto-bin=…` | 1 s | 1 s | 28 KB PPTX |
| report | quartifyr demo report: `Rscript --no-init-file render.R --project … --shell report.qmd --json --strict --offline --quarto-bin …` | 21 s | 20 s | `status: ok`, 0 warnings, 0 unfilled placeholders, 342 KB DOCX |
| repair | vendored opnca-r source build (exception path, ADR 0004) | — | 672 s | ok, see below |

¹ Online, renv also queried `crandb.r-pkg.org` for every package (see below); the offline
figure is with that fixed.

Disk: 2.5 GB for the online root, of which R 190 MB, R library 259 MB, Python 61 MB,
tools venv 233 MB, Quarto 469 MB, uv 31 MB and caches 849 MB (uv and renv; the installer
can clear them).

## No elevation, no machine changes

There is no way to answer a UAC prompt over SSH, so any step that needed elevation would
have failed; none did. Before/after snapshots of the account (`scripts/spikes/windows-state.ps1`):

| Check | Before | After the online install, all steps |
|---|---|---|
| Machine PATH (hash) | 783637315B9E7D97 | unchanged |
| User PATH | `…\WindowsApps;` | unchanged |
| HKLM `SOFTWARE\R-core\R` | 4.1.1-4.6.1, Current Version 4.6.1 | unchanged |
| HKCU `SOFTWARE\R-core` | none | none (`!recordversion`) |
| `.RData` association, Start menu, desktop | none | none |
| HKCU Uninstall | none | `R for Windows 4.6.2_is1` (from R's installer, see below) |
| `where Rscript` | not found | not found |

## Findings

1. **R: use R's own installer per user, not rig.** `R-4.6.2-win.exe /CURRENTUSER` installs
   into any folder without elevation in 15 s, and with `!recordversion` it doesn't register
   itself under HKCU `R-core`, so RStudio/Positron and the user's default R are untouched.
   It does add one per-user "Add or remove programs" entry, `R for Windows 4.6.2_is1`.
   That key name is shared by every per-user R 4.6.2, so a user's own per-user R 4.6.2
   would lose its entry: the installer should check for the key first and refuse or warn.
   rig 0.11.1 has a per-command user mode (`rig --user add 4.6.2 --no-default
   --without-path --without-pak --without-repos` with `RIG_R_INSTALL_DIR`/
   `RIG_BINARY_DIR`), which also works without elevation (47 s), but it downloads its own
   copy of the installer from `cran.rstudio.com` into `%TEMP%\rig` (not our pinned,
   checked file, and left behind), writes `HKCU\SOFTWARE\R-core\R\4.6.2`, and its "cleaning
   leftover registry entries" step deleted the uninstall key of the other per-user R 4.6.2.
   rig 0.11 also has this user mode on macOS (the Mac's rig 0.8.1 does not), which is
   relevant to #7.
2. **Python: unpack the archive ourselves.** `uv python install` (even from a local
   `UV_PYTHON_INSTALL_MIRROR`) installs fine but then fails creating its
   `cpython-3.13-windows-x86_64-none` junction: on this Windows 11 build a junction made by
   a standard user is an "untrusted mount point" (os error 448) that the same user can't
   traverse, from uv or `cmd`. It also drops `python3.13.exe` into
   `%USERPROFILE%\.local\bin`, outside the app root. Extracting the pinned
   python-build-standalone archive with `tar` and using the exact interpreter path avoids
   both and takes 2 s.
3. **renv 1.2/1.3 needs a complete *source* index, or it goes online.** renv plans the
   install from the repository's source `PACKAGES`; a package missing there is looked up
   at `https://crandb.r-pkg.org/<package>/<version>`. With only a binary index, the offline
   restore made about 130 blocked connection attempts (≈90 s of retries) before falling
   back; online, it would have sent "opnca 0.2.7" to a third-party server.
   `local_repo.R` builds a source index that lists every package (the binary entry when
   there's no source tarball) and rewrites `PACKAGES.gz` and drops `PACKAGES.rds`, which R
   reads first. With it: no crandb requests and a 107 s restore. The tool packages (opnca,
   quartifyr, reportifyr, pyro) must be in that index too.
4. **renv: tool packages must be `"Source": "Cellar"` in `renv.lock`.** With
   `"Source": "GitHub"` renv's parallel downloader fetched quartifyr, reportifyr and pyro
   from GitHub archives instead of the cellar and failed on private opnca-r, rolling the
   whole restore back. (The Mac check passed only because its sandbox blocked the network,
   so renv fell back to the cellar.) Fixed in #5: `scripts/lock_r.R` now writes
   `"Source": "Cellar"`, and `verify_pins.py` checks it.
5. **reportifyr needs pyro's project setup.** quartifyr's `QUARTIFYR_PYTHON_ENV` covers
   quartifyr's own Python calls, but reportifyr's fill pass goes through pyro, which
   requires `<project>\.venv` and a `.report_init.json` marker.
   `reportifyr::initialize_report_project()` creates both by running
   `pyro::initialize_python()`, which installs uv with `curl` when missing and runs `uv
   lock`/`uv sync` against PyPI. The spike creates the project `.venv` from the verified
   wheels (`uv pip install --offline --require-hashes`) and writes the marker with
   `reportifyr:::create_init_file()`. That is an internal function, so the quartifyr
   adapter (#32) needs this as a tested, labelled temporary workaround until quartifyr
   offers an offline project setup ([quartifyr#85](https://github.com/jprybylski/quartifyr/issues/85)). The marker records the
   OS user name.
6. **Windows PowerShell 5.1 pitfalls for the installer (#19).** With
   `$ErrorActionPreference = 'Stop'`, any line a native tool writes to stderr (uv progress,
   quartifyr's `ℹ` messages, `taskkill`) aborts the script, so judge native tools by exit
   code only. `Set-Content -Encoding utf8` writes a BOM, and deckifyr 0.3.5 then rejects
   the YAML (`E_SCHEMA_VALIDATION`, the first key becomes `﻿deckifyr`); `>` writes
   UTF-16. Embedded double quotes in native arguments are dropped.
7. **Downloads should run in parallel.** 165 files one at a time took 7.7 minutes; the
   same bytes over the same link in parallel would take a fraction of that.
8. **Offline settings that mattered:** `UV_PYTHON_DOWNLOADS=never`, `UV_NO_CONFIG=1`,
   `UV_CACHE_DIR` in the app root, `--offline --no-index --find-links --require-hashes`
   for the tools env, `renv.config.repos.override` to the local repository,
   `RENV_PATHS_ROOT` in the app root, `R_ENVIRON_USER`/`R_PROFILE_USER` disabled, quartifyr
   `--offline` and `QUARTIFYR_PYTHON_ENV`. DNS lookups still happen (Windows resolves names
   in the DNS Client service, not in our processes) but nothing connects.

## Offline variant

A per-user outbound block (`windows-offline-firewall.ps1`: `New-NetFirewallRule -LocalUser
"O:LSD:(A;;CC;;;<SID>)"`,
remote addresses everything except 127.0.0.0/8 and ::1) stopped all internet and LAN
connections for the spike user while loopback kept working. Windows Firewall logging of
dropped packets (`Set-NetFirewallProfile -LogBlocked True`, which records the PID) was on
during the runs:

| Run | Result | Blocked connections |
|---|---|---|
| Offline 1 and 2 (empty source index) | all steps ok | ≈130 to `crandb.r-pkg.org:443` from `curl.exe` started by renv, during the R restore |
| Offline 3 (`local_repo.R`, final) | all steps ok, 3 min | **0** |
| Repair path (vendored build) | ok, 11 min | ≈12 packets in 1 s to `keyserver.ubuntu.com:443` from Rtools' post-install `bash.exe` |

Every network touchpoint found: renv crandb lookups (fixed by the source index), renv's
GitHub downloads of tool packages (fixed by `Source: Cellar`), `uv python install`
downloads (avoided by unpacking), pyro's uv/PyPI bootstrap for reportifyr (avoided by the
workaround in finding 5), rig's own installer download (not used), Rtools' post-install
MSYS2 keyring step (repair path only, harmless). These feed #44.

## Repair path: vendored source build

Run offline (firewall block on), in the offline root, as the standard user: `-Steps repair`.

| Part | Command | Time | Outcome |
|---|---|---|---|
| Rtools45 | `rtools45-6768-6492.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /NOICONS /DIR=<root>\rtools45` | 150 s | ok, no elevation |
| Rust | standalone `rust-1.99.1-x86_64-pc-windows-gnu.tar.xz`, components merged into `<root>\rust` (no rustup, no `install.sh`) | 212 s | rustc 1.99.1 |
| Build | `R CMD INSTALL --library=<root>\rlib-source opnca_0.2.7-vendored.tar.gz` (Rtools and Rust bin first on PATH) | 308 s | ok, all crates from the vendored sources |
| Check | `smoke_opnca.R` against the source-built library | <1 s | all known-answer checks pass |

Total 11 minutes, with no network: cargo used only the vendored crates. One blocked
attempt: Rtools' installer runs `usr\bin\bash.exe` after install (MSYS2's first-run
keyring setup), which tried `keyserver.ubuntu.com:443` for about a second. It is harmless
(nothing needs pacman here), but an installer using Rtools should expect it, or skip
Rtools' post-install run. The source build works per user and offline, so it stays a
supported repair path; it is too slow and too large (Rtools 3.2 GB plus Rust 2.6 GB unpacked) to
be the default.

## LibreOffice (Windows half of #8)

LibreOffice 26.2.7.1 MSI from the archive URL (SHA-256 `82edb4c8…a8c7`, not the stable-dir
file). Full numbers go to the #8 write-up.

- **Per-user install:** `msiexec /a` works without UAC in a desktop session, but over SSH
  (non-interactive logon) the Windows Installer service refuses a standard user (1601,
  0x80070005). `expand_msi.ps1` unpacks the MSI with msi.dll and `expand.exe` instead, in
  any session: 141 s, 1.5 GB, 19,477 files. Don't copy the MSI's VC++ runtime DLLs next to
  `soffice.exe`; that breaks it. It needs the system VC++ 2015-2022 runtime.
- **Needs a desktop session:** headless soffice doesn't start under a non-interactive logon
  (0xC0000142), so the benchmark ran in the owner's desktop session against the spike
  user's files. Windows CI for previews needs an interactive runner.
- **deckifyr preview:** 40/40 ok (20 sequential, median 12 s; 20 four-way concurrent,
  median 13 s), no hangs, no leftover soffice. First conversion with a fresh profile 22 s,
  then 7 s.
- **quartifyr recalc: broken on Windows.** 15/15 sequential runs hung until the 150 s
  timeout and left `soffice` running; the concurrent half wasn't run (stopped at the
  45-minute cap). Its profile URL is malformed on Windows (`file://C:\…`). Filed as
  [quartifyr#86](https://github.com/jprybylski/quartifyr/issues/86). Until it's fixed, field recalculation is off on Windows.

## Upstream

- [deckifyr#94](https://github.com/jprybylski/deckifyr/issues/94): accept BOM-prefixed
  YAML (finding 6).
- [quartifyr#85](https://github.com/jprybylski/quartifyr/issues/85): offline report-project setup without pyro; one Python env for both passes
  (finding 5).
- [quartifyr#86](https://github.com/jprybylski/quartifyr/issues/86): `recalculate-fields` hangs and orphans soffice on Windows (LibreOffice
  section).
- renv, uv, rig (external): no issues filed; the workarounds above are ours.

## Cleanup

The spike user, the firewall rule, all `opncafyr-*` scheduled tasks, the bench's soffice
processes and temp profiles and `C:\Windows\Temp\opncafyr-*` were removed, and blocked-
packet logging was switched back off. Machine PATH (hash 783637315B9E7D97) and HKLM R-core
(4.1.1-4.6.1, current 4.6.1) match the "before" snapshot; no R 4.6.2, Rtools45 or
LibreOffice entries exist under HKLM Uninstall. Left over: the deleted user's profile
record and 12 registry-hive files (2.6 MB) in `C:\Users\opncafyr-spike`, which Windows
keeps loaded until a reboot; remove them after the next restart with
`Get-CimInstance Win32_UserProfile | ? LocalPath -like '*\opncafyr-spike' | Remove-CimInstance`.

## How to reproduce

```sh
uv run python scripts/spikes/spike_assets.py --platform windows-x86_64 > assets.json
# Bundle: assets.json, assets\ (the pinned files; opnca-r from its private release),
# src\ (runtime/r/renv.lock, runtime/python/tools/{pyproject.toml,uv.lock} plus
# requirements.txt from `uv export --frozen --no-emit-project`, quartifyr's
# examples/demo-report at v0.3.2) and scripts\ (scripts/spikes/*).
```

```powershell
# As a standard user. Add -Online to fetch missing files from their pinned URLs.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Bundle <dir>
```
