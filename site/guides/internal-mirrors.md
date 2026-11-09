---
title: Internal mirrors and bundles
parent: Guides
nav_order: 2
description: For administrators. Build offline bundles, point the installer at internal mirrors, and handle proxies and installation reports.
permalink: /guides/internal-mirrors/
---

# Internal mirrors and bundles

For IT administrators who prepare opncafyr installs for firewalled computers: build offline bundles, or let the installer fetch from your internal mirrors.
{: .fs-5 .fw-300 }

{: .warning }
> **This page is for administrators.** If you are installing opncafyr for yourself from a bundle you were given, follow [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}) instead.

## How the installer decides what to trust

Offline installs, mirror installs and online installs all use the same installer and the same steps. Only the source of the files differs.

- Every file the installer uses is pinned: its name and SHA-256 checksum are built into the installer.
- Every file is hashed against that pin before anything is installed, wherever it came from. A missing or changed file stops the install before it changes anything, and the error names the part.
- A bundle's own checksum list and manifest are never used to decide what to accept.
- Nothing is installed with administrator rights, and nothing is written outside the user's own folders.

So a mirror or a bundle changes only where a file comes from, never what is accepted.

## Where the files come from

An online install downloads each pinned file from its original source, over HTTPS, with the system `curl`:

| Origin | What |
|---|---|
| `https://github.com/` | Python, uv, Quarto, deckifyr, quartifyr, reportifyr and the other tool releases; the opnca-r release |
| `https://files.pythonhosted.org/` | Python packages (wheels) |
| `https://packagemanager.posit.co/` | R packages, from a dated snapshot |
| `https://cran.r-project.org/` | R itself, and a few R package binaries |
| `https://downloadarchive.documentfoundation.org/` | LibreOffice (only with `--with-libreoffice`) |
| `https://static.rust-lang.org/` | Rust (only for `repair --build-from-source`) |

The opnca-r release is not public. To use it, you need the opnca-r release files (a `manifest.json` and the binary for each platform) from whoever provides your opncafyr install.

## Option 1. Build an offline bundle

A bundle holds every pinned file for one platform, plus the installer. Build it on any connected Mac or Windows PC, for any supported platform, from the unzipped installer (`opncafyr-installer-<version>`). This is a generated payload, not the `installer/` directory in a source checkout. The source copy of `install.sh` alone cannot run: it also needs the generated `bootstrap.tsv`, `payload.json`, wheel and pins. There is no public installer zip download yet.

### Test from a source checkout

From the repository root on a connected Mac or Windows PC with `uv` installed, first build the installer payload:

```sh
uv run python scripts/make_installer.py --out dist --zip
```

This writes `dist/opncafyr-installer-<version>/` and `dist/opncafyr-installer-<version>.zip`. Unzip the zip, or use the generated folder directly; run its `install.sh` or `install.bat`, not `installer/install.sh` or `installer/install.bat` from the source tree. The installer zip contains the installer and its pins. It does not contain the downloaded runtime assets. The separate `opncafyr-bundle-<version>-<platform>.zip` is what the `bundle` command creates.

For a quick check of bundle creation, offline installation and mirror rules using test files, run `uv run pytest tests/test_installer_bundle.py -q` from the repository root. The full commands below need access to the pinned public downloads and the private opnca-r release files, either in an `--assets` folder or through `GH_TOKEN`/`GITHUB_TOKEN`. They download roughly 550 MB per platform. A complete offline installation also needs a supported target computer; see [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}).

1. Put the opnca-r release files in one folder.
2. Open Terminal (macOS) or Command Prompt (Windows) in the installer folder.
3. Run the builder. For example, on a Mac, a bundle for Windows users:

   ```sh
   ./install.sh bundle --platform windows-x86_64 --out bundles --archive --assets "<folder with the opnca-r files>"
   ```

   On Windows, a bundle for Mac users with Apple silicon:

   ```bat
   install.bat bundle --platform macos-arm64 --out bundles --archive --assets "<folder with the opnca-r files>"
   ```

{: .expect }
> The builder says `collecting ... pinned files for <platform>`, fetches each file and checks it against the pins, and ends with the bundle's ID and folder and, with `--archive`, `archive: <path>`. The `bundles` folder now holds `opncafyr-bundle-<version>-<platform>/` and `opncafyr-bundle-<version>-<platform>.zip`.

Options:

