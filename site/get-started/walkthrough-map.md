---
title: "2. Map and check"
parent: Two-analyte walkthrough
grand_parent: Get started
nav_order: 2
description: "Walkthrough part 2: map the practice file's columns, units and flag values, run the checks for one analyte, and fix the planted problems with cleaning steps."
permalink: /get-started/walkthrough-map/
---

# 2. Map and check

Tell the app what each column means, run the checks for one analyte at a time, and add the cleaning steps that fix what they find.
{: .fs-5 .fw-300 }

This is part 2 of the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}). Before you start, you should have finished [part 1]({{ '/get-started/walkthrough-import/' | relative_url }}): the practice file is imported, and its recipe has been applied with no steps ("All 168 rows: 168 kept, 0 rejected.").

All of this part happens on the **Analysis** page, in cards 2 to 5.

## Step 4. Map the columns

Card **4. Column mapping** lists every role the analysis can use. For each role below, choose the column. Where the app shows a suggestion that matches, you can select **Use**. A suggestion is never used until you pick it. Leave every role not listed here as it is.

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

<figure class="shot">
  <img src="{{ '/assets/screenshots/04-mapping.png' | relative_url }}" width="1014" height="1371" loading="lazy"
       alt="The Column mapping card: each role, such as Subject (profile) ID, Analyte code and Actual time, has a column chosen in a drop-down, and a suggestion with a confidence and reason beside it. Secondary subject ID is left unchosen, with a suggestion of SUBJID and a Use link.">
  <figcaption>Each role has a drop-down and, where the app has one, a suggestion you can take with <strong>Use</strong>.</figcaption>
</figure>

### Units

The app never guesses a unit. Set them under **Units**:

1. **Unit of time**: choose **One unit for every row** and type `h`. The file has no time unit column, and `AFRLT` is in hours.
2. **Unit of concentration**: choose **A unit column**, then `PCSTRESU`. Its only value is `"ng/mL"`.
3. **Unit of dose**: choose **A unit column**, then `DOSEU`. Its only value is `"mg"`; its blank cells are skipped.

These become the runs' *unit labels*: opnca-r will label the results in `ng/mL`, `h` and `mg`. The labels never change a number. See [Units]({{ '/guides/cleaning/' | relative_url }}#units).

### Flag values

For each flag column, the app lists every value it holds. Say what each one means. A blank value shows as `""`.

1. Under **BLQ flag**, set `"Y"` to **TRUE**, `""` (blank) to **FALSE** and `"N"` to **FALSE**. The single `"N"` (line 15) is a planted surprise: the rest of the file uses only `Y` and blank. Here you decide that `N` means "not BLQ".
2. Still under **BLQ flag**, tick **Also BLQ: concentration text starting with “<”**. Subject `TA-003`'s metabolite results from 2 h are written as `<LLOQ` with no flag. With this ticked, they count as BLQ.
3. Under **Missing flag**, set `"Y"` to **TRUE** and `""` to **FALSE**.
4. Under **Exclusion flag**, set `"Y"` to **TRUE** and `""` to **FALSE**.
5. Select **Save mapping**.

{: .expect }
> The card says "Mapping saved."

{: .fix }
> **The card says "Saved. Some roles still need a choice."** — look under **Still to do**. Usually a flag value has no **TRUE** or **FALSE** yet, or a unit is not set. Fix it and select **Save mapping** again.

## Step 5. Run the checks to find the problems

Each run analyses exactly one analyte. Rows are matched on the exact text of the analyte code, so `"METAB "` (with a space) is not `"METAB"`.

1. In **5. Analyte and checks**, under **Analyte (exact value)**, choose `"PARENT"`.
2. Under **Specimen (PCSPEC)**, choose `"PLASMA"`. The 4 `URINE` rows of `PARENT` are left out; they are counted, not rejected.
3. Select **Run checks**. The list shows several **Blocking** findings.
4. Choose `"METAB"` under **Analyte (exact value)**, then `"PLASMA"`, and select **Run checks** again. The metabolite has blocking findings of its own.

{: .fix }
> **The card says "Apply the recipe and map the analyte column first."** — go back to card **2. Cleaning steps**, select **Apply to all rows**, then check that **Analyte code** is mapped to `PCTESTCD` and select **Save mapping**.

### Read the findings

Each finding shows its level and name (for example **Blocking: conc negative**), the subject, a message, the source line numbers, and a hint on how to fix it. A blocking finding stops the run until you change the recipe or the mapping.

These are the planted problems and how this exercise fixes them:

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

{: .note }
> For the duplicates on lines 96 and 97, the two concentrations differ. In a real study you would find out which one is right. The app never picks one for you. Here you keep line 96.

## Step 6. Add the cleaning steps

Now fix the problems in the recipe. Add these 11 steps in card **2. Cleaning steps**, in this order. Each one picks rows by line number: `opncafyr_line` is the row's line number in the source file, with the header as line 1.

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

