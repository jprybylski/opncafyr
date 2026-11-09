# Event records and files without an analyte

Most concentration files have one sample per row, with the dose and route on the same row. Two other kinds of file need one more step before mapping:

- **Event records** (NONMEM style): doses and samples are separate rows. A record-kind column (`EVID`) says which is which, `AMT` holds the dose amount, `CMT` or `DVID` holds the endpoint, and one unit column (`EVENTU`) can hold mg on dose rows and ng/mL on sample rows.
- **Loosely structured files**: one sample per row, but no analyte column, and sometimes no route column, with units only in the column headers.

The app never guesses what a code means, which dose a sample belongs to, or what the analyte is. It suggests, with reasons, and you decide. Every record is accounted for, and the source file is never changed.

## Event records

After **Apply to all rows** in **3. Result**, the **Event records** panel says whether the file looks like event records, and why: for example "EVID gives each record's kind". Nothing is applied until you save it.

### 1. Columns

Choose the column for each part. Six are required:

- **Subject ID**, for example `ID`.
- **Actual time (of every record)**, for example `TIME`. This must be actual time. A nominal time column here blocks the run.
- **Record kind (EVID)**.
- **Dose amount (AMT)**.
- **Endpoint code (CMT, DVID)**.
- **Route (read on dose records)**. If the file has no route column, first add an **Add a constant column** step named `route` with the route, such as `extravascular`, in **2. Cleaning steps**. The app never fills in a route.

Optional parts are **Observed value (DV)**, **Missing-DV flag (MDV)**, **Occasion**, **Dose unit (read on dose records)**, for example `EVENTU`, and **Time unit column**, for example `TIMEUNIT`.

Each column shows the app's suggestion. **Use the suggestions** fills in only the blanks; check each one.

### 2. What each code means

- **Record kinds**: give every value a meaning: **Observation**, **Dose** or **Other record**. The app suggests the usual meanings of EVID 0, 1 and 2. It never suggests 3 or 4 (resets), because they change how the doses before them count.
- **Endpoints**: for every endpoint value seen on observation records, choose **PK analyte** or **Another endpoint**. The table shows each code's labels (such as `NAME`) and units. A code seen only on dose records, such as the dosing compartment, is never an analyte. Each PK endpoint becomes its own analyte later, matched on its exact value.
- **MDV values**: if you chose an MDV column, say whether each value means **No observation (true)** or **An observation (false)**.

Select **Save event records**, then **Apply to all rows** again.

### 3. Choices the data needs

Some data has more than one reasonable reading. The app asks only when your data has the case, and there is no default:

| Choice | When it is asked | Options |
|---|---|---|
| **Dosing interval** | A subject has more than one dose. | **First dose** (up to the second dose), **Last dose** (to the end), or **Dose number…** with the number. |
| **Dose records at the same time** | Two dose records share a time, such as a split dose. | **Sum them as one dose**. Their routes and units must agree. Otherwise remove the extra records with a step and a reason. |
| **Samples before the first dose** | There are predose samples. | **Keep** them, with a negative time after dose, or **Set aside**. |
| **A sample at a dose time belongs** | A sample has exactly the time of the dose that starts or ends the interval. | **Before that dose** or **After that dose**. |
| **PK observations with no dose** | A subject has samples but no dose, or fewer doses than the dose number. | **Reject**: the rows go to the rejected-row log with the reason "No dose record for this observation's dosing interval". |

A changed choice is a new recipe version. Apply again, and any run made after that is a new run.

### 4. Where every record went

After applying, **Where every record went** counts each record's partition:

- **Selected PK observations**: these go on to mapping and the run.
- **Predose, set aside** and **In another dosing interval**: kept out by your choices, not rejected.
- **Dose records**, **Other endpoints**, **Other records**.
- **Rejected (no dose)**: only by the **Reject** choice, and listed in the rejected-row log.
- **Unresolved (blocking)**: each kind is listed with its line numbers and what settles it.

The counts add up to every row after the cleaning steps. These always block until a recipe step fixes them, with a reason:

- a dose amount that is missing, zero or negative (a placebo dose is not a dose);
- an excluded dose record (exclude samples, not doses);
- a record whose EVID, MDV and DV disagree;
- an unknown code;
- a time that is not a number;
- a sample whose time unit is not its dose's.

**First prepared observations** shows each selected sample with its dose time, **time after dose** (its actual time minus the dose's actual time), dose, dose unit, route and the line numbers of its dose records.

### 5. Mapping

In **4. Column mapping**, the app suggests the prepared columns: `opncafyr_time_after_dose` for **Actual time**, `opncafyr_dose` for **Dose** and `opncafyr_route` for **Route**. If you named a dose unit column, the dose unit column is `opncafyr_dose_unit`. Mapping the raw `TIME` or `AMT` instead blocks the run. Map the endpoint column, such as `CMT`, as **Analyte code**, and `EVENTU` as the concentration unit column: only sample rows are left, so it holds concentration units only.

Then choose the analyte (for example `2`) and run the checks as usual. Only the selected samples of that endpoint go into the run. The run keeps the record partition file and the preparation script with its other inputs.

## A file without an analyte column

When no column looks like an analyte code, the **Event records** panel says so and offers **Declare one analyte**. Type the analyte's name, such as `DRUGX`, and select the button. It adds an ordinary **Add a constant column** step named `analyte` to **2. Cleaning steps**, where you can see and remove it. Then:

1. Add an **Add a constant column** step for the route, if there is no route column.
2. Use **Clean column names** if the headers have spaces or units, for example `Conc.(ng/mL)` becomes `conc_ng_ml`.
3. Remove or flag any sample written `.` with a reason. A blank concentration that is neither BLQ nor missing blocks the run.
4. Apply, then map the columns. A unit in a header is only a hint: declare each unit yourself under **Units**.

Nothing about this file is inferred from its name or headers.

Next: [Mapping columns and running checks](mapping-and-checks.md).
