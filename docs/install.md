# Installing opncafyr

- Installer v1: #19. Code: `installer/` (thin wrappers) and `src/opncafyr/installer/`.
- Pins: `runtime/pins.toml` and the files it points to ([dependency-graph.md](dependency-graph.md)).
- Offline touchpoints: [offline.md](offline.md).

opncafyr installs for one user, with no administrator rights. It never changes the
machine `PATH`, never writes under `/Library` or `Program Files`, and never registers its R
as the user's default R. Every file it installs is a pinned file whose SHA-256 is checked
before anything is staged.

**Research use only.** A successful install, and the smoke tests it runs, show that the
parts start and agree with the pins. They are not a validation of any calculation.

## Install

Download and unzip the installer (`opncafyr-installer-<version>.zip`), then:

| | macOS 15+ (Apple silicon or Intel) | Windows 11 (x86_64) |
|---|---|---|
| Install or update | `./install.sh` | double-click `install.bat`, or `install.bat install` |
| Check and fix | `./install.sh repair` | `install.bat repair` |
| Remove | `./install.sh uninstall` | `install.bat uninstall` |
| What's installed | `./install.sh status` | `install.bat status` |

Then start opncafyr from **opncafyr** in `~/Applications` (macOS) or from the Desktop or
Start Menu shortcut (Windows).

opnca-r's release is private. An online install downloads it through the GitHub API
when `GH_TOKEN` (or `GITHUB_TOKEN`) is set to a token that can read it. Otherwise, put
the pinned opnca-r files (`manifest.json` and the binary for your platform, from the
release) in a folder and pass `--assets <folder>`. Without either, the install stops
before changing anything, with an error naming `opnca-r`.

Options for `install` and `repair`:

