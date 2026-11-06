# 0006. Home UI: static HTML and vanilla JavaScript, no build step

- Status: accepted (owner, 2026-11-05, plan review); amended 2026-11-06 for #50 (theme)
- Date: 2026-11-05
- Issue: #18 (option A, without htmx); the build continues in the follow-up UI shell
  issue, #49

## Context
The home page at `/` is where a user picks a workspace, starts a guided analysis or
report, reviews artifacts, watches jobs, opens child apps and quits (`docs/spec.md`).
Today it is one file, `src/opncafyr/web/static/index.html`. `core/app.py` `home()` reads
it with `importlib.resources`, puts the session secret into a `<meta>` tag through the
`{{OPNCAFYR_SESSION}}` placeholder, and sets an HttpOnly, SameSite=Strict cookie for
`/apps/`.

The stack has to respect three invariants from `CLAUDE.md`:
- **Offline-capable:** nothing at runtime may need the network. No CDN assets or web
  fonts, and the wheel and offline bundle must ship the UI ready to serve.
- **Reusable core:** core templates and scripts must not name tools.
- **Security:** state-changing requests carry the `x-opncafyr-session` header
  (`core/security.py`).

## Decision
**A static HTML shell, vanilla JavaScript ES modules and one CSS file. No build step.**

- **Stack:** everything lives in `src/opncafyr/web/static/`. No Node, no bundler, no
  third-party runtime JS. Any library vendored later gets a NOTICE entry with its
  version, licence and SHA-256, and a test checks that entry against the file.
- **Serving:** a Starlette `StaticFiles` mount at `/static/`. Every asset URL is
  relative, so the page still works if a base path is added later (for example cloud
  container mode, #42). The session `<meta>` and the saved theme on `<html data-theme>` (#50) are
  the only server-side substitutions.
  No Jinja2. Home keeps `cache-control: no-store`.
- **Data from JSON APIs, not server-rendered fragments:**
  - `/api/tools` for tool tiles. No tool names in core HTML or JS; a test checks this.
  - `/api/apps` for child-app state.
  - `/api/jobs` (added by #12), polled only while
    `document.visibilityState === "visible"`.
  - Heartbeat and Quit (#14).
- **Session header:** an `api.js` fetch wrapper reads the secret from the `<meta>` tag
  and adds `x-opncafyr-session` to every state-changing request.
- **Research-use-only banner:** always visible, on every view.
- **Accessibility, target WCAG 2.2 AA:**
  - landmarks and a skip link;
  - native buttons, links and `<dialog>`, not custom widgets;
  - visible focus;
  - `aria-live="polite"` on the job panel;
  - status never shown by colour alone;
  - system fonts and `prefers-color-scheme`, with a System / Light / Dark override
    (`data-theme`, #50).
- **Checks:**
  - `axe-playwright-python` (bundles axe-core, pinned through `uv.lock`) in a dev/test
    dependency group;
  - a Playwright keyboard walk: Tab reaches every control, in order;
  - a lightweight Ubuntu CI job with Chromium. It needs no R and no child apps. (The
    existing macOS `integration-proxy` job already installs Playwright Chromium.)

## Rejected options
- **A with htmx:** htmx is a vendored asset, and it needs HTML-fragment endpoints, so
  server templates and a new Jinja2 dependency. That is little gain for a small page.
- **B, React + Vite (as deckifyr uses):**
  - adds a Node toolchain for developers;
  - built bundles must be committed, because neither the wheel nor an offline install
    can run a Node build. deckifyr commits a ~572 KB JS bundle, built into
    `inst/python/deckifyr/web/static` by `web/vite.config.ts`, and no deckifyr CI job
    rebuilds or checks it, so it can drift from source;
  - needs a new CI job with Node;
  - a bundle is harder to audit than the source it came from.
- **Precedent for plain JS:** opnca-r's `inst/www` is plain IIFE scripts with vendored
  plotly and its own `aria-live` status region. Plain JS scales to a real app.

## Revisit
- When the Gate 2 cleaning, mapping and preview screens (#21–#24) need heavy client
  state. Those screens belong to the workflow layer (`src/opncafyr/workflows/nca`), not
  core. A different choice there needs a new ADR, and it must still meet the offline and
  no-CDN rules.

## Consequences
- No frontend toolchain in CI or in the release path. The wheel ships the files as they
  are in the repo.
- The follow-up UI shell issue (#49) builds the shell on this stack.
- Tests guard two rules: no tool ids appear in core static files, and every asset URL is
  relative.
- Any vendored library adds a NOTICE entry and a checksum test.

## Amendment, 2026-11-06 (#50)
- The home page has a theme setting: System, Light or Dark. It is saved in the
  launcher's settings file (`settings-v1`, optional `theme`), not in browser storage:
  the controller binds a new port each launch, and browser storage is per origin, so it
  would forget the choice every launch.
- `home()` writes the saved value into `<html data-theme="…">`, taken only from that
  fixed list, so the first paint is already in the right theme. "system" matches no
  override rule, so the OS preference applies.
- Fonts stay system fonts; nothing new is vendored.
- `tests/test_design_tokens.py` checks the colour tokens in `app.css`: both dark blocks
  agree, and every listed text/background pair meets WCAG 2.2 AA.
