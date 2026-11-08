---
title: Repair, update and uninstall
parent: Guides
nav_order: 3
description: Check what is installed, repair a damaged install, update to a new version, and uninstall opncafyr without touching your study folders.
permalink: /guides/maintenance/
---

# Repair, update and uninstall

Keep an install healthy, move to a new version, or remove opncafyr. None of these ever changes a study folder.
{: .fs-5 .fw-300 }

The installer keeps a copy of itself inside the app folder, so you can check, repair and uninstall without the installer zip. The commands on this page use that copy:

| | Where the installer copy is | Run commands in |
|---|---|---|
| macOS | `~/Library/Application Support/opncafyr/installer/install.sh` | Terminal |
| Windows | `%LOCALAPPDATA%\opncafyr\installer\install.bat` | Command Prompt |

If you still have the installer zip, you can run the same commands from it instead (`./install.sh repair`, `install.bat repair` and so on).

## See what is installed

On macOS:

```sh
"$HOME/Library/Application Support/opncafyr/installer/install.sh" status
```

On Windows:

```bat
"%LOCALAPPDATA%\opncafyr\installer\install.bat" status
```

{: .expect }
> The installer prints the app folder, the active install (for example `active:   <version>-1`), and each install it keeps, marked `complete` or `incomplete`, with `(active)` after the one in use. It changes nothing.

The installer keeps the active install and the one before it. Older ones are removed after an update.

## Repair

A repair runs the same start-up tests as an install. If something is damaged, it installs that version again, next to the damaged one, and switches to it in one step. If nothing is damaged, it only recreates a missing icon.

1. Select **Quit** in the app.
2. Run the repair.

   On macOS:

   ```sh
   "$HOME/Library/Application Support/opncafyr/installer/install.sh" repair
   ```

   On Windows:

   ```bat
   "%LOCALAPPDATA%\opncafyr\installer\install.bat" repair
   ```

{: .expect }
> If the install is fine: `<version>-1 checked: nothing to repair`. If not, the installer says what is damaged, installs the version again, runs the start-up tests and ends with `opncafyr <version> installed`.

A repair takes the files it needs from the installer's download cache, and downloads anything missing. If you installed with extra options, give them again:

- installed from an offline bundle: add `--from-bundle` and the bundle (see [Repair offline]({{ '/guides/offline-install/' | relative_url }}#repair-offline));
- installed with `--assets` (for example, for the opnca-r files): add the same `--assets "<folder>"`;
- installed with LibreOffice: add `--with-libreoffice`.

{: .fix }
> **"Nothing is installed to repair. Run `install` instead."** — there is no complete install. Run the installer zip, as in [Install on macOS]({{ '/get-started/install-macos/' | relative_url }}) or [Install on Windows]({{ '/get-started/install-windows/' | relative_url }}).

{: .fix }
> **The repair stops at opnca-r** — the opnca-r files are not in the download cache. Add `--assets "<folder with the opnca-r files>"`, as for the first install.

To install the same version again even when nothing is damaged, run `install --reinstall` from the installer zip.

## Update to a new version

1. Get the installer zip for the new version (or the new offline bundle).
2. Select **Quit** in the app, so the next start uses the new version.
3. Run the new installer in the same way as a first install: [macOS]({{ '/get-started/install-macos/' | relative_url }}), [Windows]({{ '/get-started/install-windows/' | relative_url }}) or [offline]({{ '/guides/offline-install/' | relative_url }}#update-offline). Give the same options as before (such as `--with-libreoffice`).

{: .expect }
> The installer ends with `opncafyr <new version> installed`. The next time you start the app, it runs the new version.

The new version is set up next to the old one and switched on in one step. If the update fails or is interrupted, the old version stays in use, and the next run of the installer cleans up. Your study folders are never touched.

When you open a study folder made by an older version, the app may offer **Upgrade (backup kept) and open**. It keeps a copy of the old records before it upgrades them.

If you run an installer whose version is already installed, it says `opncafyr <version> is already installed` and changes nothing, except to recreate a missing icon.

## Build opnca-r from source (only if asked)

Do this only when the person who supports opncafyr asks you to: for example, when opnca-r's ready-made program can't be used on your computer. A normal install never builds anything.

`repair --build-from-source` sets up a new install and builds opnca-r from its source code in it, instead of using the ready-made program. Your current install stays in use until the build and the start-up tests pass, so a failed build changes nothing.

| | macOS | Windows |
|---|---|---|
| Command | `"$HOME/Library/Application Support/opncafyr/installer/install.sh" repair --build-from-source` | `"%LOCALAPPDATA%\opncafyr\installer\install.bat" repair --build-from-source` |
| Also needs | Apple's Command Line Tools. Installing them needs an administrator (`xcode-select --install`). | Nothing else. The build tools (Rtools) are installed for the build only, then removed. |
| Extra time | about 5–10 minutes | about 15–25 minutes |

- It downloads a few more pinned files: opnca-r's source and the Rust toolchain (and Rtools on Windows). Offline, the bundle must have been built with them; ask your IT team (see [Internal mirrors and bundles]({{ '/guides/internal-mirrors/' | relative_url }})).
- Running the same version's installer again keeps the source build. Updating to a new version goes back to the ready-made program.

{: .fix }
> **macOS: "Building opnca-r from source on macOS needs Apple's Command Line Tools"** — ask IT to run `xcode-select --install`, then run the command again.

{: .fix }
> **Windows: "Building from source needs a folder without spaces"** — your user folder's path has a space, and Windows has no short name for it. Follow the message: ask IT whether short (8.3) names can be turned on for the drive.

## Uninstall

Uninstalling removes opncafyr for your user account: the app, its own R and tools, its settings, logs and download cache, and its icons. It never removes a study folder. Your data, runs, reports and decks stay where they are.

### Before you uninstall

1. Select **Quit** in the app. The uninstaller does not run while the app is open.
2. Support bundles are kept in the app's `logs` folder, which is removed. To keep one, download it from **Settings** first, or copy it out of the `logs/support` folder.
3. Make sure no study folder is inside the app folder. The uninstaller refuses to run if one is.

### Run the uninstaller

On macOS, in Terminal:

```sh
"$HOME/Library/Application Support/opncafyr/installer/install.sh" uninstall
```

This removes `~/Library/Application Support/opncafyr`, the app's R in `~/Library/opncafyr`, and the **opncafyr** icon in `~/Applications`.

On Windows, in Command Prompt:

```bat
"%LOCALAPPDATA%\opncafyr\installer\install.bat" uninstall
```

This removes `%LOCALAPPDATA%\opncafyr` and the Desktop and Start Menu shortcuts. The installer's own folder goes a few seconds after the command finishes.

{: .expect }
> The uninstaller lists what it removed and ends with `opncafyr is uninstalled.` and the path of its log, which it keeps in your computer's temporary folder.

{: .tip }
> To see what would be removed without removing anything, add `--dry-run` to the command.

{: .fix }
> **"opncafyr is running. Quit it from its page first"** — select **Quit** in the app, wait for the page to say opncafyr has stopped, then run the command again.

{: .fix }
> **"A workspace is inside the app folder"** — a study folder you used is inside the app folder, which uninstalling would delete. Move that study folder somewhere else first.

### Study folders after uninstalling

Study folders are never removed. Each one keeps its `.opncafyr` folder with the app's records, so a later install can open it again. To delete a study, delete its folder yourself.

## Check paths and tools with doctor

`doctor` prints the app's version, its app folder, the active install, and each tool's health. Use it if the app won't start, or if the person who supports opncafyr asks for it.

On macOS, in Terminal:

```sh
"$HOME/Library/Application Support/opncafyr/current/launcher/bin/python" -m opncafyr doctor
```

On Windows, in PowerShell:

```powershell
$r="$env:LOCALAPPDATA\opncafyr"; & "$r\versions\$(Get-Content $r\current)\launcher\Scripts\python.exe" -m opncafyr doctor
```

{: .expect }
> Lines for `opncafyr`, `python`, `app root` and `install`, then one `tool` line for each tool, with its state and a short reason.

The lines for `uv`, `Rscript`, `quarto`, `cargo` and `soffice` show what is on your computer's own `PATH`. opncafyr doesn't use those programs, so "not found" there is not a problem.

`doctor` can also make a support bundle: see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#from-the-command-line).

## The installation report

After every install, update or repair, the installer writes an installation report in the app folder's `state/reports` folder, as `install-report-<time>.html` (printable) and `install-report-<time>.json`. It lists the versions of every part and the results of the start-up tests.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. The installation report is not a validation of the app or of any calculation.