| Option | Meaning |
|---|---|
| `--from-bundle DIR\|ZIP` | Install from an offline bundle only. The installer never uses the network, not even as a fallback (#36). |
| `--assets DIR` | A folder of pinned files to use before downloading (repeatable). Files are found by name and must match their pinned SHA-256. |
| `--with-libreoffice` | Also install LibreOffice, for deckifyr slide previews (optional; [spikes/libreoffice.md](spikes/libreoffice.md)). |
| `--reinstall` | Install again even if this version is already installed. |
| `--dry-run` | Report what would happen; change nothing. |
| `--cacert FILE`, `--proxy URL` | For HTTPS through a corporate proxy. The installer uses the system `curl`, so the OS trust store and `HTTPS_PROXY` apply too. |

## What goes where

```
<app root>/                         %LOCALAPPDATA%\opncafyr  or  ~/Library/Application Support/opncafyr
  current                           which install is active (symlink on macOS, a file on Windows)
  versions/<version>-<n>/           one complete install
    install.json                    written last: the install's record (schema install-v1)
    runtime/runtime.json            the executables the launcher may start (runtime-v1)
    python/  uv/  launcher/  tools/  rlib/  quarto/  wheelhouse/  installer/
    r/                              Windows: R 4.6.2 (from R's own installer, /CURRENTUSER)
    bin/Rscript                     macOS: wrapper for the relocated R
  installer/                        copy of the active installer (repair, uninstall)
  state/  logs/  cache/
~/Library/opncafyr/versions/<id>/   macOS only: R.framework (R needs a path without a space)
~/Applications/opncafyr.app         macOS icon
Desktop and Start Menu opncafyr.lnk Windows icons
```

Study workspaces are never under the app root, and nothing the installer does touches
them.

## How an install runs

The wrapper (`install.sh`, `install.ps1`) only gets the pinned Python: from the download
cache, the bundle, an `--assets` folder, or its pinned URL. It checks the archive's
SHA-256 (from `bootstrap.tsv`), unpacks it to `cache/bootstrap/`, and runs
`run_installer.py` with it. That script checks every payload file against `payload.json`
before importing the installer from the opncafyr wheel. The installer itself uses only the
standard library.

1. **Preflight.** Supported OS and version (Windows 11, macOS 15), not root, the app root
   writable, about 4 GB free, and no other installer running. If the bundled Python can't
   run (organisational application control), the wrapper says so.
2. **Resolve and verify.** Every pinned file for the platform is found in the download
   cache, a bundle or `--assets`, or downloaded (in parallel, with the system `curl`).
   Each one is hashed against the pins. If any file is missing or wrong, the install stops
   here and names each component; nothing has changed.
3. **Stage** into a new `versions/<id>`:
   - Python (unpacked by us) and uv;
   - the launcher and tools environments, from the verified wheels with
     `uv pip install --no-index --require-hashes`;
   - R: on Windows, R's own installer in `/CURRENTUSER` mode. Its per-user uninstall
     entry is put back as it was, so a user's own R 4.6.2 keeps its entry. On macOS,
     CRAN's `R.framework` is relocated (text files only);
   - the R library: base R `install.packages()` from a local repository of the verified
     binaries, every version checked against `renv.lock`;
   - Quarto, and optionally LibreOffice.

   Every child process gets an allow-listed environment with the offline settings from
   [offline.md](offline.md).
4. **Smoke tests:**
   - the launcher and tools environments import;
   - `library(opnca)`, quartifyr and reportifyr load;
   - `quarto --version`;
   - `deckifyr --json validate` on a sample deck;
   - the report's checks (#36): an opnca-r calculation with known answers on synthetic
     data, a Quarto DOCX render with an R chunk run by the pinned R, and a deckifyr build.

   Then the new launcher environment validates the records (`selfcheck`).
5. **Commit and activate.** `install.json` is written, then `current` is switched in one
   atomic step. The icons are updated, the previous install is kept, and older ones are
   removed.

A failure anywhere before step 5 has these results:
- the incomplete install is removed;
- the previous install stays active;
- a redacted support bundle is written to `logs/support/install-failure-<time>.zip`:
  - the logs, with the home folder, the user name and any token masked;
  - the step results;
  - OS, architecture and free disk.

An interrupted run leaves a folder without `install.json`. It is never activated, and the
next run removes it.

A second `install` of the same installer is a no-op: it only recreates a missing icon.
`repair` runs the smoke tests against the active install. If anything is damaged, it
installs it again into a new id from the download cache, or from the same `--assets` or
`--from-bundle` source. `uninstall` refuses while opncafyr is running (`--force` to
override), and it never removes a folder that the recent-workspaces list names. On
Windows, when `install.bat uninstall` runs from the installed copy
(`%LOCALAPPDATA%\opncafyr\installer`), that folder goes a few seconds after the command
finishes, because cmd is still reading `install.bat` until then.

## Offline installation: for IT administrators (#36)

Offline installation is a supported v1 use case, for firewalled environments such as GxP
cloud analytical systems. It uses the same installer and the same code path as an online
install; only the source of the files differs. In bundle mode the installer never uses
the network, not even as a fallback. Every file is checked against the SHA-256 pinned
inside the installer before anything is installed.

### 1. Build a bundle on a connected machine

Any connected Mac or Windows PC can build a bundle for any supported platform:

```sh
# macOS, for Windows users:
./install.sh bundle --platform windows-x86_64 --out bundles --archive \
    --assets <folder with the opnca-r files>        # or set GH_TOKEN
```
```bat
rem Windows, for Mac users:
install.bat bundle --platform macos-arm64 --out bundles --archive --assets <folder>
```

- `--platform`: `windows-x86_64`, `macos-arm64` or `macos-x86_64` (default: this machine).
- `--archive` also writes `opncafyr-bundle-<version>-<platform>.zip`.
- `--with-libreoffice` adds LibreOffice (optional, for slide previews; about 300–400 MB).
- `--mirror mirror.toml` fetches from internal mirrors instead (below).
- An installed opncafyr can do the same with `opncafyr bundle …`.

The builder fetches every file from its original source and checks each against the
pins. Your organisation assembles the bundle itself, so it is not a copy we redistribute
(#43). opnca-r is private: provide its release files with `--assets`, or set `GH_TOKEN`.
A macOS or Windows bundle is about 550 MB.

Contents of `opncafyr-bundle-<version>-<platform>/`:

| Path | What |
|---|---|
| `assets/<component>/<file>` | Every pinned file, e.g. `assets/r/R-4.6.2-win.exe`, `assets/r-packages/R6_2.6.1.zip`, `assets/wheels/…` |
| `installer/` | The installer itself: `install.sh`, `install.bat`, the opncafyr wheel, the pins |
| `bundle.json` | Manifest (schema `bundle-manifest-v1`): bundle ID, and for every file its component, version, SHA-256, size, source URL and licence |
| `SHA256SUMS` | Every other file's SHA-256 (`shasum -a 256 -c SHA256SUMS`, or `Get-FileHash`) |
| `sbom.spdx.json` | SPDX 2.3 software bill of materials |
| `licenses.json` | Licence inventory, read from each package's own metadata |

Two builds from the same installer list the same files and hashes; the bundle ID is a
hash of them. CI builds a bundle for each platform on every change to the installer, and
keeps it as a workflow artifact.

### 2. Carry it across and check it

Copy the folder or the zip to the target machine by your usual approved route. You can
check it with `SHA256SUMS`, but you don't have to: the installer checks every file
against the pins inside it and refuses a missing or changed file, naming the component.
It never reads `bundle.json` or `SHA256SUMS`.

There is no detached signature yet; code signing is #39. Until then, compare the
installer payload digest (in `installer/payload.json` and in the installation report)
with the value published for the release.

### 3. Install as the user, offline

```sh
opncafyr-bundle-<version>-macos-arm64/installer/install.sh install --from-bundle opncafyr-bundle-<version>-macos-arm64.zip
```
```bat
opncafyr-bundle-<version>-windows-x86_64\installer\install.bat install --from-bundle opncafyr-bundle-<version>-windows-x86_64.zip
```

`--from-bundle` takes the zip or the unzipped folder. It needs no administrator rights,
and it makes no connection attempt. CI checks that by blocking the user's network and
failing on any logged attempt: `sandbox-exec` on macOS, a per-user firewall rule with drop
logging on Windows.

**Updates.** Build a bundle for the new version and run its installer the same way. The
new version is staged next to the old one and activated in one step. If the update fails,
or is interrupted (a closed window, a power cut), the old version stays active, and the
next run cleans up.

**Repair offline:** `install.sh repair --from-bundle <bundle>`.

### 4. The installation verification report

After every install, update or repair, the installer writes a report to
`<app root>/state/reports/`. It comes as `install-report-<time>.json` (schema
`install-report-v1`) and as a printable `install-report-<time>.html`. It records:
- the opncafyr version and the installer payload digest;
- the bundle ID, and how files were trusted;
- OS and architecture;
- the install paths;
- the version and SHA-256 of every component;
- the smoke tests: an opnca-r calculation against known values (synthetic data), a
  Quarto DOCX render with an R chunk run by the pinned R, and a deckifyr build.

Your organisation's own qualification (for example IQ) may use it as evidence.
**opncafyr is for research use only; the report is not a validation of opncafyr or of
any calculation.**

### Internal-mirror mode

If your site mirrors GitHub releases, PyPI files and CRAN (Artifactory, Nexus and the
like), you don't need to carry a bundle. Describe the mirrors in a `mirror.toml`:

```toml
[mirrors]
"https://github.com/" = "https://artifacts.example.org/github-releases/"
"https://files.pythonhosted.org/" = "https://artifacts.example.org/pypi-files/"
"https://packagemanager.posit.co/cran/2026-11-06/" = "https://artifacts.example.org/cran-snapshot/"
"https://cran.r-project.org/" = "https://artifacts.example.org/cran/"
```

Then run `install.sh install --mirror mirror.toml`, or build a bundle from the mirrors
with `install.sh bundle … --mirror mirror.toml`.

- Each pinned URL is rewritten by its longest matching prefix. The file name and the
  pinned SHA-256 don't change, so a mirror changes only where a file comes from, never
  what is accepted.
- A URL without a configured prefix is refused; mirror mode never falls back to the
  public origin.
- The binary paths (`bin/windows/contrib/4.6`, `bin/macosx/…/contrib/4.6`) must be
  proxied, not only `src/contrib`.
- The private opnca-r release must be uploaded to the mirror by the site, under the same
  file names.
- Mirror credentials, if any, come from the user's `~/.netrc` (`%USERPROFILE%\_netrc`),
  never from `mirror.toml`.
- For HTTPS through a TLS-inspecting proxy, use `--cacert`.

## Testing

- `uv run pytest tests/test_installer.py tests/test_installer_parts.py` runs the
  orchestration with fake pins and fake staging steps (every CI OS).
- The `Installer` workflow (`.github/workflows/installer.yml`) runs the real installer as a
  new standard user on `macos-15` (arm64), `macos-15-intel` and `windows-2025`:
  install, second run, start the icon's launcher and check `/health`, a failure injected
  after staging (`OPNCAFYR_INSTALL_FAIL_AFTER=staging`), and uninstall with a workspace
  that must survive.
- The same workflow then covers the offline legs (#36):
  - it builds the bundle twice, compares the manifests, and keeps the zip as an artifact;
  - it blocks the user's network: `sandbox-exec` on macOS, a per-user firewall rule on
    Windows, where a deliberate attempt first proves that blocked connections are logged;
  - with the network blocked it installs from the bundle and checks the report, runs the
    Gate 2 flow on that install with the installed launcher (`scripts/offline/flow_check.py`,
    #51: import, clean, map, one opnca-r run per analyte, replay, publish, report, deck),
    refuses a tampered bundle, kills an update to a newer payload part-way through, and then
    completes it.

  Any logged connection attempt fails the job. `OPNCAFYR_PROXY_TRAP=0` turns off the
  closed loopback proxy during these legs, so a stray attempt reaches the logged block
  instead of failing quietly. `windows-2025` is Windows Server; clean Windows 11 and macOS VMs are
  #37.
- Build a payload locally with `uv run python scripts/make_installer.py`. For a scratch
  install, set `OPNCAFYR_APP_ROOT`, `OPNCAFYR_R_ROOT` (macOS) and `OPNCAFYR_APPS_DIR`
  (macOS); the launcher then finds its own install from its location.