| Option | Meaning |
|---|---|
| `--platform` | `windows-x86_64`, `macos-arm64` (Apple silicon) or `macos-x86_64` (Intel). Default: this machine's platform. |
| `--out DIR` | The folder to write the bundle in (required). |
| `--archive` | Also write `opncafyr-bundle-<version>-<platform>.zip`. |
| `--assets DIR` | A folder of pinned files to use before downloading, such as the opnca-r files. Can be repeated. |
| `--with-libreoffice` | Add LibreOffice, for slide previews in decks (optional; about 300–400 MB more). |
| `--with-source-build` | Add what `repair --build-from-source` needs: opnca-r's source, the Rust toolchain and, for Windows, Rtools (about 600 MB more). |
| `--mirror mirror.toml` | Fetch from your internal mirrors instead of the origins ([Option 2](#option-2-install-from-internal-mirrors)). |
| `--cacert FILE`, `--proxy URL` | For HTTPS through a company proxy ([below](#proxies-and-tls-inspection)). |

A macOS or Windows bundle is about 550 MB. Two builds from the same installer list the same files and checksums; the bundle ID is a hash of them. Your organisation assembles the bundle itself from the original sources.

### What a bundle contains

| Path | What |
|---|---|
| `assets/<component>/<file>` | Every pinned file |
| `installer/` | The installer itself: `install.sh`, `install.bat`, the opncafyr package and the pins |
| `bundle.json` | Manifest: the bundle ID and, for every file, its component, version, SHA-256, size, source URL and licence |
| `SHA256SUMS` | The SHA-256 of every other file |
| `sbom.spdx.json` | Software bill of materials (SPDX 2.3) |
| `licenses.json` | Licence inventory, read from each package's own metadata |

### Check it and hand it over

1. Copy the zip (or the folder) to the target computers by your usual approved route.
2. Optionally check it on arrival against `SHA256SUMS`: `shasum -a 256 -c SHA256SUMS` on macOS, or `Get-FileHash` in PowerShell. The installer doesn't need this: it checks every file against the pins inside it and refuses a missing or changed one.
3. Give users the bundle that matches their computer and point them to [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}).

{: .note }
> Bundles have no detached signature, and the installer is not code-signed yet. Until it is, you can compare the installer payload digest (in `installer/payload.json`, and in each installation report) with the value from whoever supplied the installer.

For updates, build a bundle for the new version and hand it over in the same way. Users install it like the first one; if the update fails or is interrupted, the old version stays active. Users need the bundle again to repair an offline install, so let them keep it.

## Option 2. Install from internal mirrors

If your site mirrors GitHub releases, PyPI files and CRAN (with Artifactory, Nexus and the like), users don't need a bundle. Describe the mirrors in a `mirror.toml`:

```toml
[mirrors]
"https://github.com/" = "https://artifacts.example.org/github-releases/"
"https://files.pythonhosted.org/" = "https://artifacts.example.org/pypi-files/"
"https://packagemanager.posit.co/cran/2026-11-06/" = "https://artifacts.example.org/cran-snapshot/"
"https://cran.r-project.org/" = "https://artifacts.example.org/cran/"
```

Then the user runs the installer with it:

```sh
./install.sh install --mirror mirror.toml
```

```bat
install.bat install --mirror mirror.toml
```

You can also build a bundle from the mirrors: `install.sh bundle … --mirror mirror.toml`.

Rules:

- Each pinned URL is rewritten by its longest matching prefix. The file name and the pinned SHA-256 don't change.
- Both sides of each line must start with `https://` and end with `/`.
- A URL with no matching prefix is refused, and the error names it. Mirror mode never falls back to the public origin.
- The R binary paths (`bin/windows/contrib/4.6`, `bin/macosx/…/contrib/4.6`) must be proxied, not only `src/contrib`.
- Add prefixes for LibreOffice and Rust only if users install with `--with-libreoffice` or `repair --build-from-source`.
- The opnca-r release must be uploaded to your mirror under the same file names, under the path its pinned GitHub URL maps to.
- Mirror credentials, if any, come from the user's `~/.netrc` (`%USERPROFILE%\_netrc` on Windows), never from `mirror.toml`.
- `--mirror` and `--from-bundle` can't be combined.

## Proxies and TLS inspection

The installer downloads with the system `curl`, so the operating system's trust store and the `HTTPS_PROXY` variable apply. For an online or mirror install through a company proxy, the user can also pass:

- `--proxy URL` for the HTTPS proxy;
- `--cacert FILE` for a CA bundle, when the proxy inspects TLS.

For example: `./install.sh install --proxy http://proxy.example.org:8080 --cacert company-ca.pem`.

## Application control

The installer unpacks its own Python into the user's app folder (`~/Library/Application Support/opncafyr` or `%LOCALAPPDATA%\opncafyr`) and runs programs from there: Python, R, Quarto and the tools. If your application control (for example AppLocker or a similar policy) blocks programs in user folders, allow that folder. Otherwise the installer stops with "the bundled Python can't run here".

## The installation verification report

After every install, update or repair, the installer writes a report to the app folder's `state/reports` folder, as `install-report-<time>.json` and a printable `install-report-<time>.html`. It records:

- the opncafyr version and the installer payload digest;
- the bundle ID, when installed from a bundle, and how files were trusted;
- the operating system and architecture, and the install paths;
- the version and SHA-256 of every component;
- the start-up (smoke) tests: an opnca-r calculation checked against known values on made-up data, a Quarto Word render with an R chunk run by the app's own R, and a deck build.

Your organisation's own qualification (for example an installation qualification) may use it as evidence.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. The installation report shows that the parts start and match their pins. It is not a validation of opncafyr or of any calculation.

## Technical details

The full list of network touchpoints and how each is closed, and the installer's design, are in the technical documentation: [Offline operation](https://github.com/jprybylski/opncafyr/blob/main/docs/offline.md) and [Installing opncafyr](https://github.com/jprybylski/opncafyr/blob/main/docs/install.md).
