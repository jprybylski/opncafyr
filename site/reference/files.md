---
title: Files and folders
parent: Reference
nav_order: 2
description: What opncafyr writes in a study folder and in its app folder, and which files you may edit.
permalink: /reference/files/
---

# Files and folders

What opncafyr writes where, and which files you may change.
{: .fs-5 .fw-300 }

opncafyr keeps two kinds of files apart. Your **study folders** hold your data, runs, publications, reports and decks. The **app folder** holds the app itself, its own R and tools, settings and logs. Neither is ever inside the other.

## In a study folder

| Path | Made when | What it holds | Can you edit it? |
|---|---|---|---|
| `source-data/` | you make it | your data files (CSV). The app also lists CSV files sitting directly in the study folder. | Add files freely. Don't change a file after importing it; a changed file imported again is recorded as a new source. |
| `.opncafyr/` | you select **Create workspace here** | the app's records (below). Hidden on most computers because its name starts with a dot. | **No.** Don't edit, move or delete anything in it. |
| `OUTPUTS/tables/`, `OUTPUTS/figures/` | you publish | published copies, named `<analyte>_<run-id>_<item>.<ext>`, each with a description file `<name>_<ext>_metadata.json` | **No.** Open or copy them, but don't change, rename or delete them. |
| `report/` | you select **Set up report** | the Word report project | Edit `report.qmd` with **Edit report**. Leave the rest as the app set it up. |
| `report/builds/<build-id>/` | you select **Render** | one recorded build: `report-draft.docx` or `report-final.docx`, `build.json`, `render-result.json` | **No.** Download or copy the DOCX instead. |
| `deck/` | you select **Set up deck** | the PowerPoint deck project: `presentation.yaml` (the slides), design files, standard footnotes, `fragments/` | Edit slides in the deck editor (**Open editor**) and fragments with **Edit fragments**. |
| `deck/build/` | you build inside the deck editor | unrecorded draft builds | Yours to look at; the app doesn't use them. |
| `deck/builds/<build-id>/` | you select **Build** on **Publish** | one recorded build: the PPTX, `deck-manifest.json`, `build-result.json`, `build.json`, and `previews/` if you asked for slide previews | **No.** Download or copy the PPTX instead. |

`report/` and `deck/` each also hold an `OUTPUTS/` copy of the publication's files. The app manages these copies; checks and builds use their own checked copies of the publication's files, not these.

{: .warning }
> Don't put a study folder inside a hidden folder (one whose name starts with a dot), because reports can't be built there, or inside the app folder, because the uninstaller refuses to run while one is there.

### Inside `.opncafyr/`

| Path | What it holds |
|---|---|
| `workspace.json` | the study folder's record, including the list of imported source files with their fingerprints |
| `backups/` | copies of the records made before an upgrade from an older version |
| `recipes/<recipe-id>.yaml` | cleaning recipes, with their column mapping |
| `cleaned/<recipe-id>-<key>/` | the result of applying a recipe: the generated cleaning script `clean.R`, the cleaned rows, the rejected-row log and the per-step row counts |
| `checks/` | results of opnca-r's own data check |
| `runs/<run-id>/` | one run per folder (below) |
| `publications/<publication-id>.json` | one record per publication: exact run IDs and every file fingerprint |
| `report.json`, `deck.json` | which publication the report and the deck use |
| `publish-checks/` | results of report checks |
| `jobs/<job-id>/` | each job's state, events, output logs and diagnostics |

### Inside a run folder

Run IDs look like `20261107T220451Z-1106`: the date and time the run was made (UTC) and four random characters. Publication IDs look the same with `pub-` in front.

| Path | What it holds | Written by |
|---|---|---|
| `input/analysis.csv` | the frozen rows of this one analyte (and specimen) | the app, at **Start analysis** |
| `input/source-fingerprint.json` | the source file's path, size and fingerprint | the app |
| `input/opnca-mapping.json` | the column roles, unit labels and BLQ choices handed to opnca-r | the app |
| `settings.json` | the analyte, specimen, mapping, units as declared, the unit labels passed to opnca-r (`analysis_units`), BLQ choices, acknowledged warnings and row counts | the app |
| `analysis.R` | the script that reproduces the results; its header says "Research use only" | opnca-r, when you save |
| `analysis-record.json`, `artifacts.json`, `COMPLETE` | opnca-r's record of the analysis (with the unit labels it used), its list of saved files with a description of each, and its "save finished" marker | opnca-r |
| `results/` | the result tables (CSV), their formatted copies in `results/tables/` (`.rds`), and `results/figures/` | opnca-r |
| `logs/replay-<n>/` | each replay's report and replayed tables; the report lists any table opnca-r says its script doesn't reproduce, which isn't compared | the app |
| `manifest.json` | the run's record: state, inputs, choices (with the units and `analysis_units`), tool versions, replay and, once completed, the fingerprint of every file | the app |

The input files and `settings.json` are read-only from the start. When you select **Confirm and complete**, every file in the run is recorded with its fingerprint and made read-only. **Check files** on the **Analysis** page compares them with the record.

{: .note }
> To change anything about a run, make a new run (**New run from this**). Never edit a run folder: a run whose files don't match their record can't be published.

## In the app folder

| | macOS | Windows |
|---|---|---|
| App folder | `~/Library/Application Support/opncafyr` | `%LOCALAPPDATA%\opncafyr` |
| The app's R | `~/Library/opncafyr` (R needs a path without spaces) | inside the app folder |
| Icon | `~/Applications/opncafyr.app` | `opncafyr` shortcuts on the Desktop and in the Start Menu |

Inside the app folder:

| Path | What it holds |
|---|---|
| `current` | which installed version is active |
| `versions/<version>-<n>/` | one complete install: Python, uv, the launcher, the tools, the private R library, Quarto, and (Windows) R. A folder without `install.json` is unfinished and is removed by the next install. |
| `installer/` | a copy of the active installer, used for `repair`, `status` and `uninstall` |
| `state/settings.json` | your settings, such as **Close automatically when unused for** and the theme |
| `state/recent-workspaces.json` | the **Recent study folders** list |
| `state/reports/` | an installation report after every install, update or repair: `install-report-<time>.json` and a printable `.html` |
| `state/` (other files) | the running app's own records; leave them alone |
| `logs/launcher.log` | the app's log (no data values) |
| `logs/children/` | logs of the tools the app starts |
| `logs/install/` | one folder per install or repair, with `installer.log` |
| `logs/support/` | support bundles you saved, and `install-failure-<time>.zip` after a failed install |
| `cache/` | downloaded installer files and tool caches |

Nothing in the app folder is yours to edit by hand. Use the installer's `repair`, `status` and `uninstall` commands; see [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

Uninstalling removes the app folder, the app's R and the icons. It never removes a study folder. Support bundles are in `logs/support`, which uninstall removes, so copy any you want to keep first.
