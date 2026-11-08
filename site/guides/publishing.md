---
title: Publish tables and figures
parent: Guides
nav_order: 6
description: Choose tables and figures from completed runs, publish them to the OUTPUTS folder, and check what was published.
permalink: /guides/publishing/
---

# Publish tables and figures

Copy chosen tables and figures from completed runs into the study folder's `OUTPUTS` folder, ready for a report or a deck.
{: .fs-5 .fw-300 }

Publishing is its own step on the **Publish** page. It never changes a run: it copies files out of runs, records exactly what it copied, and lets you check the copies later. Reports and decks are then built from one publication. See [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }}).

A **publication** is a fixed set of published files from exact runs, with a record of the runs and of each file's fingerprint (its SHA-256 hash, a code that changes if even one byte of the file changes).

## Which runs can be published

Only **Completed** runs can be published. A run is publishable when all three of these hold:

- its state is **Completed**: you selected **Confirm and complete** on the **Analysis** page;
- its replay passed: re-running its saved script gave the same results;
- its files still match their recorded fingerprints.

Any other run is listed in a table, **Runs that can't be published**, with the reason. For example: "Only completed runs can be published; this one is reviewing." or "The run's files no longer match their recorded hashes (Check files)."

One publication can combine runs of different analytes. Each run still holds only its own analyte's rows.

## Step 1. Choose tables and figures

1. Select **Publish** on the left.
2. Find the card **1. Choose tables and figures**. Each completed run has a box headed with its analyte in quotes, its run ID and when it was made, then "Replay passed".
3. Tick the items you want, from one run or several. The items that are ticked to begin with are a suggestion; untick anything you don't want.

<figure class="shot">
  <img src="{{ '/assets/screenshots/08-publish.png' | relative_url }}" width="1016" height="1100" loading="lazy"
       alt="The Choose tables and figures card with a box for the METAB run and one for the PARENT run. In each, the mean and individual concentration–time profiles, the individual parameter listing and the parameter summary are ticked; boxplots and summaries by group are not. Three items carry a 'shows subject IDs' label.">
  <figcaption>Each completed run offers the tables and figures opnca-r saved. Items that show subject IDs are labelled.</figcaption>
</figure>

The names are opnca-r's, and so is the flag that says whether a file shows subject IDs. For a run saved by opnca-r 0.3.1, a box typically holds:

| Offered | Ticked to begin with | Shows subject IDs |
|---|---|---|
| Summary of the parameters (table) | Yes | No |
| Parameters, one row per profile (table) | Yes | Yes |
| Concentration-time profiles, linear and log axis (figures) | Yes | Yes |
| Mean concentration-time profile, linear and log axis (figures) | Yes | No |
| Mean concentration-time profile (table) | No | No |
| Parameters, one row per profile and parameter (table) | No | Yes |
| Summary of the parameters by group, for example by dose or route (tables) | No | No |
| Boxplot of each parameter by profile (figures) | No | No |
| Any of these tables, **(formatted)** | No | As the table |

A **(formatted)** table is the same table as opnca-r laid it out, with units in the headers, a caption and footnotes. Reports and decks show it as it is. The plain table is the one the replay checked. Runs saved by an earlier opnca-r keep their earlier names, such as "Parameter summary" and "Individual parameter listing".

Items that show subject IDs carry the label **shows subject IDs**. Think about who will see the report or deck before you keep them.

{: .note }
> opnca-r calculates the mean profiles; this app never draws or recalculates them. You chose their time basis (actual or nominal time), statistic and error when you saved the run in opnca-r, and the published files' footnotes state those choices.

{: .fix }
> **"…: analysis.R doesn't recompute this table, so the replay didn't check it and it can't be published (opnca-r: …)."** — opnca-r saved a table, such as a bioequivalence table, that the run's script doesn't recalculate. A passed replay says nothing about it, so it isn't offered. Publish the run's other tables and figures.

{: .fix }
> **"Mean concentration–time (linear and log): This run was saved without the mean figures."** — the mean figures were unticked in opnca-r's **Save to run folder** box. A completed run can't change, so make a new run (**New run from this** on the **Analysis** page) and keep them ticked when you save it.

## Step 2. Check the selection

1. Optionally, type a **Label (optional)**, for example `PARENT and METAB`. The label helps you pick the publication later.
2. Select **Check**. Nothing is written yet.

The check lists **What would be published**, one line per file:

| Column | What it shows |
|---|---|
| **File** | The name the copy will have in `OUTPUTS`. It holds the analyte, the run ID and the item. |
| **Run** | The run the file comes from. |
| **Shows subject IDs** | **Yes** or **No**. |
| **Status** | **New copy**, **Already published (same bytes)**, or **Name taken by different content**. |

**Already published (same bytes)** is fine: the new publication reuses the existing copy. **Name taken by different content** means a file in `OUTPUTS` with that name no longer matches what the run holds, for example because someone edited it. The check also lists any problems with the description files under **Sidecar findings**, marked **Blocks** or **Warning**.

