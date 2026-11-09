# 0009. Cloud container mode and remote access

- Status: accepted (owner, 2026-11-08): the scope (all of #42), a private GHCR image
  built on native amd64 and arm64, slim and full variants, Docker and Podman, the
  storage policy, and one user per VM as the intended design.
- Date: 2026-11-08
- Issue: #42 (release archives: #43, still pending its licence review)
- Upstream: [deckifyr#97](../upstream.md) (project-relative paths in `deck-manifest.json`)

## Context

Some users work on a cloud VM where they have admin rights and can run Docker, and where
study data already lives (often the approved place for it). The browser stays on their
own computer. Two things differ from the desktop:

- **The browser is elsewhere.** It reaches the VM through an SSH port forward or an IDE's
  authenticated proxy (VS Code Server or code-server, Posit Workbench,
  jupyter-server-proxy). Then `Host` and `Origin` are the forward's or the proxy's, and
  behind a proxy every URL sits under a prefix such as `/proxy/8765/`.
- **The runtime is Linux.** opnca-r has no Linux binary (ADR 0004), and LibreOffice is a
  plain package there (#8).

The local URL is still a security boundary, and the opnca-r source is private while this
repository is public.

## Decision

1. **One pinned image, two variants.** `container/Dockerfile` builds `slim` and `full`
   (adds LibreOffice) for linux/amd64 and linux/arm64. Everything comes from
   `runtime/pins.toml` through `scripts/container_args.py`:
   - a digest-pinned `ubuntu:24.04`;
   - Ubuntu packages at exact versions per platform and stage (`container/apt/`), with
     anything else apt would add failing the build. `snapshot.ubuntu.com` can't be used:
     it is https-only, the bare base has no CA bundle, and it has no public arm64 archive;
   - the `[ci.linux-*]` runtimes, the R library built from the pinned source tarballs,
     and opnca-r built from its vendored tarball.

   The install runs with `--network=none` through `scripts/offline/install_runtime.sh`,
   the same script as the offline CI job. The final stage has no compiler, no Rust and no
   build assets (`scripts/container/check_image.sh` checks this). opnca-r's token reaches
   the build only as a BuildKit secret mount, never as an `ARG` or `ENV`.

2. **Private distribution only.** The image goes to the private package
   `ghcr.io/jprybylski/opncafyr` and nowhere else, because it contains private opnca-r
   and redistribution is #43's decision. The repository is public, so CI:
   - never builds on `pull_request`;
   - keeps its build cache only in a private GHCR package, never in the Actions cache;
   - never uploads an image or a layer as a workflow artifact;
   - refuses to push unless both packages already exist and are private, and checks
     again afterwards.

3. **Explicit remote access, never sniffed.** `opncafyr launch --container` is the only
   way to bind the container interface. The browser-side settings come only from options
   that the launch script passes: `--access ssh|vscode|workbench|jupyter`, `--public-url`,
   `--local-port`, `--allowed-host` and `--allowed-origin`. They never come from
   environment variables or from detecting an IDE. A preset only fills in the forward's
   or proxy's Host and Origin and the URL prefix from the URL the user gives. It never
   turns a check off. The session cookie, the one-time `/open` link, the session header
   and every Host, Origin and `Sec-Fetch-Site` check are unchanged, and there's a test
   per preset. Plain `opncafyr launch` keeps its loopback-only, exact-Host default.

4. **The port is published to the VM's loopback only.** `container/opncafyr-container`
   publishes `127.0.0.1:<port>`, runs the container on its own bridge network, and never
   opens a firewall, a security group or a public interface. Behind an IDE proxy, the
   IDE's login is the boundary. Anything served on the IDE's origin can read the page,
   so v1 assumes **one user per VM**. Multi-user hosting needs its own security design
   (out of scope).

5. **The prefix works whether or not the proxy strips it.** `BasePathMiddleware` serves
   the app with or without the prefix. The cookie is scoped to the prefix (and `Secure`
   behind https) and named after the public URL, because every container listens on the
   same port inside. Child apps are told their full public path (`<prefix>apps/<tool>/`),
   and the proxy forwards with the prefix put back, so a child sees exactly the URL the
   browser uses.

6. **One study folder per container.** `--workspace /workspace` fixes the folder: there
   is no picker, "choosing" returns it, and no other folder is accepted. Files belong to
   the invoking user (`--user`, or `--userns=keep-id` on rootless Podman).

   Storage (`core.storage`, for native Linux folders too):
   - object-storage FUSE mounts (s3fs, gcsfuse, blobfuse, goofys, mountpoint-s3, rclone
     and others) are refused, because they fake rename as copy-then-delete;
   - NFS and SMB shares are allowed after a rename probe, with a note;
   - local disks need no probe.

7. **Relative paths and the image ID in provenance.** Manifests and scripts store paths
   relative to the study folder only, so a bundle made in a container replays natively,
   and the reverse. The check moves the study to another path and owner and scans it.
   Runs, publications, builds and replays record `tools.container_image` as the **image
   ID** (the config digest). Unlike a registry digest, it survives `docker load`, so it
   matches whether the image was pulled or loaded from an archive.

## Consequences

- Cloud users get no Rust or compiler problems and get LibreOffice previews (in `full`).
  One image ID pins R, the R packages, Python, Quarto, uv and the tools.
- CI builds both architectures on native runners and runs the full Gate 2/3 flow inside
  each image offline. It then tests container mode through an SSH-style forward and a
  prefix-stripping proxy, with Docker and with Podman.
- Creating the two GHCR packages is a one-time owner step (docs/container.md).
- deckifyr writes absolute paths into its own `deck-manifest.json`. It is exempted from
  the path scan until deckifyr#97.
- On arm64, the slim image measured 788 MiB gzip and full 1076 MiB, both under GitHub's
  2 GiB limit per release file. That is input for #43, which still decides whether
  archives may be attached to releases.

## Amendment (2026-11-09, #65): a native launcher on the VM
`opncafyr launch --remote` is the native Linux counterpart of `--container` (ADR 0003
amendment). It takes the same explicit options (`--access`, `--public-url`, `--local-port`,
`--allowed-host`, `--allowed-origin`) and, like them, never from the environment. It differs in
two ways only: the listener binds the VM's `127.0.0.1` (nothing publishes a port, so the
container interface isn't needed), and it requires `--workspace` and a fixed `--port` (a headless
VM has no folder picker). The two modes are exclusive. Every check in point 3 is unchanged and
the security tests run against both. One user per VM still holds.
