---
title: Install offline from a bundle
parent: Guides
nav_order: 1
description: Install, update or repair opncafyr from an offline bundle on a computer that can't reach the internet.
permalink: /guides/offline-install/
---

# Install offline from a bundle

On a firewalled computer, install opncafyr from an offline bundle that your IT team prepared. The installer then never uses the network.
{: .fs-5 .fw-300 }

An offline bundle is one zip with every file the installer needs, and the installer itself. It is built for one kind of computer. The app works the same way after an offline install as after an online one, and it never needs the network afterwards.

This page is for the person installing. If you prepare bundles for others, see [Internal mirrors and bundles]({{ '/guides/internal-mirrors/' | relative_url }}).

## Before you start

- Check [Before you install]({{ '/get-started/before-you-install/' | relative_url }}): a supported computer, about 4 GB of free disk space, and your own user account. You do not need administrator rights.
- Get the bundle for your kind of computer from your IT team, or whoever supports opncafyr at your site. Its name ends with your platform:

  | Your computer | Bundle |
  |---|---|
  | Mac with Apple silicon | `opncafyr-bundle-<version>-macos-arm64.zip` |
  | Mac with an Intel processor | `opncafyr-bundle-<version>-macos-x86_64.zip` |
  | Windows 11 PC | `opncafyr-bundle-<version>-windows-x86_64.zip` |

  To check which Mac you have, open the Apple menu and choose **About This Mac**: **Chip** means Apple silicon, **Processor** means Intel.

- A macOS or Windows bundle is about 550 MB, and more if it includes LibreOffice. Copy it to your computer by your organisation's approved route.

You don't need a separate opnca-r download: the bundle already holds it.

## Install on macOS

1. Double-click the bundle zip in Finder. You get a folder, `opncafyr-bundle-<version>-<platform>`.
2. Open **Terminal** (in **Applications**, then **Utilities**).
3. Type `cd ` (with a space after it), drag the unzipped bundle folder onto the Terminal window, and press Return.
4. Type the command below and press Return. The `.` at the end means "this folder", the bundle.

   ```sh
   ./installer/install.sh install --from-bundle .
   ```

5. Wait until the installer finishes. Then start opncafyr from **opncafyr** in the Applications folder inside your home folder.

## Install on Windows

1. In File Explorer, right-click the bundle zip, choose **Extract All…**, then **Extract**. You get a folder, `opncafyr-bundle-<version>-windows-x86_64`.
2. Open that folder. Click the address bar, type `cmd` and press Enter. A Command Prompt window opens in the bundle folder.
3. Type the command below and press Enter. The `.` at the end means "this folder", the bundle.

   ```bat
   installer\install.bat install --from-bundle .
   ```

4. Wait until the installer finishes. Then start opncafyr from the **opncafyr** shortcut on your Desktop or in the Start Menu.

{: .expect }
> The installer says `checking ... pinned files (bundle)` and then `all ... files match their pinned SHA-256`. It installs each part, runs its start-up tests, and ends with `opncafyr <version> installed`. It makes no network connection at any point.

Every file in the bundle is checked against a fingerprint (a SHA-256 checksum) built into the installer before anything is installed. A missing or changed file is refused, so a damaged bundle can't produce a damaged install.

{: .tip }
> Keep the unzipped bundle. You need it to repair this install offline, and the steps below use it.

## If something goes wrong

{: .fix }
> **"is missing from the bundle"** or **"pinned file(s) missing or wrong"** — the installer did not find every file it needs, or a file was changed. The message names each part. Nothing has been changed. Check that:
> - the command window is in the unzipped bundle folder (step 3 on macOS, step 2 on Windows);
> - the bundle's name matches your kind of computer;
> - you ran the installer from the bundle's own `installer` folder, not one from another download.
>
> If all three are right, the bundle is incomplete or was damaged on the way. Ask for it again.

{: .fix }
> **"does not exist"** — the installer can't find the bundle at the path you gave. Open the command window in the unzipped bundle folder, as in the steps above, and run the command again.

For other messages, such as "Not enough free disk space", see the error list on [Install on macOS]({{ '/get-started/install-macos/' | relative_url }}#if-something-goes-wrong) or [Install on Windows]({{ '/get-started/install-windows/' | relative_url }}#if-something-goes-wrong). They mean the same in offline mode.

## Update offline

1. Get the bundle for the new version from your IT team.
2. Install it in the same way as above, from its own unzipped folder.

The new version is set up next to the old one and switched on in one step. If the update fails or is interrupted (a closed window, a power cut), the old version stays in use, and the next run cleans up. Your study folders are never touched.

## Repair offline

A repair checks the install and, if something is damaged, installs it again. Offline, it needs the bundle the install came from.

1. Quit opncafyr.
2. Open Terminal (macOS) or Command Prompt (Windows) in the unzipped bundle folder, as above.
3. Run:

   On macOS:

   ```sh
   ./installer/install.sh repair --from-bundle .
   ```

   On Windows:

   ```bat
   installer\install.bat repair --from-bundle .
   ```

{: .expect }
> If nothing is damaged, the installer says `checked: nothing to repair`. Otherwise it says what is damaged, installs that version again from the bundle, and switches to it.

For more on repair, updating and uninstalling, see [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

## Optional: LibreOffice

If your IT team built the bundle with LibreOffice (for slide previews in decks), add `--with-libreoffice` to the install command, and again each time you update or repair. If the bundle doesn't include it, the installer says LibreOffice's files are missing from the bundle and changes nothing; leave the option out.
