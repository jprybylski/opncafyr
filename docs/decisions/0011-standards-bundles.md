# 0011. Standards bundles: imported, verified, pinned study standards

- Status: accepted (owner, 2026-11-12, ADR review). In planning the owner chose: Git authentication on the ADR
  0008 model (the pinned Git only, HTTPS only, an optional token held for one fetch); and
  templates are never copied into every bundle: a bundle may reference a house-style bundle
  **and** a template source in the tool's own layout.
- Date: 2026-11-12
- Issues: #70; builds on #68, #69 (ADR 0010), #32, #33 (ADR 0007), #44 (offline)

## Context

Study teams want to share one versioned set of standards: study profiles (#68), tutorials
(#69) and report and deck templates, in any combination, from a GitHub or GitHub Enterprise
repository or a local copy. ADR 0007 kept the launcher on bundled templates and never let a
tool fetch one (`--from-repo`), because a fetch at run time breaks offline installs. ADR 0010
made profiles and tutorials plain, validated data files, and said #70 could carry them
unchanged.

Two constraints shaped the design:

- **Offline is a supported use case.** Nothing at launch, analysis or publication may need
  the network. A fetch must be something the user asks for, and what it fetched must stay
  usable offline.
- **House styles are shared.** An organisation keeps one report and deck template. Copying it
  into every study-type bundle would mean every change is made many times.

## Decision

1. **Format.** A bundle is a folder with `standards.yaml` (`standards-bundle-v1`): id,
   version, title, optional compatibility ranges (`requires.opncafyr`, `requires.types`),
   components and optional named variants. Each component has a type and either `files`
   (path and SHA-256 of each) or, for a template, a `ref`. Components link to each other by
   id (a profile brings its tutorial and templates). Bundles may be partial. Profiles and
   tutorials keep their own schemas (ADR 0010); nothing is duplicated.
2. **Templates by reference.** A template component may reference:
   - a template component of **another bundle** (a house style), by bundle id, version range
     and an optional hash pin. It resolves only against bundles already imported into the
     study; nothing is fetched because of it;
   - a **tool-native template source**: a repository, ref, subfolder and type in deckifyr's or
     quartifyr's own layout, with optional commit and tree-hash pins. The user imports it
     once, through the same path as a bundle, as its own snapshot. A hash pin also matches a
     local copy, so a pinned reference works offline.
   Template components may be partial: whatever they lack keeps the bundled default.
3. **Explicit, two-step import.** *Inspect* reads a source into a staging folder, verifies
   every file (hash, regular file, plain relative path, no link, size and count caps,
   allowed suffix per type, links, compatibility, the type's own content checks) and returns
   a preview; *install* applies that exact preview. Nothing is written to the study's
   visible folders or records before every check passes, and a failed import changes
   nothing.
4. **Snapshots in the study.** A verified bundle is kept read-only in
   `.opncafyr/standards/<id>/<version>-<hash>/` (a tool-native template in
   `template-<tool>/<hash>/`) and recorded in `.opncafyr/standards.json`
   (`standards-state-v1`) with its source and exact commit. Paths are study-relative, so a
   container and a native install read the same record. A new version is a new snapshot;
   earlier ones stay. The snapshot is re-hashed before every use.
5. **Never over a local edit.** Profile and tutorial files are copied into `profiles/` and
   `tutorials/`. An unedited copy from an earlier import is updated; a locally edited file,
   or one the study made itself, is a conflict the user resolves by keeping theirs or
   installing the new version beside it, with a diff. Nothing is chosen for them.
6. **Templates through the adapters.** A study *selects* a report or deck template. New
   setups use it: report files are copied from the snapshot (a `style.yaml` may be partial:
   it is rendered as `--override` over quartifyr's default `--style`); `deckifyr init
   --from-dir` runs on the snapshot (never `--from-repo`). Bindings and build manifests
   record the template (snapshot, component and its hash, the bundle or tool, the source and
   commit) as an optional, additive `standards` object. Builds still pin exact run IDs and
   file hashes.
7. **An existing project keeps its template.** A selection never changes a report or deck
   already set up, and re-binding it to another publication doesn't either; to use a new
   template, the user sets up a new project. The UI says so where it applies, and only then:
   on the Standards page beside each choice when the study has such a project (a type's
   optional `existing(root)` hook tells core, without core knowing what the project is), and
   on the Report and Deck cards, which template the project uses and when Standards now
   chooses another.
8. **Git on the ADR 0008 model.** A Git source is fetched as a job, only when the user asks,
   with the pinned Git and no user or system configuration, credential helpers, askpass, SSH
   or redirects (`core/gitfetch.py`). A token, if given, reaches Git as an HTTP header in the
   environment of the one `git fetch` process and is forgotten when the job ends: it is never
   in a command line, URL, record, log or bundle. Blobs are read from the fetched commit byte
   for byte (no checkout, filters or links). Without the pinned Git, only local copies can
   be imported.
9. **Tool-agnostic core.** Core verifies, stores and copies; component types come from core
   (tutorials) and packs (`WorkflowPack.standards`, `StandardsType`). A type declares its
   suffixes, a content check, an optional study folder to copy into, and an optional tool
   whose own template layout it accepts. Core names no tool.

## Consequences

- ADR 0007's "never `--from-repo`" stands: tools still never fetch. Its "configurable template
  path" is this selection. Without an imported template nothing changes.
- New offline touchpoint R20 (`docs/offline.md`): the user-invoked Git fetch. Local folders
  and archives make no network attempt; the offline CI flow imports one.
- deckifyr's `init --from-dir` copies only the design and layouts files; the launcher copies
  a template's other files itself (temporary, tested) until deckifyr does (upstream issue in
  `docs/upstream.md`).
- Import is guidance and formatting only. It never adopts a profile, changes a setting,
  starts a run or validates anything.

## Revisit

- If teams need SSH or credential-manager access: that would relax ADR 0008 and needs its own
  decision.
- If an existing report or deck project should switch templates in place, with a
  replace-or-keep prompt per file.
