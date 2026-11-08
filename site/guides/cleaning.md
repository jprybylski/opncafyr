---
title: Cleaning, BLQ and units
parent: Guides
nav_order: 5
description: How cleaning recipes work, what the app never does to your data, and how it handles BLQ results, units, exclusions, duplicates and ambiguous choices.
permalink: /guides/cleaning/
---

# Cleaning, BLQ and units

Cleaning is a list of explicit steps, each with a reason. The app never changes your source file and never makes a data decision for you.
{: .fs-5 .fw-300 }

You clean data on the **Analysis** page, in cards **1. Source and recipe**, **2. Cleaning steps** and **3. Result**. You then say what the columns mean in **4. Column mapping** and run the checks in **5. Analyte and checks**. For a guided example, see [part 2 of the walkthrough]({{ '/get-started/walkthrough-map/' | relative_url }}).

## What cleaning never does

- It never changes the source file.
- It never drops a row without a reason.
- It never sets a below-the-limit (BLQ) or blank concentration to zero.
- It never puts nominal time in place of actual time.
- It never guesses units, and never converts them unless you add a **Convert units** step.
- It never resolves duplicates by itself. You choose which row to keep.

Derived values always go into a new column, so the original column is still there to compare.

## Recipes

A *recipe* is the list of cleaning steps for one imported file, kept in order. Steps run from top to bottom. A recipe with no steps uses the data as it is: nothing is removed unless a step says so, with a reason.

1. In **1. Source and recipe**, under **Imported file**, choose the file you imported on the **Data** page.
2. Select **New recipe**, or choose a recipe you made before under **Recipe**. Each one shows how many steps it has.
3. In **2. Cleaning steps**, choose a step in **Add a step…**, fill in the small form, and select **Add step**. **Note (optional)** lets you say why the step is needed; the note is kept with the recipe.
4. Use **Up**, **Down** and **Remove** to change the order or take a step out.

Every value you type is matched exactly, including spaces and letter case.

| Step | What it does |
|---|---|
| **Clean column names** | Makes column names lower case, with `_` in place of spaces and other marks, for example `Conc (ng/mL)` becomes `conc_ng_ml`. |
| **Rename a column** | Gives a column a new name. It cannot reuse a name the data has had. |
| **Trim spaces** | Removes spaces at the start and end of values in the columns you pick. |
| **Check numbers** | Reads a column as a number or a whole number, with a point or comma as the decimal mark. |
| **Replace values** | Replaces exact values in one column, for example a misspelt route. |
| **Copy a column** | Copies a column into a new column. |
| **Join columns** | Joins columns into a new column, with a separator you choose. |
| **Add a constant column** | Adds a new column with the same value on every row. |
| **Derive elapsed time** | Works out time since dose from a sample date-time and a dose date-time, in the unit you choose. You also describe the time zones in words. |
| **Convert units** | Converts values whose unit is exactly the text you give into another unit. This is the only way a value changes unit. |
| **Make a TRUE/FALSE column** | Makes a new TRUE/FALSE column from the values of another column. You say what each value means. |
| **Exclude rows (they stay, marked)** | Marks rows as excluded, with a reason. They stay in the data and opnca-r sees them as excluded. |
| **Remove rows to the rejected-row log** | Takes rows out of the cleaned data, with a reason. They are listed in the rejected-row log. |
| **Report duplicate rows** | Lists rows that have the same values in the columns you pick. It removes nothing. |

### Choosing rows

**Exclude rows** and **Remove rows** need you to say which rows. Under **Rows**:

1. Choose a column. The first choice, `opncafyr_line`, is the row's line number in the source file. The header is line 1, so the first data row is line 2. Checks and the rejected-row log use the same line numbers.
2. Choose a condition: **is**, **is one of (one per line)** or **is blank**.
3. Type the value, or one value per line.
4. Type a **Reason**. Every removed or excluded row needs one.

## Before and after counts

In **3. Result**:

- **Preview** runs the steps on the first 200 rows. Use it to try a step quickly.
- **Apply to all rows** runs the steps on every row. You must apply the recipe before you can map its columns.

The result shows a summary line, for example "All 168 rows: 159 kept, 9 rejected", and **Rows at each step**: for every step, the rows in, rows out, rows rejected, values changed, new columns, and anything the step reported (such as duplicate lines). For every step, rows in always equal rows out plus rows rejected.

When you change a step, the result is cleared and the app says "The recipe changed. Preview or apply it again."

## The rejected-row log

**Rejected rows** lists each row a **Remove rows** step took out: its line in the source file, the step that removed it and the reason. No row is dropped without a line in this log. The log records line numbers and reasons, not the row's values, so check the source file at that line if you need to see what was removed.

