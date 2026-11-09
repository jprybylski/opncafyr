# Two-analyte walkthrough

This exercise takes you from an empty folder to a Word report and a PowerPoint deck. It uses a small practice file, [two_analyte.csv](example/two_analyte.csv), that comes with this guide.

The file is made up. It has 10 subjects (`TA-001` to `TA-010`), each with a parent drug (`PARENT`) and a metabolite (`METAB`) in the column `PCTESTCD`. Problems have been planted in it on purpose, so you can see how the app finds them and how you fix them.

The choices in this exercise, such as which rows to remove and the BLQ rules, are there to practise the app. They are not advice for a real study.

Set aside about an hour. You can stop at any point and come back: everything you do is saved in the study folder.

## Before you start

1. Install opncafyr: see [Installing and updating](install.md).
2. Start the app. On **Home**, the **Tools** card should show opnca-r, quartifyr + reportifyr and deckifyr as **Available**. If one shows **Unavailable**, see [A tool shows Unavailable](troubleshooting.md#a-tool-shows-unavailable).

## Step 1. Make the study folder

1. In Finder or File Explorer, make a new folder in your Documents folder called `TA-walkthrough`.
2. Inside it, make a folder called `source-data`.
3. Download the practice file, [two_analyte.csv](example/two_analyte.csv), from this page of the guide in the app, and move it into `source-data`.
4. In the app, on **Home**, select **Choose study folder…** and pick `TA-walkthrough`.
5. The app says "This folder is not a workspace yet." Select **Create workspace here**.
6. The top bar now shows `TA-walkthrough`.

## Step 2. Look at the data and import it

1. Select **Data** on the left. The **Datasets** table lists `source-data/two_analyte.csv` with 168 rows and 21 columns.
2. Select **Preview**.
3. Under **Candidate analytes**, the `PCTESTCD` values are `"PARENT"` (86 rows), `"METAB"` (81 rows) and `"METAB "` (1 row, with a trailing space). The app points out that `"METAB"` and `"METAB "` differ only by a space and stay separate analytes.
4. Look through **Columns** and **First 20 rows**. The columns you will use are `USUBJID`, `PCTESTCD`, `PCTEST`, `PCSPEC`, `AFRLT`, `NFRLT`, `CONC`, `PCSTRESU`, `LLOQ`, `BLQFL`, `MISSFL`, `EXCLFL`, `EXCLREAS`, `DOSE`, `DOSEU` and `ROUTE`.
   The **#** column in **First 20 rows** counts data rows. The line numbers used later (`opncafyr_line`) count the header as line 1, so row # 14 is line 15.
5. Select **Import**. The **Imported** column shows **Recorded**.

## Step 3. Make a recipe and apply it as it is

You first run the data through with no cleaning, so the checks can show you what needs fixing.

1. Select **Analysis** on the left.
2. In **1. Source and recipe**, under **Imported file**, choose `source-data/two_analyte.csv`. If the list shows only *Choose…*, reload the page (Cmd+R or Ctrl+R).
3. Select **New recipe**. **Recipe** now shows the new recipe with 0 steps.
4. In **2. Cleaning steps**, select **Apply to all rows**.
5. In **3. Result**, the summary says "All 168 rows: 168 kept, 0 rejected."

## Step 4. Map the columns

Card **4. Column mapping** now appears. For each role, choose the column below. Where the app shows a suggestion that matches, you can select **Use**. Leave every role not listed here as it is.

| Role | Column |
|---|---|
| Subject (profile) ID | `USUBJID` |
| Analyte code | `PCTESTCD` |
| Analyte label | `PCTEST` |
| Specimen | `PCSPEC` |
| Actual time | `AFRLT` |
| Nominal time | `NFRLT` |
| Concentration | `CONC` |
| Concentration unit | `PCSTRESU` |
| LLOQ | `LLOQ` |
| BLQ flag | `BLQFL` |
| Missing flag | `MISSFL` |
| Exclusion flag | `EXCLFL` |
| Exclusion reason | `EXCLREAS` |
| Dose | `DOSE` |
| Dose unit | `DOSEU` |
| Route | `ROUTE` |

Do not map **Secondary subject ID**. In this file `SUBJID` has a typo on one row, which would add a warning.

Then set the units under **Units**:

1. **Unit of time**: choose **One unit for every row** and type `h`. The file has no time unit column, and `AFRLT` is in hours.
2. **Unit of concentration**: choose **A unit column**, then `PCSTRESU`. Its only value is `"ng/mL"`.
3. **Unit of dose**: choose **A unit column**, then `DOSEU`. Its only value is `"mg"`; its blank cells are skipped.

These become the runs' unit labels: opnca-r will label the results in `ng/mL`, `h` and `mg`. The labels never change a number.

Then say what the flag values mean:

1. Under **BLQ flag**, set `"Y"` to **TRUE**, `""` (blank) to **FALSE** and `"N"` to **FALSE**. The single `"N"` (line 15) is a planted surprise: the rest of the file uses only `Y` and blank. Here you decide that `N` means "not BLQ".
2. Still under **BLQ flag**, tick **Also BLQ: concentration text starting with “<”**. Subject `TA-003`'s metabolite results from 2 h are written as `<LLOQ` with no flag. With this ticked, they count as BLQ.
3. Under **Missing flag**, set `"Y"` to **TRUE** and `""` to **FALSE**.
4. Under **Exclusion flag**, set `"Y"` to **TRUE** and `""` to **FALSE**.
5. Select **Save mapping**. The card says "Mapping saved."

## Step 5. Run the checks to find the problems

1. In **5. Analyte and checks**, under **Analyte (exact value)**, choose `"PARENT"`.
2. Under **Specimen (PCSPEC)**, choose `"PLASMA"`. The 4 `URINE` rows of `PARENT` are left out; they are counted, not rejected.
3. Select **Run checks**. The list shows several **Blocking** findings.
4. Choose `"METAB"` under **Analyte (exact value)**, then `"PLASMA"`, and select **Run checks** again. The metabolite has blocking findings of its own.

Each finding gives the source line numbers. These are the planted problems and how this exercise fixes them:

| Lines | Analyte | Finding | Why it blocks | Fix in this exercise |
|---|---|---|---|---|
| 16 | PARENT | conc unclassified blank | A blank concentration that is neither BLQ nor missing. | Remove the row. |
| 24 | PARENT | exclusion no reason | The row is excluded (`EXCLFL` is `Y`) but `EXCLREAS` is blank. | Exclude it again, with a reason. |
| 35 | METAB | route missing | The route is blank. The app never fills one in. | Remove the row. |
| 66 | PARENT | conc negative | The concentration is -0.020. | Remove the row. |
| 76, 77 | PARENT | duplicate key | Line 77 is an exact copy of line 76. | Remove line 77. |
| 82 | METAB | time not numeric | Actual time is written `~8`. | Remove the row. |
| 96, 97 | METAB | duplicate key | Same subject and time, different concentrations. | Remove line 97. |
| 114 | PARENT | conc not numeric, route unrecognised, route varies | An extra sample with `12,4` (decimal comma) and the route `EXTRAVASULAR` (misspelt). | Remove the row. |
| 120 | METAB | blq and missing | The row is flagged both BLQ and missing. | Remove the row. |
| 121 to 136 | both | profile no dose | Subject `TA-008` has no dose record. | Exclude the whole profile, with a reason. |
| 150 | PARENT | dose varies | `TA-009`'s 12 h sample says 200; the rest of its profile says 100. | Remove the row. |

You will also see notes headed **opnca format check**, for example *Role 'time' … expected 'ACTUAL_TIME'*. They come from opnca-r's own check of column names. The app passes your mapping to opnca-r, so these notes need no action, and they never block.

For the duplicates on lines 96 and 97, the two concentrations differ. In a real study you would find out which one is right. The app never picks one for you. Here you keep line 96.

## Step 6. Add the cleaning steps

Add these 11 steps in card **2. Cleaning steps**, in this order. Each one picks rows by line number.

| # | Step | Rows | Reason |
|---|---|---|---|
| 1 | Remove rows to the rejected-row log | `opncafyr_line` **is** `16` | blank concentration, neither BLQ nor missing |
| 2 | Remove rows to the rejected-row log | `opncafyr_line` **is** `35` | route not recorded |
| 3 | Remove rows to the rejected-row log | `opncafyr_line` **is** `66` | negative concentration |
| 4 | Remove rows to the rejected-row log | `opncafyr_line` **is** `82` | actual time not recorded as a number |
| 5 | Remove rows to the rejected-row log | `opncafyr_line` **is** `114` | invalid extra sample (decimal comma, misspelt route) |
| 6 | Remove rows to the rejected-row log | `opncafyr_line` **is** `120` | flagged both BLQ and missing |
| 7 | Remove rows to the rejected-row log | `opncafyr_line` **is** `150` | dose differs from the rest of the profile |
| 8 | Remove rows to the rejected-row log | `opncafyr_line` **is** `77` | duplicate of line 76 |
| 9 | Remove rows to the rejected-row log | `opncafyr_line` **is** `97` | duplicate of line 96 |
| 10 | Exclude rows (they stay, marked) | `opncafyr_line` **is** `24` | excluded at site; reason added here |
| 11 | Exclude rows (they stay, marked) | `opncafyr_line` **is one of (one per line)**: lines 121 to 136 | no dose record for this subject |

For each step:

1. In **Add a step…**, choose the step.
2. Under **Rows**, choose `opncafyr_line` as the column and the condition shown, then type the line number.
3. Type the **Reason** exactly as shown, or in your own words.
4. Select **Add step**.

For step 11, paste these 16 line numbers into the box, one per line:

```
121
122
123
124
125
126
127
128
129
130
131
132
133
134
135
136
```

Why remove some rows and exclude others? Line 24 and subject `TA-008` are real samples that should not count, so they stay in the data, marked as excluded, with a reason opnca-r can see. The removed rows are not usable as data, so they go to the rejected-row log.

**Fix a step with Edit.** If a step has a wrong value, such as the wrong line number, you don't need to remove it and add it again:

1. Select **Edit** on the step. Its form opens with its values and note filled in, under "Editing step N". The other steps' buttons wait until you finish.
2. Change the value, then select **Save step**. The step stays in its place. **Cancel** changes nothing. If the recipe can't take the change (for example a later step needs a column this step made), the form stays open with the reason and what you typed.
3. The recipe has changed, so select **Apply to all rows** again, then run the checks again.

To practise: open step 7, change the line number from `150` to `105`, save, apply and run the checks. The finding for the dose that differs returns, now next to a step that removes the wrong row. Edit step 7 back to `150`.

Then run the recipe:

1. Select **Apply to all rows**.
2. In **3. Result**, the summary says "All 168 rows: 159 kept, 9 rejected."
3. **Rows at each step** shows one row rejected by each of steps 1 to 9, and none by steps 10 and 11.
4. **Rejected rows (9)** lists each removed line with its step and reason.
5. Check that card **4. Column mapping** still shows your choices. The mapping is kept with the recipe. If anything is listed under **Still to do**, fix it and select **Save mapping**.

## Step 7. Check PARENT and acknowledge its warnings

1. In **5. Analyte and checks**, choose `"PARENT"` (now 81 rows) and the specimen `"PLASMA"` (77 rows).
2. Select **Run checks**. There should be no blocking findings now, and three warnings. A copy of the blocking findings and warnings, with their hints, now also appears under **2. Cleaning steps**, so you can fix the recipe without scrolling back here.
3. Read each warning, then tick **I have reviewed this and want to go on**:
   - **Warning: lloq varies**: subject `TA-002`'s rows carry an LLOQ of 0.10; all other `PARENT` plasma rows carry 0.05.
   - **Warning: time negative**: subject `TA-003`'s predose sample was taken 15 minutes before the dose (line 38).
   - **Warning: blq above lloq**: subject `TA-006`'s 24 h sample is flagged BLQ but its concentration, 0.080, is above the LLOQ (line 102).
4. The notes need no action: the flag counts per profile, the subject accountability and an **opnca format check** note.

## Step 8. Start the PARENT run

In **6. Start analysis**:

1. **Label for this analyte (shown in reports)** shows `Parent drug`. Keep it.
2. **BLQ before the dose**: choose **Set to zero**.
3. **BLQ after the dose**: choose **Exclude the sample**.
4. **BLQ at the end of the profile**: choose **Exclude the sample**.
5. **BLQ method**: choose **Conventional (rules above)**.
6. Select **Start analysis**. The card says the run was created, and the run appears in **7. Runs** as **Preparing**.

## Step 9. Analyse PARENT in opnca-r and save

1. In **7. Runs**, select **Open in opnca-r** on the new run. opnca-r opens inside the app.
2. Look at **Data** and **Profiles** if you like. The data is the 77 `PARENT` plasma rows, already mapped. Under **Unit labels**, **Concentration**, **Time** and **Dose** show `ng/mL`, `h` and `mg`, marked "set by the launcher".
3. Go to **Analyze**. Select the **Standard NCA** preset, then **Run analysis**.
4. Look at **Results**. Subject `TA-008` has no parameter values, because its whole profile is excluded.
5. Go to **Export**. In the **Save to run folder** box, keep **Mean concentration-time figures (linear and log) and their table** ticked.
6. Set **Time basis** to **Nominal time**. Keep **Statistic** as **Arithmetic mean** and **Error** as **SD**.
7. Select **Save to run folder**.

## Step 10. Replay and complete PARENT

1. Select **Analysis** on the left and scroll to **7. Runs**.
2. Wait for the run to reach **Reviewing**. You may first see "Run … was saved by opnca-r and is ready for review."; the replay then starts by itself.
3. Wait for "Run … replayed and matches its saved results. Confirm it to complete the run."
4. Select **Confirm and complete**. The run is now **Completed**.

## Step 11. Run METAB the same way

1. In **5. Analyte and checks**, choose `"METAB"` (77 rows) and `"PLASMA"` (77 rows). Do not choose `"METAB "` with the space: that is the 1-row variant on line 147, and it stays out of this run.
2. Select **Run checks**. There should be no blocking findings and two warnings. Tick **I have reviewed this and want to go on** for each:
   - **Warning: time negative**: subject `TA-003`'s predose sample (line 39).
   - **Warning: conc zero not blq**: subject `TA-005`'s 24 h concentration is 0 with no BLQ flag (line 86).
3. A note says subject `TA-004`'s times are not in order (lines 67 and 69): its 24 h sample comes before its 12 h sample in the file. The order does not change the results. Another note says subject `TA-003` has fewer than 3 measurable samples after the dose. That is because of the `<LLOQ` results you counted as BLQ.
4. In **6. Start analysis**, the label shows `Metabolite M1`. Check that the BLQ choices are still **Set to zero**, **Exclude the sample**, **Exclude the sample** and **Conventional (rules above)**.
5. Select **Start analysis**.
6. Open the new run in opnca-r, analyse and save it as in Step 9, then replay and complete it as in Step 10.

**7. Runs** now shows two **Completed** runs: one for `"PARENT"`, one for `"METAB"`. Each has its own frozen rows, script and results.

## Step 12. Publish both runs

1. Select **Publish** on the left.
2. In **1. Choose tables and figures**, there is a box for each run. Keep the items that are ticked to begin with: the parameter summary, the individual parameter listing, and the individual and mean concentration–time profiles.
3. Under **Label (optional)**, type `PARENT and METAB`.
4. Select **Check**. Each file shows as **New copy**.
5. Select **Publish**. The new publication appears in **2. Publications**, with both analytes.

## Step 13. Build the Word report

In **4. Report (DOCX)**:

1. Under **Publication**, choose the publication labelled `PARENT and METAB`.
2. Select **Set up report** and wait for it to finish. The button then changes to **Use this publication**, and **Edit report** appears.
3. Under **Status**, choose **Draft**.
4. Select **Check**. The card should say "The last check found nothing to fix."
5. Select **Render** and wait. This can take a few minutes.
6. Select **Download DOCX** and open the file in Word.

## Step 14. Build the PowerPoint deck

**Known issue:** the deck tool in this version can't build a slide from a table that has an empty cell, and this exercise's tables have some (subject `TA-008` has no values, and some METAB profiles have no λz). The build would stop with "IndexError: tuple index out of range". Until a fixed version ships, give the deck its own publication with the figures only:

1. On **Publish**, in **1. Choose tables and figures**, untick the **Parameter summary** and the **Individual parameter listing** in both run boxes. Keep the figures ticked.
2. Under **Label (optional)**, type `Figures for the deck`, select **Check**, then **Publish**. The figures were already published in Step 12, so **Check** shows them as **Already published (same bytes)**: the new publication reuses the same files.

Then, in **5. Deck (PPTX)**:

1. Under **Publication**, choose `Figures for the deck`.
2. Select **Set up deck** and wait for it to finish. The button then changes to **Use this publication**, and **Edit fragments** appears. If you already set up a deck from another publication, the button reads **Use this publication**: select it and choose **Replace presentation.yaml**.
3. If you like, select **Open editor**. The deckifyr editor opens under **Apps** on the left. Double-click the title text on the first slide, type a new title, click outside the text, and select **Save** at the top of the editor. Then select **Publish** on the left and select **Close editor**.
4. Select **Check**. The card should say "The last check found nothing to fix."
5. Select **Build** and wait.
6. Select **Download PPTX** and open the file in PowerPoint.

## Step 15. Keep a snapshot of your work (optional)

Version history keeps a copy of files you choose, with Git, on this computer. You don't need it, and a snapshot is not a run, a publication or a build: those stay as they are. See [Version history](version-history.md).

1. Select **Version history** on the left. Leave **Also allow sending…** unticked, and select **Open version history**.
2. The page opens under **Apps**. Select **Start version history here**, keeping the suggested files to leave out ticked: they are scratch files and caches the app can rebuild.
3. Under **Before your first snapshot**, type your name and email, then select **Save my name and email**.
4. Under **Changes**, in **Editable sources**, tick your cleaning recipe (in `.opncafyr/recipes/`), `report/report.qmd` and `deck/presentation.yaml`. Leave the rest unticked.
5. Under **Save snapshot**, type `Cleaning recipe, report text and slides`, then select **Save snapshot**.

The page confirms the snapshot was saved, and the files you ticked leave the **Changes** list. Files in the other groups stay listed until you choose to snapshot them. Runs and builds carry **Protected**, and data and results carry **May identify subjects**.

## What you have now

Your `TA-walkthrough` folder now holds:

- `source-data/two_analyte.csv`, unchanged;
- two completed, sealed runs, one per analyte, each with its own replay check (in the hidden `.opncafyr` folder);
- `OUTPUTS`, with the published tables and figures;
- `report` and `deck`, with your builds;
- if you did Step 15, a hidden `.git` folder with your first snapshot, and a `.gitignore` listing the files left out.

The report and deck built, which shows the files were assembled as recorded. It does not show that the analysis is right. That judgement is yours.
