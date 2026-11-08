---
title: "4. Publish, report and deck"
parent: Two-analyte walkthrough
grand_parent: Get started
nav_order: 4
description: "Walkthrough part 4: publish the tables and figures of both completed runs, then build a Word report and a PowerPoint deck from them."
permalink: /get-started/walkthrough-publish/
---

# 4. Publish, report and deck

Publish tables and figures from both completed runs, then build a Word report and a PowerPoint deck from them.
{: .fs-5 .fw-300 }

This is part 4 of the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}). Before you start, you should have finished [part 3]({{ '/get-started/walkthrough-run/' | relative_url }}): **7. Runs** on the **Analysis** page shows two **Completed** runs, one for `"PARENT"` and one for `"METAB"`.

Publishing copies chosen tables and figures from completed runs into the study folder's `OUTPUTS` folder. Reports and decks are then built from those copies. Publishing never changes a run, and only **Completed** runs can be published.

## Step 12. Publish both runs

1. Select **Publish** on the left.
2. In **1. Choose tables and figures**, there is a box for each run. Keep the items that are ticked to begin with: the parameter summary, the individual parameter listing, and the individual and mean concentration–time profiles.
3. Under **Label (optional)**, type `PARENT and METAB`.
4. Select **Check**. Each file shows as **New copy**. Nothing is written yet.
5. Select **Publish**.

<figure class="shot">
  <img src="{{ '/assets/screenshots/08-publish.png' | relative_url }}" width="1016" height="1100" loading="lazy"
       alt="The Choose tables and figures card with a box for the METAB run and one for the PARENT run, each marked Replay passed. In each, the mean and individual concentration–time profiles (linear and log), the individual parameter listing and the parameter summary are ticked; the individual items are marked 'shows subject IDs'. Boxplots and other tables are unticked.">
  <figcaption>Each completed run has its own box. Items that show subject IDs are marked.</figcaption>
</figure>

{: .expect }
> The message names the new publication and says "12 file(s) from 2 run(s)". The publication appears in **2. Publications**, with both analytes.

{: .fix }
> - **A run has no box** — only **Completed** runs can be published. Any other run is listed as one that can't be published, with the reason. Go back to [part 3]({{ '/get-started/walkthrough-run/' | relative_url }}) and complete it.
> - **"The selection changed; check it again first."** — you changed the ticks or the label after **Check**. Select **Check** again, then **Publish**.

The publication records the exact runs it used and the fingerprint (SHA-256) of every file, never "the latest" run. Reports and decks always name a publication. See [Workspace and run provenance]({{ '/guides/provenance/' | relative_url }}).

## Step 13. Build the Word report

In **4. Report (DOCX)**:

1. Under **Publication**, choose the publication labelled `PARENT and METAB`.
2. Select **Set up report** and wait for it to finish. The button then changes to **Use this publication**, and **Edit report** appears.
3. Under **Status**, choose **Draft**.
4. Select **Check**.
5. Select **Render** and wait. This can take a few minutes.
6. Select **Download DOCX** and open the file in Word.

<figure class="shot">
  <img src="{{ '/assets/screenshots/09-report.png' | relative_url }}" width="1016" height="429" loading="lazy"
       alt="The Report (DOCX) card after setup: it says which publication report/ uses, with the Publication drop-down, the Use this publication button, the Status drop-down and the Check, Render and Edit report buttons, and the message 'Setting up the report finished.'">
  <figcaption>After setup, the card names the publication the report uses.</figcaption>
</figure>

{: .expect }
> - After **Check**, the card says "The last check found nothing to fix."
> - After **Render**, the build appears in the table below, with when it was made, the publication, the runs and the settings it used.

{: .fix }
> - **"Choose draft or final."** — pick **Draft** under **Status**, then select **Render** again.
> - **The check lists problems** — each is marked **Blocks** or **Warning**. The check looks for anything that would block or weaken the build, such as a missing or broken description file. Select **Check published files** in **3. Published files** to check everything in `OUTPUTS`.
> - **The render fails** — see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}). A failed render keeps nothing, so a half-made report never looks finished.