```text
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

{: .note }
> **Why remove some rows and exclude others?** Line 24 and subject `TA-008` are real samples that should not count, so they stay in the data, marked as excluded, with a reason opnca-r can see. The removed rows are not usable as data, so they go to the rejected-row log.

{: .fix }
> **A step is in the wrong place or has the wrong value** — use **Up** and **Down** to move it. To change a value, select **Edit** on the step, change it and select **Save step**; the step keeps its place.

### Fix a step with Edit

 If a step has a wrong value, such as the wrong line number, you don't need to remove it and add it again:

1. Select **Edit** on the step. Its form opens with its values and note filled in, under "Editing step N". The other steps' buttons wait until you finish.
2. Change the value, then select **Save step**. The step stays in its place. **Cancel** changes nothing. If the recipe can't take the change (for example a later step needs a column this step made), the form stays open with the reason and what you typed.
3. The recipe has changed, so select **Apply to all rows** again, then run the checks again.

To practise: open step 7, change the line number from `150` to `105`, save, apply and run the checks. The finding for the dose that differs returns, now next to a step that removes the wrong row. Edit step 7 back to `150`.

Then run the recipe:

1. Select **Apply to all rows**.
2. Check that card **4. Column mapping** still shows your choices. The mapping is kept with the recipe. If anything is listed under **Still to do**, fix it and select **Save mapping**.

{: .expect }
> - In **3. Result**, the summary says "All 168 rows: 159 kept, 9 rejected."
> - **Rows at each step** shows one row rejected by each of steps 1 to 9, and none by steps 10 and 11.
> - **Rejected rows (9)** lists each removed line with its step and reason. This is the rejected-row log.

<figure class="shot">
  <img src="{{ '/assets/screenshots/03-cleaning.png' | relative_url }}" width="1014" height="1277" loading="lazy"
       alt="The Rows at each step table for the 11 cleaning steps. Steps 1 to 9, Remove rows to the rejected-row log, each go down by one row with one rejected, from 168 rows in to 159 out. Steps 10 and 11, Exclude rows, keep all 159 rows and report the excluded lines: 24, then 121 onwards.">
  <figcaption>For every step, rows in equal rows out plus rows rejected. The exclude steps keep their rows and mark them.</figcaption>
</figure>

{: .fix }
> - **The summary is not "159 kept, 9 rejected"** — look at **Rows at each step**. A remove step that rejected 0 rows has the wrong line number or condition; a step that rejected more than one has picked too many rows. Fix that step and select **Apply to all rows** again.
> - **Step 10 or 11 rejected rows** — it was added as **Remove rows to the rejected-row log** instead of **Exclude rows (they stay, marked)**. Remove it, add it again as an exclude step, and apply again.
> - **The result says "The recipe changed. Preview or apply it again."** — you changed a step after applying. Select **Apply to all rows**.

## Step 7. Check PARENT and acknowledge its warnings

1. In **5. Analyte and checks**, choose `"PARENT"` (now 81 rows) and the specimen `"PLASMA"` (77 rows).
2. Select **Run checks**. There should be no blocking findings now, and three warnings. A copy of the blocking findings and warnings, with their hints, also appears under **2. Cleaning steps**.
3. Read each warning, then tick **I have reviewed this and want to go on**:
   - **Warning: lloq varies**: subject `TA-002`'s rows carry an LLOQ (lower limit of quantification) of 0.10; all other `PARENT` plasma rows carry 0.05.
   - **Warning: time negative**: subject `TA-003`'s predose sample was taken 15 minutes before the dose (line 38).
   - **Warning: blq above lloq**: subject `TA-006`'s 24 h sample is flagged BLQ but its concentration, 0.080, is above the LLOQ (line 102).
4. The notes need no action: the flag counts per profile, the subject accountability and an **opnca format check** note.

<figure class="shot">
  <img src="{{ '/assets/screenshots/05-checks.png' | relative_url }}" width="1016" height="885" loading="lazy"
       alt="The Analyte and checks card with PARENT (81 rows) and PLASMA (77 rows) chosen. Three warnings, lloq varies for TA-002, time negative for TA-003 and blq above lloq for TA-006, each have an 'I have reviewed this and want to go on' box. Two notes follow, and the summary line reads 0 blocking, 3 to acknowledge, 2 notes.">
  <figcaption>After cleaning, PARENT has no blocking findings. Each warning needs its own tick.</figcaption>
</figure>

{: .expect }
> The summary line under the findings starts "0 blocking, 3 to acknowledge". The opnca format check note arrives a little later, once opnca-r's own data check finishes.

{: .fix }
> **There are still blocking findings** — compare their line numbers with the table in Step 5. The matching cleaning step is missing or has the wrong line number. Fix it in card **2. Cleaning steps**, select **Apply to all rows**, then **Run checks** again.

Your acknowledgements are recorded with the run. The `METAB` checks come in part 3, when you start its run.

To learn more about each check, see [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }}).

**Next:** [3. Run each analyte]({{ '/get-started/walkthrough-run/' | relative_url }})
