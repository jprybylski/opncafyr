# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
- **Tutorials: walkthroughs that point at the app while you work** (#69, ADR 0010). A new
  **Tutorials** page lists the example **Your first analysis** (import, mapping, one exact
  analyte, run review, publication) and the study's own `tutorials/*.yaml`. A docked,
  non-modal panel gives each step's directions, rings the control to use (a guidance target)
  and opens the step's page.
  - **Controls.** Next waits for a read-only condition when a step names one, and Skip always
    works. The panel also has Back, Pause/Resume and Exit tutorial (Escape).
  - **A missing control** gets an explanation, **Look again** and **Continue without
    highlight**.
  - **The tutorial never acts.** The engine (`web/static/js/tutorial.js`) only reads tutorial
    files and writes the user's own place. A test checks it calls nothing but `api/tutorials*`
    and never clicks, submits or sets a value.
  - **Progress** is per user in `<state>/tutorial-progress.json` (`tutorial-progress-v1`), with
    the tutorial's hash and step id and no study path or data. It survives a reload or a new
    launch.
  - The format (`tutorial-v1`) allows plain text and a view, target and condition from the
    catalog, nothing else. Examples are copied into the study to edit and never overwrite a
    file.
- **Study profiles: follow a team's steps for a kind of study** (#68, ADR 0010). A new
  **Study profile** page follows a YAML file in the study's `profiles/` folder. It lists
  instructions, checklists, **Go to …** actions with hints, review checkpoints, run slots and a
  publication step. Three examples ship read-only: dose escalation, fed/fasted and
  drug-drug interaction. Copying one never overwrites a file.
  - **Guidance only.** The schema (`study-profile-v1`) has no field for an analyte, dose, unit,
    BLQ rule, exclusion or comparison, and nothing is chosen for the user. A slot takes only a
    completed run the user picks, each run fills one slot, and a publication step accepts only
    a publication whose runs are exactly the slots' runs.
  - **Progress** is in `.opncafyr/study-profile.json` (`study-profile-state-v1`), per profile
    file, with the SHA-256 of the text followed. An edited file blocks progress until
    **Adopt edited profile**, which keeps the progress of steps that still exist and the
    earlier hashes.
  - **Provenance.** **Start analysis** and **Choose tables and figures** offer **Study profile
    step (optional)**, set to **None**. A chosen step is recorded as `workflow_context`
    (profile id, version, SHA-256, `profiles/` path and step id): optional and additive in
    `run-manifest-v1` and `publication-v1`. A sealed record is never changed.
  - Version history lists `profiles/` and `tutorials/` with the editable sources.
- **Guidance contracts for tutorials and study profiles** (#68, #69, ADR 0010). Controls and
  cards carry `data-target` ids that core (`shell.*`) and the NCA pack (`nca.*`) declare.
  Pages register read-only milestone conditions, such as `nca.run.completed`.
  `GET /api/guidance` lists them, and a test checks that every declared id is on the page.
  `core.guidance` reads user-written guidance files: a 256 kB cap, values read as text only
  (no dates, numbers or tags), no YAML aliases, a closed
  schema, and problems named by place and rule, never by value.
- **Native Linux install: Ubuntu 24.04, x86_64 and aarch64, no root, apt, compiler or
  container** (#65, ADR 0003 amended). It uses opnca-r 0.3.3, which publishes the Linux
  binaries (opnca-r#61); opnca-r is pinned at 0.3.3 for every platform. opnca-r's manifest
  calls the arm architecture `arm64`, where our platform is `linux-aarch64`; one function maps
  it (`installer/platforms.py`). `[platforms].pending` stays as a mechanism and is empty.
  - **Installer.** `install.sh` runs on Linux and reads a ZIP bundle with `unzip`, `bsdtar` or
    `python3`. Preflight checks Ubuntu 24.04, glibc 2.39 or later and not root, then the 36
    shared libraries the stack links (`installer/linux_libs.py`), and names the missing ones
    with the Ubuntu packages to ask an administrator for; it never runs apt. R is Posit's
    `.deb`, unpacked in Python (links refused) and relocated, with `Rscript` replaced by a
    wrapper that names `RHOME`. The library is Posit's noble binaries, pinned per
    architecture with the User-Agent that selects them, and installed one at a time (no
    `make`). `<app root>/bin/opncafyr` starts it. LibreOffice and `repair
    --build-from-source` are refused on Linux. Update, repair, rollback and uninstall are the
    existing code paths.
  - **Launch.** `opncafyr launch --remote` is the native counterpart of `--container`: the
    listener stays on the VM's `127.0.0.1`, reached through an SSH forward or an IDE proxy,
    with a fixed `--workspace` (no folder picker on a headless VM). Every Host, Origin,
    session-secret and `Sec-Fetch-Site` check is the container's, and the tests run for both.
  - **Pins.** `linux-x86_64` and `linux-aarch64` in `runtime/pins.toml` (Linux runtimes are
    pinned once, under `[ci.<platform>]`), 126 R package binaries per architecture in
    `binaries.toml` (the generator fails when a download is not a binary built for its
    architecture), manylinux wheels, dugite-native Git, and opnca-r 0.3.3's Linux binaries
    (hashes checked against the release's `SHA256SUMS` and manifest). Existing platforms'
    pins are unchanged except opnca-r's version. `--allow-pending` (development) installs a
    platform that is still pending, without opnca-r.
  - **Checked** on a developer Mac in Ubuntu 24.04 containers, as an unprivileged user with
    no network, no compiler and no `make`: on arm64 the install ran end to end with opnca-r's
    own smoke calculation, the DOCX and PPTX smoke builds, and the full Gate 2 flow (two real
    analyses, replay, publish, DOCX report, PPTX deck). `installer-linux.yml` runs the same
    legs natively on both architectures under strace: a computer missing one library refused
    by name; install; a no-op second run; repair; the flow; a native launch reached through
    its forward (a foreign Host, a foreign Origin and a missing session secret refused);
    a tampered and a missing asset refused with the old install still running; an update
    killed part-way, then completed; uninstall leaving a workspace; and no connection attempt
    in any trace. Not run: a real firewalled VM.
- **Edit the report's settings and footnotes in the app** (#64). The project-file editor
  now lists two YAML files after the report, as bounded exceptions to "report sources
  only": **Report settings** (`report/config.yaml`) and **Footnotes**
  (`report/standard_footnotes.yaml`). Each shows a short description above it. A save is
  checked in plain words and never refused: not valid YAML (with the line), a setting
  the bundled template doesn't have, a value of the wrong kind, or a changed
  `report_dir_name`/`outputs_dir_name`. **Check** repeats these as `config-yaml` findings;
  **Render** refuses a settings file that isn't YAML or changes the two folder names. The
  figure-size finding (#62) now says where to change it. New report projects get comments
  in `config.yaml`; existing projects keep theirs. Nothing else under `report/` is offered;
  deckifyr keeps its own editor and opnca-r's stays off. Both guides list the files.
- **Edit a saved cleaning step, and findings next to the steps** (#63).
  - **Edit** on each cleaning step opens its form on the saved values and note. **Save step**
    replaces the step in the same place through the usual whole-recipe save and its
    validation, and **Cancel** changes nothing. A refused edit keeps the form open with the
    reason and what was typed. While one step is open, the other steps' buttons wait. Every
    kind of step the page can add is covered (a browser test opens and saves each one back
    unchanged, and the recipe hash stays the same). A step with a combined row condition
    (`all`) or settings the form can't show is not editable; it says why. No new server route.
  - A **Findings** panel under the step adder shows the last check's blocking findings and
    warnings with their hints, for the analyte and specimen and time it was checked. It is a
    native `<details>` (keyboard operable, scrolls inside itself, sticky on wide screens). The
    acknowledgement boxes stay in section 5.
  - Findings are tied to the recipe hash they were checked against. After a change to a step,
    the mapping or the source they are marked **Out of date** and hidden in both places, and
    no acknowledgement carries over (the server already refused one made under another hash).
    Before, a mapping-only change left the findings on screen as if current.
  - The Runs table's last column header, which was empty, is now "Action" (found by the first
    accessibility check of the Analysis page).
- **Container image and cloud container mode** (#42, ADR 0009).
  - `container/Dockerfile` builds slim and full (LibreOffice) images for linux/amd64 and
    linux/arm64 from `runtime/pins.toml` only. Inputs: a digest-pinned Ubuntu base, Ubuntu
    packages locked per platform (`container/apt/`), and opnca-r built from its vendored
    tarball through a BuildKit secret. The install runs with `--network=none` through
    `scripts/offline/install_runtime.sh`, shared with the offline CI job. The final image
    has no compilers.
  - `scripts/container/check_image.sh` runs the full flow inside the image offline and
    checks libraries, toolchain, fonts, secrets and portable paths.
    `.github/workflows/container.yml` builds on native runners and pushes to the
    **private** GHCR package only (never on pull requests, no Actions cache), then tests
    container mode with Docker and Podman.
  - `opncafyr launch --container` with `--access ssh|vscode|workbench|jupyter`: explicit
    options add the forward's or IDE proxy's Host, Origin and URL prefix. No security
    check is relaxed.
  - `container/opncafyr-container` starts, reattaches, links and stops one container per
    study folder, published to 127.0.0.1 only.
  - Study folders on object-storage FUSE mounts are refused; network shares are probed.
  - Manifests record the container's image ID.
  - `[ci.linux-aarch64]` pins; `fetch.py --platform/--opnca-release/--with-libreoffice`.
  - Guides: [container.md](docs/container.md), the user guide's cloud chapter and the
    website's *Run on a cloud computer*.
- **Event records and files without an analyte column** (#58, ADR 0005 Q9). NONMEM-style
  files, with separate dose and sample records, now reach a replayable one-analyte run.
  - The recipe gets an optional `events` block (recipe-v1 `schema_version` 1.1; 1.0
    recipes and their hashes are unchanged). It names the subject, time, record-kind,
    amount, endpoint and route columns, and optionally DV, MDV, occasion, dose unit and
    time unit. It says what each record-kind, endpoint and MDV value means, and holds the
    user's choices.
  - Each choice has no default and is asked only when the data has the case: dosing
    interval (first, last or dose number N); summing same-time dose records; keeping or
    setting aside predose samples; whether a sample at a dose time comes before or after
    it; and rejecting PK samples with no dose.
  - A fixed R stage (`r/nca_events_lib.R`) runs after the cleaning steps in the same
    standalone `clean.R`. It puts every record in one partition: selected, predose set
    aside, outside the interval, dose (used or unused), other endpoint, other record,
    rejected (by choice, to the rejected-row log) or unresolved.
  - It writes `prepared.csv`: the selected PK samples, raw columns unchanged, with new
    `opncafyr_dose`, `_dose_unit`, `_route`, `_dose_time`, `_time_after_dose`,
    `_dose_number` and `_dose_lines` columns. `records.csv` holds every line's partition,
    and `events.json` the counts.
  - Nothing is inserted, summed, dropped or filled in without a choice. A dose's CMT is
    never an analyte, and EVENTU is read per record.
  - Unresolved records give new blocking checks that apply to every analyte:
    `event_kind_unknown`, `event_endpoint_unknown`, `event_mdv_unmapped`,
    `event_flag_conflict`, `event_time_not_numeric`, `event_dose_amount_invalid`,
    `event_excluded_dose`, `event_dose_unresolved`, `event_multiple_doses`,
    `event_same_time_doses`, `event_same_time_doses_differ`, `event_predose`,
    `event_at_dose_time`, `event_unmatched` and `event_time_units_differ`.
  - Mapping must use the prepared time, dose and route columns (`event_mapping_mismatch`),
    and TAU and infusion duration are refused for event records (`event_role_unsupported`).
    An analyte column that keeps two endpoint codes blocks (`analyte_endpoints_mixed`), and
    a mapped nominal time is a warning (`event_nominal_basis`). The nominal-as-actual block
    follows the event time through the prepared column's lineage.
  - Runs prepared from event records keep `input/event-records.csv` and `input/prepare.R`.
    Their `settings.json` (run-settings-v1 1.1) has a `preparation` block with the rules,
    hash, accounting and both files' hashes, and the manifest's lineage has
    `events_sha256`. Other runs are unchanged.
  - The Analysis page's **3. Result** card has an **Event records** panel. It shows the
    suggested layout, columns and EVID meanings with reasons, applied only by an explicit
    action. It also has the code tables with counts and labels, only the choices the data
    needs, where every record went, unresolved lines, and a dose-to-sample preview.
  - For a file with no analyte column, **Declare one analyte** adds a visible
    **Add a constant column** step.
  - API: `GET /api/nca/recipes/{id}/events-help`, `PUT /api/nca/recipes/{id}/events`. A
    steps save keeps the saved events block.
  - Seeded fixtures `nonmem_events.csv`, `nonmem_events_planted.csv` and
    `generic_single.csv` (`tests/fixtures/make_event_fixtures.py`). Unit tests and R-backed
    tests cover every check, the partitions, the standalone script and complete,
    replayed opnca-r runs for an event file and a generic file.
  - Docs: ADR 0005 Q9 (plus Q7 rows, a Q8 amendment and rejected options), the spec,
    architecture, a new in-app guide chapter, the website's cleaning guide and glossary.
- Optional **Version history** (#56, ADR 0008): Git snapshots of files the user chooses
  from the open study folder, in gitneighbr's managed mode (0.2.1) under
  `/apps/gitneighbr/`.
  - A snapshot is not a run, a publication or a build. Those stay fixed, and no Git
    action may change them in the folder (protected paths).
  - The page appears only while a study folder is open. Nothing happens on folder
    selection: no setup, fetch or connection until the user acts.
  - Set-up is explicit. Suggested `.gitignore` rules are offered then, each with a
    reason, and are never written over an existing `.gitignore`. They cover noise and
    files not needed to reproduce results (scratch, rebuildable caches, render logs).
  - Changed files are grouped (records, generated, sources, app files, data). Nothing is
    pre-selected. Files that can identify subjects are labelled. A snapshot holds exactly
    the selected files and leaves everything else, the user's own staging included, as it
    was.
  - Sending is off unless turned on for the session. Send lists every outgoing snapshot
    and file, needs a confirmation (two for labelled files) and pushes exactly what was
    listed. Update fast-forwards only, and never over protected files or local work.
    HTTPS only, with the access token held in memory.
  - A study folder inside another Git repository, or one using Git LFS, filters,
    includes or other unsupported settings, is explained and left unchanged.
  - Both user guides cover it: a chapter with screenshots, an optional walkthrough step,
    the study-folder and files pages, troubleshooting, limitations and the glossary. All
    guide screenshots are regenerated (`scripts/guide_screenshots.py` now captures the
    version history pages too).
  - gitneighbr 0.2.1 is pinned as a cellar R package (`[packages.gitneighbr]`, its
    release tarball and `SHA256SUMS`); its dependencies were already locked. On Windows
    it keeps Git for Windows' line-ending and certificate-store defaults, so folders
    checked out by other Git tools aren't shown as changed and HTTPS works with a
    company's own certificates.
- A pinned portable **Git** (dugite-native 2.56.0, `[optional.git]`), the only Git the
  tools use. It is installed by default and is part of offline bundles. If it can't be
  fetched, unpacked or started, the install still succeeds without version history, and
  `install.json` and the install report list it under `optional`. `--without-git` leaves
  it out. `doctor` reports it.
- Core, tool-agnostic:
  - `web_app.guard_mutations`: the proxy refuses a guarded app's state-changing requests
    (423) while jobs run or other tools have unsaved or busy work, and otherwise holds a
    workspace lease that keeps jobs and project-file saves from starting.
  - A child launched with a `workspace_root` stops when another workspace opens.
  - `WorkflowView.requires_workspace` hides a view's link until a folder is open.
- Runs pass their unit labels to opnca-r (#57). Results in other units are no longer
  labelled with opnca-r's defaults (ng/mL, h, mg).
  - When a run is made, the labels come from its frozen one-analyte rows. Each role's
    declared unit is used, or the one unit its unit column holds across those rows, in
    the spelling table's form. Blank unit cells are skipped.
  - A missing, unknown or mixed unit still blocks the run (ADR 0005 Q2, amended).
    Nothing is guessed, defaulted or forwarded as free text. Labels are metadata: only a
    recorded 'Convert units' step changes values.
  - The labels go to opnca-r in the preset's `units`. opnca-r 0.3.1 shows them read-only
    and refuses requests with other labels. The launcher now also passes
    `--require-explicit-units`.
  - `settings.json` and `manifest.json` record the labels as `analysis_units`, next to
    the declared `units`. The field is optional in `run-settings-v1` and
    `run-manifest-v1`, so older runs still validate.
  - A save fails when opnca-r's `effective_settings.units` aren't the run's labels with
    `units_source: "preset"`.
  - A run prepared before this change won't open in opnca-r (`run_outdated`). Make a new
    run from it.
  - A time unit given as a column is now checked for unknown and mixed values, like
    concentration and dose.
  - Tests cover a run converted from ng/mL to ug/L end to end: the same estimates, every
    concentration unit relabelled, and the replay passes.
- A documentation website (#53), published from `site/` to
  <https://jprybylski.github.io/opncafyr/> by `.github/workflows/docs.yml`.
  - Jekyll with Just the Docs, in the app's own colours and status meanings, light and
    dark. It uses system fonts only and loads nothing from other sites.
  - Pages:
    - Get started: before you install, macOS and Windows installs, opening the app, and a
      four-part two-analyte walkthrough with the practice file.
    - Guides: offline bundles, internal mirrors (for administrators), maintenance,
      provenance, cleaning/BLQ/units, publishing, reports and decks, troubleshooting.
    - Reference: glossary, files, platforms, limitations, technical documents.
  - The screenshots, practice CSV and logo are copied in from the app's own files at build
    time, so the website and the in-app Guide share one source. The in-app Guide is
    unchanged and still works offline.
  - CI builds with the Pages base path and checks links, anchors, images and alt text
    (html-proofer).
  - `scripts/site_smoke.py` opens key pages at laptop and phone widths, light and dark,
    under `/opncafyr/`. It checks loading, images, alt text, sideways scrolling, keyboard,
    search and axe WCAG 2.2 AA.
  - Merges to `main` deploy through `deploy-pages`.
  - `scripts/guide_review.py` warns (advisory) when user-facing files change without both
    guides, or when the UI is newer than the screenshots.
  - `site/README.md` has the owner, an update checklist and a release review. CLAUDE.md
    points to it.
- The woven logo is used everywhere (#54).
  - The macOS `.app` now has an icon (`CFBundleIconFile`), and the Windows shortcuts use
    `opncafyr.ico` instead of Python's icon. A repair adds the icon to an older `.app`.
    The installer CI checks both icons.
  - The launcher has a 32 px PNG favicon next to the SVG one.
  - The README shows the wordmark (with a dark-background variant, `logo-dark.svg`) above
    the research-use notice. It now links both guides, and its status section describes
    what works today.
  - The docs website uses the mark and wordmark.
  - `scripts/export_logo.py` exports every PNG, `.ico` and `.icns` from the SVGs (pure
    Python packing, offline). It also writes `docs/design/logo-sizes.png`, the visual
    check at 16–256 px on light and dark, and records hashes that a test checks.
- `scripts/guide_screenshots.py` captures from a folder under `/tmp`, so the study path in
  the top bar is no longer a per-user temp folder.
- CI's test matrix also runs on `ci/**` branches.
- `repair --build-from-source` (#34): installs afresh with opnca-r built from its vendored
  source tarball instead of the prebuilt binary, for when the binary can't be used.
  - It uses the pinned standalone Rust toolchain and, on Windows, Rtools45 run as the
    user. Both are unpacked into a short, space-free build folder and removed afterwards.
    Cargo runs offline with its home inside that folder, so nothing goes to `~/.cargo`.
  - The previous install stays active until the build and smoke tests pass. The record
    says `build_from_source` and lists the files used.
  - macOS needs Apple's Command Line Tools for it, and preflight says so.
    `bundle --with-source-build` carries the extra files for offline repairs.
  - CI builds it on macOS (offline, from a bundle) and Windows as a standard user.
- The user's own R library is never used (#34).
  - Every R the installer and the launcher start (health probes and deckifyr included)
    sees only the app's library and R's own.
  - A new smoke test, `r_library_isolation`, checks this on every install and repair.
  - The default install refuses to compile anything.
  - CI plants a decoy `opnca` in the user's R library and checks that it never loads
    and that the library is unchanged.
- CI also tampers with the opnca-r binary in a bundle and checks that the install is
  refused and the previous one kept (#34).
- A user guide for scientists (#40), in [docs/user-guide/](docs/user-guide/index.md) and
  inside the app: **Guide** in the side navigation, and "Read the guide" on Home.
  - Chapters cover install, the study folder, data and cleaning, mapping and checks,
    analyte runs, publishing, reports, decks, troubleshooting (with the support bundle) and
    uninstall. A two-analyte walkthrough goes from an empty folder to a report and a deck
    with a practice file that ships with the guide.
  - It opens with the same research-use notice as the app's banner.
  - The app ships the guide (the wheel carries it as `opncafyr/web/guide/`) and shows it
    with a small in-house Markdown reader, offline, building the page from text only.
  - `scripts/guide_screenshots.py` regenerates the screenshots from the fixture flow;
    `--serve` runs the app with an empty study folder to follow the guide by hand.
- The generated cleaning script (`clean.R`) states "Research use only" in its header
  (#40). Its text changed, so a recipe's cleaned result made before this change counts as
  stale: apply the recipe again. Completed runs are not affected. opnca-r's saved
  `analysis.R` says the same since opnca-r 0.3.0 ([opnca-r#53](https://github.com/mantra-opnca/opnca-r/issues/53)).
- A redacted support bundle (#38): **Settings → Support → Create support bundle…** or
  `opncafyr doctor --bundle` (`--preview` prints it without saving).
  - It holds versions, the install's components, smoke results and hash check, tool
    health, the open study's recent job states and exit codes, and the end of the
    launcher, app and installer logs.
  - It never holds data, results, job output logs (only their sizes), study file names or
    the recent-folders list. Paths are shortened, identifier values masked and every
    decimal number replaced, so a concentration in an error message can't get through.
  - You see every part before it is saved, and it is saved on this computer only, in
    `logs/support/`.
  - Workflow packs can name identifier values to mask (`WorkflowPack.sensitive_values`);
    the NCA pack names the mapped subject ID columns.
- A toy second tool, `example_echo`, and a guide to adding a tool,
  [docs/adding-a-tool.md](docs/adding-a-tool.md) (#41).
  - The toy has a job, which summarizes a CSV's columns without writing any cell value,
    and a small web app served under `/apps/example_echo/`. It is found from its
    descriptor alone.
  - Adding it changed nothing under `core/` and left the workspace schema alone; a test
    checks that no file outside its folder names it.
  - Release wheels leave it out (`pyproject.toml`). `scripts/check_wheel.py` checks every
    wheel `make_installer.py` builds, and CI builds one and checks it. A development
    checkout shows its tile.
- A woven concentration-time curve logo, with boxplots and unfinished threads, as
  scalable SVGs and transparent PNGs. The launcher uses the mark in its header and favicon.
- A project-file editor in the launcher, requested by tools through their descriptor
  (#52). The report uses it first: **Edit report** on the report card, or **Edit project
  files** on the quartifyr tile.
  - Source mode: line numbers, find, undo, Save and Discard.
  - Visual mode edits only plain headings, paragraphs and lists. Front matter, chunks,
    shortcodes, divs, placeholders and other syntax stay locked, byte for byte.
  - The preview is approximate and runs nothing.
  - A save is refused if the file changed on disk since it was opened; both versions
    stay on screen until you choose. Saves are atomic and keep file permissions. Paths
    are checked by core: no links, hidden folders, `OUTPUTS/` or files the adapter
    didn't list.
  - Unsaved text blocks idle shutdown, and Quit offers Save and quit or the new
    Discard and quit.
  - Report Check, Render and re-binding wait for unsaved text.
  - Draft or final is now a saved report setting. A render without a status uses it,
    or asks.
  - The build manifest records the settings and their hash.
  - A build is marked out of date once its inputs change.
  - A render copies the project under the editor's lock, so a save never changes a
    render's inputs.
  - Descriptor format: `project_editor` is a new optional key of `tool-descriptor-v1`.
    The adapter supplies a `ProjectEditor` (`core/adapter.py`).
  - A test-only second tool (`toy_notes`) uses the editor without core changes.
  - **New file**, when the tool's adapter allows it. You type a name; the tool sets the
    folder, the suffix and the starting text. An existing file is never replaced.
  - The deck uses it for its Quarto fragments: **Edit fragments** on the deck card opens
    every `.qmd` in `deck/`, and **New fragment** makes `deck/fragments/<name>.qmd`.
    - A save warns about slide breaks or a second top-level heading, which deckifyr's
      strict check rejects.
    - Deck check, build and re-binding wait for unsaved fragment text.
    - A deck build copies the project under the editor's lock.
- The offline CI job and the Installer workflow's bundle install now run the Gate 2 flow
  through the launcher, with the network blocked (#51). `scripts/offline/flow_check.py`
  drives the launcher app in-process, the way the home page does:
  - import the two-analyte fixture, clean it, map it;
  - per analyte: snapshot, save in opnca-r through `/apps/opnca/`, replay, complete;
  - check that the two runs share no rows;
  - publish, render the DOCX report, build the PPTX deck.

  The adapters start every tool and apply the offline settings; the driver runs with a
  clean environment. It replaces the direct tool calls (`import_check.py`,
  `opnca_check.R`, the hand-built report project). The Installer workflow runs it after
  `install --from-bundle`: macOS leg 7b under `sandbox-exec`, the Windows `flow` leg under
  the firewall rule. The flow's deck has figures only until deckifyr#96 (a table with an
  empty cell fails to build) is fixed and pinned.
- deckifyr adapter: set up, edit, check and build a PPTX deck from one publication (#33).
  - **Set up deck** runs `deckifyr init` (the bundled minimal deck, never `--from-repo`).
    It then writes:
    - an "NCA results" `presentation.yaml`: a title slide and one `{rpfy}:` slide per
      published table or figure, data text quoted, no BOM, manifest always set;
    - the bundled footnotes;
    - a copy of the publication's files.

    Rebinding asks before replacing an edited deck.
  - **Open editor** starts `deckifyr serve` on the project under `/apps/deckifyr/`.
    Closing, quitting or idling saves unsaved edits. Check, build and rebinding wait for
    them. The launcher shell now refreshes the app list when a workflow restarts an app,
    so the editor shows as soon as it is ready.
  - **Check** (`deckifyr --json validate --strict`) and **Build** (`build --strict`) run
    from the tools environment with `--trusted-binaries-only` and the pinned Rscript,
    Quarto and soffice. Each uses a staging copy holding exactly the publication's files
    (hash-checked) after the sidecar checks (#30). A validation failure is a readable job
    failure.
  - **Builds**:
    - a build counts only when deckifyr says ok, its manifest exists and the PPTX hash
      matches;
    - previews are taken from the manifest's list, never from a folder scan;
    - `deck/builds/<id>/` holds the PPTX, deckifyr's manifest, the previews and
      `build.json` (`publish-build-v1`: exact run IDs, every file and input hash, the
      deckifyr version, structured warnings);
    - "Download PPTX" serves a build only while it still matches.
  - Slide previews are offered only when a short LibreOffice conversion succeeds in time
    (docs/spikes/libreoffice.md).
  - Decision recorded in docs/plan.md: deck and report projects are trusted user-authored
    projects. Quarto blocks and flextable `.rds` files are allowed; code runs only through
    the pinned binaries.
  - Integration tests against deckifyr 0.3.6 cover:
    - the generated deck validates and builds with sidecar footers;
    - the editor loads under the launcher origin;
    - an unsaved edit blocks a build and is saved when the editor closes;
    - previews are built with real LibreOffice.
- quartifyr report adapter: set up, check and render a DOCX from one publication (#32).
  - **Set up report** lays out `report/` offline:
    - quartifyr 0.3.3's `initialize_report_project_offline()` and the bundled extensions
      (no pyro, uv or network);
    - the bundled template (ADR 0007), quartifyr's reference DOCX, a generated
      `report.qmd` (data text reduced to plain words) and a copy of the publication's
      files.

    The project is bound to that publication (`publish-binding-v1`). Binding it to
    another publication asks before replacing an edited `report.qmd`.
  - **Check** runs the sidecar checks (#30) and quartifyr's `check.R` on a staging copy
    that holds exactly the publication's files, each checked against its hash.
  - **Render** runs `render.R --strict --offline` with the pinned Quarto on that copy:
    - success means exit 0 and status `ok`; the DOCX that quartifyr lists is re-hashed and
      kept in `report/builds/<id>/` with `build.json` (new `publish-build-v1`: the
      publication, exact run IDs, every published file and input hash, tool versions);
    - any failure, partial render, timeout (whole process tree killed) or cancel keeps
      nothing;
    - one report job runs at a time;
    - "Download DOCX" serves a build only while it matches its manifest.
  - Every report job runs with offline uv, app-owned uv and Deno caches, a throwaway git
    identity, and the pinned uv first on `PATH` (docs/offline.md R1-R4, R12, S4, S6).
  - The job manager gains `ok_exit_codes` (a check tool may exit 1 to report findings)
    and `diagnose(ctx, code)` (a tool's own JSON error becomes the job's message).
  - Integration tests render a two-analyte publication with real quartifyr and Quarto.
- Publication curator: completed runs' tables and figures copied into `OUTPUTS/` with a
  publication manifest (#31).
  - The Publish page lists every run. Only completed runs whose replay passed and whose
    sealed files still match can be chosen, and the API enforces the same (409
    `not_publishable`).
  - Each run offers what opnca-r saved. Pre-selected:
    - the parameter summary;
    - the individual listing;
    - individual profiles, linear and log.

    Optional: summaries by group and parameter boxplots. One publication can combine
    analytes.
  - ADR 0007's mean concentration–time figure wasn't saved by opnca-r 0.2.8
    (mantra-opnca/opnca-r#50); with 0.2.9 it is pre-selected too (see Changed). The
    launcher never draws it.
  - "Check" shows names, statuses and sidecar findings without writing anything.
    "Publish" then:
    - copies each file byte for byte to
      `OUTPUTS/<tables|figures>/<analyte>_<run-id>_<artifact>.<ext>`;
    - writes its reportifyr sidecar in Python (meta_type `"NA"`; source is the run's
      `analysis.R`);
    - checks both (#30);
    - records `.opncafyr/publications/<id>.json` last.
  - A published file is never replaced. A name holding different bytes refuses the whole
    publication before anything is written, and identical files are reused. Files from an
    interrupted publish show as "not recorded" and are never used.
  - `publication-v1` is finalised in place (nothing wrote it before). It records:
    - exact run IDs, with each run's manifest and replay SHA-256;
    - the SHA-256 of each source artifact, published copy and sidecar;
    - whether each item shows subject IDs.
  - New user guide `docs/publishing.md`, including why the publishing path has no R
    scripts.
- Checks of reportifyr metadata sidecars before anything is published or rendered (#30).
  - reportifyr only logs a missing or broken sidecar, and the report still succeeds. So the
    launcher checks each table and figure in `OUTPUTS/` first
    (`workflows/nca/publish/sidecars.py`):
    - the sidecar exists, is valid JSON, and matches reportifyr 0.4.0's format (new
      schema `reportifyr-sidecar-v1`);
    - its `object_meta.path` and file type match the file;
    - its BLAKE3 `hash` matches the file's bytes;
    - its `meta_type` and abbreviations are defined in `standard_footnotes.yaml`.
  - Findings use the new `preflight-v1` shape (quartifyr's check-result shape) and name
    files and checks only, never data values.
  - The new Publish page lists them ("Check published files"; `GET
    /api/nca/outputs/check`).
  - The report template's `standard_footnotes.yaml` and `config.yaml` (quartifyr v0.3.3
    demo-report, ADR 0007) are bundled in `adapters/quartifyr/template/`.
  - New launcher dependency `blake3` 1.0.11, pinned per platform in
    `runtime/python/wheels.toml`.
- Replay verification of saved runs (#29).
  - A verified save is replayed at once: `analysis.R` runs with the pinned R and opnca-r
    in a copy of the run, and its tables are compared with the saved results. IDs,
    counts, flags and status must be equal; other numbers agree within a relative
    tolerance (default 1e-10, recorded). The report (`replay-v1`, with a readable list
    of differences) is kept in the run's `logs/`.
  - A run can be completed, and so published, only after a passed replay; "Replay again"
    reruns it. The Analysis page shows the result and the differences.
  - Gate 2's exit test runs both analytes of the two-analyte fixture end to end, through
    real opnca-r sessions and saves, to two sealed, replayed runs with no cross-analyte
    rows.
- Durable save of an analysis into its run folder (#28).
  - The launcher watches the run folder for opnca-r's server-side save (never a browser
    download). A save counts only when `COMPLETE` matches `artifacts.json`, every artifact
    re-hashes, nothing is unlisted or stray, and the record and `analysis.R` name the
    snapshot by hash and relative path. A partial or tampered save fails the run.
  - On a complete save the session stops and the run moves to review with its script,
    record, artifacts, opnca-r's effective settings, versions and exclusions. A reviewed
    run is completed, and sealed, only after its replay passes (#29).
  - Points excluded inside opnca-r replace the launcher's exclusion column; until
    mantra-opnca/opnca-r#49 is fixed such a save fails with a message.
- opnca-r sessions on a run (#27).
  - "Open in opnca-r" starts `serve.R` on the run's frozen input with its preset, the run
    folder as output folder and the run id as storage namespace, so nothing from another
    run's session carries over. Opening another run, or stopping the launcher, stops the
    session and its whole process tree. The embedded page reloads with the new session.
  - The adapter adds `--require-explicit-settings` and `--no-guess-roles` (opnca-r
    0.2.8): no default BLQ method, no role guessed from column names.
  - Confirmed against opnca-r 0.2.8 that CONC on BLQ rows doesn't change results (ADR
    0005, "Still to confirm").
- Run bundles and the final run-manifest-v1 (#26).
  - `run-manifest-v1` is finalised in place: the spec's minimum fields are required, a
    reviewing or completed run must name its script, record, export and artifacts, a
    completed run its replay and seal, a failed run its failure. Lineage records the
    recipe hashes and parent run. The spec's example manifest now validates.
  - `core/runs.py` stages and publishes runs, validates every manifest read and write
    (atomic), moves runs through the job states, and seals completed runs: every file
    hashed into the manifest, then read-only. A sealed run can't change; "New run from
    this" (`POST /api/nca/runs/{id}/rerun`) makes a linked one.
  - `opncafyr runs verify <study> [--run ID]` and `GET /api/runs/{id}/verify` report
    changed, missing and added files.
- Frozen one-analyte input snapshots (#25).
  - `POST /api/nca/runs` checks and gates the selection, then writes
    `.opncafyr/runs/<run-id>/` through a staging folder and one rename: the rows whose
    analyte (then specimen) value equals the chosen text exactly, with `NCA_CONC`,
    `NCA_BLQ`, `NCA_MISSING`, `NCA_EXCLUDED` and the exclusion reason added. BLQ text never
    becomes 0; excluded rows stay.
  - `input/source-fingerprint.json`, the opnca-r preset (`input/opnca-mapping.json`) and
    `settings.json` (new schema `run-settings-v1`) are written with it; the input and
    settings are read-only. `run-manifest-v1` gains specimen, units, acknowledgements,
    row accounting and the settings file hash.
  - BLQ rules and method must be chosen (409 `needs_choice` otherwise). Values that differ
    only in spaces or case are other analytes. The Analysis page lists runs.
- Pre-analysis checks (#24).
  - `workflows/nca/checks.py` runs every ADR 0005 Q7 check (21 blocking, 6 warnings, 4
    info) on one exact analyte and specimen selection of the cleaned data. Findings carry
    physical source lines, never values in logs; subject IDs are registered with the log
    redactor.
  - opnca-r's `check_data_format()` runs on the same selection as an R job; its issues are
    added as info.
  - `POST /api/nca/runs` answers 409 with the findings while anything blocks or a
    warning is not acknowledged. The Analysis page lists findings by level, asks for each
    acknowledgement and keeps "Start analysis" disabled until then.
  - Each planted case of the two-analyte fixture gives exactly its expected findings,
    with and without R.
- Column-role mapping (#23).
  - The recipe gains a `mapping` block: the column for each role, the units of time,
    concentration and dose (a unit column or one declared value, never inferred) and what
    each value of the BLQ, missing and exclusion flag columns means. It is hashed with the
    recipe; cleaning results are keyed on the steps alone.
  - Suggestions ported from makecoolplots (`workflows/nca/roles.py`): alias names plus
    value checks, each with a reason. A weak match or two fitting columns is a
    `needs_choice` question with candidates; nominal time is never suggested as actual
    time. Units are normalised by spelling only; unrecognised routes are listed for an
    explicit recode.
  - `GET /api/nca/recipes/{id}/mapping-help` and `PUT .../mapping`; the Analysis page shows
    each suggestion beside its role and fills in nothing until the user picks it.
  - `tests/fixtures/adpc.csv`, copied from makecoolplots with its generator excerpt.
- Cleaning recipes (#22).
  - `recipe-v1` is finalised in place (nothing had written the draft): ordered steps
    `{op, args, note}`, with `clean_names`, `rename`, `trim`, `parse_type`, `recode`,
    `derive`, `derive_elapsed_time`, `convert_units`, `flag`, `exclude`, `filter_rows` and
    `report_dupes`. Row conditions are a fixed shape, never an expression.
  - `workflows/nca/recipe.py` refuses a step that would set a BLQ or blank value to zero,
    derive a constant zero, overwrite a column (so nominal time can't replace actual time)
    or change the launcher's own columns. Recipes are hashed as canonical JSON.
  - Each recipe compiles to a standalone, readable `clean.R` (the fixed library
    `r/nca_clean_lib.R` plus one call per step; recipe text only as escaped string
    literals), run as a job with the pinned R. Outputs: `cleaned.csv`, `rejected.csv`
    (line, step, reason) and `steps.json` (rows in, out and rejected per step, values
    changed, issues with lines, duplicate groups). The job is accepted only if every step
    accounts for its rows and the source is unchanged. Preview runs the same script on the
    first 200 rows.
  - Workflow packs can add routes, page assets and pages (`WorkflowPack.mount`, `static`,
    `views`; `GET /api/workflows`); the NCA pack's Analysis page edits, previews and
    applies recipes. `core/rscript.py` is the one way R is run (`Rscript --vanilla`).
  - The two-analyte fixture's scenario recipes use the `{op, args}` shape; CI runs the
    R-backed tests in `tests/integration/nca` with the proxy harness.
- Offline installation (#36), as a supported v1 use case
  ([docs/install.md](docs/install.md#offline-installation-for-it-administrators-36)).
  - The bundle builder (`install.sh bundle`, `install.bat bundle`, `opncafyr bundle`)
    fetches every pinned file for a platform from its original source and checks each
    against the pins. It writes a bundle containing:
    - the installer;
    - `bundle.json` (schema `bundle-manifest-v1`): every file with its version, SHA-256,
      source URL and licence;
    - `SHA256SUMS`;
    - an SPDX 2.3 SBOM;
    - a licence inventory read from each package's own metadata.

    `--archive` writes a deterministic zip.
  - `install --from-bundle` (folder or zip) never uses the network. A missing or tampered
    file is refused, naming its component, and the previous install stays active. Updates
    from a newer bundle use the same staging and atomic activation.
  - Internal-mirror mode: `--mirror mirror.toml` rewrites each pinned URL to a configured
    mirror prefix, keeps the pinned SHA-256, and never falls back to the origin.
  - Every install, update or repair writes an installation verification report (JSON,
    schema `install-report-v1`, and printable HTML). It records:
    - versions and hashes;
    - the bundle ID and the payload digest;
    - OS and paths;
    - smoke tests, now including an opnca-r calculation with known answers, a Quarto
      DOCX render with an R chunk and a deckifyr build.

    It is for research use and is not a validation claim.
  - The `Installer` workflow builds each platform's bundle twice and compares the
    manifests. It then installs from the bundle with the user's network blocked
    (`sandbox-exec` on macOS, a per-user firewall rule on Windows) and fails on any logged
    connection attempt. It also checks that a tampered bundle is refused, and that an
    update killed mid-way and then completed leaves the right install active. The bundle
    is kept as a workflow artifact.
- Installer v1 (#19): a per-user installer with no administrator rights
  ([docs/install.md](docs/install.md)).
  - `install.sh` (macOS) and `install.bat`/`install.ps1` (Windows) only fetch and verify
    the pinned Python, then run `opncafyr.installer`. The installer uses the standard
    library only, a rule a boundary test checks.
  - Every pinned file is found in the download cache, an `--assets` folder or an offline
    bundle (`--from-bundle`, never the network), or downloaded with the system `curl`. Each
    is checked against `runtime/pins.toml` before anything is staged, and a missing or
    tampered file is named by its component. The private opnca-r release needs
    `GH_TOKEN` or `--assets`.
  - Each install is staged into its own `versions/<id>`:
    - Python and uv;
    - the launcher and tools environments, from hash-pinned wheels
      (`runtime/python/wheels.toml`, made by `scripts/pin_wheels.py`);
    - R: on Windows, R's own `/CURRENTUSER` installer, which leaves the user's own R
      registration as it was; on macOS, CRAN's `R.framework` relocated to
      `~/Library/opncafyr`;
    - the R library, installed with base R from a local repository and checked against
      `renv.lock`;
    - Quarto, and LibreOffice with `--with-libreoffice`.
  - Smoke tests and a schema self-check run before `install.json` is written. Then the
    `current` pointer flips atomically and the icon is created: `~/Applications/opncafyr.app`
    on macOS, Desktop and Start Menu shortcuts on Windows.
  - A failure leaves the previous install active and writes a redacted support bundle.
  - A second run is a no-op. `repair` re-checks and rebuilds; `uninstall` removes the app,
    R and the icon, never a workspace.
  - `scripts/make_installer.py` builds the payload.
  - The new `Installer` workflow runs it all as a standard user on macOS arm64, macOS
    Intel and Windows Server 2025.
  - Launcher: finds its own install and `runtime.json` from its location; `doctor` shows
    the install; `pythonw.exe` (no console) is handled.
  - New schemas `install-v1` and `payload-v1`. `runtime-v1` gains an optional `wheelhouse`.
  - `runtime/pins.toml` names each component's licence, and `verify_pins.py` checks the
    licences and `wheels.toml`.
  - The opnca-r release checks moved to `opncafyr.installer.opnca_release`;
    `opncafyr.adapters.opnca.release` re-exports them.
- LibreOffice decision (#8): an optional, verified per-user download for deckifyr slide
  previews, pinned in `[optional.libreoffice]` (26.2.7.1, archive URLs). quartifyr's
  field recalculation stays off until quartifyr#84/#86. Report:
  `docs/spikes/libreoffice.md`.
- macOS install spike (#7). `scripts/spikes/macos-install.sh` installs the pinned stack
  as a standard user on macOS 15 by relocating CRAN's R, online or offline, and runs the
  same checks; the `install-spike` workflow (manual, or `spike/**` branches) runs it on
  GitHub's arm64 and Intel runners. Report: `docs/spikes/macos-install.md`.
- Windows install spike (#6). `scripts/spikes/windows-install.ps1` installs the pinned
  stack as a standard user on Windows 11, online or from local files, and runs opnca-r,
  its server, a deck build and a report render. It passed with no elevation, no machine
  changes and, offline, no connection attempts. Report: `docs/spikes/windows-install.md`.
- Offline audit (#44). `docs/offline.md` lists every network touchpoint at install and
  run time with its resolution, including those found by the install spikes. A new
  `offline` CI job installs the whole stack from local files
  (`scripts/offline/fetch.py`, `offline_phase.sh`) and runs the dataset import, an
  opnca-r calculation, a quartifyr render and a deckifyr build in a network namespace
  under strace; `scripts/offline/check_trace.py` fails the job on any attempt to leave
  the machine. Linux CI pins are in `runtime/pins.toml` `[ci.linux-x86_64]`.
- Runtime pins and dependency graph (#5).
  - `runtime/pins.toml` pins uv 0.13.7, Python 3.13.18, R 4.6.2, Quarto 1.11.9, opnca-r
    0.2.7, deckifyr 0.3.5, quartifyr 0.3.2, reportifyr 0.4.0, pyro 0.1.1, Rust 1.99.1 and
    Rtools45 by exact URL and SHA-256 for Windows x86_64 and macOS arm64/x86_64.
  - `runtime/r/renv.lock` locks the app-private R library (130 packages from the Posit
    Package Manager snapshot of 2026-11-06 plus the four tool packages from the cellar),
    and `runtime/r/binaries.toml` pins every package's binary per platform and its source
    tarball. macOS gdtools comes from CRAN, because P3M's build links XQuartz.
  - `runtime/python/tools` locks one Python environment for deckifyr (with the web editor
    and previews), quartifyr and reportifyr, so pyro never creates its own.
  - `scripts/verify_pins.py` checks the pins and locks (now a CI step) and, with
    `--network`, downloads and hashes every file. `scripts/lock_r.R` and
    `scripts/pin_r_binaries.py` regenerate them; `scripts/check_r_restore.R` restores the
    lock into an isolated library. `docs/dependency-graph.md` explains what needs what.
- Dataset scan, preview and import (#21).
  - `GET /api/datasets` lists data files directly in the study folder and in
    `source-data/`, with size, rows, columns and candidate analytes. Nothing deeper is
    read.
  - `POST /api/datasets/preview` returns columns, inferred types with up to three
    counterexamples, missing counts, the row count (estimated and labelled for files
    over 50 MB), the first rows, and the size and SHA-256 of the file. Source files are
    opened read-only and never changed.
  - `POST /api/datasets/import` records the file's path, size and SHA-256 in
    `workspace.json` `sources`, giving later recipes a `source_id`.
  - Formats plug in through workflow packs: `core.datasets.Importer` and
    `DatasetAnnotator`, found by `core.registry.load_workflows()`. The NCA pack adds a
    CSV importer (delimiter sniffing, UTF-8 BOM, `"", NA, ., NaN, NULL` as missing,
    readr-like types, ported from makecoolplots) and an analyte annotator that lists
    exact raw values and flags values differing only by spaces or case.
  - A Data view (`#/data`) in the launcher lists the files, previews one and imports it.
- Launcher visual design (#50), approved by the owner on 2026-11-06.
  - Approved design and screenshots: `docs/design/home-v1/`. Teal accent, a dark
    slate top bar and a light sidebar, the reverse of the tools' own layout. Status
    tones (ok, warn, danger, info) match the tools'. System fonts only.
  - `app.css` holds colour, spacing, radius, shadow and type tokens for light and dark,
    with a contrast table. `tests/test_design_tokens.py` checks every listed pair
    against WCAG 2.2 AA.
  - App shell: a top bar with the open study folder, a live-jobs indicator, a theme
    switch and Quit; the research-use notice and page status lines on every view;
    sidebar views Home, Jobs and Settings (`#/`, `#/jobs`, `#/settings`).
  - Home is a dashboard: a study-folder card, tool cards with capabilities and health,
    recent runs and publications with status pills, and an activity card for jobs
    (message, start time and duration, a moving bar while running, Cancel). The Jobs
    view lists every job.
  - Theme: System, Light or Dark, saved as `theme` in `settings-v1` (an optional,
    additive field). The server renders it on `<html data-theme>` (ADR 0006 amended).
    `PUT /api/settings` now takes either setting on its own, and saving one keeps the
    other.
  - Component reference at `static/components.html` (linked from Settings): buttons,
    pills, fields, status lines, empty state, tables, activity items and dialogs, for
    the guided screens (#21–#24) to reuse.
  - Tools' web apps open inside the launcher (owner's choice, option A): `#/apps/<id>`
    shows the app in a same-origin iframe under a tool strip (Home / name, state,
    Reload, Open in new tab). The sidebar folds into an icon rail and lists every web
    app. Each frame stays loaded while other views are shown, so work in a tool
    survives a visit to Home.
  - The proxy adds `Content-Security-Policy: frame-ancestors 'self'` to every `/apps/`
    response, so only the launcher can frame a tool. The "not running" page's home link
    targets the top window.
  - Using a tool now keeps the launcher open: each proxied request counts as an open
    page for idle shutdown, so auto-close no longer stops the app while someone works
    in a tool in another tab.
- Local-origin hardening finished (#11).
  - The browser gets the session cookie only from a one-time link: the launcher (and a
    second launch that reopens it) opens `/open?token=…`. The token is stored as a
    SHA-256 file name in `state/open-tokens/`, lives 120 s, and works once; `/open`
    sets the cookie and redirects to `/`.
  - The cookie (`HttpOnly; SameSite=Strict`) now covers the whole origin, not only
    `/apps/`. Every request except `/health` and `/open` needs it, so another process
    or user on the machine can no longer read the home page, its secret or the API.
    `Sec-Fetch-Site` is checked everywhere. A page without a session explains how to
    reopen opncafyr.
  - `core/redaction.py`: launcher log records and child-app output in
    `logs/children/` are redacted of registered identifier values and `token=` query
    values; table-like log arguments become a row count. Workflows register values
    when they read a dataset (#21, #25).
  - The boundary test now allows `subprocess` only in `core/proctree.py` and
    `core/picker.py`, and forbids `os.exec*`/`spawn*`/`startfile`, `pty` and
    `asyncio.create_subprocess_*` as well as `shell=True` and `os.system`/`popen`.
  - Tests: missing or invalid cookie or secret → 403, foreign Host → 400, cross-site
    Origin or `Sec-Fetch-Site` → 403, no `Access-Control-*` header on any response,
    more path-escape forms, and subject IDs from the two-analyte fixture never reaching
    the launcher log or a child log.
- Controller core finished (#9).
  - `launch --host` accepts only `127.0.0.1` and exits with an error for anything else,
    `::1` included.
  - `/health` adds `uptime_s`; it still carries no paths or data.
  - The launcher log: JSON lines in `logs/launcher.log` (rotated), with uvicorn's
    loggers routed to the same handlers and a filter hook for redaction.
  - Tests: `launch --no-browser` serves `/health`, two launchers in parallel get
    different ports, and the browser opens exactly once, only after `/health` answers.
- Tool registry and finalized `tool-descriptor-v1` (#13).
  - Descriptors now state `schema_version`, the runtime `executables` they need
    (symbolic names, never paths) and a structured `health` probe; `runtime` may be
    `binary`; jobs may set `timeout_s`; `optional_requirements` name an executable, the
    capabilities it enables and a message for when it is missing.
  - An invalid descriptor stops `opncafyr launch` before it binds a port, with an error
    naming the file and field (`DescriptorError`). Job schema names must be bundled
    schemas, so the deckifyr and quartifyr job entries wait for #32 and #33.
  - `core/health.py` probes each tool once at startup in the background (argv only,
    through `proctree.spawn`). `/api/tools` adds `version`, `health` and
    `missing_requirements`; the home page shows them on each tool tile, and
    `opncafyr doctor` prints them.
  - Tests: invalid fields are named, descriptors inside a workspace are ignored, and
    every bundled stub reports its health.
- ADR 0007 records the templates and the identifiable-data policy (#3).
  - v1 ships one generic NCA report (quartifyr 0.3.2 `examples/demo-report` and
    `org-reference.docx`) and deckifyr's bundled default deck, with an auto-built "NCA
    results" deck. Both templates ship with the install and are never fetched.
  - The curator pre-selects all four artifact types.
  - Coded subject IDs may appear in run bundles and published outputs, never in logs,
    URLs, telemetry or diagnostic bundles.
  - A likely direct-identifier column needs an explicit keep-or-exclude choice.
  - Source files are copied into `source-data/` by default, with a reference-only
    option.
- Home page UI shell (#49, ADR 0006). The page is a static shell, plain ES modules and one
  stylesheet in `web/static/`, served from a `/static/` mount. No build step and no
  third-party code.
  - Sections: study folder, tools, runs and publications, jobs, application (idle
    setting and Quit). The research-use-only banner is in the shell header.
  - Tool tiles come from `/api/tools` (now with `display_name`) and child-app state from
    `/api/apps`; core static files never name a tool, and a test enforces it.
  - The job panel polls `/api/jobs` only while the page is visible, announces state
    changes in an `aria-live="polite"` region and has a Cancel button per live job.
  - `api.js` adds `x-opncafyr-session` to every state-changing request.
  - `/` sends a `self`-only Content-Security-Policy; static files send
    `cache-control: no-cache`. Every asset URL is relative.
  - Accessibility (WCAG 2.2 AA target): landmarks, skip link, native controls and
    `<dialog>`, visible focus, states shown as a word and a symbol, light and dark
    themes.
- `GET /api/workspace/artifacts` (`core/artifacts.py`): a read-only list of the runs
  (`.opncafyr/runs/*/manifest.json`) and publications in the open workspace. A run
  without a valid manifest, or whose manifest names another run, shows as `incomplete`.
  Symlinks are skipped and no file paths are returned.
- Browser checks for the home page (`pytest -m ui`, dependency group `ui` with
  Playwright and `axe-playwright-python`): it renders with stub adapters, makes no
  request off its origin, a Tab walk reaches every control in order, and axe finds no
  violations in light or dark mode. A new Ubuntu CI job runs them with Chromium.
- Launcher lifecycle (#14). `core/lifecycle.py` decides when the launcher stops.
  - The home page sends a heartbeat every 15 s while it is visible
    (`POST /api/session/heartbeat`). Closing the tab sends a best-effort `leave` with
    `fetch(..., {keepalive: true})`; it is only a hint.
  - **Idle shutdown** after 30 minutes by default (ADR 0003) with no visible page, no
    live job and no child reporting unsaved edits or busy work. The home page sets it
    (5 minutes to 2 hours, or never) and it is saved in `state/settings.json`
    (`settings-v1`). `opncafyr launch --idle-timeout MINUTES|off` overrides it for one
    launch.
  - **Quit application** (`POST /api/quit`).
    - Live jobs: the default keeps them running, refuses new jobs and stops when they
      finish. `{"jobs": "cancel"}` cancels them. The page asks first, and *Keep the app
      open* (`POST /api/quit/abort`) withdraws a pending quit.
    - Unsaved edits: the quit is refused (409 `needs_choice`) until the user saves in
      the editor or chooses *Save and quit* (`{"unsaved": "save"}`).
  - Stop order: refuse new jobs, stop the children, close the listener, clear the state
    record.
  - Web-app adapters can report `activity()` (`ChildActivity`: unsaved, busy). deckifyr
    reads `dirty` and `active_jobs` from its `api/health`. A probe that fails counts as
    unsaved, so it never lets the launcher stop on its own.
  - `GET /api/lifecycle` reports pages, live jobs, unsaved and busy tools, the timeout
    and the time left before an idle stop.
- Single-instance launcher (#10). `core/instance.py` holds an OS lock on
  `state/instance.lock` (`flock` on macOS/Linux, `msvcrt.locking` on Windows) for the
  life of the launcher and writes `state/instance.json` (`instance-v1`: pid, pid start
  time, port, instance id, start time, version; no session secret, user-only on POSIX).
  - A second launch that finds the lock held probes `/health` on the recorded port and
    checks the instance id, then opens the browser there and exits 0 without binding.
  - A launch that gets the lock treats any old record as stale and starts. It logs
    whether the recorded pid was dead or alive (a reused pid).
  - A lock holder that does not answer is waited for (20 s), then reported with its pid.
    Nothing is ever killed because a record names it.
  - A clean stop removes the record. `/health` now returns `instance_id` (part of #9).
- Workspace selection (#17).
  - `core/picker.py` shows the native folder dialog from the controller: AppleScript
    `choose folder` on macOS, `FolderBrowserDialog` in Windows PowerShell. Both scripts
    are fixed. The prompt and start folder reach them as argv or environment data. Both
    dialogs can create a new folder.
  - `core/workspace.py` adds `inspect_folder`, `init_workspace` and `open_workspace`.
    - Inspecting shows the path, write permission, item count and state, and writes
      nothing.
    - Initializing creates only `.opncafyr/workspace.json`, which now records
      `app_version`.
    - A workspace with a newer schema is refused, with a message naming its version.
  - The recently used list lives in the app state dir (`recent-workspaces-v1`, paths
    only, at most 10).
  - `core/migrations/` holds the ordered `vN → vN+1` steps. A migration runs only when the
    user agrees, backs up to `.opncafyr/backups/` first, and must validate before it
    replaces the file. A failed step leaves the original untouched. The first step
    upgrades a synthetic schema 0.
  - API:
    - `GET /api/workspace`;
    - `POST /api/workspace/pick`;
    - `POST /api/workspace/inspect`;
    - `POST /api/workspace/init`;
    - `POST /api/workspace/open`.

    The browser cannot name an arbitrary folder: only a folder picked in this session or
    one on the recent list is accepted. Opening a workspace runs the job manager's sweep.
    Switching while jobs run is refused.
  - The home page has a minimal *Study folder* section. Manual test steps are in
    `docs/testing/workspace-picker.md`.
- Two-analyte scientific fixture (#20). `tests/fixtures/make_fixtures.py` is seeded and
  stdlib-only, and writes three files:
  - `two_analyte.csv`: PARENT and METAB on a shared subject × time grid;
  - `two_analyte_mixed_units.csv`: the same rows with mixed PARENT units;
  - `two_analyte.expected.json`: row counts, each planted case with its line numbers,
    the full list of blocking and warning findings under a standard setup, and mapping
    scenarios.

  Each blocking and warning check in ADR 0005 Q7 is planted in the data, triggered by a
  scenario, or marked not plantable (`session_exclusion_no_reason`). The planted cases
  include BLQ as a flag and as `<LLOQ` text, exclusions with and without reasons, exact
  and conflicting duplicates, an invalid row, a subject with no dose, and a second
  specimen. `tests/test_fixtures.py` fails when the committed files differ from a fresh
  run. `.gitattributes` keeps their line endings unchanged on Windows checkouts.
- ADR 0006 records the home UI stack (#18). It is a static HTML shell with vanilla JS ES
  modules and one CSS file, served from `/static/`. There is no build step, no Node and no
  third-party runtime JS. The page uses WCAG 2.2 AA landmarks and controls, and an axe
  check runs in CI. The shell itself is built in #49.
- ADR 0005 records the data conventions (#2):
  - the analyte is filtered on the exact code value, with a separate specimen role;
  - units come from a column or a user declaration, never inferred;
  - BLQ and LLOQ are built by explicit recipe steps, and all three BLQ rules are always
    chosen by the user;
  - every exclusion has a reason;
  - duplicates, undosed profiles and nominal time used as actual time are blocking;
  - a table sets each check's level (warning or blocking) with a snake_case id for #24.
- Job manager (#12) in `core/jobmanager.py`.
  - Each job lives in `.opncafyr/jobs/<job-id>/`:
    - `state.json` (new schema `job-v1`, written atomically);
    - `events.jsonl` (messages written for a person);
    - `stdout.log` and `stderr.log`;
    - `diagnostic.json` when the job fails or is cancelled.
  - Every job must set a timeout. Each pool has a concurrency limit, default 1.
  - Outputs are written to `<target>.staging/`. They are renamed into place only after a
    clean exit and a passing review, and an existing target is never replaced.
  - Cancel kills the whole process tree.
  - Opening a workspace marks jobs left by a dead launcher `failed`, kills their recorded
    tree, and does not promote their staging.
  - `GET /api/jobs`, `GET /api/jobs/{id}?after=N` and `POST /api/jobs/{id}/cancel`.
    Jobs are submitted only from trusted Python code. Stopping the controller cancels
    running jobs; since #14 an explicit Quit keeps them running by default.
  - `proctree.kill_recorded_tree()`.
  - `OwnedProcess.kill_tree()` no longer walks a pid that has already been reaped.
- Proxy compatibility harness (#16) in `tests/integration/proxy/`, run with
  `pytest -m integration`. It runs opnca-r 0.2.7 and deckifyr 0.3.5 from their pinned
  releases behind the proxy and checks:
  - that every page request stays under the prefix (Playwright);
  - an API round trip for each app: opnca-r schema plus calculate, and deckifyr save,
    build and PPTX download;
  - the attacks reproduced in the upstream reviews, both through the proxy and straight to
    the child port;
  - that an orderly stop and a tree kill both leave no processes behind.

  Results are in `docs/spikes/basepath.md`. CI runs the harness in a new macOS 15 job.
  The opnca-r half needs the `OPNCA_R_RELEASE_TOKEN` secret and is skipped with a
  warning without it.
- `opncafyr.adapters.opnca.release` selects an opnca-r asset from `manifest.json` by its
  fields and verifies it against `runtime/pins.toml`, with no network. #34 reuses it.
- Reverse proxy for child apps under `/apps/<tool>/` (#15).
  - Request and response bodies are streamed, with a per-descriptor body limit (413).
  - `/apps/<tool>` redirects to `/apps/<tool>/` with a 308.
  - Before forwarding, the proxy rewrites `Host` to the child's port, drops `Origin`,
    `Cookie` and the session header, and injects the child's token. It strips
    `Set-Cookie` and CORS headers from responses.
  - A crashed child gets a 502 page with a restart button.
  - `GET /api/apps` reports each app's state.
  - The home page sets a `HttpOnly; SameSite=Strict; Path=/apps/` session cookie. It is
    required on every proxied request, along with a same-origin `Sec-Fetch-Site` and our
    own `Origin` on state-changing requests.
- opnca and deckifyr web adapters (start, ready, stop):
  - opnca-r 0.2.7 runs `scripts/serve.R` with `--base-path`, the API and shutdown tokens,
    `--lock-dataset`, `--no-overwrite` and `--require-blq-rules`;
  - deckifyr 0.3.5 runs `serve` with `--root-path`, its API token, a fixed port and
    `--trusted-binaries-only`.
- Child process supervision (#12, #9). `core/proctree.py` spawns argv arrays only, with an
  allow-listed environment (it always drops `OPNCAFYR_*` and the variables that widen
  opnca-r's Host check), and kills whole process trees: freeze-and-walk on POSIX, a Job
  Object on Windows. `core/supervisor.py` starts one child per tool:
  - with a per-child token, a fixed loopback port (retried after a port race) and a
    launcher-owned cwd and TMPDIR;
  - waits for the adapter's readiness probe;
  - stops through the adapter's orderly stop, then a tree kill.
- `core/runtime.py` and `schemas/runtime-v1.schema.json`: the installed runtime record that
  names the absolute paths of the pinned executables.
- Descriptors can set `web_app.max_body_bytes`. The registry finds web-app adapters by
  package scan, and a test checks that `core/` never imports adapters or workflows.
- Project scaffold: loopback FastAPI controller with `/health`, Host/Origin and
  session-secret checks, workspace path canonicalization, job state machine, trusted tool
  descriptor registry, JSON Schemas (tool descriptor, workspace, run manifest,
  publication, recipe), adapter stubs for opnca, deckifyr and quartifyr, installer stubs,
  CI, and the plan of action.

### Changed
- **The website and guides lead with what the workspace solves** (#72). The home page,
  **Before you install** and the README now say what opncafyr brings: one version-pinned
  workspace for NCA, reports and decks, installed per user without administrator rights
  from verified downloads, an IT-prepared offline bundle or internal mirrors, or run on a
  Linux cloud computer beside the data. Where data stays is stated the same way in both
  guides for native and cloud use (the computer that runs opncafyr; nothing sent unless
  version-history sending is turned on). The home page also links the cloud install and
  mentions application controls. Steps that say which machine runs a command are
  unchanged.
- **After Quit, the tab says plainly that opncafyr has stopped** (#73). The working page
  is replaced by a full page headed **opncafyr has stopped**, with the next step (start it
  from the app shortcut). The old navigation, forms and tool links are made `inert` and
  hidden, the title changes, focus moves to the heading, and no further request leaves the
  tab, so no stale "Could not load…" errors appear. The page states only what the server
  confirmed (jobs cancelled, this tab's editors saved, changes discarded).
  - **Finishing work before quitting.** "Keep running and quit when finished" shows a band
    under the top bar with the number of running jobs and each job's progress text, and
    **Keep the app open** until the stop has started. A quit cancelled from another tab
    clears the band; an abort that comes too late shows the stopped page.
  - **Connection lost.** A request that gets no response, with no confirmed Quit, leads to
    a separate **Connection to opncafyr was lost** page with **Check again**, never to
    "has stopped". This also covers tabs that didn't press Quit.
  - Cancelling a Quit dialog, going back to save, or a failed Quit request keeps the page
    and says so. Both user guides describe the new pages.
- Clarified how to build the installer payload from a source checkout before testing
  offline bundle creation, and distinguished the installer zip from a platform bundle.
- opnca-r is pinned at 0.3.1, which releases opnca-r#32, #53 and #54 (0.3.0) and #56–#58
  (0.3.1). Imports are unchanged. flextable, a new suggested package, is already in
  `renv.lock`.
  - **Saved runs.** `artifacts.json` is schema 2, an additive change. A save also holds
    `results/tables/*.rds`, the formatted flextable copies of the CSVs. Replay no longer
    compares a table that opnca-r marks `replay.reproduced: false`, such as the
    bioequivalence tables. `replay.json` lists those under `not_reproduced` with
    opnca-r's reason.
- The publishing curator offers what opnca-r says a run saved, not what file-name
  patterns suggest (#59).
  - A save's `artifacts.json` schema 2 is validated against the schema opnca-r installs
    (`opnca/schemas/opnca-artifacts-v2.schema.json` in the app's R library). A listing
    that breaks it, or a missing schema, fails the save. A problem names the place and
    the rule, never a value.
  - The run manifest keeps each file's schema-2 fields (`id`, `title`, `kind`,
    `identifies_subjects`, `caption`, `footnote`, `replay`, `derived_from` and the rest).
    They are optional in `run-manifest-v1`, so older runs still validate. `kind` is
    opnca-r's, so a formatted `.rds` table is now `table`.
  - For such a run, each item takes opnca-r's title, and "shows subject IDs" comes from
    `identifies_subjects`. Sidecar notes come from the caption, the footnote and the
    mean-profile choices. A figure or table that a later opnca-r saves is offered without
    launcher changes. Ids, and so published file names and ADR 0007's pre-selection, stay
    as they were.
  - Formatted `.rds` tables are offered next to their CSV, as "… (formatted)". They are
    never pre-selected and carry the CSV's identifier flag. reportifyr places them in a
    report, and deckifyr renders them in a deck.
  - A CSV table that the replay doesn't recompute is not offered, nor is its formatted
    copy. The Publish page says why.
  - Runs saved before opnca-r 0.3.1 have no `identifies_subjects` and keep the path
    rules.
  - **API numbers.** opnca-r's API no longer rounds numbers to 4 decimals. The test that
    checks the replay against opnca-r's own results now uses 1e-10.
  - **Roles.** Under `--no-guess-roles`, opnca-r's server refuses a request that omits a
    role (opnca-r#57). The launcher's preset already names every role.
- Adapters can bundle their job schemas (#55).
  - A descriptor's `jobs.<capability>` schema name is now looked up in the adapter's own
    `schemas/` folder, then among the launcher's schemas. A name in both places, or in
    neither, stops startup with the field named. `input_schema` is optional, for jobs
    whose input isn't a JSON document.
  - A `JobRequest` can name one output document (`output_document`) and a check
    (`output_check`, usually the contract's `JobContract.check_output`). The job manager
    checks the staged document before the review, and a problem fails the job without
    quoting values.
  - `example_echo` declares `jobs.summarize_csv` with its bundled `summary-v1` schema and
    a 120 s timeout. Nothing was added to `src/opncafyr/schemas/`.
  - The unused `core.adapter.Adapter` Protocol and its `JobHandle`, `ValidationResult` and
    `Artifact` types were removed. ADR 0002, `docs/architecture.md` and
    `docs/adding-a-tool.md` describe the contract adapters actually implement.
- opnca-r is pinned at 0.2.9 (PR #51), which releases opnca-r#49 and #50. Its R
  dependencies are unchanged, and the installer smoke values are the same.
  - **Exclusions (#49).** A point excluded inside opnca-r is now added to the launcher's
    `NCA_EXCLUDED` rows instead of replacing them, so such a save no longer fails. The
    run keeps the in-app points (by their opnca-r source) with a "no reason recorded"
    warning, and the replay reproduces both sets. The check that fails a save which lost
    the launcher's exclusions stays.
  - **Mean figures (#50).** A run can save `mean-linear`/`mean-log` and
    `results/opnca-mean-profiles.csv` (compared on replay like every results table). The
    curator pre-selects the mean figures as ADR 0007 decided, offers the table as an
    option, and puts the record's choices (statistic, error measure, time basis,
    grouping) in their sidecar notes. A run saved without them says so in its own block,
    replacing the page-wide "not saved by this opnca-r version" note.
- quartifyr is pinned at 0.3.3, which releases quartifyr#81–#86: staged renders,
  `--soffice-bin`, the LibreOffice fixes for macOS and Windows, and
  `initialize_report_project_offline()` for one Python environment serving both passes.
  It was pinned within the release's 24-hour refresh window; re-verify the hashes after
  2026-11-08T00:29Z. The adapter workarounds and the LibreOffice recalculation switch are
  unchanged for now.
- deckifyr is pinned at 0.3.6, which releases deckifyr#93 (offline mode for the R
  wrappers, `init --from-repo` refused offline) and #94 (project files that start with a
  UTF-8 BOM, and no locale-encoding fallback on Windows). Its dependencies are unchanged.
- opnca-r is pinned at 0.2.8 (PR #48), which releases opnca-r#45 (macOS binaries built
  for R's own minimum: `macos_min` 13.0 on arm64, 11.0 on x86_64), #46 (save lock,
  `INCOMPLETE_SAVE`, `--require-explicit-settings`, `--no-guess-roles`,
  `effective_settings` in the record) and #47 (the shutdown reply always arrives whole).
  Its dependencies are unchanged. The macOS install spike now fails on a truncated
  shutdown reply. Our macOS minimum stays 15 and the adapter flags are unchanged for now.
- The README says opncafyr and opnca are under development and that public installation
  is not yet supported.
- Recorded the v1 platform matrix (ADR 0003, #1): Windows 11 x86_64, macOS arm64/x86_64
  (macOS 15 until opnca-r#45), R 4.6.2, a 30-minute configurable idle timeout and no
  native Linux. Also recorded the opnca-r install path (ADR 0004, #4): prebuilt binaries,
  with the vendored source tarball as the repair/exception fallback.
  `runtime/pins.toml` now has the platform table and the R pin.
- Reviewed the merged opnca-r (#23-#30, v0.2.6) and quartifyr (#69-#72) work against the
  issues we filed. `docs/upstream.md` now has a status column and the follow-ups
  (opnca-r #40-#43, quartifyr #75-#78). `docs/plan.md` risks are updated, and
  `runtime/pins.toml` pins opnca-r 0.2.6 assets and the quartifyr commit and tarball.
- Reviewed deckifyr 0.3.4 (#82-#86) and filed follow-ups #88-#91. `runtime/pins.toml` now
  pins the deckifyr 0.3.4 wheel, sdist and R tarball by SHA-256.
- Reviewed quartifyr 0.3.2 (#75-#78) and filed follow-ups #81-#82. `runtime/pins.toml`
  now pins the quartifyr 0.3.2 tag commit, tarball and `SHA256SUMS` by SHA-256.
- Reviewed deckifyr 0.3.5 (#88-#91) and pinned its wheel, sdist and R tarball by SHA-256.
  `upstream-blocked` removed from #33.
- Reviewed opnca-r 0.2.7 (#40-#43) and filed follow-ups #45-#46. `runtime/pins.toml` now
  pins the 0.2.7 binaries, vendored tarball, `manifest.json` and `SHA256SUMS` by SHA-256.
  `upstream-blocked` removed from #27.

### Fixed
- Resolve local bundle and asset folders to absolute paths before staging, so a
  Windows offline install can launch the pinned R installer when given `--from-bundle .`.
  The offline guide also uses an absolute Windows bundle path for older installers.
- **Cloud `-full` walkthrough failures** (#62).
  - The Publications list and the file picker on the Publish page no longer keep an
    error after a later request succeeds. The first request can run before a study folder
    is open; that case now says "Open a study folder…" instead of showing an error, and a
    slow earlier request can't overwrite a newer one.
  - Version history trusts exactly the study folder you opened. In the container the mount
    is often owned by another user, and Git then refused it ("dubious ownership"). The
    launcher now writes one `safe.directory` entry, for that folder's resolved path, into
    its own Git configuration file for the managed gitneighbr only. Never `*`, never a
    parent folder; the entry is replaced when another study opens and the person's Git
    name and email in that file are kept. The container also warns at start when the
    study folder's owner isn't the user running opncafyr, and the cloud container guide
    says how to match them. Not reproduced here: needs a mount with a different owner.
  - **Report: formatted tables and figure width.**
    - A formatted table (`.rds`) that carries its own caption is refused by the report
      Check and Render with a message naming the file; use its CSV, or put it in a deck.
      A fixed R program inspects each file (`r/report_rds_check.R`); only a status is
      written. A table that isn't a flextable, or can't be read, is refused too. The cause
      was confirmed with the real tools (reportifyr 0.4.0, quartifyr 0.3.4): the same table with a
      caption leaves its placeholder unfilled, and without one it is filled. opnca-r
      0.3.2 leaves the caption off (opnca-r#59), so the refusal now means a run saved by an
      earlier opnca-r, and the message says to make a new run.
    - The render error now names the files whose placeholders stayed unfilled, not just
      their number.
    - Figures use the 6 inch default width instead of their pixel size
      (`use_artifact_size: false` in the bundled `config.yaml`); opnca-r's 2000 px PNGs came
      out about 27 inches wide. Check warns about an older project that still says `true`
      and never rewrites it.
    - The report render no longer passes quartifyr's `--strict`. quartifyr 0.3.4 warns when
      a table is still too wide at 7 pt, and `--strict` refuses that: opnca-r's own 22-column
      summary table can't fit a portrait page, so every default report failed (found by the
      offline CI job's first run). The adapter now accepts a `warning` result only when every
      warning is that one and there is no error, unfilled placeholder or missing metadata, and
      records it in the build; any other warning still fails and now says what it was.
  - **opnca-r 0.3.2 and quartifyr 0.3.4 pinned** (`runtime/pins.toml`, `renv.lock`, the
    installer scripts and CI). opnca-r 0.3.2 leaves the flextable caption off the saved
    formatted tables (opnca-r#59). quartifyr 0.3.4 fits wide tables to the page
    (`fit_tables`, on by default) and adds `{{< landscape >}}` and `{{< portrait >}}`
    (quartifyr#88); the guides say how to put a table on a landscape page. Hashes match
    each release's `SHA256SUMS` and `verify_pins.py --network` passes for both. quartifyr
    0.3.4 was pinned within its 24 hour refresh window: re-verify it after
    2026-11-10T19:53Z.
  - **Checked with the real tools** on a developer machine (macOS, R 4.6.1, Quarto
    1.10.18; not the container image): a formatted table saved by opnca-r 0.3.2 has no
    caption and fills its placeholder; the same table with a caption does not. An
    11-column formatted table is fitted to 6.5 inches with a fixed layout, and a 2000 px
    figure is no wider than 6.5 inches. New integration tests cover these. A table too
    wide for a portrait page at 7 pt (14 columns with long header words) makes the strict
    render fail with quartifyr's message; a landscape section for it was not rendered.
  - **A publication of figures only now renders.** reportifyr's table step fails on a
    missing `OUTPUTS/tables`, which the render's staging copy never had when no table was
    published. The staging copy now always has `figures`, `tables` and `listings`.
  - **Deck: formatted tables.** A formatted table (`.rds`) is a `reportifyr` element (a
    picture), as deckifyr requires; as a `table` element it failed with a content error.
    deckifyr's `E_INTERNAL` now reads as an unexpected error in the deck tool, with the
    exception's name, instead of "deckifyr reported an error".
  - **deckifyr 0.3.7 pinned** (`runtime/pins.toml`, the tools lock and `wheels.toml`): a
    table with an empty header or data cell builds, and the cell stays empty (deckifyr#96,
    the `IndexError` from the walkthrough). The wheel, sdist and R tarball match the release's
    `SHA256SUMS`, and `verify_pins.py --network` passes for the section. Decks with
    tables no longer need the figures-only workaround: the offline flow's deck holds tables
    again (the `offline` CI job is the first run of that), and the guides' "known issue"
    text and the walkthrough's figures-only publication are gone.
  - **Revise a publication.** **Revise** on the Publications card ticks that publication's
    files in the curator, so you can add or remove files and publish a new, separate
    publication. It records `revises` (optional, additive in `publication-v1`; also accepted
    by `POST /api/nca/publications`). The earlier publication, its files, and the reports and
    decks bound to it stay as they were; a report or deck changes publication only when you
    choose it there. Files whose run can no longer be published are left out, and the card
    says how many.
- Two offline bundles built from the same pins could differ: the SBOM recorded the
  build time to the second. It now records `SOURCE_DATE_EPOCH`, or the archive's fixed
  entry time (#36).
- opnca-r's server didn't start on any installed copy on macOS, or on Windows when the
  user name has a space: "'R_TempDir' contains space". The launcher gave its tool
  servers a temp folder under the app folder (`~/Library/Application Support/...`). Tool
  servers now get the same space-free temp folder as R jobs (#12), removed when they stop.
  Found by the Installer workflow's Gate 2 flow (#51).
- R jobs in a study folder whose path has a space (for example "PK Study") stopped at
  once with "'R_TempDir' contains space". Their temp folder was inside the study folder.
  Such a job now gets a private temp folder in the system temp folder instead, which is
  removed with the job (#12). This affected cleaning, the data-format check, replay and
  report jobs.