## Exclude or remove?

- **Exclude** when the sample is real but should not count, for example a haemolysed sample or a subject with no dose record. The row stays in the run's data, marked as excluded, with its reason, and opnca-r sees it as excluded.
- **Remove** when the row is not usable data, for example a duplicate copy or a value that cannot be read. The row goes to the rejected-row log with its reason.

## Exclusions

A row can be excluded in two ways:

- an **Exclude rows (they stay, marked)** step in the recipe, with its reason; or
- an exclusion flag column in the data, mapped as **Exclusion flag**, with its reasons in a column mapped as **Exclusion reason**. If you have not mapped an **Exclusion reason** column, the app asks for one reason for every excluded row.

An excluded row with no reason is a blocking finding (**exclusion no reason**). To fix it, add an **Exclude rows** step for that row, with the reason.

Samples you exclude inside opnca-r are recorded, but opnca-r does not record a reason for them. The app adds a warning to the run when that happens.

## BLQ results

*BLQ* means below the lower limit of quantification: the result is too low for the assay to measure reliably. The *LLOQ* (lower limit of quantification) is that limit.

The app never sets a BLQ or blank concentration to zero, and never decides how BLQ results count in the analysis. You make each of these choices:

1. **Say which rows are BLQ.** Map your BLQ flag column as **BLQ flag**. The app lists every value it holds, with its row count; choose **TRUE** or **FALSE** for each. A blank value shows as `""`. Any value you leave unset blocks the run, because its meaning is unclear.
2. **Say whether text such as `<LLOQ` is BLQ.** Under **BLQ flag**, tick **Also BLQ: concentration text starting with “<”** if your data writes some results that way. Those rows are then treated as BLQ, and the text is kept as it was.
3. **Keep BLQ and missing apart.** A sample that was not analysed is missing, not BLQ. Map a missing flag column as **Missing flag**. A row flagged as both blocks the run (**blq and missing**).
4. **Choose the BLQ rules for the run.** In **6. Start analysis**, choose how BLQ samples are handled for **BLQ before the dose**, **BLQ after the dose** and **BLQ at the end of the profile**. Each offers **Exclude the sample**, **Set to zero**, **Half the LLOQ** or **LLOQ / √2**. Then choose the **BLQ method**: **Conventional (rules above)** or **Censored log-normal**. There is no default: opnca-r uses exactly what you pick.

{: .note }
> **Set to zero** is a rule you choose for the analysis, and opnca-r applies it. Cleaning itself never writes a zero: the BLQ result stays as it was in the run's frozen data.

Two warnings point at BLQ results that may be wrong in the source. You must look at each and tick **I have reviewed this and want to go on** before you can start the run:

- **conc zero not blq**: a concentration of 0 after the dose, with no BLQ flag. It may be a hidden BLQ.
- **blq above lloq**: a BLQ-flagged concentration above the LLOQ. The source may be inconsistent.

## Units

The app never guesses a unit. Under **Units** in **4. Column mapping**, say how each unit is given, for time, concentration and dose:

- **A unit column**: choose the column that holds the unit on each row, for example `PCSTRESU`. Its values are shown next to it.
- **One unit for every row**: type the unit, for example `h`, `ng/mL` or `mg`.

If a column name contains a unit, the app says, for example, "The header says ng/mL: confirm it above." You still have to choose. The app tidies spelling only (for example `ng/ml` becomes `ng/mL`, `µg/l` becomes `ug/L`, `mcg` becomes `ug` and `hr` becomes `h`). Blank cells in a unit column are skipped.

Different units within one analyte, for time, concentration or dose, even equivalent ones such as `ng/mL` and `ug/L`, block the run until you add a **Convert units** step. So does a unit the app does not know.

### Unit labels in opnca-r

The units you confirm become the run's *unit labels*. When you select **Start analysis**, the app works out each label from the analyte's cleaned rows: the unit you typed, or the one unit its unit column holds. It passes them to opnca-r, whose **Data** view shows them under **Unit labels**, read-only and marked "set by the launcher". opnca-r uses them in its results, table headers (for example `cmax (ng/mL)`), chart axes, its record of the analysis and its `analysis.R` script. The run's `settings.json` and `manifest.json` record them too.

{: .note }
> Unit labels are words only. opnca-r never converts or rescales a value, and it does not check that the units fit together. A label never changes an estimate. Values change unit only through a **Convert units** step, which is recorded. A save labelled in other units than the run's is refused.

## Time

**Actual time** must be elapsed time since the dose, as a number. The app never puts nominal (planned) time in its place:

