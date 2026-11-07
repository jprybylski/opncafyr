# Word reports

The **4. Report (DOCX)** card on the **Publish** page builds a Word report from one publication. The report is a project in the study folder's `report` folder, made from a template that comes with the app. The app uses quartifyr and reportifyr to build it.

A report that builds without errors shows that the files were assembled as recorded. It does not show that the analysis is right.

![The Report (DOCX) card after the report has been set up for a publication](images/09-report.png)

## Set up the report

1. Under **Publication**, choose a publication. Each shows its ID, its label and its analytes.
2. Select **Set up report**. This runs as a job. When it finishes, the card says which publication `report` uses.

The new report has one captioned table or figure for each published file, with footnotes taken from the files' descriptions.

To use a different publication later, choose it and select **Use this publication**. If you have edited the report text, the app asks first: **Replace report.qmd** starts again from the template, **Keep my report.qmd** keeps your text.

## Check the report

Select **Check**. The check looks for anything that would block or weaken the build, such as a missing or broken description file. It works on a copy that holds exactly the publication's files, each checked against its fingerprint. When nothing is wrong, the card says "The last check found nothing to fix." Otherwise it lists each problem with **Blocks** or **Warning**.

## Edit the report

Select **Edit report**. The report text opens in the app's project-file editor:

- **Source** shows and edits everything.
- **Visual** edits only plain headings, paragraphs and lists. The parts that place tables, figures and footnotes are locked, so you cannot break them by accident.
- **Preview** is an approximate view. The real report may look different.

Select **Save** to keep your changes, or **Discard changes**. If the file changed on disk while you were editing, nothing is overwritten: the app shows both versions and you choose which one to keep.

While the editor has unsaved changes, **Check** and **Render** wait. The card says "The report editor has unsaved changes. Save or discard them first."

## Draft or final

Choose **Draft** or **Final** under **Status**. This is a setting of the report, and every build records it. **Render** asks you to choose before it starts.

## Render and download

1. Select **Render**. This runs as a job and may take a few minutes. It works offline.
2. When it finishes, the build appears in the table below, with when it was made, the publication, the runs and the settings it used.
3. Select **Download DOCX** to save the Word file.

Every build is kept in `report/builds` with a record of the exact runs and file fingerprints it used. Download works only while the file still matches that record.

A build is marked **Out of date** once the report text, its settings or the published files change after it was made. The build itself is never changed; render again to get a current one.

A render that fails, runs out of time or is cancelled keeps nothing, so a half-made report never looks finished. If a render fails, see [A report render fails](troubleshooting.md#a-report-render-fails).

When you open the report, Word may ask whether to update fields. That is expected.
