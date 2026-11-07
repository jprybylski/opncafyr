# 0007. Report and deck templates, default publications, and identifiable data

- Status: accepted (owner, 2026-11-06)
- Date: 2026-11-06
- Issue: #3
- Unblocks: #31, #32, #33, #38, and the redaction rules in #11

## Context
`docs/spec.md` left two decisions open: "report and deck templates" and the "treatment of
potentially identifiable data". The security section already requires that raw
patient or subject values stay out of logs, telemetry, URLs and diagnostic bundles, and
that no dataset is sent to a remote service by default.

What the tools need:
- **quartifyr** renders a reportifyr-initialized Quarto project: `report.qmd` and
  `_quarto.yml` at the project root (the shell source; `_quarto.yml` sets
  `output-dir: report/shell`), plus `report/config.yaml`,
  `report/standard_footnotes.yaml` and the `quartifyr` and `A2-ai/quarto-plus`
  extensions. Its worked example is `examples/demo-report/`, and the reference DOCX is
  `inst/templates/org-reference.docx`.
- **deckifyr** `init` copies its bundled minimal example deck unless given
  `--from-dir` or `--from-repo`. A `--from-repo` template is fetched with `git` at run
  time, which an offline install cannot do (#44). Decks consume reportifyr `OUTPUTS/`
  through `{rpfy}:` references and `<stem>_<ext>_metadata.json` sidecars.
- **Run bundles** freeze a one-analyte `input/analysis.csv` (ADR 0005, #25, #26).
  Replaying `analysis.R` (#29) needs the subject key, so run bundles necessarily contain
  subject IDs.

## Decision

### 1. Report template
v1 ships **one generic NCA report**. Template sources, at the pinned quartifyr
(0.3.2, commit `5f3b0f5`, `runtime/pins.toml`):
- `examples/demo-report/`: `report.qmd`, `_quarto.yml`, `report/config.yaml`,
  `report/standard_footnotes.yaml`;
- `inst/templates/org-reference.docx`.

The **owner** owns the reference DOCX and the standard footnotes. The launcher
installs them with the app, not from the network. Organisation-specific report shells
come later, as a swappable template path. They must not need a change to core.

### 2. Deck template
v1 uses **deckifyr's bundled default** (the minimal example that `deckifyr init` copies
when given no template source), at the pinned deckifyr (0.3.5). It ships inside the
installed deckifyr package; the launcher never passes `--from-repo`.

The launcher offers an **auto-built "NCA results" deck** from a publication's artifacts.
The user can then edit it in the managed deckifyr editor (#33).

### 3. Publishable artifacts
All four artifact types are offered and **pre-selected** in the publication curator
(#31). The user can deselect any of them.

| Artifact | Contains subject IDs |
|---|---|
| Parameter summary table | No |
| Individual parameter listing | Yes |
| Mean concentration–time figure (linear and log) | No |
| Individual concentration–time profiles | Yes |

Only artifacts from `completed` runs can be published, and the publication manifest pins
run IDs and artifact SHA-256s (`CLAUDE.md`, `publication-v1`).

> **Amendment (2026-11-07, #31).** opnca-r 0.2.8 saves no mean concentration–time figure,
> only individual profiles and parameter boxplots. The curator therefore lists the mean
> figure as unavailable, and pre-selects the other three types. The launcher doesn't draw
> it, because opnca-r is the calculation authority. The upstream request is
> mantra-opnca/opnca-r#50. Once a pinned opnca-r saves the figure, it will be pre-selected
> as decided here.
>
> **Update (2026-11-07).** opnca-r 0.2.9 saves `mean-linear` and `mean-log` with the table
> behind them, and the launcher pins it. The mean figures are pre-selected as decided here.
> A run saved without them (left out in opnca-r, or saved by 0.2.8) says so in the curator.

### 4. Identifiable data
| Where | Coded subject IDs (e.g. `USUBJID`) | Other subject-level values |
|---|---|---|
| Run bundles (`input/analysis.csv`, results) | Allowed (needed for replay) | Only columns the user kept (below) |
| Published tables and figures, `OUTPUTS/`, reports, decks | **Allowed** | As in the artifact |
| Logs, URLs, telemetry, diagnostic bundles | **Never** | **Never** |

- **Redaction scope for #11 and #38.** Subject IDs and other row-level data values are
  never written to logs, URLs, telemetry or diagnostic bundles.
  - Messages and diagnostics may carry counts, column names, check ids and row numbers.
  - They may not carry cell values.
  - Child-app output that is captured into launcher logs must be filtered to this rule
    before it is written or bundled.
- **Direct identifiers.** A column that looks like a direct identifier (names, initials,
  dates of birth, raw calendar dates) is flagged at import (#21). The user must choose to
  keep it in the analysis input or exclude it, through a `needs_choice` prompt.
  - Nothing is dropped automatically, and the choice is recorded in the recipe.
  - Excluding a column changes only the analysis input; the source file is never
    rewritten.

### 5. Retention of source data
By default, the source file is **copied into `source-data/`** with its SHA-256, so every
run stays replayable from inside the study folder.

A **"reference only"** option records just the path and SHA-256. It is meant for large or
restricted files. With it, replay needs the original to be unchanged at that path, and
the run's frozen `analysis.csv` is still kept.

## Consequences
- **#32 (quartifyr adapter)** sets up report projects from the bundled demo-report sources
  and `org-reference.docx`, and always passes `--offline`.
- **#33 (deckifyr adapter)** initializes decks from deckifyr's bundled default and builds
  the "NCA results" deck. It never fetches a template over git.
- **#31 (publication curator)** pre-selects all four artifact types.
- **#11 and #38** redact as in section 4. A test with a fixture of subject IDs proves that
  none reach a log file or a diagnostic bundle.
- **#21 (CSV import)** adds the direct-identifier flag and the copy-by-default import with
  the reference-only option. **#22 (cleaning recipes)** records the keep-or-exclude
  choice.
- Template files ship with the install and are covered by the pins and the offline
  bundle (#5, #36).

## Revisit
- When an organisation needs its own report shell or deck template. Add a configurable
  template path; core stays tool-agnostic.
- When a sponsor's data policy forbids coded IDs in published outputs or copies of
  source data. The curator pre-selection and the retention default become per-workspace
  settings.
