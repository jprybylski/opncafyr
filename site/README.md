# opncafyr documentation website

The source of <https://jprybylski.github.io/opncafyr/>: Jekyll with the
[Just the Docs](https://just-the-docs.com/) theme, built and deployed by
`.github/workflows/docs.yml`. This README is not published.

## Two guides

| | In-app Guide | This website |
|---|---|---|
| Source | `docs/user-guide/*.md` | `site/**/*.md` |
| Where it is read | Inside the app (**Guide**), beside the work | In any browser, before the app is installed |
| Network | Never needed: it ships in the wheel | Online |
| Screenshots | `docs/user-guide/images/` (the source) | The same files, copied in at build time |

Neither replaces the other, and neither is a placeholder. The website may mention the
in-app Guide; the in-app Guide must stay complete and usable offline, so it never depends
on the website. Content can move in either direction: copy verified steps, terms and
explanations, and fit them to the page.

## Layout

| Path | What |
|---|---|
| `_config.yml` | Site settings, search, callouts (`note`, `tip`, `warning`, `research`, `expect`, `fix`) |
| `index.md` | Home page |
| `get-started/`, `guides/`, `reference/` | Sections; each `index.md` lists its pages |
| `_sass/custom/setup.scss` | Fonts (system only) and the `$ocf-*` palette defaults |
| `_sass/color_schemes/opncafyr.scss`, `opncafyr-dark.scss` | Light and dark schemes: the app's own tokens (`src/opncafyr/web/static/app.css`) |
| `assets/css/just-the-docs-opncafyr-dark.scss` | The dark stylesheet, loaded for `prefers-color-scheme: dark` |
| `_sass/custom/custom.scss` | Callout colours, screenshots, home page, focus, reduced motion |
| `_includes/` | Head (favicon, dark stylesheet, scrollable regions), title (wordmark), footer |
| `assets/screenshots/`, `assets/images/`, `assets/example/` | **Copied in by `preview.sh sync`**, never committed (`.gitignore`) |

Shared assets have one source each: the guide's screenshots and practice CSV in
`docs/user-guide/`, and the logo exports in `src/opncafyr/web/static/` (made by
`scripts/export_logo.py`).

## Writing pages

- Front matter: `title`, `parent` (and `grand_parent` under the walkthrough), `nav_order`,
  `description`, and a `permalink` with a trailing slash.
- Every internal link and image goes through `relative_url`:
  `[Install on Windows]({{ '/get-started/install-windows/' | relative_url }})`. The site
  lives under `/opncafyr/`; a raw `/…` link breaks there.
- Screenshots use `<figure class="shot">` with the image's `width`/`height`, `loading="lazy"`,
  alt text that says what matters in the picture, and a short caption.
- Task pages: numbered actions, bold labels exactly as on screen, then `{: .expect }` (what
  you should see) and `{: .fix }` (if something goes wrong) callouts.
- Write for a new user. Define jargon the first time; no internal issue numbers.
- Describe only what the shipped app and installer do. Mark anything planned as not
  available yet, with no instructions for it.
- **Research use only.** Use the app's banner wording; never suggest clinical or
  regulatory validation. A built report or deck shows the files were assembled, not that
  the analysis is right.
- Publish only reviewed, made-up example content: no study data, identifiers, local user
  paths, secrets, private repository URLs or internal-only design documents.

## Preview and build

Needs Ruby 3.1 or newer (macOS: `brew install ruby`, then put
`/opt/homebrew/opt/ruby/bin` first on `PATH`) and, for the browser check, `uv`.

```sh
site/preview.sh            # http://127.0.0.1:4000/opncafyr/ with live reload
site/preview.sh build      # build into site/_site and check links, anchors, images, alt
uv run --group ui python scripts/site_smoke.py site/_site    # the browser smoke check
```

Gems are locked in `Gemfile.lock` (with Linux platforms for CI) and installed into
`site/vendor/`. To update them: `bundle update` in this folder, then
`bundle lock --add-platform x86_64-linux aarch64-linux`, and check the site.

## Checks

`docs.yml` runs on pull requests and on pushes to `main` and `ci/**` that touch the site, the
guide, the app's screens or the installer:

1. **Build** with the Pages base path (`configure-pages`).
2. **html-proofer:** internal links, anchors, images, missing alt text. External links aren't
   fetched, so they never make a build flaky.
3. **`scripts/site_smoke.py`:** serves the build under `/opncafyr/` and opens the home,
   install, walkthrough and troubleshooting pages at 1280x800 and 390x844, light and dark.
   It fails on a failed request (a broken base path), a broken image, alt text that names a
   file, sideways scrolling on a phone, a first Tab that misses the skip link, empty search
   results, a request to any other site, or an axe WCAG 2.2 A/AA violation (contrast
   included).
4. **Deploy** (main only): `upload-pages-artifact`, then `deploy-pages` into the
   `github-pages` environment.
5. **Guide review** (advisory): `scripts/guide_review.py` warns when user-facing files
   changed without changes under both `docs/user-guide/` and `site/`, or when the UI changed
   after the screenshots were captured.

## Keeping both guides current

**Owner:** the maintainer who merges to `main` (today, the repository owner). A change is
not finished until both guides are true for it.

When a change touches any of these, review **both** guides in the same change, or open a
linked follow-up issue before merging:

- screens, labels, messages or workflow steps (`src/opncafyr/web/static/`,
  `src/opncafyr/workflows/nca/web/`);
- the installer, its commands or options (`installer/`, `src/opncafyr/installer/`,
  `src/opncafyr/cli.py`);
- supported platforms or pinned tool versions (`runtime/pins.toml`);
- what artifacts are written or how they behave (runs, publications, reports, decks);
- the support bundle (`src/opncafyr/core/support.py`);
- research-use wording.

Checklist for such a change:

- [ ] `docs/user-guide/` steps, labels and messages match the app.
- [ ] The matching website pages match too (search `site/` for the label you changed).
- [ ] Screenshots: if a pictured screen changed, rerun `scripts/guide_screenshots.py`
      (it needs a full runtime) and commit the new images; the site picks them up.
- [ ] Install, platform and limitation pages match the installer and `runtime/pins.toml`.
- [ ] `scripts/guide_screenshots.py --check`, `site/preview.sh build` and
      `scripts/site_smoke.py` pass.

## Release review

Before each release, against the shipped installer and UI:

- [ ] Install from a clean standard-user account on macOS and Windows, online and from an
      offline bundle, following only the website's pages. Repair, update and uninstall too.
- [ ] Follow the two-analyte walkthrough on the website and in the in-app Guide, from an
      empty folder to a built report and deck. Fix every step that differs.
- [ ] Recapture the screenshots if any screen changed since the last capture (the guide
      review job says so).
- [ ] Read the home page, install pages and limitations for anything that is now out of date
      or claims more than the app does.
- [ ] Check the published site: <https://jprybylski.github.io/opncafyr/>, a deep link, and
      an image.