{: .expect }
> Every file shows **New copy** or **Already published (same bytes)**, and there are no **Sidecar findings**.

{: .fix }
> **"This selection can't be published as it is; see below."** — read the table. If a file shows **Name taken by different content**, don't overwrite or delete it by hand to make the name free: ask the person who supports opncafyr at your site, or make a new run, which gives new file names.

## Step 3. Publish

1. Select **Publish**.
2. Wait for the message.

If you change the ticks or the label after checking, **Publish** asks you to check again first.

{: .expect }
> The message names the new publication and counts its files and runs, for example "Recorded pub-20261107T220505Z-8f50: 12 file(s) from 2 run(s)." The publication appears in the card **2. Publications**.

{: .fix }
> **"The selection changed; check it again first."** — select **Check**, review the table, then select **Publish**.
>
> **"Choose at least one table or figure."** — tick at least one item in a run's box.
>
> **"No completed runs yet. Complete a run on the Analysis page first."** — finish a run first: open it in opnca-r, save it, wait for the replay, then select **Confirm and complete**.

## What publishing does

When you select **Publish**, the app:

1. copies each file into `OUTPUTS/tables` or `OUTPUTS/figures`, named `<analyte>_<run-id>_<item>.<ext>`. The copy is byte for byte the same as the run's file. The run's file is not moved or linked;
2. writes a small description file next to each copy, named `<name>_<ext>_metadata.json`. Reports and decks use it for footnotes: where the result came from (the run's `analysis.R`), the analyte and run, and abbreviations;
3. checks every copy and description file on disk;
4. writes the publication record last, in the study folder's hidden `.opncafyr/publications` folder.

A published file is never replaced. If any file name in `OUTPUTS` already holds something different, the whole publication is refused and nothing is written.

If publishing is interrupted, the files already copied belong to no publication. Reports and decks never use them, and **Check published files** lists them as `not-recorded`.

## What a publication pins

The publication record names exact things, never "the latest run":

- each run's ID and analyte;
- the fingerprint (SHA-256) of each run's sealed record and of its passed replay report. The run's record in turn holds the fingerprint of the source data file and of every file in the run;
- for every item: the fingerprint of the run's original file, of the published copy and of its description file, and whether it shows subject IDs;
- the app version, the publication's label and when it was made.

A report or deck is always built from one named publication, and every build records that publication and these fingerprints. Changing which runs a report uses means choosing a different publication.

## Step 4. Review your publications

The card **2. Publications** lists every publication in the study folder:

| Column | What it shows |
|---|---|
| **Publication** | Its ID, such as `pub-20261107T220505Z-8f50`. |
| **Label** | The label you typed, if any. |
| **Analytes** | The analytes of the runs it holds. |
| **Files** | How many files it holds. |
| **Created** | When it was made. |

You can't edit or delete a publication from the app. To publish a different selection, make a new publication; the files it shares with an earlier one are reused, not copied twice.

To look at the published files themselves, open the study folder's `OUTPUTS` folder in Finder or File Explorer. Open the files to read them, but don't change, rename or delete them.

## Step 5. Check published files

Run this check whenever you suspect a file in `OUTPUTS` was changed, and before you share a report or deck.

1. In the card **3. Published files**, select **Check published files**.
2. Read the message and any findings.

{: .expect }
> "12 file(s) checked; every sidecar matches." (with your own count). If `OUTPUTS` is empty, the message is "OUTPUTS/ holds no files yet."

Each finding names a check, a file, and a level: **Blocks** (a report or deck that uses the file can't be built) or **Warning**.

| Check | Level | What it means |
|---|---|---|
| `missing-artifact` | Blocks | The published file is missing, or is a link instead of a file. |
| `missing-sidecar` | Blocks | The file has no description file next to it. |
| `invalid-sidecar` | Blocks | The description file can't be read, isn't in the expected format, or is a link. |
| `path-mismatch` | Blocks | The description file describes a different file. |
| `file-type` | Blocks | The description file gives a different file type. |
| `hash-mismatch` | Blocks | The file's bytes no longer match the fingerprint in its description file: the file was changed. |
| `unknown-meta-type` | Blocks | The description file asks for a footnote type the report's standard footnotes don't define. |
| `unknown-abbreviation` | Blocks | The description file uses an abbreviation the standard footnotes don't define. |
| `orphan-sidecar` | Warning | A description file has no matching table or figure. |
| `not-recorded` | Warning | No publication lists this file, so reports and decks won't use it. |

{: .fix }
> **A `hash-mismatch` or `missing-artifact` finding** — someone changed, moved or deleted a published file. The run itself is untouched. Don't edit the file back by hand. Ask the person who supports opncafyr at your site; they may ask for a [support bundle]({{ '/guides/troubleshooting/' | relative_url }}#make-a-support-bundle).

The same description-file checks run again automatically before every report check, render, deck check and build.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

Next: [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }}).
