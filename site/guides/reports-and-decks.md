---
title: Build reports and decks
parent: Guides
nav_order: 7
description: Set up, edit, check and build a Word report (DOCX) or a PowerPoint deck (PPTX) from one publication, and find the files it makes.
permalink: /guides/reports-and-decks/
---

# Build reports and decks

Turn one publication into a Word report or a PowerPoint deck, check it, build it and download the file.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

Both are built on the **Publish** page: the report in the card **4. Report (DOCX)**, the deck in the card **5. Deck (PPTX)**. Each is a project in your study folder (`report` and `deck`) that uses one publication at a time.

**A build that finishes without errors shows that the files were assembled as recorded. It does not show that the analysis is right.** That judgement stays with you and your reviewers.

## Before you start

- You need at least one publication. See [Publish tables and figures]({{ '/guides/publishing/' | relative_url }}).
- The study folder must not be inside a hidden folder (one whose name starts with a dot, such as `~/.studies`). Reports can't be built there.
- Reports and decks are built with the app's own R, Quarto and tools, and work offline.

## Word report (DOCX)

The report is a quartifyr project in the study folder's `report` folder, made from a template that comes with the app. quartifyr and reportifyr (with Quarto) build the Word file.

<figure class="shot">
  <img src="{{ '/assets/screenshots/09-report.png' | relative_url }}" width="1016" height="429" loading="lazy"
       alt="The Report (DOCX) card. It says the report uses one publication, shows that publication chosen under Publication with a Use this publication button, a Status list reading Draft or final, and the Check, Render and Edit report buttons.">
  <figcaption>The report card after the report has been set up for a publication.</figcaption>
</figure>

### Step 1. Set up the report

1. Under **Publication**, choose a publication. Each shows its ID and its analytes.
2. Select **Set up report**. This runs as a job.

{: .expect }
> "Setting up the report finished." The card says which publication `report/` uses, the button now reads **Use this publication**, and **Edit report** appears.

The new report has one captioned table or figure for each published file, with footnotes taken from the files' description files.

{: .fix }
> **"Choose a publication first."** — pick one under **Publication**. If the list is empty, publish first.

### Step 2. Edit the report text (optional)

1. Select **Edit report**. The report text, `report.qmd`, opens in the app's project-file editor.
2. Choose how to edit:
   - **Source** shows and edits everything.
   - **Visual** edits only plain headings, paragraphs and lists. The parts that place tables, figures and footnotes are locked, so you can't break them by accident.
   - **Preview** is an approximate view. It runs nothing, and the real report may look different.
3. Select **Save** (or press Ctrl+S, or Cmd+S on a Mac) to keep your changes, or **Discard changes**.
4. Select **Publish** on the left to go back to the report card.

You can also open the editor from the quartifyr tile on **Home**, with **Edit project files**.

If the file changed on disk while you were editing, nothing is overwritten. The editor shows "The file changed on disk" with both versions. Choose **Keep mine (replace the disk version)** or **Use the disk version (discard mine)**.

{: .fix }
> **"The report editor has unsaved changes. Save or discard them first."** — **Check** and **Render** wait while the editor holds unsaved text. Go back to the editor and select **Save** or **Discard changes**.

### Step 3. Choose draft or final

Under **Status**, choose **Draft** or **Final**. This is a saved setting of the report, and every build records it.

### Step 4. Check the report

1. Select **Check**. This runs as a job.
2. Read the result under the buttons.

{: .expect }
> "The last check found nothing to fix."

