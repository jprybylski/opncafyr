# Publishing runs to reports and decks

A completed run becomes a report (DOCX) or a deck (PPTX) in two steps. Both start on the
**Publish** page.

1. **Publish** tables and figures from completed runs into `OUTPUTS/`. This is a separate,
   reviewable step that never changes a run.
2. **Build** a report or deck from one publication: see [Reports](#reports-32) and
   [Decks](#decks-33).

Research use only. A successful DOCX or PPTX build shows that the files were assembled as
recorded. It does not validate the analysis.

## Publishing tables and figures (#31)

Only runs that are **completed** can be chosen. A completed run has passed its replay and
was sealed when it was confirmed. Its files must also still match their recorded hashes;
the page says why any other run can't be chosen. The server enforces the same rules, so an
API call can't get around them.

Each completed run offers the tables and figures that opnca-r saved:

| Offered | From the run | Pre-selected | Shows subject IDs |
|---|---|---|---|
| Parameter summary | `results/opnca-summary.csv` | Yes | No |
| Individual parameter listing | `results/opnca-parameters-wide.csv` | Yes | Yes |
| Individual concentration–time profiles (linear, log) | `results/figures/profiles-linear`, `profiles-log` | Yes | Yes |
| Parameter summary by group | `results/opnca-summary-by-<group>.csv` | No | No |
| Boxplots of parameters | `results/figures/boxplot-<parameter>` | No | No |

Since opnca-r 0.2.9 a run can also offer the **mean** concentration–time profiles, which
ADR 0007 pre-selects, and the table behind them:

| Offered | From the run | Pre-selected | Shows subject IDs |
|---|---|---|---|
| Mean concentration–time profiles (linear, log) | `results/figures/mean-linear`, `mean-log` | Yes | No |
| Mean concentration–time table | `results/opnca-mean-profiles.csv` | No | No |

opnca-r computes the means; the launcher never draws or recalculates them. You choose the
time basis (actual or nominal time), the statistic and the error measure in opnca-r's Save
box, and opnca-r refuses to save the mean figures until the time basis is chosen. Nominal
time is usually the right choice: with actual times most time points hold one sample. The
choices are written in the analysis record, and the published files' footnotes state them.
If a run was saved without the mean figures (they were unticked in opnca-r, or an older
opnca-r saved the run), the page says so; make a new run to include them.

One publication can combine runs of different analytes.

**Check** shows each file's published name, whether it shows subject IDs, and the checks of
its metadata sidecar, without writing anything. **Publish** then does four things:
- It copies each file to `OUTPUTS/tables/` or `OUTPUTS/figures/` as
  `<analyte>_<run-id>_<artifact>.<ext>`. Files are copied, never moved or linked, and are
  byte-identical to the run's file.
- It writes a reportifyr metadata sidecar next to each file:
  `<name>_<ext>_metadata.json`, in reportifyr 0.4.0's format.
- It checks every copy and sidecar (#30).
- It records `.opncafyr/publications/<id>.json` (`publication-v1`). That record names the
  exact run IDs and the SHA-256 of each run manifest, replay report, source file,
  published copy and sidecar.

A published file is never replaced. If a name already holds different bytes, for example
because someone edited the file, the whole publication is refused before anything is
written. The same file published again reuses the existing copy and its sidecar. The
record is written last. If publishing is interrupted, the files already copied are listed
as **not recorded** under "Published files", and reports and decks never use them.

### Metadata sidecars

reportifyr adds footnotes from a sidecar: the source script, notes and abbreviations. It
also checks that a table or figure is the one the sidecar describes, using a BLAKE3 hash.
When a sidecar is missing or broken, reportifyr only writes a log line and the report
still "succeeds". So the launcher checks every sidecar before publishing, rendering or
building (`workflows/nca/publish/sidecars.py`):
- the file and its sidecar exist;
- the sidecar is valid JSON in reportifyr's format;
- its path and file type match the file;
- its hash matches the file's bytes;
- its footnote type (`meta_type`) and abbreviations are defined in the report's
  `standard_footnotes.yaml`.

The sidecars the launcher writes have these parts:
- **source** (`source_meta`): the run's `analysis.R`, with the run's save and seal times;
- **author**: the computer account that published the file;
- **notes**: the artifact, analyte and run ID;
- **abbreviations**: only those the bundled footnotes define;
- **footnote type**: always `"NA"`. The footnote types in the bundled template carry
  canned text that states units (for example "hours" and "ng/mL"), and units are never
  assumed.

"Published files" on the Publish page runs the same checks over everything in `OUTPUTS/`.

## Reports (#32)

The **Report (DOCX)** card on the Publish page sets up `report/`, a quartifyr project for
one publication.

1. **Set up report** lays out the project offline, from the template bundled with the app
   (ADR 0007):
   - `report.qmd` with one captioned table or figure per published file;
   - the owner's `standard_footnotes.yaml` and `config.yaml`;
   - quartifyr's reference DOCX;
   - the quarto-plus and quartifyr extensions.

   It also writes a copy of the publication's files in `report/OUTPUTS/`. Analyte names
   from the data are reduced to plain words before they go into `report.qmd`, so they
   can't open a code chunk or a shortcode.
2. **Use this publication** binds the project to another publication. If you edited
   `report.qmd`, you choose whether to replace it or keep your version.
3. **Check** runs two sets of checks on a copy of the project that holds exactly the
   publication's files, each checked against its recorded hash:
   - the launcher's sidecar checks;
   - quartifyr's `check.R`.

   It lists what would block or weaken the render.
4. **Edit report** opens `report.qmd` in the launcher's project-file editor (#52):
   - **source mode** edits everything;
   - **visual mode** edits only plain headings, paragraphs and lists, and keeps the
     front matter, shortcodes, divs, chunks and placeholders exactly as they are;
   - the **preview** is approximate and runs nothing.

   Save writes only if the file hasn't changed on disk since you opened it; otherwise
   both versions stay on screen until you choose. Check and Render wait while the editor
   holds unsaved text. Draft or final is a report setting, kept in
   `report/opncafyr-settings.json`; a render asks until you choose.
5. **Render** renders that same copy with quartifyr's strict,
   offline render. It keeps the DOCX that quartifyr's result lists, after re-hashing it, in
   `report/builds/<build-id>/` with two records:
   - `build.json` (`publish-build-v1`). It lists the publication, the exact run IDs, the
     hash of every published file, every input (hashed before the render), and the tool
     versions;
   - quartifyr's own `render-result.json`.

   **Download DOCX** serves it only while it still matches its manifest. A build is
   marked **Out of date** once its source, settings or published files change; the
   build itself is never changed or relabelled.

A render that fails, leaves placeholders unfilled, misses metadata, runs past its time
limit or is cancelled keeps nothing. The whole process tree is stopped at the time
limit. One report job runs at a time.

Field recalculation (LibreOffice) is not used, so Word may ask to update fields when the
file is opened (docs/spikes/libreoffice.md). Quarto can't render a project inside a hidden
folder, so a study folder must not sit inside one (for example `~/.studies/`).

## Decks (#33)

The **Deck (PPTX)** card sets up `deck/`, a deckifyr project for one publication.

1. **Set up deck** copies deckifyr's bundled minimal deck. It never fetches a template
   (ADR 0007). It then writes:
   - an "NCA results" `presentation.yaml`: a title slide, then one slide per published table
     or figure, referenced as `{rpfy}:<file>` so their footers come from the sidecars;
   - the bundled `standard_footnotes.yaml`;
   - a copy of the publication's files in `deck/OUTPUTS/`.
2. **Open editor** starts the deck editor inside the launcher, under `/apps/deckifyr/`.
   Closing it, quitting, or the idle timer saves unsaved edits first.
   - The editor's own Build button makes drafts in `deck/build/`. The launcher doesn't
     record those.
   - **Use this publication** binds the deck to another publication. You must close the
     editor first, and if you edited `presentation.yaml` you choose whether to replace or
     keep it.
3. **Edit fragments** opens the deck's Quarto fragments (`.qmd`) in the launcher's
   project-file editor (#52). **New fragment** makes `deck/fragments/<name>.qmd`; then add
   a Quarto element in the deck editor and choose it. A save warns when a fragment has a
   slide break (`---`) or more than one top-level heading, which deckifyr's check rejects.
4. **Check** runs `deckifyr validate --strict` on a copy of the deck holding exactly the
   publication's files, after the launcher's sidecar checks. A validation error is shown
   as the check's failure; deckifyr's warnings are listed.
5. **Build** (optionally with slide previews) builds that copy. The build counts only when
   all of these hold:
   - deckifyr reports success;
   - its manifest exists;
   - the manifest's PPTX hash matches the file.

   The launcher keeps these in `deck/builds/<build-id>/`:
   - the PPTX;
   - deckifyr's manifest;
   - the previews deckifyr's manifest lists, each hashed;
   - `build.json` (`publish-build-v1`).

   **Download PPTX** serves it only while it still matches.

Check and Build wait while the deck editor or the fragment editor has unsaved edits:
save them, or close the deck editor (which saves). Slide previews are offered only when LibreOffice is installed and a short test
conversion finishes in time.

Decks and reports are your own projects, and they are trusted. Quarto blocks and flextable
`.rds` tables may run R code when the deck is built. They run with the app's pinned R,
Quarto and LibreOffice only, never with a program a deck file names.

### Why there are no R scripts

quartifyr, reportifyr and deckifyr can all be driven from R, and their examples do so.
The launcher doesn't, so a study folder has no R scripts for publishing, rendering or
building.
- **Sidecars** are written by the launcher in Python, in the format reportifyr's R
  `write_*_with_metadata()` functions produce. Those R functions need the study to be
  set up as a reportifyr project first (with pyro and uv), and they need a global git
  identity to fill in the author. The launcher needs neither, so publishing works offline
  and never touches your git configuration. The hash uses the same BLAKE3 algorithm
  reportifyr uses.
- **Decks** are built by running deckifyr's Python command line from the app's pinned tools
  environment, with trusted binaries only. deckifyr's R wrappers are not used (#33).
- **Reports** are rendered only through quartifyr's fixed `render.R` and `check.R` entry
  points, with the pinned R, Quarto and Python (#32).

Every tool runs from the versions pinned with the app, and nothing reaches the network.
The source of each published number is the run's own `analysis.R`, which the sidecar
names.
