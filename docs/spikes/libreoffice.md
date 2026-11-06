# Spike #8: LibreOffice per user

- Date: 2026-11-06
- Version: LibreOffice 26.2.7.1 (the "still" branch), pinned in `runtime/pins.toml`
  `[optional.libreoffice]` from the permanent archive URLs (the `stable/` files are
  different files and disappear with the next release).
- Where: the Windows and macOS install spikes ([windows-install.md](windows-install.md),
  [macos-install.md](macos-install.md)), as a standard user, with the network blocked.
  Scripts: `scripts/spikes/windows-libreoffice.ps1`, `scripts/spikes/expand_msi.ps1`,
  `scripts/spikes/macos-install.sh --libreoffice <dmg>`.

**Decision: LibreOffice is an optional, verified per-user download for slide previews.**
It is not part of the default install, and offline bundles include it only as an optional
component. Its only working use is deckifyr's slide previews. quartifyr's field
recalculation fails with it on both platforms, so the quartifyr adapter doesn't offer
that step until quartifyr#84 and quartifyr#86 are fixed.

## Results

| | Windows 11 x86_64 | macOS 15 arm64 |
|---|---|---|
| Download | MSI, 373 MB | DMG, 299 MB |
| Per-user install, no admin | `msiexec /a <msi> TARGETDIR=…` in the desktop session (≈45 s, no UAC); over SSH it fails with 1601. `expand_msi.ps1` unpacks the MSI in any session (141 s). | `hdiutil attach -nobrowse -readonly`, `ditto LibreOffice.app` into the app root, detach. No prompt. |
| Installed size | 1,504 MB | 801 MB |
| Signature | — | Notarized (The Document Foundation); passes Gatekeeper even when quarantined |
| First conversion, fresh profile | 21.9 s (then 6.9 s warm) | 2 s |
| deckifyr preview, 20 sequential | 20/20, median 12.1 s | 20/20, 29 s in total |
| deckifyr preview, 20 at 4-way concurrency | 20/20, median 13.2 s | 20/20, 9 s in total |
| Hangs and leftover processes (previews) | 0 and 0 | 0 and 0 |
| quartifyr `--recalculate-fields` | 0/15: every run hit the 150 s timeout and left `soffice` running | 0/9 (6 in the VM, 3 on a developer Mac with 26.2.5): every run hit the timeout, no leftovers |
| Network | none | none |

Concurrent recalculation wasn't run on either platform: each failure costs the full
timeout. Intel Macs weren't tested, because the only Intel machine was a CI runner, which
has no desktop session (below).

## Findings

1. **LibreOffice needs the user's desktop session.** Started from SSH, a service or
   `sudo -u`, `soffice` hangs (macOS, XPC "Connection interrupted") or fails to start
   (Windows, `0xC0000142`). The launcher runs in the user's session, so this is fine
   there. But a "LibreOffice is installed" check isn't enough. The capability check
   must run a short probe conversion under a timeout. CI can't test LibreOffice as a
   separate standard user, and Windows needs an interactive runner for it.
2. **Windows uses the system VC++ 2015–2022 runtime.** The MSI carries those DLLs for
   System32. Copied next to `soffice.exe` instead, they break it (`soffice --version`
   prints nothing). The spike machine had the runtime. When `vcruntime140_1.dll` or
   `msvcp140.dll` is missing, previews stay unavailable with a message, because
   installing the redistributable needs admin.
3. **The profile must be a valid file URL.** `-env:UserInstallation=` with a raw space
   (macOS `Application Support`) makes soffice abort. A Windows path in the form
   `file://C:\…` is also not a file URL, and that is quartifyr's recalculation bug on
   Windows (quartifyr#86). Build the URL with `Path.as_uri()`. deckifyr 0.3.5 uses
   space-free temporary profiles and works.
4. **quartifyr's field recalculation doesn't work.** On macOS it always times out
   (quartifyr#84). On Windows it also leaves `soffice.bin` running and then fails on the
   locked temporary profile (quartifyr#86). quartifyr finds `soffice` on `PATH` only
   (quartifyr#83), which breaks our rule that executables come from the pinned runtime.
5. **No network.** Install and every run worked with the network blocked.

## Licence

LibreOffice is licensed under MPL-2.0 (some components under other free licences, listed
in its own licence files). Redistributing the unmodified installer or app in an offline
bundle is allowed, provided its licence and notice files stay with it (they ship inside
the app). We don't modify it. The Document Foundation's trademark policy allows
unmodified binaries to keep the name. The launcher shows it as "LibreOffice
(optional, for slide previews)", with its licence.

## What this means for the work ahead

- **Installer (#19, #36):** an "Install LibreOffice for slide previews" option downloads
  the pinned file, checks its SHA-256 and installs per user:
  - Windows: `msiexec /a` in the user's session, with `expand_msi.ps1` as the fallback;
    no app-local VC++ DLLs.
  - macOS: `ditto` from the DMG.

  The bundle builder includes it only when asked.
- **deckifyr adapter (#33):** `preview_deck` is available when the pinned `soffice` exists
  *and* a probe conversion finishes within a short timeout. Pass it as an absolute
  `--soffice-bin` with `--trusted-binaries-only`. The descriptor's
  `optional_requirements` entry already disables `preview_deck` when it's missing.
- **quartifyr adapter (#32):** never pass `--recalculate-fields` or
  `--resolve-same-page-crossrefs` until quartifyr#84, #86 and #83 are fixed and
  re-tested. The report still renders without them. The descriptor needs no change:
  these are options of `render_report`, not capabilities.