Otherwise the card lists **Report check findings**, each marked **Blocks** or **Warning**, with the check, the file and what is wrong. See [What the checks mean](#what-the-checks-mean).

{: .fix }
> **A formatted table (`.rds`) is listed as Blocks** — the report tools can't place a formatted table that carries its own caption: a report that included one left that table's place unfilled. Use the CSV version of the table in the report, or use the formatted one in a deck. **Render** stops for the same reason.
>
> **A warning about `use_artifact_size`** — an older report project still sizes figures by their pixels, which can run past the page. Set `use_artifact_size: false` in `report/config.yaml` and render again. New reports already have it.

### Step 5. Render and download

1. Select **Render**. This runs as a job and may take a few minutes.
2. When it finishes, the build appears in the table below, with when it was made, the publication, the runs and the settings it used.
3. Select **Download DOCX** to save the Word file.

{: .expect }
> "The render finished." A new row in the builds table, with a **Download DOCX** link.

When you open the file, Word may ask whether to update fields. That is expected.

A build is marked **Out of date** once the report text, its settings or the published files change after it was made. Hover over the label to see what changed. The build itself is never changed; render again to get a current one.

{: .fix }
> **"Choose draft or final."** — pick one under **Status**, then select **Render** again.
>
> **The render fails** — read the message on the card or on the **Jobs** page, select **Check** and fix what it lists, and look at your last edit in **Source** mode. A failed render keeps nothing, so you can render again as often as you need. More in [A report or deck fails to build]({{ '/guides/troubleshooting/' | relative_url }}#a-report-or-deck-fails-to-build).

### Use a different publication for the report

1. Under **Publication**, choose the other publication.
2. Select **Use this publication**.
3. If you have edited the report text, the app asks first. **Replace report.qmd** starts again from the template; **Keep my report.qmd** keeps your text.

## PowerPoint deck (PPTX)

The deck is a deckifyr project in the study folder's `deck` folder. You edit its slides in the deck editor, which opens inside the app.

<figure class="shot">
  <img src="{{ '/assets/screenshots/10-deck.png' | relative_url }}" width="1016" height="395" loading="lazy"
       alt="The Deck (PPTX) card. It says the deck uses one publication, shows that publication chosen with a Use this publication button, and the Open editor, Check, with slide previews (unticked and greyed out), Build and Edit fragments controls.">
  <figcaption>The deck card after the deck has been set up. Without LibreOffice, "with slide previews" can't be ticked.</figcaption>
</figure>

A table with an empty cell builds: the cell stays empty on the slide. opnca-r's tables have empty cells whenever a parameter can't be calculated, for example a profile without λz (the terminal elimination rate constant). A formatted table (`.rds`) goes on its slide as a picture.

### Step 1. Set up the deck

1. Under **Publication**, choose a publication.
2. Select **Set up deck**. This runs as a job.

{: .expect }
> "Setting up the deck finished." The card says which publication `deck/` uses, the button now reads **Use this publication**, and **Edit fragments** appears.

The new deck is an "NCA results" deck: a title slide, then one slide for each published table or figure. Slide footers come from the files' description files.

### Step 2. Edit the slides (optional)

1. Select **Open editor**. The deck editor opens inside the app, and is listed under **Apps** on the left.
2. Change the slides there, and select **Save** in the editor.
3. Select **Publish** on the left, then **Close editor**. Closing saves any edits you didn't save.

Quitting the app, or the app closing itself after a time with no open page, also saves the editor's edits first.

{: .note }
> The deck editor has its own **Build** button. Builds made there are drafts, kept in `deck/build`, and the app does not record them. Only **Build** on the **Publish** page makes a recorded build.

### Step 3. Add a Quarto fragment (optional)

A fragment is a short piece of text, a table or a figure written in Quarto that a slide can show.

1. Select **Edit fragments**. The deck's fragments open in the app's project-file editor.
2. To make a new one, type a name under **New quarto fragment name** and select **Create**. Names use letters, digits, `-` and `_`. The file is saved as `deck/fragments/<name>.qmd`.
3. Write the fragment and select **Save**.
4. In the deck editor, add a Quarto element to a slide and choose your fragment.

A fragment must not contain a slide break (`---`) or more than one top-level heading. The editor saves it but warns you ("Saved, with notes: …"), and the deck check will fail until you fix it.

{: .warning }
> Fragments can run R code when the deck is built. They always run with the app's own R and Quarto, but use only fragments you wrote or trust.

### Step 4. Check the deck

1. Select **Check**. This runs as a job.
2. Read the result.

{: .expect }
> "The last check found nothing to fix."

Otherwise the card lists **Deck check findings**, each marked **Blocks** or **Warning**. See [What the checks mean](#what-the-checks-mean).

{: .fix }
> **"The fragment editor has unsaved changes. Save or discard them first."** — save or discard in the fragment editor. **Check** and **Build** also wait while the deck editor has unsaved edits: save there, or select **Close editor**.

### Step 5. Build and download

1. Tick **with slide previews** if you want a picture of each slide. This needs LibreOffice, which is optional and not installed by default. Without it, the box can't be ticked.
2. Select **Build**. This runs as a job.
3. When it finishes, the build appears in the table, with when it was made, the publication and the runs it used.
4. Select **Download PPTX** to save the PowerPoint file.

{: .expect }
> "The build finished." A new row in the builds table, with a **Download PPTX** link.

{: .fix }
> **"unexpected error in the deck tool"** — see [Troubleshooting]({{ '/guides/troubleshooting/' | relative_url }}); make a support bundle.
>
> **"with slide previews" is greyed out ("LibreOffice is not installed.")** — not a fault. Decks build without previews. Whoever supports your install can add LibreOffice with the installer's `--with-libreoffice` option.

### Use a different publication for the deck

1. If the deck editor is open, select **Close editor** first.
2. Under **Publication**, choose the other publication and select **Use this publication**.
3. If you have edited the deck, the app asks first. **Replace presentation.yaml** starts again; **Keep my presentation.yaml** keeps your version.

## What the checks mean

Every check and build works on a temporary copy of the project that holds exactly the publication's files. Each file is checked against the fingerprint recorded in the publication before it is used, so an edited file in `OUTPUTS` can't slip into a build.

| Step | What it checks | What counts as success |
|---|---|---|
| Report **Check** | 1. The description file of every published file (the same checks as [Check published files]({{ '/guides/publishing/' | relative_url }}#step-5-check-published-files)). 2. quartifyr's own check of the report project, which lists what would block or weaken the render. | No **Blocks** findings. |
| Report **Render** | Runs quartifyr's strict, offline render on the copy, after hashing every input. | quartifyr reports success, every placeholder is filled and no metadata is missing; the DOCX it lists is re-hashed and kept. |
| Deck **Check** | 1. The description files, as above. 2. deckifyr's strict check of the deck, including your fragments. A deckifyr error fails the check; its warnings are listed. | No **Blocks** findings. |
| Deck **Build** | Builds the copy with deckifyr's strict mode. | deckifyr reports success, its build record exists, and the PPTX's fingerprint matches that record. |
| **Download** | Re-checks the file against its build record. | The file still matches. If it was changed, the download is refused ("The DOCX no longer matches its build manifest."). |

**Blocks** means the render or build can't go ahead until you fix it. **Warning** means it can, but read the finding first.

A render or build that fails, runs past its time limit or is cancelled keeps nothing, so a half-made file never looks finished.

## Where the files land

| What | Where in the study folder |
|---|---|
| Report project (your text, settings, template) | `report/`, with `report.qmd` as the main text |
| Recorded report builds | `report/builds/<build-id>/`: `report-draft.docx` or `report-final.docx`, plus `build.json` and `render-result.json` |
| Deck project | `deck/`, with `presentation.yaml` (the slides) and any fragments you made in `fragments/` |
| Deck editor drafts (not recorded) | `deck/build/` |
| Recorded deck builds | `deck/builds/<build-id>/`: the PPTX, `deck-manifest.json`, `build-result.json`, `build.json`, and `previews/` when built with slide previews |

Each build's `build.json` records the publication, the exact run IDs, the fingerprint of every published file and input, the settings and the tool versions. Downloads are named after the build, for example `report-<build-id>-draft.docx` or `deck-<build-id>.pptx`.

Don't edit or delete files under `builds/`: a changed file can no longer be downloaded from the app. For the full map, see [Files and folders]({{ '/reference/files/' | relative_url }}).
