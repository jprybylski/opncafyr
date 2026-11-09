# Installing and updating

opncafyr installs for you alone. You do not need administrator rights. It puts everything in your own user folder and does not change other programs, including any R you already have.

The installer downloads each part it needs and checks every file against a fingerprint built into the installer. If any file is missing or does not match, it stops before changing anything.

## Before you start

- macOS 15 or later (Apple silicon or Intel), or Windows 11.
- About 4 GB of free disk space.
- The installer zip, `opncafyr-installer-<version>.zip`, from the person who supports opncafyr at your site.

If your study data lives on a Linux cloud computer where you can run Docker or Podman, you can run opncafyr there instead: see [Running on a cloud computer (container mode)](cloud-container.md).

If your computer cannot reach the internet, or your organisation blocks downloads, ask your IT team; they can use the offline bundle described in the developer documentation. The app works the same way after an offline install.

## Install on macOS

1. Unzip `opncafyr-installer-<version>.zip`. You get a folder with `install.sh` in it.
2. Open **Terminal** (in Applications, then Utilities).
3. Type `cd ` (with a space after it), drag the unzipped folder onto the Terminal window, and press Return.
4. Type the command below and press Return.

```sh
./install.sh
```

5. Wait. The installer prints each step. A first install can take a while, because it downloads and tests R, Quarto and the tools.
6. When it finishes, open your home folder in Finder, open **Applications** and double-click **opncafyr**.

## Install on Windows

1. Unzip `opncafyr-installer-<version>.zip`.
2. Double-click `install.bat` in the unzipped folder. A window opens and shows each step.
3. Wait until it says it has finished.
4. Start opncafyr from the **opncafyr** shortcut on your Desktop or in the Start Menu.

## If the installer stops at opnca-r

opnca-r is not public, so the installer may not be able to download it. If the installer stops with an error that names `opnca-r`, nothing has been changed. Ask the person who gave you the installer for the opnca-r files. Put them in one folder, then run the install again with that folder:

```sh
./install.sh install --assets "<folder with the opnca-r files>"
```

On Windows, open **Command Prompt** in the installer folder and run:

```bat
install.bat install --assets "<folder with the opnca-r files>"
```

## Starting and quitting the app

- Starting the app opens a new tab in your web browser with the **Home** page.
- If the app is already running, starting it again opens the same app in a new tab.
- To stop the app, select **Quit** in the top bar. If jobs are still running, the app asks whether to keep them running and quit when they finish, cancel them and quit, or not quit. If an editor has unsaved changes, it asks you to save or discard them.
- The app also closes itself after a time with no open page. You can change this on the **Settings** page; see [The app closed by itself](troubleshooting.md#the-app-closed-by-itself).

## Updating

Run the installer of the new version in the same way as a first install. The new version is set up next to the old one and switched on in one step. If the update fails or is interrupted, the old version stays in use. Your study folders are never touched.

## Checking and repairing an install

The installer keeps a copy of itself inside the app's folder, so you can check and repair the install later without the zip. Quit the app first.

On macOS, in Terminal:

```sh
"$HOME/Library/Application Support/opncafyr/installer/install.sh" repair
```

On Windows, in Command Prompt:

```bat
"%LOCALAPPDATA%\opncafyr\installer\install.bat" repair
```

Repair runs the same start-up tests as an install. If something is damaged, it installs that version again. Use `status` in place of `repair` to see what is installed without changing anything.

## What gets installed where

| | macOS | Windows |
|---|---|---|
| App folder | `~/Library/Application Support/opncafyr` | `%LOCALAPPDATA%\opncafyr` |
| R used by the app | `~/Library/opncafyr` | inside the app folder |
| Icon | `~/Applications/opncafyr` | Desktop and Start Menu shortcuts |

The app folder holds the installed versions, settings, logs and a download cache. Study folders are never inside it, and the installer never writes to them.

After every install, update or repair, the installer writes an installation report in the app folder's `state/reports` folder. It lists the versions of every part and the results of the start-up tests. The report is not a validation of the app or of any calculation.

## If an install fails

A failed install leaves your previous install in use and removes the half-finished one. It also writes a support file, `logs/support/install-failure-<time>.zip`, in the app folder. See [Getting help](troubleshooting.md#getting-help) for what to do with it.
