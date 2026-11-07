# 0005. Data conventions: analyte, units, BLQ, exclusions, duplicates, time and check levels

- Status: accepted (owner, 2026-11-05, plan review); provisional until checked on the
  first sponsor dataset
- Date: 2026-11-05
- Issue: #2
- Unblocks: #20, #22, #23, #24, #25

## Context
`docs/spec.md` ("Each analyte run produces a replayable artifact set") says the handling
of flagged records, and which checks warn or block, must be decided against
representative data before implementation. The spec already fixes the invariants: never
rewrite the source, never silently drop rows, never set BLQ to zero, never substitute
nominal for actual time, never infer units, never resolve duplicates automatically, one
analyte per run.

opnca-r is the calculation authority. What it expects:
- **Engine columns** (`~/opnca/docs/guide/03_data_formats.md`): USUBJID, ACTUAL_TIME and
  CONC are required. DOSE and ROUTE are needed for dose metrics. NOMINAL_TIME, INF_DUR,
  LLOQ, BLQ, MISSING, EXCLUDED and TAU are optional. Routes are `iv-bolus`,
  `iv-infusion`, `extravascular` and their aliases; a blank or unknown route is an error.
  There is no analyte column, so the launcher filters. The guide's own example stores a
  BLQ row as `CONC=0, BLQ=1`. We do not write 0.
- **BLQ** (`~/opnca/docs/guide/07_blq_and_missing.md`): rules apply by position
  (predose, postdose, terminal). Excluded wins over BLQ. A row cannot be both missing
  and BLQ.
- **opnca-r** (`~/opnca/bindings/r`): `opnca()` defaults `blq_predose = "zero"`
  (`R/opnca.R` ~450). BLQ rules take `exclude`, `zero`, `half-lloq`, `lloq-over-sqrt2`;
  `blq_model(method = "conventional" | "censored_lognormal")`. EXCLUDED is a logical
  column with no reason field. There are no unit arguments. The app QC
  (`.opnca_app_qc`, `R/app-server.R`) separates errors from warnings and needs flags as
  TRUE/FALSE. `check_data_format()` (`R/data_conventions.R`) never transforms data.
  `--require-blq-rules` (`inst/scripts/serve.R`) makes the three BLQ rules a required
  choice.
- **opnca MCP layer** (`~/opnca/crates/opnca-application/src/dataset.rs`): units are
  never inferred, a duplicate time is an error, zero-vs-BLQ is a warning, and an
  exclusion needs a reason and an authority.
- **makecoolplots**: `kb/topics/data-checking.md` (checklist) and `kb/topics/sparse-pk.md`
  ("a sample that wasn't analysed is missing, not BLQ").
  `crates/mcp-inspect/src/nca_input.rs` drops undosed subjects with a warning and
  defaults an unknown route to extravascular. **We do neither.**
- Upstream: opnca-r#29 (app maps LLOQ/BLQ/missing, done), opnca-r#42
  (`--require-blq-rules`, done), opnca-r#46 (`--require-explicit-settings` also requires
  `blq_method`, done in 0.2.8; our adapter always presets it).

### Reference datasets
None is committed. Each is named by package or path and version.

| Dataset | Version | What it shows |
|---|---|---|
| `pharmaversesdtm::pc` + `ex` | 1.5.0 (R 4.6.1) | Synthetic SDTM. `<BLQ` in PCORRES/PCSTRESC with PCSTRESN 0 or NA; PCSTRESU `ug/ml`; PCLLOQ 0.01; ISO PCDTC only; one PCTESTCD (`XAN`) over PLASMA and URINE; EXROUTE `TRANSDERMAL`, EXSTDTC date only |
| `~/makecoolplots/fixtures/adpc.csv`, `adpc_no_paramcd.csv` | makecoolplots `7a9b174` | ADaM style (from xgxr). PARAMCD, AVAL/AVALC/AVALU, AFRLT/NFRLT, BLQFL, ANL01FL; an AVAL written `<LLOQ` |
| `~/makecoolplots/fixtures/sad.csv`, `mad.csv` | makecoolplots `7a9b174` | NONMEM style. TIME/NOMTIME, CMT/NAME, LIDV, CENS, per-row EVENTU |
| `~/makecoolplots/fixtures/messy_generic.csv` | makecoolplots `7a9b174` | Units only in headers, `.` as a conc, untrimmed IDs, duplicate (id, time) rows |
| `datasets::Theoph` | R 4.6.1 | Real, deidentified, no BLQ, no unit columns |

