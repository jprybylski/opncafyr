---
title: Platforms and versions
parent: Reference
nav_order: 3
description: Supported operating systems, CPU architectures and browsers, and the exact versions of R, Python, Quarto and the tools that opncafyr installs.
permalink: /reference/platforms/
---

# Platforms and versions

Which computers opncafyr runs on, and the exact versions it installs.
{: .fs-5 .fw-300 }

## Supported computers

| | Supported | Not supported |
|---|---|---|
| macOS | macOS 15 or later, on Apple silicon (arm64) or Intel (x86_64) | macOS 14 and earlier |
| Windows | Windows 11, 64-bit Intel or AMD (x86_64) | Windows 10; Windows on Arm, including running under emulation |
| Linux | In a container on a cloud computer (x86_64 or arm64) with Docker or Podman: [Run on a cloud computer]({{ '/get-started/install-cloud-container/' | relative_url }}) | A desktop install |

You also need:

- about 4 GB of free disk space;
- permission to run programs installed in your own user folder. Some organisations block this with application controls; the installer says so if it can't run its own Python.

## No administrator rights

opncafyr installs for one user, with no administrator rights and no elevated prompt.

- Everything goes into your own user folders. See [Files and folders]({{ '/reference/files/' | relative_url }}#in-the-app-folder).
- It never changes the machine `PATH`, and never writes under `/Library` or `Program Files`.
- It never makes its R your default R, and never reads or writes your own R libraries. An R you already have keeps working as before.
- It never touches your study folders.

The one exception is an unusual repair, `repair --build-from-source` on macOS, which needs Apple's Command Line Tools; installing those needs an administrator. A normal install compiles nothing.

## Browser

The app opens in your default web browser, at a private address on your own computer (`127.0.0.1`). Nothing you load into it leaves the computer, and it needs no internet connection while you use it.

No list of supported browsers has been published yet.

## What opncafyr installs

Every file below is pinned to an exact version and checked against a fingerprint built into the installer before it is used. The same versions are used for online installs, internal mirrors and offline bundles.

| Component | Version | Used for |
|---|---|---|
| Python | 3.13.18 | the app itself and the Python side of the tools |
| uv | 0.13.7 | setting up the app's Python environments |
| R | 4.6.2 | opnca-r, report building and fragments in decks |
| Quarto | 1.11.9 | rendering reports and Quarto content in decks |
| opnca-r | 0.3.1 | the NCA calculations |
| quartifyr | 0.3.3 | building the Word report |
| reportifyr | 0.4.0 | report footnotes from the description files |
| deckifyr | 0.3.6 | the deck editor and building the PowerPoint deck |
| pyro | 0.1.1 | a helper package that reportifyr and quartifyr need |

The R packages the tools depend on come from a fixed, dated snapshot and are installed into the app's private R library. opnca-r is installed from a ready-built package for your platform, so a normal install compiles nothing.

### Optional

| Component | Version | Used for |
|---|---|---|
| LibreOffice | 26.2.7.1 | slide previews in decks only. Not installed by default; the installer adds it with `--with-libreoffice`. Decks build without it. |

### Only for `repair --build-from-source`

When opnca-r's ready-built package can't be used, this repair builds it from source. It uses a pinned Rust toolchain (1.99.1) and, on Windows, Rtools45. They are unpacked for the build only and removed afterwards. A normal install never uses them.

## Checking what is installed

Run the installer's `status` command to see what is installed, and read the installation report in the app folder's `state/reports` folder. See [Repair, update and uninstall]({{ '/guides/maintenance/' | relative_url }}).

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. The installation report and its start-up tests show that the parts start and agree with the pinned versions; they are not a validation of the app or of any calculation.
