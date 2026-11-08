---
title: Install on Windows
parent: Get started
nav_order: 3
description: Install opncafyr for your own user account on Windows 11, with no administrator rights.
permalink: /get-started/install-windows/
---

# Install on Windows

Run `install.bat` from the unzipped installer. It installs opncafyr for you alone and adds **opncafyr** shortcuts to your Desktop and Start Menu.
{: .fs-5 .fw-300 }

Before you start, check [Before you install]({{ '/get-started/before-you-install/' | relative_url }}): you need 64-bit Windows 11, about 4 GB of free disk space, and the installer zip, `opncafyr-installer-<version>.zip`. You do not need administrator rights.

If your computer can't reach the internet, follow [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}) instead.

## Step 1. Unzip the installer

1. In File Explorer, right-click `opncafyr-installer-<version>.zip` and choose **Extract All…**, then **Extract**.
2. Open the extracted folder, `opncafyr-installer-<version>`. It holds `install.bat`.

Run the installer from the extracted folder, not from inside the zip.

## Step 2. Run the installer

1. Double-click `install.bat`. A window opens and shows each step.
2. Wait. A first install can take a while, because it downloads and tests R, Quarto and the tools. Leave the window open.
3. The window closes by itself when the installer has finished.

{: .expect }
> The window prints a line for each part in brackets (`[python]`, `[r]`, `[quarto]` and so on), then `[smoke tests]` with an `ok` line for each test, and ends with `opncafyr <version> installed`. When it closes, an **opncafyr** shortcut is on your Desktop.

{: .tip }
> To keep the installer's messages on screen after it finishes, run it from Command Prompt instead: in File Explorer, open the installer folder, click the address bar, type `cmd` and press Enter. In the Command Prompt window that opens, type `install.bat` and press Enter.

If the installer stops with an error, it changes nothing: your previous install, if you had one, stays in use. See [If something goes wrong](#if-something-goes-wrong) below.

## Step 3. Start opncafyr

1. Double-click the **opncafyr** shortcut on your Desktop, or find **opncafyr** in the Start Menu.

{: .expect }
> Your web browser opens a new tab with the app's **Home** page, and the research-use notice across the top. Continue with [Open the app and choose a study folder]({{ '/get-started/first-study/' | relative_url }}).

## If the installer stops at opnca-r

The opnca-r release is not public, so the installer may not be able to download it. It then stops before changing anything, with an error that names `opnca-r`.

1. Ask the person who gave you the installer for the opnca-r files, and put them together in one folder.
2. Open Command Prompt in the installer folder (click the address bar in File Explorer, type `cmd`, press Enter).
3. Run the installer with that folder:

   ```bat
   install.bat install --assets "<folder with the opnca-r files>"
   ```

The installer checks these files against the same fingerprints as any download, so a wrong or changed file is refused.

## Optional: slide previews with LibreOffice

LibreOffice is optional. Without it, PowerPoint decks still build, but without slide previews, and the **Tools** card says "Slide previews unavailable: LibreOffice is not installed." That message is not a fault.

To add LibreOffice (about 300–400 MB more), open Command Prompt in the installer folder and run:

```bat
install.bat install --with-libreoffice
```

{: .note }
> Add `--with-libreoffice` again each time you update or repair, or the new install is set up without LibreOffice.

## If something goes wrong

If the window closed before you could read the message, run the installer again from Command Prompt (see the tip in [Step 2](#step-2-run-the-installer)) so the message stays on screen.

{: .fix }
> **"opncafyr needs Windows 11"** or **"opncafyr needs 64-bit Windows 11"** — this computer runs an older or 32-bit Windows. Use a supported computer.

{: .fix }
> **"Not enough free disk space"** — free up space on the drive that holds your user folder until about 4 GB is free, then run the installer again.

{: .fix }
> **"Running elevated is not needed"** — this is a warning, not an error: you started the installer as an administrator. It still installs for your own account only. Next time, double-click `install.bat` normally.

{: .fix }
> **"Another opncafyr installer is running"** — wait for the other installer window to finish, then try again.

{: .fix }
> **"pinned file(s) missing or wrong"** with `download failed` — the installer could not download a file. Check your network connection and run the installer again; files it already downloaded are kept, so it picks up where it stopped. If you are behind a company proxy, ask IT, or see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#installer-problems).

{: .fix }
> **"The bundled Python can't run here"** — your organisation's application control blocks programs in `%LOCALAPPDATA%\opncafyr`. Ask IT to allow it.

{: .fix }
> **"the installer is damaged"** — the zip was not fully downloaded or a file was changed. Download the installer again and extract it again.

{: .fix }
> **"Install failed at step ..."** — the installer removed the half-finished install, kept your previous one, and wrote a support file, `logs\support\install-failure-<time>.zip`, in `%LOCALAPPDATA%\opncafyr`. Send it to the person who supports opncafyr; see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#if-the-install-failed).

## What the installer did

- It put opncafyr, its own Python, R, Quarto and the tools in `%LOCALAPPDATA%\opncafyr`. R is installed for your account only; a copy of R you already have keeps working as before.
- It added **opncafyr** shortcuts to the Desktop and the Start Menu.
- It wrote an installation report in the app folder's `state\reports` folder, listing the version of every part and the results of the start-up tests. The report is not a validation of the app or of any calculation.
- It kept a copy of itself in the app folder, so you can repair, update or uninstall later without the zip. See [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

It did not change your `PATH`, any R you already have, or anything outside your user folder.