None has two PK analytes. #20 generates that fixture (`tests/fixtures/two_analyte.csv`).
Rows marked **synthetic** below are made up because no reference set shows the rule.

## Decision

### Q1. Analyte
- The user maps the analyte **code** column. Suggest `PCTESTCD`, `PARAMCD`, `ANALYTE`,
  `DVID`, `CMT`. Label columns (`PCTEST`, `PARAM`, `NAME`, `DVNAME`) are display only.
- The filter is exact string equality on the raw CSV text. No trimming or case folding
  unless an earlier recipe step did it. `"2"` is not `"2.0"`.
- Specimen/matrix (`PCSPEC` etc.) is its own role, filtered the same way. If more than
  one specimen value remains after the analyte filter and the role is unmapped, that is
  blocking.
- Rows for other analytes are counted in row accounting, not rejected.

```
USUBJID      PCTESTCD  PCTEST      PCSPEC  PCSTRESC  PCDTC     (pharmaversesdtm 1.5.0)
01-701-1028  XAN       XANOMELINE  PLASMA  0.1015... 2013-07-19T00:05:00
01-701-1015  XAN       XANOMELINE  URINE   <BLQ      2014-01-02T06:00:00
```
Filter `PCTESTCD == "XAN"` keeps 3556 PLASMA and 1016 URINE rows. Specimen unmapped:
blocking (`specimen_mixed`). Map PCSPEC and pick `PLASMA` to proceed.

### Q2. Units
- Units come from a per-row unit column (`PCSTRESU`, `AVALU`, `EVENTU`) or from an
  explicit user declaration in mapping.
- A unit in a header name is a hint. The user must confirm it.
- Normalisation is spelling only, from a fixed table: `µ`/`μ` → `u`, `mcg` → `ug`,
  `ml` → `mL`. Anything outside the table asks the user.
- Mixed concentration or dose units within the analyte are blocking, even when
  equivalent (`ng/mL` vs `ug/L`). Only an explicit, recorded `convert_units` or `recode`
  step fixes them.
- Time and dose units must be declared too.
- opnca-r takes no unit arguments, so units are recorded in the run manifest.

```
PCSTRESU = "ug/ml"            -> "ug/mL" (spelling table, shown)    (pharmaversesdtm)
"Conc.(ng/mL)" header         -> hint "ng/mL"; user confirms        (messy_generic)
AVALU "ng/mL" + AVALU "ug/L"  -> units_mixed, blocking              (synthetic)
Theoph: Time, conc, Dose      -> no unit anywhere; user declares    (datasets::Theoph)
```

### Q3. BLQ
- The snapshot carries a logical BLQ column (TRUE or FALSE on every row) and LLOQ when
  mapped or declared.
- An explicit recipe step builds BLQ from:
  - a flag column with a user-confirmed value table (`Y`/blank, `1`/`0`, `CENS`), and/or
  - result text matching a pattern shown to the user (`^<`, `BLQ`, `BLOQ`, `BQL`) in
    `PCORRES`, `PCSTRESC` or `AVALC`.
  A value outside the table blocks.
- Source CONC on BLQ rows is kept as it is; a blank becomes NA. The launcher never
  writes 0. A non-numeric CONC on a BLQ row (such as `<LLOQ`) is not a blocking
  `conc_not_numeric`; the snapshot holds NA and the source keeps the text.
- LLOQ is never guessed (for example from LIDV on CENS=1 rows). The user maps a column
  or declares a constant.
- Every session starts with all three BLQ rules and `blq_method` chosen by the user
  (`--require-blq-rules` plus a preset). There is no launcher default.
- opnca-r's `zero` rule is allowed only as an explicit user choice (a common predose
  convention). It is recorded in settings.
- A sample that was not analysed is MISSING, never BLQ.

```
USUBJID     AVAL AVALC BLQFL AFRLT    -> CONC=NA  BLQ=TRUE   (adpc.csv)
XGXSAD-001       BLQ   Y     48.229      LLOQ: no column; user declares or blocks
PCSTRESC "<BLQ"  PCSTRESN 0  PCLLOQ 0.01 -> CONC=0 (as in source)  BLQ=TRUE  LLOQ=0.01
                                                                    (pharmaversesdtm)
ID TIME   LIDV CENS               -> BLQ=TRUE via CENS table; LIDV 0.05 is not LLOQ
1  48.229 0.05 1                     unless the user maps or declares it   (sad.csv)
```

