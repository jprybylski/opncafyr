---
title: Troubleshooting
parent: Guides
nav_order: 8
description: Fixes for common problems, where the logs are, and how to make a redacted support bundle for whoever supports opncafyr.
permalink: /guides/troubleshooting/
---

# Troubleshooting

Fixes for common problems, and how to get help without sending your data.
{: .fs-5 .fw-300 }

Most problems show up as a message on the page you are using, or as a failed job. Start there: the app writes its messages for you to read, and they usually say what to do. The app's **Guide** has the same help and works without a network.

## Installer problems

If the installer stops with an error, it has changed nothing: your previous install, if any, stays in use. The install pages list the common messages: [macOS]({{ '/get-started/install-macos/' | relative_url }}#if-something-goes-wrong), [Windows]({{ '/get-started/install-windows/' | relative_url }}#if-something-goes-wrong), [offline]({{ '/guides/offline-install/' | relative_url }}#if-something-goes-wrong).

{: .fix }
> **An error that names `opnca-r`** — the opnca-r release is not public, so the installer could not download it. Ask whoever provides your opncafyr install for the opnca-r files, then run the installer again with `--assets "<folder with the opnca-r files>"`. See [Install on macOS]({{ '/get-started/install-macos/' | relative_url }}#if-the-installer-stops-at-opnca-r) or [Install on Windows]({{ '/get-started/install-windows/' | relative_url }}#if-the-installer-stops-at-opnca-r).

{: .fix }
> **Downloads fail behind a company proxy** — the installer uses your computer's own `curl`, so your system's certificates and the `HTTPS_PROXY` setting apply. If that isn't enough, ask IT for the proxy address and, if the proxy inspects secure connections, a certificate file. Then add `--proxy <address>` and `--cacert "<certificate file>"` to the install command.

{: .fix }
> **Your computer can't reach the internet at all** — install from an offline bundle instead: see [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}).

{: .fix }
> **The Windows installer window closed before you could read it** — run `install.bat` from Command Prompt so the messages stay on screen. See [Install on Windows]({{ '/get-started/install-windows/' | relative_url }}).

### If the install failed

A failed install removes the half-finished install, keeps your previous one in use, and writes its own support file, `logs/support/install-failure-<time>.zip`, in the app folder (`~/Library/Application Support/opncafyr` on macOS, `%LOCALAPPDATA%\opncafyr` on Windows). It holds the install logs and step results, the operating system, architecture and free disk space, with your home folder, user name and any passwords or tokens masked. Send it to the person who supports opncafyr.

Many install problems are fixed by a repair: see [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}#repair).

## The app doesn't start

{: .fix }
> **macOS: "opncafyr is not installed correctly"** — the icon can't find the installed app. Run a repair: see [Repair]({{ '/guides/maintenance/' | relative_url }}#repair).

{: .fix }
> **The icon is missing** — run a repair. It recreates a missing icon.

{: .fix }
> **Nothing opens, or no browser tab appears** — wait a few seconds and start the app from its icon again. If the app is already running, this opens it in a new tab. If it still doesn't open, run `doctor` (see [Check paths and tools with doctor]({{ '/guides/maintenance/' | relative_url }}#check-paths-and-tools-with-doctor)), then make a support bundle [from the command line](#from-the-command-line).

{: .fix }
> **The page says "opncafyr has stopped. You can close this tab."** — the app was quit, or closed itself after a time with no open page. Start it again from its icon. See [The app closed by itself](#the-app-closed-by-itself).

## A tool shows Unavailable

On **Home**, each tool in the **Tools** card shows its health. **Unavailable** means the app could not start or find that tool. The tile shows a short reason underneath.

1. Quit the app and start it again. Tools are checked each time the app starts.
2. If the tool is still **Unavailable**, repair the install: see [Repair]({{ '/guides/maintenance/' | relative_url }}#repair).
3. If repair does not help, make a [support bundle](#make-a-support-bundle).

"Slide previews unavailable: LibreOffice is not installed." is not a fault. LibreOffice is optional; without it, decks build without slide previews.

## A job failed

1. Select **Jobs** on the left.
2. Find the job marked **Failed**. Its message says what went wrong.
3. Fix what the message names, then start the job again from the page where you started it.

A failed or cancelled job keeps nothing half-made, so you can always start it again. If the message is unclear, make a support bundle: it includes recent job states and messages, without your data.

If you opened a study folder and the app says unfinished jobs "were marked failed", the app was stopped while they ran. Start them again.

## A check blocks the run

A **Blocking** finding means the data or mapping needs a change before a run can start. The app never fixes these for you.

1. Read the finding's message and hint. The line numbers are lines in the source file; the header is line 1.
2. Change the mapping (card **4. Column mapping**) or add a cleaning step (card **2. Cleaning steps**). [Blocking findings]({{ '/guides/cleaning/' | relative_url }}#blocking-findings) lists the usual fixes.
3. If you changed the recipe, select **Apply to all rows** again.
4. Select **Run checks** again.

If **Start analysis** stays greyed out, read the line above it. It says what is left: run the checks, fix blocking findings, acknowledge warnings, or choose all three BLQ rules and the BLQ method. (BLQ means below the lower limit of quantification.)

If the analyte you want shows no rows, check its exact text. `"METAB"` and `"METAB "` are different values. To join them, add a **Replace values** or **Trim spaces** step; that is your decision, and it is recorded. See [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }}).

## opnca-r doesn't open

- **Open in opnca-r** shows only on runs that are **Preparing** or **Running**. A run that has been saved cannot be opened again; make a new run instead.
- While opnca-r starts, the page says "opnca-r is starting. It opens here when it is ready." Wait a moment.
- If the page says "opnca-r is not running", go back to **Analysis** and select **Open in opnca-r** again.
- If opnca-r shows **Stopped unexpectedly**, select **Reload**, or open the run again from **Analysis**.
- If the **Analysis** page shows an error after **Open in opnca-r**, read it, then check opnca-r's health on **Home**.
- If it says the run "was prepared before the launcher passed its unit labels to opnca-r", the run was made by an older version of the app and can't be opened in opnca-r any more. Make a new run: set up cards **5. Analyte and checks** and **6. Start analysis** again and select **Start analysis**.

## A save or replay does not go through

- If the **Analysis** page says the save failed, the run is **Failed**. Select **New run from this** to try again with the same choices. One reason is a save whose unit labels are not the run's: opnca-r labelled the results in other units.
- If the page says the run "did not replay to the same results", it lists the differences. Select **Replay again**. If it still does not match, the run cannot be completed or published. Make a new run, and make a support bundle so someone can look into it.

## A report or deck fails to build

1. Read the message on the **4. Report (DOCX)** or **5. Deck (PPTX)** card, or the failed job on the **Jobs** page.
2. Select **Check** and fix what it lists. Most build problems show up there first.
3. If you edited the report, look at your last change. Use **Source** in the editor to see the full text.
4. Make sure the study folder is not inside a hidden folder (one whose name starts with a dot). Reports cannot be built there.
5. If the editor has unsaved changes, save or discard them. **Check** and **Render** wait until you do.

A failed build keeps nothing, so you can try again as often as you need.

{: .fix }
> **A deck build stops with "IndexError: tuple index out of range"** — this is a known issue in this version of the deck tool: it can't put a table with an empty cell on a slide. Build the deck from a publication with figures only. See [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }}).

## The app closed by itself

The app closes itself after a time with no open page, to free the computer's memory. It never closes while a job is running or an editor has unsaved changes.

To change the time, go to **Settings** and choose **Close automatically when unused for**: from 5 minutes to 2 hours, or **Never**. The default is 30 minutes.

When the app has stopped, the page says "opncafyr has stopped. You can close this tab." Start the app again from its icon. Your work is in the study folder, so nothing is lost.

## Make a support bundle

The person who supports opncafyr may ask for a support bundle: a zip file that helps them find out why something went wrong. You see everything in it before it is saved, and it never leaves your computer unless you send it.

1. Go to **Settings**.
2. Under **Support**, select **Create support bundle…**.
3. A window lists every part of the bundle. Open a part to read it. Nothing is saved yet.
4. Select **Save on this computer**, or **Cancel**.

<figure class="shot">
  <img src="{{ '/assets/screenshots/11-support.png' | relative_url }}" width="1280" height="800" loading="lazy"
       alt="The Support bundle window over the Settings page. It lists the parts of the bundle, such as README.txt, system.json, install.json, runtime.json, tools.json, jobs.json and three log files, each with its size, above the buttons Save on this computer and Cancel.">
  <figcaption>The support bundle window lists every part before anything is saved.</figcaption>
</figure>

{: .expect }
> The page says the bundle was saved in the app's `logs/support` folder, and that it stays on this computer. Under **Support** it lists your recent bundles; select one to download a copy, for example to attach it to an email.

### What a support bundle holds

- the versions of the app and its tools, your operating system, and the active install;
- whether the installed files still match the installer's fingerprints;
- each tool's health;
- for the open study folder: its name, counts of what its records hold, and the states and short messages of its 20 most recent jobs;
- the end of the app's log, of each tool's log, and of the last install log.

### What it never holds

- your data, cleaned data or results;
- the names of files in your study folder;
- the jobs' full output logs (only their sizes);
- your list of recent study folders, or your computer's environment settings.

In every message, subject identifiers, numbers and your user name are masked, your home folder is shown as `~`, and passwords and tokens are removed.

The bundle stays on your computer. It is never sent anywhere. Send it yourself if you choose to.

### From the command line

If the app will not start, you can make a support bundle from Terminal or PowerShell. It shows the list of parts and asks before it saves anything.

On macOS, in Terminal:

```sh
"$HOME/Library/Application Support/opncafyr/current/launcher/bin/python" -m opncafyr doctor --bundle
```

On Windows, in PowerShell:

```powershell
$r="$env:LOCALAPPDATA\opncafyr"; & "$r\versions\$(Get-Content $r\current)\launcher\Scripts\python.exe" -m opncafyr doctor --bundle
```

- To read everything first without saving, use `doctor --preview` in place of `doctor --bundle`.
- To include a study folder's recent jobs, add `--workspace` and the folder, for example `--workspace "$HOME/Documents/TA-study"`.

{: .expect }
> The command lists each part with its size and asks `Save it on this computer? [y/N]`. Type `y` and press Enter. It prints `Saved` and the path of the zip in the app's `logs/support` folder.

## Where the logs are

All logs are in the app folder: `~/Library/Application Support/opncafyr` on macOS, `%LOCALAPPDATA%\opncafyr` on Windows. A support bundle includes the end of each one, masked, so you rarely need to open them yourself.

| File or folder in the app folder | What it holds |
|---|---|
| `logs/launcher.log` | The app's own log |
| `logs/children/<tool>.log` | The log of each tool with its own screen, such as opnca-r and deckifyr |
| `logs/install/<time>-install/`, `logs/install/<time>-repair/` | One folder per install or repair, with `installer.log` and a log for each step |
| `logs/launcher-app.log` | macOS only: messages from starting the app with its icon |
| `logs/support/` | Saved support bundles and install-failure bundles |
| `state/reports/` | Installation reports, one per install, update or repair |

The uninstaller writes its log to your computer's temporary folder, because it removes the app folder.

The app keeps cell values out of its logs, and masks subject identifiers in its own log and the tools' logs before anything is written. Each job's full output logs stay in the study folder, next to the run, and are never put in a support bundle.
