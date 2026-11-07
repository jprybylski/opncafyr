# Dependency graph

**Updated:** 2026-11-06 (#5). Exact versions and checksums live in
[`runtime/pins.toml`](../runtime/pins.toml); this page says which component needs what
and why. Edges come from each tool's DESCRIPTION / pyproject at the pinned release.

## Overview

```
launcher (opncafyr)        uv 0.13.7 ─► Python 3.13.18 ─► launcher venv (uv.lock)
 │
 ├─ opnca adapter ────────► R 4.6.2 ─► private library (runtime/r/renv.lock)
 │                                       └─ opnca 0.2.9 (binary, cellar)
 │                                          + R6, plumber2, httpuv, reqres, jsonlite, processx
 │
 ├─ deckifyr adapter ─────► tools venv (runtime/python/tools/uv.lock)
 │                            └─ deckifyr 0.3.6 [web, preview]
 │                          optional: Rscript + flextable (.rds tables), Quarto (Quarto
 │                          elements), LibreOffice (slide previews, #8)
 │
 └─ quartifyr adapter ────► R 4.6.2 ─► quartifyr 0.3.3 ─► reportifyr 0.4.0 ─► pyro 0.1.1
                            Quarto 1.11.9 + extensions bundled in quartifyr
                            tools venv as QUARTIFYR_PYTHON_ENV (python-docx, PyYAML, Pillow)
                            optional: LibreOffice (field recalculation, #8)

repair path only (ADR 0004): Rust 1.99.1 (+ Rtools45 on Windows, Command Line Tools on
macOS) to build opnca_0.2.9-vendored.tar.gz
```

## Components

| Component | Needs | Pinned as | Notes |
|---|---|---|---|
| Launcher | uv, Python 3.13 | `[uv]`, `[python]`, `[python_envs.launcher]` (repo `uv.lock`) | fastapi, uvicorn, httpx, pydantic, jsonschema, pyyaml, platformdirs, psutil, blake3 (reportifyr sidecar hashes, #30) |
| opnca-r 0.2.9 | R ≥ 4.2 (we pin 4.6.2); R6; for the web app (`scripts/serve.R`): plumber2 ≥ 0.2.0, httpuv, reqres, jsonlite, processx | `[packages.opnca-r]`: `manifest.json`, one binary per platform, vendored source tarball | Binary chosen from `manifest.json` by fields (`opncafyr.adapters.opnca.release`). Its web app packages are only `Suggests`, so `scripts/lock_r.R` adds them. Source build needs Cargo, rustc ≥ 1.87.0, xz. |
| deckifyr 0.3.6 | Python ≥ 3.10; python-pptx, pydantic, PyYAML, Pillow; extras `web` (fastapi, uvicorn) and `preview` (pymupdf) | `[packages.deckifyr]` wheel; tools venv | Calls Rscript (flextable `.rds`), quarto and soffice. The adapter passes absolute `--rscript-bin`, `--quarto-bin`, `--soffice-bin` with `--trusted-binaries-only`. `deckifyr build` needs none of LibreOffice. Its R wrapper package is not installed. |
| quartifyr 0.3.3 | R ≥ 4.1; cli, jsonlite, openssl, quarto (R pkg), withr, processx, pyro, reportifyr, yaml; Quarto ≥ 1.6.36; Python: python-docx, PyYAML | `[packages.quartifyr]` source tarball (pure R) | Both Quarto extensions ship inside the tarball (`inst/extensions/`): `quartifyr` (GPL-3.0-or-later) and an unmodified copy of `A2-ai/quarto-plus` at be6ac44 (MIT). Offline init uses `QUARTIFYR_PYTHON_ENV`; render takes `--offline` (quartifyr#72, #76). LibreOffice from `--soffice-bin` / `QUARTIFYR_SOFFICE_BIN`, else `PATH` (quartifyr#83). |
| reportifyr 0.4.0 (external) | R ≥ 4.1; assertthat, cli, digest, dplyr, flextable, fs, pyro, gert, ggplot2, here, jsonlite, log4r, officer, processx, purrr, rlang, this.path, tictoc, xml2, yaml; Python: python-docx, PyYAML, Pillow | `[packages.reportifyr]` source tarball from its GitHub release (pure R) | Never modified. r-universe keeps only the latest build, so it is pinned to the release tarball. Runs `uv run -m reportipyr.cli` without `--no-sync`: see [offline.md](offline.md). |
| pyro 0.1.1 (external) | R ≥ 4.1; here, log4r, processx, rlang | `[packages.pyro]` source tarball (pure R) | Would curl-install uv and run `uv lock`/`uv sync` if asked to build a Python env; we never ask (the tools venv is passed in). Its bundled spec pins python-docx 1.1.2, PyYAML 6.0.2, Pillow 11.1.0 and needs Python ≥ 3.12. |
| R 4.6.2 | — | `[r]` CRAN installers | Windows: R's Inno Setup installer in `/CURRENTUSER` mode (#6). macOS: the `.pkg` payload is expanded and relocated to a space-free R root (#7). rig's user mode works without admin but downloads its own unpinned installer, so it isn't used. |
| R packages | — | `[r_packages]`: `runtime/r/renv.lock` + `runtime/r/binaries.toml` | 130 packages: 126 from the P3M snapshot of 2026-11-06 plus the 4 cellar packages. 67 have compiled code, so users get **binaries** only. Installed with base R's `install.packages()` from a local repository and checked against the lock (renv's restore needs the Command Line Tools on macOS, #7). |
| Quarto 1.11.9 | — | `[quarto]` macOS tarball (universal), Windows zip | `quarto add` is never run. |
| Tools Python env | Python 3.13 | `[python_envs.tools]` (`runtime/python/tools/uv.lock`, 23 packages) | deckifyr `[web,preview]` + python-docx 1.1.2, PyYAML 6.0.2, Pillow 11.1.0 (pyro's pins, compatible with deckifyr's lower bounds), and pymupdf 1.28.2: pymupdf 2.x has no macOS x86_64 wheel. One env for deckifyr and for quartifyr/reportifyr, so pyro never creates one. |
| Rust 1.99.1, Rtools45 | repair path only | `[rust]`, `[rtools]` | rustup-init (online) and standalone toolchain archives (offline). |
| LibreOffice 26.2.7.1 | optional | `[optional.libreoffice]` | Optional per-user download for deckifyr previews only; quartifyr's field recalculation doesn't work with it yet ([spikes/libreoffice.md](spikes/libreoffice.md)). |

## The R package set

`scripts/lock_r.R` computes the closure (Depends, Imports, LinkingTo) of the four cellar
packages' DESCRIPTION files plus opnca-r's web-app packages and renv, against a dated
Posit Package Manager (P3M) snapshot. The same date always gives the same lock.
`scripts/pin_r_binaries.py` then pins, for every package, the exact file per platform
(`macos-arm64`: `bin/macosx/sonoma-arm64`, `macos-x86_64`: `bin/macosx/big-sur-x86_64`,
`windows-x86_64`: `bin/windows`, all `contrib/4.6`) plus the source tarball, and checks
that each macOS binary links nothing outside `/usr/lib`, `/System` and `R.framework`.

Findings while pinning:
- **P3M's macOS gdtools links XQuartz** (`/opt/X11/lib/libX11`, `libXrender`, …), which a
  standard user cannot install. gdtools is needed by flextable, so by reportifyr. CRAN's
  own macOS build of gdtools 0.5.3 is self-contained, so the two macOS gdtools files come
  from CRAN (`origin = "cran"` in `binaries.toml`). CRAN keeps only the current binary,
  so those two URLs break when gdtools is next updated; the offline bundle (#36) must
  keep a copy, and the pin moves with the next snapshot.
- Package binaries hard-code `/Library/Frameworks/R.framework` only in two places:
  `opnca.so` links `…/Versions/4.6/Resources/lib/libR.dylib` and `data.table` links
  `…/lib/libomp.dylib`. Everything else resolves R symbols at load time. The macOS
  relocation (#7) has to handle those two.
- CRAN builds R 4.6 arm64 for macOS 14+ (`sonoma-arm64`); our minimum is macOS 15 anyway.

## Regenerating and verifying

```sh
# 1. Lock (no installs). <cellar> holds the pinned opnca, quartifyr, reportifyr, pyro tarballs.
Rscript --vanilla scripts/lock_r.R --snapshot 2026-11-06 --cellar <cellar>
# 2. Pin every file per platform (downloads ~450 MB; --cache keeps them).
uv run python scripts/pin_r_binaries.py --cache <dir>
# 3. Tools env lock.
uv lock --project runtime/python/tools
# 4. Check everything. CI runs the structure check; --network downloads and hashes all
#    ~540 files (GH_TOKEN for opnca-r, which is private).
uv run python scripts/verify_pins.py
GH_TOKEN=$(gh auth token) uv run python scripts/verify_pins.py --network
```

**Isolated restore check** (2026-11-06, macOS 26.6 arm64, R 4.6.1, network denied with
`sandbox-exec` and every HTTP(S) request sent to a logging proxy on loopback): a local
CRAN-like repository built from the pinned macOS arm64 binaries and source tarballs, plus
a cellar with the four pinned packages, restored all 130 packages of `renv.lock` into an
empty library in 10 s; opnca, quartifyr, reportifyr, plumber2, flextable and officer load
from that library, and no request was attempted. `scripts/check_r_restore.R` reproduces
it. Two lock and repository rules came out of this and the Windows spike (#6):
- The four tool packages are locked as `"Source": "Cellar"`. With `"Source": "GitHub"`,
  renv 1.3 downloads them from GitHub before it looks in the cellar, which fails for
  private opnca-r. `verify_pins.py` enforces it.
- The repository's *source* index must list every package (the binary entry where there
  is no source tarball). renv plans from it and looks up anything missing on
  `crandb.r-pkg.org`, which also sends the package name to a third party. With opnca
  missing, the proxy saw one such request. A clean per-user R has
only base and recommended packages in its own library; a dev machine's R usually has many
more, which makes renv skip them, so the script hides them.

The tools env: `uv sync --locked --no-install-project` of `runtime/python/tools` into a
fresh venv installs deckifyr 0.3.5 and imports deckifyr, docx, yaml, PIL, fastapi,
uvicorn and pymupdf.
