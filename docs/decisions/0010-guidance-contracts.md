# 0010. Guidance contracts: targets, conditions, tutorials and study profiles

- Status: proposed (2026-11-12). The owner chose, in planning: shared contracts first;
  user-authored YAML with in-app validate and preview (no in-app editor yet); the
  tutorial engine as a generic core capability; study profiles in the NCA pack; study
  copies in visible `tutorials/` and `profiles/` folders.
- Date: 2026-11-12
- Issues: #68 (study workflow profiles), #69 (tutorial walkthroughs)

## Context

Two requests need the same three things:

- **#68**: study teams arrange the existing NCA steps into a named journey, such as dose
  escalation, fed/fasted or DDI, with checklists, review checkpoints and explicit
  multi-run grouping.
- **#69**: someone learning the app gets short directions with a highlight on the control
  to use, while doing the work themselves.

Neither may become a second way around validation, the one-analyte gate or the security
boundary. The home page had none of the three things both need:

- **Stable names for controls.** Only card sections had stable ids. Controls got
  render-order ids such as `f-3`.
- **Milestones.** There was no way to ask "is a run completed?" without new API calls.
  Page state lived inside each module.
- **A safe way to read a user-written file** that names those things.

Core must stay tool-agnostic (ADR 0002). The home page runs no inline code and loads
nothing from elsewhere (ADR 0006). Workspace files hold data and options only.

## Decision

1. **Targets.** A target is a control or card marked `data-target="<id>"`. Ids are dotted
   and start with their owner:
   - `shell.` for core;
   - the pack id for a pack, such as `nca.mapping.save`.

   Core declares the shell's targets. A pack declares its own in `WorkflowPack.targets`.
   The shell marks every pack view's sidenav link as `shell.nav.<view id>`. A test checks
   that every declared id is marked in the page assets.
2. **Conditions.** A condition is a read-only milestone, such as `nca.run.completed`. The
   page that knows it registers a function over its own in-memory state, and guidance only
   asks whether it holds. A test checks that no condition function calls an API helper,
   changes `location`, or emits events. Packs declare theirs in `WorkflowPack.conditions`.
3. **One catalog.** `core.guidance.build_catalog` joins the shell's and the packs'
   targets, conditions and view ids at start-up, and refuses duplicates and misplaced
   owners. `GET /api/guidance` lists it.
4. **Guidance files are inert data.** Tutorials (`tutorial-v1`) and study profiles
   (`study-profile-v1`) are YAML that the user writes. They are read through
   `core.guidance.parse_guidance`, which:
   - caps the size at 256 kB;
   - reads every value as text (a `BaseLoader`: no dates, numbers or tagged objects), with
     aliases refused;
   - validates against a closed schema;
   - names problems by place and rule, never by value.

   A file may name a target, a condition or a view from the catalog, and nothing else:
   no HTML, script, URL, endpoint, path or command. Text is shown with `textContent`.
5. **Where files live.**
   - Examples ship read-only with a pack (`WorkflowPack.tutorials`, and the NCA pack's
     `profiles/`).
   - A study's own copies are in the visible `tutorials/` and `profiles/` folders. They
     are read through `resolve_in_root` with plain `.yaml` names only.
   - Copying an example never overwrites a file.
6. **Tutorials are a core capability.** The engine (`web/static/js/tutorial.js`) only reads:
   - It highlights a target, shows directions and waits for conditions.
   - Its only write is its own progress, to a per-user state file
     (`tutorial-progress-v1`). Browser storage is lost every launch (ADR 0006 amendment).
   - It never submits a form, never calls a workflow endpoint and never picks an option.
   - Inside an embedded app it gives instructions at the launcher's toolstrip only. Hooks
     into opnca-r or deckifyr would be a separate upstream request.
7. **Study profiles are NCA-pack data.**
   - **Step types:** instruction, checklist, action, run slot, review checkpoint and
     publication. A profile has no field for an analyte value, dose, unit, BLQ rule,
     exclusion or comparison, so it can't carry a scientific default.
   - **Progress and slot assignments:** these are in `.opncafyr/study-profile.json`
     (`study-profile-state-v1`).
   - **Edits:** an edited profile file is detected by its SHA-256 and must be adopted
     before progress changes again.
   - **Recording a profile step:** a run or publication records one only when the user
     picks it when creating it, in an optional `workflow_context` (profile id, version,
     SHA-256 and step id) in `run-manifest-v1` / `publication-v1`. A sealed record is
     never changed.
   - **Multi-run grouping:** this is an ordinary publication whose runs equal the
     profile's slots. That is a check, not a new kind of record.

## Consequences

- Adding a control that guidance can point at means a `data-target` and a declared id. A
  removed or renamed id fails the tests, and a user's file that names it gets a clear
  validation problem instead of a silent no-op.
- Completing a tutorial or a profile proves nothing about the science. The user guides say
  so, and opnca-r remains the calculation authority.
- Workspace schema `workspace-v1` is unchanged: profile state is its own record.
- #70 (importing standards bundles) can carry these files unchanged: they are data
  validated by the same loader.

## Rejected options

- **A tutorial engine inside the NCA pack.** It would reach into core's DOM, and other
  packs could not reuse it.
- **CSS selectors in tutorial files.** They are brittle, and they would let a file aim at
  anything on the page.
- **Profile state in `workspace.json`.** It needs a `workspace-v2` migration for
  workflow-specific data that core must not interpret.
