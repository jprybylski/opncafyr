> [!WARNING]
> **Under development.** opncafyr and opnca are not yet released. Public installation is
> not supported.

# opncafyr

A local, per-user workspace for noncompartmental analysis and reporting. You click an
icon, pick a study folder, prepare a CSV, choose **one analyte**, and run it in
[opnca-r](https://github.com/mantra-opnca/opnca-r). Each run produces a replayable bundle:
input snapshot, R script, settings, results and a hashed manifest. Completed runs can then
be published into a Word report ([quartifyr](https://github.com/jprybylski/quartifyr) +
reportifyr) or a PowerPoint deck ([deckifyr](https://github.com/jprybylski/deckifyr)).

> **Research use only.** opncafyr assembles artifacts; it does not make them scientifically
> or regulatorily validated.

Using it: the [user guide](docs/user-guide/index.md) (also inside the app, under **Guide**).

## Status

Pre-alpha scaffold. The controller boots, serves `/health` and enforces its loopback
security checks; everything else is planned. See [docs/plan.md](docs/plan.md) for the
roadmap and [docs/spec.md](docs/spec.md) for the technical specification.

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
