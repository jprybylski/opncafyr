---
title: Install on Linux
parent: Get started
nav_order: 3.7
description: Install opncafyr for your own user account on an Ubuntu 24.04 cloud computer where you can't run Docker or Podman, from an offline bundle.
permalink: /get-started/install-linux/
---

# Install on Linux

Install opncafyr for your own user on an Ubuntu 24.04 cloud computer, with no administrator rights, from an offline bundle. Your browser shows the pages through an SSH connection or your cloud IDE.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.

{: .note }
If you can run Docker or Podman on the cloud computer, [running opncafyr in a container]({{ '/get-started/install-cloud-container/' | relative_url }}) is the simpler route. This page is for computers where you can't.

## What you need

- A cloud computer running **Ubuntu 24.04**, on a 64-bit Intel or AMD processor (x86_64) or an Arm processor (aarch64), where you are the only user.
- An ordinary user account. Don't use `root` or `sudo`: the installer refuses to run as `root`.
- About 4 GB of free disk space on the computer's own disk.
- An offline bundle for your processor, built by your IT team: `opncafyr-bundle-<version>-linux-x86_64` or `…-linux-aarch64` (a folder or a zip). See [Install offline from a bundle]({{ '/guides/offline-install/' | relative_url }}).
- These system libraries. The installer can't add them without administrator rights, so it checks first and lists any that are missing for your administrator:

  ```
  libbz2-1.0 libcairo2 libcurl4t64 libdeflate0 libfontconfig1 libfreetype6 libfribidi0
  libgcc-s1 libgfortran5 libgit2-1.7 libglib2.0-0t64 libgomp1 libharfbuzz0b libicu74
  libjpeg-turbo8 liblzma5 libopenblas0-pthread libpango-1.0-0 libpangocairo-1.0-0
  libpcre2-8-0 libpng16-16t64 libreadline8t64 libsodium23 libssl3t64 libstdc++6 libtiff6
  libtirpc3t64 libuv1t64 libwebp7 libwebpmux3 libxml2 libzstd1 zlib1g
  ```

  A font package such as `fonts-dejavu-core` is recommended, so reports have text to draw.

## Install

1. Copy the bundle to the cloud computer by your organisation's approved route.
2. Run the installer from inside it, as your own user:

   ```sh
   opncafyr-bundle-<version>-linux-x86_64/installer/install.sh install --from-bundle opncafyr-bundle-<version>-linux-x86_64
   ```

   If you have the zip instead of the folder, the computer needs `unzip` (or `python3`) to read it. Otherwise unzip it on another machine and copy the folder.
3. Wait. The installer checks every file, installs, and runs its tests. It makes no network connection.
4. It prints how to start opncafyr. The start command is `~/.local/share/opncafyr/bin/opncafyr`.

If a library is missing, the installer stops before changing anything and prints the library names and the Ubuntu packages to ask for. Send that list to your administrator and run the installer again.

## Start it and open it in your browser

There is no desktop on the cloud computer, so there is no icon and no folder picker. Name your study folder and a port:

```sh
~/.local/share/opncafyr/bin/opncafyr launch --remote --port 8765 --workspace ~/study
```

It prints a link that works once, within two minutes. Open it on your own computer:

- **SSH.** On your own computer run `ssh -L 8765:127.0.0.1:8765 <the cloud computer>`, then open the printed link in your browser.
- **A cloud IDE** (VS Code Server, Posit Workbench, jupyter-server-proxy). Add `--access vscode`, `--access workbench` or `--access jupyter` and `--public-url` with the address your IDE shows for the port, exactly as in [Run on a cloud computer]({{ '/get-started/install-cloud-container/' | relative_url }}).

opncafyr listens on the cloud computer's own address (`127.0.0.1`) only, never on a public one, and it refuses any request that isn't from your connection or your IDE. It is built for one user on the cloud computer.

## What isn't available on Linux

- Slide previews (they need LibreOffice, which needs administrator rights to install).
- `repair --build-from-source` (it needs a compiler).

Everything else works as on macOS and Windows. Updating, repairing and uninstalling work the same way: run the installer in a newer bundle, or `install.sh repair --from-bundle <bundle>`, or `install.sh uninstall`. If an update fails part-way, the old version stays active.

Next: [Open the app and choose a study folder]({{ '/get-started/first-study/' | relative_url }}).
