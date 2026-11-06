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
   - `deckifyr --json validate` on a sample deck.

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

## Offline bundles

`--from-bundle` takes a folder or a `.zip` laid out as:

```
assets/<component>/<file>     every pinned file, e.g. assets/r/R-4.6.2-win.exe,
                              assets/r-packages/R6_2.6.1.zip, assets/wheels/…
installer/                    the installer payload (wrappers, wheel, pins)
```

The installer looks files up by their pinned name and SHA-256. It never reads a bundle's
own hash list, so a bundle can't change what is accepted.

## Testing

- `uv run pytest tests/test_installer.py tests/test_installer_parts.py` runs the
  orchestration with fake pins and fake staging steps (every CI OS).
- The `Installer` workflow (`.github/workflows/installer.yml`) runs the real installer as a
  new standard user on `macos-15` (arm64), `macos-15-intel` and `windows-2025`:
  install, second run, start the icon's launcher and check `/health`, a failure injected
  after staging (`OPNCAFYR_INSTALL_FAIL_AFTER=staging`), and uninstall with a workspace
  that must survive. `windows-2025` is Windows Server; clean Windows 11 and macOS VMs are
  #37.
- Build a payload locally with `uv run python scripts/make_installer.py`. For a scratch
  install, set `OPNCAFYR_APP_ROOT`, `OPNCAFYR_R_ROOT` (macOS) and `OPNCAFYR_APPS_DIR`
  (macOS); the launcher then finds its own install from its location.
