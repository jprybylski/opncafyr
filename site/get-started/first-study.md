---
title: Open the app and choose a study folder
parent: Get started
nav_order: 4
description: Start opncafyr, find your way around the app, and create or open a study folder.
permalink: /get-started/first-study/
---

# Open the app and choose a study folder

Start opncafyr, learn the layout, and set up the study folder that will hold your data, runs, reports and decks.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

## Step 1. Start opncafyr

Start the app from its icon:

- **macOS:** open **Applications** in your home folder (in Finder, **Go** menu, then **Home**) and double-click **opncafyr**.
- **Windows:** double-click the **opncafyr** shortcut on your Desktop, or choose **opncafyr** in the Start Menu.

{: .expect }
> After a few seconds your web browser opens a new tab with the app's **Home** page.

The app runs only on your computer. The browser connects to it at an address that starts `http://127.0.0.1`, which only your own computer can reach. Nothing you load into the app leaves the computer.

If the app is already running, starting it again opens the same app in a new tab. To come back to the app later, start it from its icon again rather than from a bookmark or the browser's history.

### Starting it from a command line

You usually don't need this. If the icon is missing, or you want to see the app's messages, you can start it from a command line.

On macOS, in Terminal:

```sh
"$HOME/Library/Application Support/opncafyr/current/launcher/bin/python" -m opncafyr launch
```

On Windows, in PowerShell:

```powershell
$r="$env:LOCALAPPDATA\opncafyr"; & "$r\versions\$(Get-Content $r\current)\launcher\Scripts\python.exe" -m opncafyr launch
```

The command keeps running while the app is open. It stops when you select **Quit** in the app.

{: .fix }
> **macOS: "opncafyr is not installed correctly"** — the icon can't find the installed app. Repair the install: see [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

{: .fix }
> **The icon is missing** — run a repair (see [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}#repair)). It recreates a missing icon. Running the installer zip again does the same.

{: .fix }
> **No browser tab opens** — wait a few seconds and start the app from its icon again. If nothing opens, see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}).

## Step 2. Find your way around

<figure class="shot">
  <img src="{{ '/assets/screenshots/01-home.png' | relative_url }}" width="1280" height="1183" loading="lazy"
       alt="The Home page with a study folder open. The left side lists Home, Data, Jobs, Analysis, Publish, Settings and Guide, and under Apps, deckifyr and opnca-r. The main area shows the study folder card, then the Tools, Activity, Recent runs and Publications cards.">
  <figcaption>The Home page, with a study folder open.</figcaption>
</figure>

- **The left side** has **Home**, **Data**, **Jobs**, **Analysis**, **Publish**, **Settings** and **Guide**. Under **Apps** it lists the tools that have their own screens, such as opnca-r and deckifyr.
- **The top bar** shows the open study folder, how many jobs are running, a **Theme** button and **Quit**.
- **The research-use notice** is on every page.
- **Guide** holds the user guide inside the app. It works without a network.

**Home** has a **Study folder** card at the top and four cards under it:

- **Tools** lists the installed tools: opnca-r, quartifyr + reportifyr, and deckifyr. Each shows what it can do and its health: **Available**, **Checking** or **Unavailable**.
- **Recent runs** lists this study's runs, newest first, with the analyte and the state.
- **Publications** lists the sets of tables and figures you have published.
- **Activity** shows running jobs and the last few finished ones. **All jobs →** opens the **Jobs** page.

{: .fix }
> **A tool shows Unavailable** — quit the app and start it again. If it is still **Unavailable**, see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}#a-tool-shows-unavailable).

"Slide previews unavailable: LibreOffice is not installed." and "Field recalculation unavailable: LibreOffice is not installed." are not faults. LibreOffice is optional.

## Step 3. Make a folder for the study

A study folder keeps one study's data, cleaning, runs, reports and decks together. You choose where it lives, for example in your Documents folder or on a shared drive you can write to.

1. In Finder or File Explorer, make a new folder for the study, for example `Documents/TA-study`.
2. Inside it, make a folder called `source-data` and copy your data files into it.

{: .warning }
> Don't put a study folder inside a hidden folder (one whose name starts with a dot). Reports cannot be built there. Don't put it inside the app folder either.

## Step 4. Create or open the study folder in the app

1. Go to **Home** and select **Choose study folder…**. Your computer's folder picker opens.
2. Pick the study folder and confirm.
3. The app shows what it found: the folder, whether you can write there (**Can write here**), and how many items it holds.
4. Select the button the app offers:
   - **Create workspace here** for a folder that is not a study folder yet. This adds only a `.opncafyr` folder.
   - **Open workspace** for a folder that is already a study folder.
   - **Upgrade (backup kept) and open** for a study folder made by an older version of opncafyr. The app keeps a copy of the old records before it upgrades them.

{: .expect }
> The study folder's name shows on the **Home** page and in the top bar, and the folder appears under **Recent study folders**.

To switch studies, select **Change study folder…**, or pick one under **Recent study folders**.

{: .fix }
> **"Can write here: No"** — you don't have permission to write to that folder. Choose a folder you own, or ask whoever manages the shared drive.

{: .fix }
> **A recent folder shows "(not found)"** — it has been moved, renamed or deleted. Select **Change study folder…** and pick it in its new place.

{: .fix }
> **Unfinished jobs "were marked failed"** — the app was stopped while they ran. Start them again from the page where you started them.

## What the study folder contains

When you create a workspace, the app adds one folder, `.opncafyr`. Your computer may hide it, because its name starts with a dot. It holds the app's records: imported files, cleaning recipes, runs, publications and jobs.

Later steps add more folders, but only when you use them:

| Folder | Made when | What it holds |
|---|---|---|
| `.opncafyr` | you create the workspace | the app's records |
| `source-data` | you make it | the data files you want to work with |
| `OUTPUTS` | you publish | copies of the tables and figures you chose |
| `report` | you set up a report | the Word report project and its builds |
| `deck` | you set up a deck | the PowerPoint deck project and its builds |

The app never changes, moves or rewrites your source files. Cleaning reads them and writes its results inside `.opncafyr`. The [file and artifact map]({{ '/reference/files/' | relative_url }}) describes these folders in more detail.

Uninstalling opncafyr never removes a study folder. A later install can open it again.

## Quitting

Select **Quit** in the top bar. If jobs are still running, the app asks whether to **Keep running and quit when finished**, **Cancel jobs and quit**, or **Don't quit**. If an editor has unsaved changes, it asks you to save or discard them.

The app also closes itself after 30 minutes with no open page (you can change this on **Settings**). It never does so while a job is running or an editor has unsaved changes. Your work is in the study folder, so nothing is lost.

## Next step

Try the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}). It uses a small practice file and takes you from an empty folder to a report and a deck.
