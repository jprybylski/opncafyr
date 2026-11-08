---
title: "1. Import and clean"
parent: Two-analyte walkthrough
grand_parent: Get started
nav_order: 1
description: "Walkthrough part 1: make a study folder, preview and import the practice CSV, and run it through a cleaning recipe."
permalink: /get-started/walkthrough-import/
---

# 1. Import and clean

Make a study folder for the practice file, look at the data, import it, and run it through a cleaning recipe for the first time.
{: .fs-5 .fw-300 }

This is part 1 of the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}). You need the practice file, <a href="{{ '/assets/example/two_analyte.csv' | relative_url }}" download>two_analyte.csv</a>, and the app running with every tool **Available** on **Home**.

In this part you apply the recipe with no steps, so the checks in part 2 can show you what needs fixing. You add the cleaning steps in part 2, once you have seen the problems.

## Step 1. Make the study folder

A study folder keeps one study's data, cleaning, runs, reports and decks together.

1. In Finder or File Explorer, make a new folder in your Documents folder called `TA-walkthrough`.
2. Inside it, make a folder called `source-data`.
3. Download the practice file, <a href="{{ '/assets/example/two_analyte.csv' | relative_url }}" download>two_analyte.csv</a>, and move it into `source-data`.
4. In the app, on **Home**, select **Choose study folder…** and pick `TA-walkthrough`.
5. The app says "This folder is not a workspace yet." Select **Create workspace here**.

{: .expect }
> The top bar now shows `TA-walkthrough`. The app has added one hidden folder, `.opncafyr`, for its own records. Nothing else in the folder changes.

{: .fix }
> - **The app offers Open workspace instead of Create workspace here** — the folder is already a study folder. Make a new, empty `TA-walkthrough` folder somewhere else, or use a different name.
> - **The folder is inside a hidden folder** (one whose name starts with a dot) — move it. Reports cannot be built inside a hidden folder.

## Step 2. Look at the data and import it

1. Select **Data** on the left. The **Datasets** table lists `source-data/two_analyte.csv` with 168 rows and 21 columns.
2. Select **Preview**. Previewing reads the file and never changes it.
3. Under **Candidate analytes**, the `PCTESTCD` values are `"PARENT"` (86 rows), `"METAB"` (81 rows) and `"METAB "` (1 row, with a trailing space). The app points out that `"METAB"` and `"METAB "` differ only by a space and stay separate analytes.
4. Look through **Columns** and **First 20 rows**. The columns you will use are `USUBJID`, `PCTESTCD`, `PCTEST`, `PCSPEC`, `AFRLT`, `NFRLT`, `CONC`, `PCSTRESU`, `LLOQ`, `BLQFL`, `MISSFL`, `EXCLFL`, `EXCLREAS`, `DOSE`, `DOSEU` and `ROUTE`.
5. Select **Import**.

<figure class="shot">
  <img src="{{ '/assets/screenshots/02-data-preview.png' | relative_url }}" width="1280" height="800" loading="lazy"
       alt="The Data page with two_analyte.csv listed at 168 rows and 21 columns, and its preview below: size, SHA-256 fingerprint, rows, columns, separator, encoding, and the candidate analytes PARENT (86 rows), METAB (81 rows) and METAB with a trailing space (1 row).">
  <figcaption>The preview shows the file's facts and its candidate analytes, in quotes so a stray space is visible.</figcaption>
</figure>

{: .expect }
> The **Imported** column shows **Recorded**. The app has recorded the file's size and fingerprint (SHA-256); it has not copied or changed the file.

{: .note }
> The **#** column in **First 20 rows** counts data rows. The line numbers used later (`opncafyr_line`) count the header as line 1, so row # 14 is line 15.

{: .fix }
> **The file is not listed** — the app lists CSV files that sit directly in the study folder or in its `source-data` folder, not in deeper folders. Check where you saved it, then select **Scan again**.

## Step 3. Make a recipe and apply it as it is

A recipe is the list of cleaning steps for one imported file, kept in order. You first run the data through with no cleaning, so the checks can show you what needs fixing.

1. Select **Analysis** on the left.
2. In **1. Source and recipe**, under **Imported file**, choose `source-data/two_analyte.csv`.
3. Select **New recipe**. **Recipe** now shows the new recipe with 0 steps.
4. In **2. Cleaning steps**, select **Apply to all rows**.

{: .expect }
> In **3. Result**, the summary says "All 168 rows: 168 kept, 0 rejected." Card **4. Column mapping** appears below.

{: .fix }
> - **Imported file shows only *Choose…*** — reload the page (Cmd+R or Ctrl+R).
> - **The page says "Import a file on the Data page first."** — go back to Step 2 and select **Import**.
> - **The summary shows rows rejected** — you are on a recipe that already has steps. Select **New recipe** and apply again.

**3. Result** also has **Rows at each step**, which is empty because the recipe has no steps yet, and **First rows after cleaning**. Once a step removes rows, a **Rejected rows** list appears too: the rejected-row log. You add the steps in part 2. The source file itself is never changed: cleaning works on a copy inside `.opncafyr`. See [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }}) for what each step can do.

**Next:** [2. Map and check]({{ '/get-started/walkthrough-map/' | relative_url }})
