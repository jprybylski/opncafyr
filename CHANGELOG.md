# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
- Project scaffold: loopback FastAPI controller with `/health`, Host/Origin and
  session-secret checks, workspace path canonicalization, job state machine, trusted tool
  descriptor registry, JSON Schemas (tool descriptor, workspace, run manifest,
  publication, recipe), adapter stubs for opnca, deckifyr and quartifyr, installer stubs,
  CI, and the plan of action.

### Changed
- Recorded the v1 platform matrix (ADR 0003, #1): Windows 11 x86_64, macOS arm64/x86_64
  (macOS 15 until opnca-r#45), R 4.6.2, a 30-minute configurable idle timeout and no
  native Linux. Also recorded the opnca-r install path (ADR 0004, #4): prebuilt binaries,
  with the vendored source tarball as the repair/exception fallback.
  `runtime/pins.toml` now has the platform table and the R pin.
- Reviewed the merged opnca-r (#23-#30, v0.2.6) and quartifyr (#69-#72) work against the
  issues we filed. `docs/upstream.md` now has a status column and the follow-ups
  (opnca-r #40-#43, quartifyr #75-#78). `docs/plan.md` risks are updated, and
  `runtime/pins.toml` pins opnca-r 0.2.6 assets and the quartifyr commit and tarball.
- Reviewed deckifyr 0.3.4 (#82-#86) and filed follow-ups #88-#91. `runtime/pins.toml` now
  pins the deckifyr 0.3.4 wheel, sdist and R tarball by SHA-256.
- Reviewed quartifyr 0.3.2 (#75-#78) and filed follow-ups #81-#82. `runtime/pins.toml`
  now pins the quartifyr 0.3.2 tag commit, tarball and `SHA256SUMS` by SHA-256.
- Reviewed deckifyr 0.3.5 (#88-#91) and pinned its wheel, sdist and R tarball by SHA-256.
  `upstream-blocked` removed from #33.
- Reviewed opnca-r 0.2.7 (#40-#43) and filed follow-ups #45-#46. `runtime/pins.toml` now
  pins the 0.2.7 binaries, vendored tarball, `manifest.json` and `SHA256SUMS` by SHA-256.
  `upstream-blocked` removed from #27.
