# Publishing runs to reports and decks

A completed run becomes a report (DOCX) or a deck (PPTX) in two steps. Both start on the
**Publish** page.

1. **Publish** tables and figures from completed runs into `OUTPUTS/`. This is a separate,
   reviewable step that never changes a run.
2. **Build** a report or deck from one publication: see [Reports](#reports-32).

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

ADR 0007 also pre-selects a **mean** concentration–time figure. opnca-r 0.2.8 doesn't save
one, so the page lists it as unavailable (mantra-opnca/opnca-r#50). The launcher doesn't
draw it either, because opnca-r is the calculation authority.

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
4. **Render** (choose draft or final) renders that same copy with quartifyr's strict,
   offline render. It keeps the DOCX that quartifyr's result lists, after re-hashing it, in
   `report/builds/<build-id>/` with two records:
   - `build.json` (`publish-build-v1`). It lists the publication, the exact run IDs, the
     hash of every published file, every input (hashed before the render), and the tool
     versions;
   - quartifyr's own `render-result.json`.

   **Download DOCX** serves it only while it still matches its manifest.

A render that fails, leaves placeholders unfilled, misses metadata, runs past its time
limit or is cancelled keeps nothing. The whole process tree is stopped at the time
limit. One report job runs at a time.

Field recalculation (LibreOffice) is not used, so Word may ask to update fields when the
file is opened (docs/spikes/libreoffice.md). Quarto can't render a project inside a hidden
folder, so a study folder must not sit inside one (for example `~/.studies/`).

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
