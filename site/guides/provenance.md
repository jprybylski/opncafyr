---
title: Workspace and run provenance
parent: Guides
nav_order: 4
description: What a study folder records, why each run is a sealed, replayable bundle, and how publications, reports and decks pin exact runs and file fingerprints.
permalink: /guides/provenance/
---

# Workspace and run provenance

Every result in a study folder can be traced back to the source file, the cleaning, the choices you made and the tool versions that produced it.
{: .fs-5 .fw-300 }

*Provenance* is the record of where a result came from. opncafyr keeps that record in the study folder, next to your data, and uses file fingerprints so you can tell whether anything has changed since.

A *fingerprint* here is a SHA-256 hash: a long code computed from a file's exact bytes. Change one byte and the fingerprint changes. The app shows fingerprints, shortened, on the **Data** and **Publish** pages.

## The study folder

A study folder keeps one study's data, cleaning, runs, reports and decks together. You choose where it lives. When you create a workspace in a folder, the app adds one folder, `.opncafyr`, and nothing else. Later folders appear only when you use them:

| Folder | Made when | What it holds |
|---|---|---|
| `source-data` | you make it | the data files you want to work with |
| `.opncafyr` | you select **Create workspace here** | the app's records (see below) |
| `OUTPUTS` | you publish | copies of the tables and figures you chose |
| `report` | you set up a report | the Word report project and its builds |
| `deck` | you set up a deck | the PowerPoint deck project and its builds |

Your computer may hide `.opncafyr`, because its name starts with a dot.

{: .warning }
> Do not put a study folder inside a hidden folder (one whose name starts with a dot). Reports cannot be built there.

The app's runtimes and tools are installed elsewhere, in your user account's app-data folder, never in the study folder. For a full map of the files, see [Files and artifacts]({{ '/reference/files/' | relative_url }}).

### What `.opncafyr` holds

`.opncafyr` holds the app's records for this study:

- the list of imported source files, with each file's size and fingerprint;
- your cleaning recipes, and the results of applying them, including each rejected-row log;
- one folder per run;
- one record per publication;
- which publication the report and the deck use;
- the jobs the app has run, with their logs.

The app reads and writes these files itself. Make changes through the app, not by editing them.

The records have a version number. When you open a study folder made by an older version of the app, it offers **Upgrade (backup kept) and open** and keeps a copy of the old records before upgrading them.

## Source files are never changed

The app never changes, moves or rewrites your source files. Previewing reads a file; cleaning reads it and writes its results inside `.opncafyr`.

When you select **Import** on the **Data** page, the app records the file's size and fingerprint. It does not copy the file. Each run later checks that the file still matches what was recorded. If you change the file and import it again, the app records it as a new source, and the old runs keep pointing to the old version.

## Runs are sealed, replayable bundles

A run is one analyte's analysis. Each run is its own folder inside `.opncafyr`, and holds:

- a frozen copy of that analyte's rows, after cleaning and the analyte and specimen filters, which is read-only;
- the source file's fingerprint, and the recipe it was cleaned with;
- your mapping, units, BLQ choices and the warnings you acknowledged, and the unit labels passed to opnca-r;
- once opnca-r saves it: the analysis script opnca-r wrote, opnca-r's record of the analysis, and its result tables (as CSV files, with formatted copies) and figures;
- the tool versions used, such as opnca-r and R;
- the replay report;
- a manifest that lists all of the above with their fingerprints.

Rows of one analyte never appear in another analyte's run. Two runs can share a recipe, but each has its own frozen rows, script and results.

### The life of a run

| State | What it means |
|---|---|
| **Preparing** | The run was created with **Start analysis**. |
| **Running** | opnca-r is open on this run. |
| **Reviewing** | opnca-r saved the run, and the app is replaying it. |
| **Completed** | The run is sealed. Its files can no longer change. |
| **Failed** | The save could not be accepted. |

A run whose records are damaged shows as **Incomplete** on **Home**, and never as finished.

### Replay

When opnca-r saves a run, the app checks the saved files, then *replays* the run: it runs the saved analysis script again, on its own copy of the frozen data, and compares every result table with the saved one. If they match, you select **Confirm and complete**. If they do not, the app lists the differences, and the run cannot be completed or published.

{: .note }
> A matching replay shows that the saved script reproduces the saved numbers. It does not show that the analysis choices were right.

### Sealing

When you complete a run, the app *seals* it: every file in the run is listed with its fingerprint and size, and made read-only. To check a completed run at any time, select **Check files** in **7. Runs** on the **Analysis** page. The app says "every file matches its recorded hash", or lists what changed, went missing or was added.

## Any change makes a new run

A completed run never changes. To change anything that went into it, such as a cleaning step, the mapping, an exclusion or a BLQ rule, you make a new run:

- **New run from this** (on a completed or failed run) makes a new run with the same analyte and choices, on the recipe as it is now. If the recipe changed, the checks run again and you may need to acknowledge warnings again.
- Or set up the new run in cards **5. Analyte and checks** and **6. Start analysis** and select **Start analysis**.

The old run stays as it was, and the new one is linked to it. That way, every report you have already built still points to the run it was built from.

## Only completed runs can be published

Publishing copies chosen tables and figures from runs into `OUTPUTS`. Only **Completed** runs can be published: runs whose replay matched, that you confirmed, and whose files still match their recorded fingerprints. Any other run is listed as one that can't be published, with the reason.

Publishing never changes a run. Each copy in `OUTPUTS` is byte for byte the same as the run's file, with a small description file next to it that reports and decks use for footnotes. A published file is never replaced: if a file name in `OUTPUTS` already holds something different, the whole publication is refused and nothing is written.

## What is pinned

Nothing downstream refers to "the latest" run. Each step names exact runs and fingerprints:

| Record | What it pins |
|---|---|
| Imported source | the file's path, size and fingerprint |
| Run | the source fingerprint, the recipe, the frozen rows, your choices, the tool versions, and the fingerprint of every file in the run |
| Publication | the exact run IDs, and the fingerprint of every file it used |
| Report or deck | the one publication it is built from |
| Report or deck build | the exact runs and file fingerprints it used |

Every report build is kept in `report/builds` and every deck build in `deck/builds`. **Download DOCX** and **Download PPTX** work only while the file still matches its record. A report build is marked **Out of date** once the report text, its settings or the published files change after it was made; the build itself is never changed, so render again to get a current one.

## Unfinished work never looks finished

Cleaning, checks, replays, report renders and deck builds run as jobs. A job that fails or is cancelled keeps nothing half-made: its results never look complete. If publishing is interrupted, the files already copied are not part of any publication, and reports and decks never use them. If the app stops while a job is running, opening the study folder again marks that job as failed.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. A successful build shows the files were assembled as recorded, not that the analysis is right.

## Related pages

- [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }})
- [Publish tables and figures]({{ '/guides/publishing/' | relative_url }})
- [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }})
- [3. Run each analyte]({{ '/get-started/walkthrough-run/' | relative_url }}), in the walkthrough
