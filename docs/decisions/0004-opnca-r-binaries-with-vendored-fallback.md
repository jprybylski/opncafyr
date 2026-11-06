# 0004. Install opnca-r from prebuilt binaries, with the vendored source tarball as fallback

- Status: accepted (owner, 2026-11-06); provisional until #6/#7 pass on clean accounts
- Date: 2026-11-06
- Issue: #4 (option C, hybrid)

## Context
`docs/spec.md` calls this the most consequential early decision: either invest in
platform R binaries for opnca-r, or accept a first install that compiles Rust. The owner
has a history of opnca-r install failures on target machines, and installing rustup and
Rtools as a standard Windows user is one of the failure modes the spec warns about.

opnca-r 0.2.7 (pinned in `runtime/pins.toml`) now provides:
- prebuilt R 4.6 binaries for `macos-arm64` and `macos-x86_64` (`.tgz`) and
  `windows-x86_64` (`.zip`), which matches the matrix in ADR 0003;
- `opnca_<ver>-vendored.tar.gz`, which builds without reaching crates.io;
- `manifest.json` (schema_version 1, `source_commit`, R version, rustc, a SHA-256 for
  each asset), plus `SHA256SUMS` and a `BUILD-INFO.txt` for each binary.

## Decision
**Option C (hybrid), with binaries as the normal path.**

- **Installer (#19, #34):**
  - Pick the binary from `manifest.json` by `kind`, `platform`, `arch` and `r_minor`,
    never by file name.
  - Verify the manifest and the asset against the SHA-256s pinned in `runtime/pins.toml`.
  - Rename the asset to `opnca_<ver>.<ext>`, then install it through the renv cellar.
  - The selection and verification code lives in `opncafyr.adapters.opnca.release`. It
    makes no network calls, so the online, mirror and offline-bundle paths all share it.
- **No Rust toolchain on a normal install.** Rust, plus Rtools on Windows or the Command
  Line Tools on macOS, are provisioned only in an explicit repair or exception path that
  builds the vendored tarball. Examples: a macOS version below the binary's minimum
  (opnca-r#45), or a corporate block on the binary.
- **Container (#42) and CI:** build the vendored tarball once, at image build time.
- **Offline (#36):** the bundle carries the platform binary and the vendored tarball.
  Neither needs the network.

## Rejected options
- **A, binaries only:** now that upstream ships the vendored tarball, keeping a fallback
  costs almost nothing. It covers cases binaries can't, such as a macOS version below the
  binary's minimum.
- **B, always build from source:** this means longer installs, plus Rust, Rtools, linker
  and R ABI failures on standard-user machines. Those are exactly the failures the spec
  and the owner's own history point to.

## Still to confirm
- #6/#7: a clean standard-user install of the binary loads, calculates and renders, and
  how long it takes.
- #34: a renv cellar restore of the binary. Upstream opnca-r#43 asked for this; we check
  it again on our side.
- The macOS minimum once opnca-r#45 is fixed (ADR 0003).

## Consequences
- Each opnca-r release we pin needs the manifest, `SHA256SUMS`, every binary and the
  vendored tarball hashed in `runtime/pins.toml`.
- The exception path has to be tested too (the vendored build in the container and in
  CI), so it never goes stale.