When you open the report, Word may ask whether to update fields. That is expected.

## Step 14. Build the PowerPoint deck

{: .warning }
> **Known issue:** the deck tool in this version can't build a slide from a table that has an empty cell, and this exercise's tables have some (subject `TA-008` has no values, and some METAB profiles have no λz). The build would stop with "IndexError: tuple index out of range". Until a fixed version ships, give the deck its own publication with the figures only. The Word report has no such limit.

First, publish the figures on their own:

1. On **Publish**, in **1. Choose tables and figures**, untick the **Parameter summary** and the **Individual parameter listing** in both run boxes. Keep the figures ticked.
2. Under **Label (optional)**, type `Figures for the deck`, select **Check**, then **Publish**.

{: .expect }
> The figures were already published in Step 12, so **Check** shows them as **Already published (same bytes)**: the new publication reuses the same files.

Then, in **5. Deck (PPTX)**:

1. Under **Publication**, choose `Figures for the deck`.
2. Select **Set up deck** and wait for it to finish. The button then changes to **Use this publication**, and **Edit fragments** appears. If you already set up a deck from another publication, the button reads **Use this publication**: select it and choose **Replace presentation.yaml**.
3. If you like, select **Open editor**. The deckifyr editor opens under **Apps** on the left. Double-click the title text on the first slide, type a new title, click outside the text, and select **Save** at the top of the editor. Then select **Publish** on the left and select **Close editor**.
4. Select **Check**.
5. Select **Build** and wait.
6. Select **Download PPTX** and open the file in PowerPoint.

<figure class="shot">
  <img src="{{ '/assets/screenshots/10-deck.png' | relative_url }}" width="1016" height="395" loading="lazy"
       alt="The Deck (PPTX) card after setup: it says which publication deck/ uses, with the Publication drop-down, Use this publication, and the Open editor, Check, 'with slide previews' (unticked and greyed out), Build and Edit fragments controls, and the message 'Setting up the deck finished.'">
  <figcaption>After setup, the card names the publication the deck uses. Slide previews need LibreOffice, which is optional.</figcaption>
</figure>

{: .expect }
> - After **Check**, the card says "The last check found nothing to fix."
> - After **Build**, the build appears in the table, with when it was made, the publication and the runs it used.

{: .fix }
> - **The build stops with "IndexError: tuple index out of range"** — the deck's publication includes a table. Close the deck editor if it is open, then choose `Figures for the deck` under **Publication** and select **Use this publication**; if the app asks, choose **Replace presentation.yaml**. Then **Check** and **Build** again.
> - **Check or Build waits** — the deck editor or the fragment editor has unsaved changes. Save them, or close the deck editor.
> - **The editor's own Build button** makes a draft for you to look at. The app does not record it; only **Build** on the **Publish** page makes a recorded build.

## Review what you made

Your `TA-walkthrough` folder now holds:

- `source-data/two_analyte.csv`, unchanged;
- two completed, sealed runs, one per analyte, each with its own replay check (in the hidden `.opncafyr` folder);
- `OUTPUTS`, with the published tables and figures;
- `report` and `deck`, with your builds.

Every build is kept in `report/builds` or `deck/builds`, with a record of the exact runs and file fingerprints it used. **Download DOCX** and **Download PPTX** work only while the file still matches that record. A report build is marked **Out of date** once the report text, its settings or the published files change after it was made; render again to get a current one.

Open the DOCX and the PPTX and read them. Check that each table and figure is there, that the captions and footnotes name the right analyte and run, and that the numbers look right to you.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

{: .warning }
> The report and deck built, which shows the files were assembled as recorded. It does not show that the analysis is right. That judgement is yours.

## Where to go next

- [Workspace and run provenance]({{ '/guides/provenance/' | relative_url }}): what the study folder records, and why any change makes a new run.
- [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }}): every cleaning step, and when the app asks you to choose.
- [Build reports and decks]({{ '/guides/reports-and-decks/' | relative_url }}): editing the report text and the slides.

**Back to:** [Two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }})
