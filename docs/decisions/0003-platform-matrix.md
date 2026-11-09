# 0003. v1 platform matrix: Windows 11 x86_64, macOS arm64/x86_64, R 4.6.2

- Status: accepted (owner, 2026-11-06; offline part 2026-11-04); amended 2026-11-09 for native Linux (#65)
- Date: 2026-11-06
- Issue: #1

## Context
`docs/spec.md` ("Decisions that control the first release") left the supported operating
systems, CPU architectures, R minor, offline installation and idle timeout open. Every
install artifact (R version, renv library path, Quarto archive, opnca-r binary matrix, Rust
target) depends on these answers. opnca-r 0.2.7 now ships R 4.6 binaries for
`macos-arm64`, `macos-x86_64` and `windows-x86_64` (`r_version: 4.6.2` in its
`manifest.json`), plus a vendored source tarball.

## Decision

| Question | Answer | Why |
|---|---|---|
| Windows | **Windows 11 x86_64 only**, tested in CI. No Windows 10, no arm64. | opnca-r ships one Windows binary (`windows-x86_64`, GNU target). Windows arm64 has no Rtools GNU path. Running x86_64 under emulation on arm64 is untested, so it's out of scope. The owner confirmed Windows 10 isn't needed. |
| macOS | **arm64 and x86_64.** Minimum **macOS 15** until mantra-opnca/opnca-r#45 lands, then **macOS 13**. | Both architectures have binaries. CRAN R 4.6 targets macOS 13, but the opnca-r 0.2.7 binaries target macOS 15 (no deployment target set upstream). The final minimum is the highest of R, Quarto, uv's Python and opnca-r. |
| R | **One pin: R 4.6.2.** | Every opnca-r 0.2.7 binary is built for R 4.6. One pin keeps the binary matrix at three assets. |
| Offline install | **Supported in v1**, alongside the default online install, for native installs and for cloud container mode. | Firewalled environments (such as GxP cloud analytical systems) are common. Install works online or from a verified bundle or mirror through one code path (#36, #44). |
| Idle timeout | **30 minutes by default, user-configurable** (5 minutes up to off). The timer runs only with no visible-page heartbeat, no running job and no unsaved editor. | It covers a meeting or a lunch break. Unsaved edits always block shutdown, so a timeout never loses work. #14 owns the mechanics. |
| Linux | **No native Linux in v1.** Linux runs only through cloud container mode (#42). CI keeps Linux for the Python core and the container image. | The container builds opnca-r once from the vendored tarball at image build time, so no Linux binary is needed. |

The resulting matrix is **{Windows 11 x86_64, macOS arm64, macOS x86_64} × R 4.6.2**. It is
what #5, #6, #7 and #34 target. It is recorded in `runtime/pins.toml` (`[platforms]`,
`[r]`).

## Still open
Whether cloud container mode is a v1 target, and which access routes it needs (SSH
forward, VS Code Server, Posit Workbench, jupyter-server-proxy), is tracked in #42.

## Consequences
- The install spikes (#6, #7) run on Windows 11 x86_64 and macOS 15 (arm64 and x86_64).
- CI integration jobs that load the opnca-r binary run on macOS 15 runners.
- When opnca-r#45 is fixed, lower `macos_min` in `runtime/pins.toml` after re-pinning the
  new binaries, and recheck Quarto's and uv Python's minimums.
- Adding Windows arm64 or Linux binaries later means a new opnca-r build target and a new
  ADR.

## Amendment (2026-11-09, #65): native Linux
The owner decided (2026-11-09) to support a **native Linux install** for a single-user cloud VM
where Docker and Podman are not allowed, beside cloud container mode. This replaces the
"No native Linux in v1" row; the sentence above that says Linux binaries "later means a new
ADR" is answered by this amendment.

| Question | Answer | Why |
|---|---|---|
| Linux | **Ubuntu 24.04, x86_64 and aarch64, glibc 2.39 or later.** Any other distribution, version or glibc fails the preflight with a message that points to the container. | Posit's R and its R package binaries are built on Ubuntu 24.04, and the offline CI job and the container already use it. A wider glibc range needs another relocatable R and an older-glibc opnca-r build: a large increase in work and risk. |
| R | **Posit's Ubuntu 24.04 R 4.6.2 `.deb`, unpacked in user space and relocated.** No root, dpkg or apt. | It is already pinned (`[ci.linux-*.r]`). Relocation follows the macOS recipe: rewrite the original prefix in R's text files, and replace `Rscript`, which has the prefix compiled in, with a wrapper that names `RHOME`. |
| R library | **Posit's noble binaries, pinned per architecture** (126 packages), installed one at a time. | P3M serves a binary at the source URL only to R's own User-Agent; each pin is URL + User-Agent + SHA-256. No compiler runs: a parallel install needs `make`, so installs are serial. |
| System libraries | **Preflight only.** The installer checks the 36 shared libraries the stack links directly, and names the missing ones and the Ubuntu packages to ask an administrator for. Nothing is bundled and nothing is installed. | A user without root can't add a library, and bundling about 40 `.deb` files per architecture needs hash-pinned archives the distribution doesn't keep, plus the redistribution review in #43. |
| opnca-r | **Needs a Linux binary from opnca-r** (mantra-opnca/opnca-r#61). Until it is pinned, Linux is listed under `[platforms].pending` and the installer refuses it. | The only other Linux route is the vendored source build, which needs Rust and a C compiler, so it contradicts "no compiler". |
| Not on Linux | LibreOffice (slide previews) and `repair --build-from-source`. Both are refused with a message. | Previews need root-installed libraries; a source build needs a C toolchain. The container has both. |
| Access | `opncafyr launch --remote`: the listener stays on the VM's `127.0.0.1`, reached through an SSH forward or an IDE proxy on the VM, with a fixed `--workspace` (there is no folder picker on a headless VM). Every Host, Origin, session-secret and `Sec-Fetch-Site` check is the one the container uses. | One user per VM, as in ADR 0009. |

The matrix is now **{Windows 11 x86_64, macOS arm64, macOS x86_64, Ubuntu 24.04 x86_64,
Ubuntu 24.04 aarch64} × R 4.6.2**, recorded in `runtime/pins.toml` (`[platforms]`:
`supported`, `pending`, `linux_*`).

Checked in an Ubuntu 24.04 container as an unprivileged user with no network, no compiler
and no `make`, on both architectures: the relocated R starts, all 126 binaries install and
load, Quarto, uv, Python and the pinned Git run, and the installer completes, including its
DOCX and PPTX smoke builds. A real firewalled VM has not been tried.
