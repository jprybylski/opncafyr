---
title: Before you install
parent: Get started
nav_order: 1
description: What opncafyr does, which computers it runs on, what you need before you install it, and where it puts its files.
permalink: /get-started/before-you-install/
---

# Before you install

Check what opncafyr needs, and get the installer, before you start.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

## What opncafyr is for

opncafyr is a workspace for non-compartmental analysis (NCA) of concentration data. It runs on your own computer and opens in your web browser. In it you:

1. choose a study folder that keeps one study's data, runs, reports and decks together;
2. import a concentration file (CSV) and clean it, with every step recorded;
3. map the file's columns to the roles the analysis needs (subject, time, concentration and so on), and run checks;
4. pick one analyte (one substance measured, such as a parent drug or a metabolite) and run it in opnca-r, the tool that does the NCA calculations;
5. publish completed runs to a Word report or a PowerPoint deck.

Each run is saved with a record of exactly what went into it, so it can be replayed and checked later. When a report or deck builds without errors, that shows the files were assembled as recorded. It does not show that the analysis is right.

## Getting the installer

opncafyr is pre-release software. There is no public download page yet: you get the installer, a zip file named `opncafyr-installer-<version>.zip`, from the person who supports opncafyr at your site.

The installer also needs the opnca-r release, which is not public. Ask whoever provides your opncafyr install how you get access to it. They may give you a folder of opnca-r files to use during the install; the install pages show what to do with it.

## Supported computers

| | Supported | Not supported |
|---|---|---|
| macOS | macOS 15 or later, on Apple silicon or Intel | macOS 14 and earlier |
| Windows | Windows 11, 64-bit (x86_64) | Windows 10; Windows on Arm |
| Linux | Ubuntu 24.04 (x86_64 or aarch64) with no desktop, installed from an offline bundle. Not available yet: [Install on Linux]({{ '/get-started/install-linux/' | relative_url }}). In a container with Docker or Podman today: [Run on a cloud computer]({{ '/get-started/install-cloud-container/' | relative_url }}) | Other distributions and versions; a desktop install |

The installer checks this first. On a computer it doesn't support, it stops before changing anything and says why.

## What you need

- **Your own user account.** You do not need administrator rights, and you should not run the installer as an administrator or with `sudo`. opncafyr installs for you alone.
- **About 4 GB of free disk space** in your user folder. The installer checks this before it starts.
- **A network connection, or an offline bundle.** See [Online or offline](#online-or-offline) below.
- **A web browser.** The app opens in your default browser.
- **Time.** A first install downloads and tests R, Quarto and the tools, so it can take a while.

opncafyr does not change other programs. It brings its own copies of Python, R, Quarto and the tools, and it never reads or changes any R you already have, or your R packages.

## Online or offline

You can install opncafyr in two ways. The app works the same way after either one.

**Online (most installs).** The installer downloads each part it needs, about 550 MB in all, from its official source (GitHub, the Python package index, and R's package servers). Every file is checked against a fingerprint (a SHA-256 checksum) built into the installer. If a file is missing or doesn't match, the installer stops before changing anything.

**Offline, from a bundle.** If your computer can't reach the internet, or your organisation blocks downloads (common on firewalled analysis systems), your IT team can prepare an offline bundle: one zip with every file the installer needs. The installer then never uses the network. See [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}). Your IT team may instead set up internal mirrors; see [Internal mirrors and bundles]({{ '/guides/internal-mirrors/' | relative_url }}) (for administrators).

Once installed, opncafyr does not need the network. Nothing you load into it leaves your computer.

## Where it installs

Everything goes into your own user folder.

| | macOS | Windows |
|---|---|---|
| App folder | `~/Library/Application Support/opncafyr` | `%LOCALAPPDATA%\opncafyr` |
| R used by the app | `~/Library/opncafyr` | inside the app folder |
| Icon | **opncafyr** in `~/Applications` (the Applications folder inside your home folder) | **opncafyr** shortcuts on the Desktop and in the Start Menu |

The app folder holds the installed versions, settings, logs and a download cache. Your study folders are never inside it, and the installer never writes to them.

`~` means your home folder. `%LOCALAPPDATA%` is the `AppData\Local` folder inside your Windows user folder.

## Next step

- [Install on macOS]({{ '/get-started/install-macos/' | relative_url }})
- [Install on Windows]({{ '/get-started/install-windows/' | relative_url }})
- [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }})
