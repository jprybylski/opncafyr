# 0013. Embedded guidance: tutorial highlights inside a child app

- Status: accepted (owner, 2026-11-17). The owner chose: target ids declared in each tool's
  descriptor and confirmed by the running app; read-only conditions included in v1.
  Amends ADR 0010 §6.
- Date: 2026-11-17
- Issues: #77 (follows #69 and #74)

## Context

A tutorial (ADR 0010) highlights launcher controls marked with `data-target`. Inside an
embedded app (opnca-r, deckifyr) it could only give text directions at the toolstrip.

The child frames are same-origin, so the launcher could technically query a child's DOM.
ADR 0010 rules that out, because a child's markup is not a stable interface and the
launcher must not act inside a child app. The child should instead draw its own highlight
on request, through a small fixed contract that each app adopts upstream.

Two facts limit the design:

- **The guidance catalog is fixed at start-up.** It validates tutorial and profile files,
  but children start later, per session, and may be stopped.
- **Core is tool-agnostic** (ADR 0002). No tool id may appear in core code or the core page.

## Decision

1. **Markers.** The child marks a few controls with `data-target="<tool id>.<name>"`, in
   its own namespace and with the launcher's id grammar, such as `opnca.export.save`.

2. **Declared in the descriptor, confirmed at run time.**
   - **Declared.** A tool's `descriptor.yaml` lists the ids it supports for the pinned
     version under `web_app.guidance` (`targets` and `conditions`, each with an id and a
     title). `build_catalog` adds them to the catalog with `app: <tool id>`, so a tutorial
     naming them validates whether the app is running or not. The id must start with the
     tool id, which also limits tool ids that offer guidance to the dotted-id grammar (no
     underscores).
   - **Confirmed.** The child's reply decides what happens at run time.
   - **Drift check.** The child also lists its ids in its health response
     (`guidance: {version: 1, targets: [...], conditions: [...]}`). Only an integration
     test against the real tool reads that list. It checks that the descriptor declares
     nothing the pinned app lacks.

3. **Messages.** They are plain objects. The child ignores anything else.
   - **Parent to child:** `{type: "guidance", v: 1, op, id, seq}`.
     - `op` is `highlight`, `clear` or `condition`.
     - `id` is a declared id, or absent for `clear`.
     - `seq` is an integer.
   - **Child to parent:** `{type: "guidance-reply", v: 1, op, id, seq, state}`.
     - For `highlight`, `state` is `"shown"` or `"absent"`.
     - For `condition`, it is `true` or `false`.
     - For an id the child doesn't know, it is `"unknown"`.
     - `clear` gets no reply.
   - **What never travels:** a message carries no HTML, script, selector, URL or text for
     display.

4. **Origins.**
   - The launcher posts only to a frame it created, with `targetOrigin` set to its own
     origin. It accepts a reply only when the reply comes from one of its own frames'
     windows and from its own origin.
   - The child accepts a request only when `event.origin === location.origin` and
     `event.source === window.parent`. It replies to `event.source` with the same target
     origin.
   - Only one core file, `web/static/js/framebridge.js`, calls `postMessage`. A test checks
     that.

5. **What the child does.**
   - **`highlight`:** puts its own ring on the first marked element that is visible (an
     element with a `hidden` ancestor counts as absent) and scrolls it into view.
   - **`clear`:** removes the ring.
   - **`condition`:** reads the child's own state.
   - **What it must not do:** move focus, change view, click, submit or change state. A
     highlight is visual only, and the launcher's tutorial panel says the same in its
     live region for screen-reader users. The ring respects forced colours and reduced
     motion.

6. **Fallbacks.**
   - **App not open or stopped:** the step gets the existing "isn't on the page right now"
     recovery.
   - **`absent`:** the same recovery.
   - **No reply within about a second** (an older app without the hook): the step says
     that this version of the app can't show highlights and that the user should follow
     the directions. "Continue without highlight" works as before.
   - A condition the child doesn't answer counts as unmet. The step's Skip still works.

Version 1 ids:

| App | Targets | Conditions |
|---|---|---|
| opnca-r | `opnca.nav.export` (the Export view's rail button), `opnca.export.save` (Save to run folder) | `opnca.run.saved` (the current results are saved to the run folder) |
| deckifyr | `deckifyr.session.save` (Save), `deckifyr.nav.build` (the Build tab), `deckifyr.build.run` (Build) | — |

A new id, or a breaking change to the message shape, needs a child release, a descriptor
change, and, for the shape, `v: 2`.

## Consequences

- **Steps that work inside an app.** A tutorial step can point inside opnca-r or deckifyr
  and wait on an opnca-r save. It still has to carry full text directions, because an
  older app or a stopped one can only show the text.
- **Adding a tool.** A new tool offers guidance with three things: markers and a listener
  in its own page, ids in its descriptor, and the health list. Core navigation and the
  workspace schema are unchanged (`docs/adding-a-tool.md`).
- **Pinning a new version.** It means checking the health list against the descriptor.
  The integration test does that.
- **Upstream issues.** These are recorded in `docs/upstream.md`.

## Rejected options

- **Reading or styling the child's DOM from the launcher.** Same-origin makes it possible,
  but a child's markup is not an interface, and it would let the launcher act inside the
  app.
- **A catalog read only from the running child's health.** A tutorial naming a child id
  could not be validated while the app was stopped, and the catalog would change during
  a session.
