<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="src/opncafyr/web/static/logo-dark.png">
    <img src="src/opncafyr/web/static/logo.png" alt="opncafyr" width="375">
  </picture>
</h1>

> **Research use only.** This workspace has not been validated for clinical or regulatory
> decisions. A report or deck that builds shows the files were assembled as recorded, not
> that the analysis is right.

> [!WARNING]
> **Under development.** opncafyr and opnca are not yet released. Public installation is
> not supported.

A local, per-user workspace for noncompartmental analysis and reporting. You click an
icon, pick a study folder, prepare a CSV, choose **one analyte**, and run it in
[opnca-r](https://github.com/mantra-opnca/opnca-r). Each run produces a replayable bundle:
input snapshot, R script, settings, results and a hashed manifest. Completed runs can then
be published into a Word report ([quartifyr](https://github.com/jprybylski/quartifyr) +
reportifyr) or a PowerPoint deck ([deckifyr](https://github.com/jprybylski/deckifyr)).

## Guides

- **Website:** [jprybylski.github.io/opncafyr](https://jprybylski.github.io/opncafyr/):
  installation on macOS and Windows (online or offline), a two-analyte walkthrough with
  a practice file, guides and reference.
- **In the app:** the [user guide](docs/user-guide/index.md), under **Guide** in the
  app's left-hand navigation. It ships with the app and works without a network.

## Status

Pre-release. What works today:

- A per-user installer for macOS and Windows that needs no admin rights. It installs
  online or from a verified offline bundle, and it can repair, update and uninstall.
- Study folders; CSV import with cleaning steps that keep before/after counts and a
  rejected-row log; column and unit mapping; and data checks for one analyte.
- One-analyte runs in opnca-r, saved as hashed bundles that are replayed and compared
  before they are marked complete.
- Publishing completed runs' tables and figures, then building a Word report or a
  PowerPoint deck from them.
- The in-app guide and a redacted support bundle.

Still to come: code signing and notarization, an updater with rollback, and a
standard-user test matrix. See [docs/plan.md](docs/plan.md) for the roadmap and
[docs/spec.md](docs/spec.md) for the technical specification.

## How it fits together

```
installer ──► app root (pinned uv/Python, R, Quarto, packages)
launcher  ──► FastAPI on 127.0.0.1:<ephemeral>
               ├── /                 workspace home + guided forms
               ├── /api/...          workspace, jobs, manifests
               ├── /apps/opnca/      managed opnca-r app
               └── /apps/deckifyr/   managed deckifyr editor
              Rscript / uv jobs: cleaning, NCA replay, report and deck builds
```

The core (`src/opncafyr/core`) is tool-agnostic. Tools plug in through a trusted
descriptor plus an adapter (`src/opncafyr/adapters/<tool>`). See
[docs/architecture.md](docs/architecture.md).

## Development

```sh
uv sync                    # create the dev environment
uv run pytest              # tests
uv run ruff check          # lint
uv run ruff format         # format
uv run opncafyr launch     # start the controller and open a browser
uv run opncafyr doctor     # show paths and available tools
```