- Mapping a nominal time column as **Actual time** blocks the run (**time nominal as actual**). There is no way around this.
- A column of clock times, such as `08:30`, blocks the run too (**time clock format**). If you only have date-times, add a **Derive elapsed time** step first.
- **Rename a column** cannot reuse a name the data has had, so a nominal time column can't take the actual time column's name.

## Duplicates

The app never picks between duplicate rows. Two or more rows that share a subject and actual time are a blocking finding (**duplicate key**), with their line numbers. Find out which row is right, then exclude or remove all but one, with a reason.

To look for duplicates on other columns, add a **Report duplicate rows** step. It lists rows with the same values in the columns you pick, and removes nothing.

## When the app asks you to choose

When a choice is unclear, the app asks rather than guessing:

- **Column roles.** In **4. Column mapping**, the **Suggestion** column may say **Suggested: COLUMN (75%)**, with how sure the app is and why. Select **Use** to take it, or pick another column; a suggestion is never used until you pick it. When the app can't tell, for example because two columns fit equally well, it says **Your choice:** and lists the candidates.
- **Flag values.** Every value of a BLQ, missing or exclusion flag needs **TRUE** or **FALSE**.
- **Units.** Every unit must be declared, even when the header names one. A unit column must hold one known unit for the analyte.
- **Routes.** If you map **Route**, the app shows each route value and how opnca-r reads it. A value opnca-r does not know shows as "Not recognised: add a “Replace values” step". The app never fills in a blank route.
- **Analyte values.** Rows are matched on the exact text of the analyte code, so `"METAB "` (with a space) is not `"METAB"`. The app points out values that differ only by spaces or letter case; they stay separate analytes unless you clean them, for example with **Trim spaces**.
- **Warnings.** Each warning needs its own tick in **I have reviewed this and want to go on**. Your acknowledgements are recorded with the run.
- **BLQ rules.** All three rules and the method must be chosen. There is no default.

Select **Save mapping** to keep your choices. Anything that still blocks is listed under **Still to do**, and the message says "Saved. Some roles still need a choice." until nothing is left.

## Blocking findings

**Run checks** reports findings for one analyte (and one specimen, if you mapped **Specimen**). Each shows its level and name, the subject, a message, the source line numbers, and a hint. A blocking finding stops the run until you change the recipe or the mapping:

| Finding | What it means | Usual fix |
|---|---|---|
| role unmapped | A required role has no column. | Map it. |
| analyte no rows | No row has exactly this analyte value. | Pick a value from the list. |
| specimen mixed | More than one specimen is left. | Map the specimen column and choose one. |
| time not numeric | Actual time is not a number. | Fix it with a step, or remove the row with a reason. |
| conc not numeric | Concentration is not a number, not blank and not BLQ. | Use **Check numbers** for decimal commas, or remove the row. |
| conc unclassified blank | A blank concentration is neither BLQ nor missing. | Flag it in the source data, or remove it with a reason. |
| conc negative | A negative concentration. | Remove or exclude the row with a reason. |
| route missing | The route is blank. | Remove the row, or replace the value, with a reason. |
| route unrecognised | opnca-r does not know this route. | Add a **Replace values** step. |
| route varies, dose varies | A profile has more than one route or dose. | One route and one dose per profile. |
| units mixed, units undeclared | Time, concentration or dose units differ, or a unit is missing or unknown. | Add **Convert units**, or declare the unit. |
| duplicate key | Two or more rows share a subject and actual time. | Exclude or remove all but one, with a reason. |
| blq and missing | A row is flagged both BLQ and missing. | A row is one or the other. |
| flag value unmapped | A flag value has no TRUE or FALSE. | Say what it means in the mapping. |
| exclusion no reason | An excluded row has no reason. | Add an **Exclude rows** step with the reason. |
| time nominal as actual | The actual time column is nominal time. | Map an actual elapsed time column. |
| time clock format | Actual time holds clock times. | Map elapsed time, or use **Derive elapsed time**. |
| profile no dose | A profile has no dose or no route. | Add the dose, or exclude the profile with a reason. |

After you change the recipe, select **Apply to all rows** again, then **Run checks** again.

Other warnings you may see are **lloq varies** (the LLOQ is not the same on every row, which can be right after an assay change), **time negative** (predose samples are normal) and **id usubjid not one to one** (a second subject ID column does not match the subject ID one to one). Notes need no action.

## Related pages

- [Workspace and run provenance]({{ '/guides/provenance/' | relative_url }}): why any change to cleaning, mapping, exclusions or BLQ rules makes a new run.
- [1. Import and clean]({{ '/get-started/walkthrough-import/' | relative_url }}) and [2. Map and check]({{ '/get-started/walkthrough-map/' | relative_url }}), in the walkthrough.
