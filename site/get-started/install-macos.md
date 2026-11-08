---
title: Install on macOS
parent: Get started
nav_order: 2
description: Install opncafyr for your own user account on a Mac, with no administrator rights.
permalink: /get-started/install-macos/
---

# Install on macOS

Run the installer from Terminal. It installs opncafyr for you alone and adds an **opncafyr** icon to your own Applications folder.
{: .fs-5 .fw-300 }

Before you start, check [Before you install]({{ '/get-started/before-you-install/' | relative_url }}): you need macOS 15 or later, about 4 GB of free disk space, and the installer zip, `opncafyr-installer-<version>.zip`. You do not need administrator rights.

If your Mac can't reach the internet, follow [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}) instead.

## Step 1. Unzip the installer

1. Double-click `opncafyr-installer-<version>.zip` in Finder. (Your browser may have unzipped it already.)
2. You get a folder, `opncafyr-installer-<version>`, with `install.sh` in it.

## Step 2. Open Terminal in the installer folder

1. Open **Terminal** (in **Applications**, then **Utilities**).
2. Type `cd ` (with a space after it). Don't press Return yet.
3. Drag the unzipped installer folder from Finder onto the Terminal window. Its path appears after `cd `.
4. Press Return.

## Step 3. Run the installer

1. Type the command below and press Return.

   ```sh
   ./install.sh
   ```

2. Wait. The installer prints each step as it goes. A first install can take a while, because it downloads and tests R, Quarto and the tools. Keep the Terminal window open until it finishes.

{: .expect }
> The installer starts with a line such as `opncafyr <version> install on macos-arm64 (macOS 15.x)`, then `checking ... pinned files (online)` and `all ... files match their pinned SHA-256`. It prints each part in brackets (`[python]`, `[r]`, `[quarto]` and so on), then `[smoke tests]` with an `ok` line for each test. It ends with `activated <version>-1`, the path of an installation report, and `opncafyr <version> installed (<version>-1) in ... s`.

If the installer stops with an error, it changes nothing: your previous install, if you had one, stays in use. See [If something goes wrong](#if-something-goes-wrong) below.

## Step 4. Start opncafyr

1. In Finder, open your home folder (**Go** menu, then **Home**), then open **Applications**. This is your own Applications folder, not the one at the top of the disk.
2. Double-click **opncafyr**.

{: .expect }
> Your web browser opens a new tab with the app's **Home** page, and the research-use notice across the top. Continue with [Open the app and choose a study folder]({{ '/get-started/first-study/' | relative_url }}).

{: .tip }
> To start opncafyr from the Dock, drag **opncafyr** from that Applications folder onto the Dock.

## If the installer stops at opnca-r

The opnca-r release is not public, so the installer may not be able to download it. It then stops before changing anything, with an error that names `opnca-r`.

1. Ask the person who gave you the installer for the opnca-r files, and put them together in one folder.
2. In the same Terminal window, run the installer again with that folder. Type the start of the command, then drag the folder onto the window to fill in its path:

   ```sh
   ./install.sh install --assets "<folder with the opnca-r files>"
   ```

The installer checks these files against the same fingerprints as any download, so a wrong or changed file is refused.

## Optional: slide previews with LibreOffice

LibreOffice is optional. Without it, PowerPoint decks still build, but without slide previews, and the **Tools** card says "Slide previews unavailable: LibreOffice is not installed." That message is not a fault.

To add LibreOffice (about 300–400 MB more), run the installer with this option:

```sh
./install.sh install --with-libreoffice
```

{: .note }
> Add `--with-libreoffice` again each time you update or repair, or the new install is set up without LibreOffice.

## If something goes wrong

{: .fix }
> **`Permission denied` when you run `./install.sh`** — the file lost its "can run" setting while it was copied. Run `bash install.sh` instead; it does the same thing.

{: .fix }
> **`no such file or directory: ./install.sh`** — Terminal is not in the installer folder. Repeat [Step 2](#step-2-open-terminal-in-the-installer-folder), and drag the folder that holds `install.sh`.

{: .fix }
> **"opncafyr needs macOS 15 or later"** — this Mac's macOS is too old. Update macOS, or use a supported computer.

{: .fix }
> **"Not enough free disk space"** — free up space in your home folder until about 4 GB is free, then run the installer again.

{: .fix }
> **"don't run the installer as root or with sudo"** — run `./install.sh` without `sudo`, as yourself.

{: .fix }
> **"Another opncafyr installer is running"** — wait for the other installer window to finish, then try again.

{: .fix }
> **"pinned file(s) missing or wrong"** with `download failed` — the installer could not download a file. Check your network connection and run the installer again; files it already downloaded are kept, so it picks up where it stopped. If you are behind a company proxy, ask IT, or see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#installer-problems).

{: .fix }
> **"the bundled Python can't run here"** — your organisation's security software blocks programs in `~/Library/Application Support/opncafyr`. Ask IT to allow it.

{: .fix }
> **"the installer is damaged"** — the zip was not fully downloaded or a file was changed. Download the installer again.

{: .fix }
> **"Install failed at step ..."** — the installer removed the half-finished install, kept your previous one, and wrote a support file, `logs/support/install-failure-<time>.zip`, in the app folder. Send it to the person who supports opncafyr; see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#if-the-install-failed).

## What the installer did

- It put opncafyr, its own Python, R, Quarto and the tools in `~/Library/Application Support/opncafyr`, and the app's R in `~/Library/opncafyr`.
- It added the **opncafyr** icon to `~/Applications`.
- It wrote an installation report in the app folder's `state/reports` folder, listing the version of every part and the results of the start-up tests. The report is not a validation of the app or of any calculation.
- It kept a copy of itself in the app folder, so you can repair, update or uninstall later without the zip. See [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

It did not change your `PATH`, any R you already have, or anything outside your user folder.
