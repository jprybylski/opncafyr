# Container image and cloud container mode

- Issue: #42. Decision: [ADR 0009](decisions/0009-cloud-container-mode.md). Release
  archives: #43 (pending its licence review).
- User steps: [user guide](user-guide/cloud-container.md) and the website's
  *Run on a cloud computer* page.

**Research use only.** A working image proves that the parts assemble and the flow runs.
It does not validate any calculation.

## What's in the image

| Path | What |
|---|---|
| `/opt/R/4.6.2` | Posit's R 4.6.2 build, copied without its `.deb` (whose Depends pull in compilers) |
| `/opt/opncafyr/rt` | the pinned uv, Python 3.13.18 and Quarto 1.11.9 |
| `/opt/opncafyr/envs` | the launcher and tools virtualenvs, from hashed requirements |
| `/opt/opncafyr/rlib` | the app-private R library (renv.lock), with opnca-r built from its vendored tarball |
| `/opt/opncafyr/runtime/runtime.json` | the runtime record (`OPNCAFYR_RUNTIME_FILE`) |
| `/opt/libreoffice26.2` | `full` only: TDF's LibreOffice 26.2.7.1 |
| `/state`, `/workspace` | mount points: launcher state and the study folder |

The image has no compilers, no Rust and no build assets. Its Ubuntu packages are at the
exact versions in `container/apt/lock-<platform>-<stage>.txt`. Its fonts are DejaVu and
Liberation only: proprietary fonts are never added (#43). If a template needs others,
mount your own at run time.

Tags in `ghcr.io/jprybylski/opncafyr` (private):

| Tag | When |
|---|---|
| `edge`, `edge-full` | every build of `main` |
| `<version>`, `<version>-full` | a `v<version>` tag |
| `sha-<12 hex>`, `sha-<12 hex>-full` | every CI build (multi-arch) |
| `sha-<full sha>-<arch>[-full]` | per-architecture staging tags |

The run manifest records the **image ID** (`docker image inspect --format '{{.Id}}'`), not
a tag or registry digest (ADR 0009).

## Build it locally

```sh
container/build.sh                      # slim, this machine's architecture
container/build.sh --full               # with LibreOffice
container/build.sh --platform linux-x86_64   # other architecture, under emulation (slow)
container/build.sh --assets <dir>       # from a folder fetched elsewhere (no network, no token)
```

- Every build argument comes from `runtime/pins.toml` (`scripts/container_args.py`).
- opnca-r's token comes from `GH_TOKEN` or `gh auth token`. It reaches the build only
  as a BuildKit secret.
- The build runs `scripts/container/check_image.sh` afterwards (`--no-check` skips it).

Stages:
1. `fetch`: every pinned file, hash-checked, by `scripts/offline/fetch.py`.
2. `wheel`: opncafyr's own wheel, the only stage that reads `src/`.
3. `build`: R and compilers, then `scripts/offline/install_runtime.sh` with
   `--network=none`.
4. `launcher`: the wheel installed in its own layer.
5. `slim`, then `full`.

A source change rebuilds only the wheel and the layers after it. Allow about 20–40 minutes
the first time on a laptop; most of it is the R library and opnca-r built from source.

`scripts/container/check_image.sh <image> [slim|full]` checks that:
- every shared object resolves;
- there is no toolchain and no proprietary font;
- the token appears in no file, layer history, label or env (when `GH_TOKEN` is set);
- the full Gate 2/3 flow passes inside the image with `--network none`, as a user with
  no passwd entry. `full` also converts the deck to PDF with LibreOffice;
- the study verifies after moving it to another path and owner, and no file in it names
  a container path (deckifyr's own `deck-manifest.json` is excepted until deckifyr#97);
- the size, compressed and uncompressed, is printed.

Measured 2026-11-08 (gzip / uncompressed), all under GitHub's 2 GiB limit per release file (#43):

| | slim | full |
|---|---|---|
| amd64 (CI) | 860 / 1808 MiB | 1172 / 2516 MiB |
| arm64 (local) | 788 / 2672 MiB | 1076 / 3727 MiB |

`--apt-mirror URL` (build argument `APT_MIRROR`) takes the Ubuntu packages from a mirror,
such as an internal one (#36) or Azure's, which the amd64 CI runners use because they can't
reach `archive.ubuntu.com` reliably. Only the source changes: apt still checks Ubuntu's
signatures, and the locks still pin every version.

### Re-pinning the Ubuntu packages

`scripts/container/lock_apt.sh linux-x86_64` (and `linux-aarch64`) regenerates the locks
from the pinned base image and `container/apt/{runtime,build,full}.txt`. Run it to add a
package, or when a build fails because a locked version has left the Ubuntu archive. Then
review the diff like any other re-pin.

## CI and the private package

`.github/workflows/container.yml` runs on pushes to `main`, on `v*` tags and on manual
dispatch, never on pull requests. It works in four steps:
1. `private`: both packages must exist and be private.
2. `build`: native `ubuntu-24.04` and `ubuntu-24.04-arm` runners build `slim` and `full`,
   run the checks, and push per-architecture tags.
3. `publish`: makes the multi-arch tags, then checks the package is still private.
4. `container-mode`: runs `scripts/container/mode_check.py` with Docker and with Podman.

This repository is public, and the image contains private opnca-r. So:
- the build cache lives only in the private package `ghcr.io/jprybylski/opncafyr-cache`,
  never in the Actions cache, which fork PRs can read;
- no image or layer is ever uploaded as a workflow artifact;
- logs print only IDs, counts and hashes.

### One-time setup: create both packages as private

Done on 2026-11-08: both packages exist and are private (placeholder tag `bootstrap`).
Granting the repository Actions access (step 3 below) has no API and is done on GitHub.


CI refuses to push until `opncafyr` and `opncafyr-cache` exist and are private, so a
first push can never create a public package. Create both once with an **empty** image,
so nothing private is exposed even if a package came out public by mistake:

```sh
gh auth refresh -s write:packages,read:packages
gh auth token | docker login ghcr.io -u jprybylski --password-stdin
for pkg in opncafyr opncafyr-cache; do
  printf 'FROM scratch\nLABEL org.opencontainers.image.source=https://github.com/jprybylski/opncafyr\n' \
    | docker build --quiet -t "ghcr.io/jprybylski/$pkg:bootstrap" -
  docker push "ghcr.io/jprybylski/$pkg:bootstrap"
done
```

Then, for each package:
1. On GitHub, open **Your profile → Packages → `<package>` → Package settings**.
2. Check that the visibility is **Private**.
3. Under **Manage Actions access**, add `jprybylski/opncafyr` with the **Write** role.

Then run the workflow manually (**Actions → container → Run workflow**).

### Pulling the image

On the cloud VM, sign in with a token that can read packages (a fine-grained or classic
token with `read:packages`; the owner grants access to anyone else):

```sh
echo "$TOKEN" | docker login ghcr.io -u <user> --password-stdin   # or: podman login
docker pull ghcr.io/jprybylski/opncafyr:edge
```

Without registry access:
1. On a connected machine, run `docker save ghcr.io/jprybylski/opncafyr:edge | gzip > opncafyr.tar.gz`.
2. Carry the file over.
3. Run `docker load -i opncafyr.tar.gz`.

These archives stay private: attaching them to releases is #43.

## Cloud container mode

`container/opncafyr-container start <study folder>` (see the user guide):

- names the container `opncafyr-<hash of the folder's real path>`, one per study folder;
  a second `start` reattaches and prints a fresh link;
- publishes `127.0.0.1:<port>` only and uses its own bridge network. It never touches a
  firewall or a security group. **Don't open the port publicly:** behind an IDE proxy
  the IDE's login is the boundary, and nothing else stands in for it;
- runs as the invoking user (`--user`; rootless Podman adds `--userns=keep-id` and
  `--security-opt label=disable`, so SELinux hosts don't relabel the study folder), with
  `--cap-drop ALL` and `no-new-privileges`;
- keeps launcher state (settings, logs, the single-instance lock) in the named volume
  `opncafyr-<hash>-state` at `/state`, a real Linux filesystem on every engine (locks
  through Docker Desktop's virtiofs host shares don't exclude); `forget` deletes it;
- passes the image ID with `--container-image` and the access options to
  `opncafyr launch --container`.

Access routes (`--access`):

| Route | Public URL | Prefix | Host the browser sends |
|---|---|---|---|
| `ssh` (default) | `http://127.0.0.1:<local port>/` | none | `localhost`/`127.0.0.1:<local port>` |
| `vscode` | `https://<ide>/proxy/<port>/` (or `/absproxy/`) | yes | the IDE's; it forwards as `localhost:<port>` |
| `jupyter` | `https://<hub>/user/<name>/proxy/<port>/` | yes | the hub's |
| `workbench` | `https://<host>/s/<session>/p/<id>/` | yes | Workbench's |

The prefix works whether or not the proxy strips it. The settings are always explicit
options, never read from the environment, and never weaken a check (ADR 0009).

Supported storage for the study folder:

| Storage | Behaviour |
|---|---|
| Local disk (ext4, xfs and others) | allowed |
| NFS (EFS, Filestore), SMB (Azure Files) | allowed after a rename probe, with a note |
| Docker Desktop or WSL host shares (virtiofs, 9p, grpcfuse) | allowed after the probe |
| Object storage as a drive (s3fs, gcsfuse, blobfuse, goofys, mountpoint-s3, rclone and others) | refused |

### Container or native?

| | Native installer | Container |
|---|---|---|
| Where | Windows 11, macOS 15+ desktops | Linux cloud VMs with Docker or Podman |
| Admin rights | none | to run Docker or Podman (usually already granted) |
| opnca-r | prebuilt binary | built from the vendored source, in the image |
| Slide previews | optional LibreOffice download | the `full` image |
| Runs replay in the other | yes | yes |

Checked 2026-11-09: a run made through `opncafyr-container` (the arm64 `edge` image,
`mode_check.py --keep`) verified with `opncafyr runs verify` on a native Windows 11 x86_64
install, and its `analysis.R` replayed there with the launcher's replay job. All three
result tables matched exactly (largest relative difference 0, tolerance 1e-10).
`mode_check.py` also checks that each run records the running image's ID.