### Q4. Exclusions
- Two sources:
  1. a mapped source column with a confirmed value table, plus a reason column (or one
     user-entered reason for the whole set);
  2. selection in the launcher before the session.
- Every exclusion has a reason.
- Excluded rows stay in the snapshot with `EXCLUDED=TRUE`. They are never removed.
- `ANL01FL` is never an exclusion unless the user maps it.
- In-session opnca-r exclusions are read from its saved record and marked "reason not
  recorded by opnca-r" (a warning). An upstream request for a reason field is noted for
  #28 and not filed now.

```
USUBJID     AFRLT  AVAL ANL01FL         -> not excluded; ANL01FL is unmapped   (adpc.csv)
XGXSAD-001  -0.089      ""
USUBJID  TIME CONC SAMPFL REASON        -> EXCLUDED=TRUE, reason kept          (synthetic)
S-101    4    9.1  X      "hemolysed"
```

### Q5. Duplicates
- Key: profile (USUBJID plus the mapped grouping roles: period, occasion, treatment,
  specimen) × ACTUAL_TIME, within the analyte, over non-excluded rows.
- Exact copies and key duplicates with different values are both blocking.
- They are resolved only by an explicit step with a reason: `exclude` (the row stays,
  flagged) or `filter_rows` (the row goes to the rejected log).
- Never keep-first, never averaging. The UI may offer a pre-filled step for exact
  copies; the user approves it.

```
line 8:  S-002,0,0,50,...       line 50: S-002,0,0,50,...   exact copy   (messy_generic)
line 23: S-004,2,3.042,...      line 53: S-004,2,3.042,...  exact copy
S-201,2,4.10   S-201,2,3.95     same key, different CONC: blocking          (synthetic)
```

### Q6. Time
- Actual time elapsed from dose must be a mapped numeric column (`ACTUAL_TIME`, `AFRLT`,
  `TIME`) with a declared unit.
- Deriving it from datetimes (`PCDTC − EXSTDTC`) happens only through an explicit recipe
  step `derive_elapsed_time`. The step records its source columns, unit and time-zone
  rule. A partial date, mixed time zones or an unparseable value blocks that row. The
  row is never dropped.
- Mapping a nominal column (`NFRLT`, `NRRLT`, `NOMTIME`, `NTIME`, `PCTPTNUM`) as actual
  time is blocking, with no override in v1.
- Clock strings such as `08:30` are blocking.

```
AFRLT 0.17  NFRLT 0.1            -> map AFRLT; NFRLT as actual blocks      (adpc.csv)
TIME 0.565  NOMTIME 0.5  TIMEUNIT "Hours"                                  (sad.csv)
PCDTC 2013-07-19T00:05:00, EXSTDTC 2014-01-02 (date only, all 591 ex rows)
  -> derive_elapsed_time blocks every row: dose time is partial    (pharmaversesdtm)
```

### Q7. Warning vs blocking
Blocking checks stop the run until a recipe or mapping change fixes them. Warnings let
the user proceed; the acknowledgement is recorded in `settings.json` and the run
manifest. Info is shown, not acknowledged. Check ids are snake_case for reuse in #24.
"Stricter" marks checks that opnca-r's app QC treats as a warning.

| Check id | Level | Rationale |
|---|---|---|
| `role_unmapped` | blocking | id, actual time, conc, dose, route and analyte are all required |
| `analyte_no_rows` | blocking | the exact-value filter matched nothing; nothing to run |
| `specimen_mixed` | blocking | plasma and urine in one profile are not one PK profile |
| `time_not_numeric` | blocking | opnca-r errors too |
| `conc_not_numeric` | blocking | not blank and not BLQ; we never guess a number (stricter) |
| `conc_unclassified_blank` | blocking | a blank conc must be BLQ or MISSING (stricter) |
| `conc_negative` | blocking | opnca-r errors too |
| `route_missing` | blocking | opnca-r errors; never defaulted |
| `route_unrecognised` | blocking | e.g. `TRANSDERMAL` needs an explicit recode step |
| `route_varies` | blocking | one route per profile (stricter) |
| `dose_varies` | blocking | one dose per profile (stricter) |
| `units_mixed` | blocking | even if equivalent; fixed only by a recorded step |
| `units_undeclared` | blocking | units are never inferred |
| `duplicate_key` | blocking | never auto-resolved (stricter) |
| `blq_and_missing` | blocking | opnca forbids both states on one row |
| `flag_value_unmapped` | blocking | a value outside the confirmed table is ambiguous |
| `exclusion_no_reason` | blocking | every launcher exclusion carries a reason |
| `time_nominal_as_actual` | blocking | spec invariant; no override in v1 |
| `time_clock_format` | blocking | a clock time is not elapsed time |
| `time_derivation_failed` | blocking | partial date, mixed time zone or unparseable value |
| `profile_no_dose` | blocking | the opnca-r app needs DOSE and ROUTE; never dropped |
| `conc_zero_not_blq` | warning | post-dose 0 without a BLQ flag; may be a hidden BLQ |
| `blq_above_lloq` | warning | BLQ-flagged conc above LLOQ; source may be inconsistent |
| `lloq_varies` | warning | can be legitimate (assay change, dilution) |
| `time_negative` | warning | predose samples are normal |
| `id_usubjid_not_one_to_one` | warning | a second id column disagrees with USUBJID |
| `session_exclusion_no_reason` | warning | opnca-r records no reason; we cannot add one |
| `time_not_sorted` | info | order does not change the result |
| `profile_flag_counts` | info | BLQ, missing and excluded counts per profile |
| `subject_accountability` | info | subjects and rows in/out after each step |
| `too_few_points` | info | fewer than 3 measurable post-dose points; opnca-r still warns in-session |

### Q8. Subjects with no dose
- The profile is kept, never dropped.
- A profile without DOSE or ROUTE is blocking, because the opnca-r app needs both.
- It is resolved by a recipe fix (such as joining `ex`) or by excluding the profile with
  a reason.

```
USUBJID  TIME CONC DOSE ROUTE           -> profile_no_dose, blocking           (synthetic)
S-301    1    2.4
```

### What each answer unblocks
| Issue | Uses |
|---|---|
| #20 fixtures | Q1–Q8: plant one case for each blocking and warning check, two analytes, two specimens |
| #22 cleaning recipe | Q2 unit steps, Q3 BLQ step, Q5 resolution steps, Q6 `derive_elapsed_time` |
| #23 mapping | Q1 analyte/specimen roles, Q2 unit declarations, Q3 LLOQ, Q4 exclusion source |
| #24 validation | Q7 table and check ids |
| #25 snapshot and session | Q3 BLQ/LLOQ columns and BLQ presets, Q4 EXCLUDED, units in the manifest |

## Rejected options
- **Filter on the label.** Labels change between deliveries and are not unique.
- **Infer units from header names or convert equivalent units silently.** The spec
  forbids inference, and a silent conversion hides a data error.
- **Write 0 for BLQ, as the opnca example does.** That rewrites source data and hides
  the BLQ choice inside the input.
- **Use LIDV or another value as LLOQ on censored rows.** That is a guess.
- **Treat ANL01FL as an exclusion by default.** Its meaning is sponsor-specific.
- **Keep-first or average duplicates.** That is automatic resolution.
- **Drop undosed subjects or default route to extravascular** (makecoolplots). Both are
  silent changes.
- **Follow opnca-r's warning levels for duplicates, dose/route variation and
  non-numeric conc.** The session would run on data the user has not reviewed.

## Still to confirm
- Recheck every rule on the first sponsor dataset.
- Confirm that opnca-r ignores CONC on BLQ-flagged rows (pharmaversesdtm carries
  PCSTRESN 0 on some BLQ rows). If it does not, a non-NA CONC on a BLQ row becomes
  blocking until resolved. To check in #24/#27.
- Upstream request for an exclusion reason in opnca-r, at #28.

## Consequences
- The #20 fixture must plant each blocking and warning case, with a seeded generator.
- recipe-v1 needs explicit BLQ, time-derivation, unit and duplicate-resolution steps.
  Each shows before/after row counts and writes the rejected-row log.
- The opnca adapter always starts opnca-r with `--require-blq-rules` and presets all
  three BLQ rules and `blq_method` from the user's choice, which also covers opnca-r#46.
- The run manifest records units, the analyte raw value, warning acknowledgements and
  exclusion reasons.
- These rules make data fit for the calculation. They do not validate the science; this
  is a research-use tool.
