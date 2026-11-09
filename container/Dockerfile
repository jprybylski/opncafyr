# syntax=docker/dockerfile:1.10
# check=skip=InvalidDefaultArgInFrom
#
# opncafyr container image (#42). Build it with container/build.sh (local) or
# .github/workflows/container.yml (CI); both take every build argument from
# runtime/pins.toml through scripts/container_args.py, never by hand.
#
#   targets: slim (default final image) and full (slim + LibreOffice for deck previews)
#   secret:  gh_token, a token that can read mantra-opnca/opnca-r releases (BuildKit
#            secret mount only; never an ARG or ENV, so it is in no layer or history)
#
# Stages:
#   base    pinned Ubuntu + the locked runtime packages (container/apt/)
#   fetch   downloads and hash-checks every pinned file (scripts/offline/fetch.py)
#   assets  the fetched folder; replace it with --build-context assets=<dir> to build from
#           a folder fetched and verified elsewhere (offline or customer-run builds, #43)
#   wheel   opncafyr's own wheel (the only stage that reads src/)
#   build   R's .deb, compilers and Rust; installs the runtime with the network cut
#           (scripts/offline/install_runtime.sh, the same path as the offline CI job)
#   launcher  build + the opncafyr wheel, in its own layer
#   slim    base + the built /opt/R and /opt/opncafyr; no compilers, no Rust, no assets
#   full    slim + LibreOffice from TDF's pinned .deb set
#
# Research use only: a working image proves the parts assemble, not the science.

ARG BASE_IMAGE

FROM ${BASE_IMAGE} AS base
ARG PLATFORM
# Optional Ubuntu mirror (container/apt/install.sh); not a pin: versions come from the locks.
ARG APT_MIRROR=
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
COPY container/apt/ /opt/opncafyr-apt/
RUN test -n "${PLATFORM}" && APT_MIRROR="${APT_MIRROR}" /opt/opncafyr-apt/install.sh "/opt/opncafyr-apt/lock-${PLATFORM}-runtime.txt"

FROM base AS fetch
ARG PLATFORM
ARG UV_URL
ARG UV_SHA256
ADD --checksum=sha256:${UV_SHA256} ${UV_URL} /tmp/uv.tar.gz
RUN tar --no-same-owner -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 && rm /tmp/uv.tar.gz
WORKDIR /src
ENV UV_CACHE_DIR=/tmp/uv-cache UV_PYTHON_INSTALL_DIR=/tmp/uv-python UV_NO_PROGRESS=1
# Only what the pins and locks need, so a source change doesn't refetch anything.
COPY pyproject.toml uv.lock .python-version /src/
COPY runtime/ /src/runtime/
COPY scripts/offline/ /src/scripts/offline/
RUN --mount=type=secret,id=gh_token,env=GH_TOKEN,required=true \
    uv run --no-project --python 3.13 python scripts/offline/fetch.py \
      --platform "${PLATFORM}" --opnca-release --with-libreoffice --no-launcher-wheel \
      --out /assets \
 && rm -rf /tmp/uv-cache

FROM scratch AS assets
COPY --from=fetch /assets /

# opncafyr's own wheel, in its own stage: a source change rebuilds only this and the
# layer that installs it.
FROM fetch AS wheel
COPY src/ /src/src/
COPY docs/user-guide/ /src/docs/user-guide/
COPY README.md VERSION /src/
RUN uv build --wheel --quiet --out-dir /wheel && rm -rf /tmp/uv-cache

FROM base AS build
ARG PLATFORM
ARG APT_MIRROR=
ARG R_VERSION
ARG R_DEB_FILE
RUN APT_MIRROR="${APT_MIRROR}" /opt/opncafyr-apt/install.sh "/opt/opncafyr-apt/lock-${PLATFORM}-build.txt"
RUN --mount=type=bind,from=assets,target=/assets \
    dpkg -i "/assets/runtime/${R_DEB_FILE}" && test -x "/opt/R/${R_VERSION}/bin/Rscript"
# The install itself runs with no network, exactly as in the offline CI job (#44). Only
# the scripts and locks it reads are mounted, so the cache survives source changes.
RUN --network=none \
    --mount=type=bind,from=assets,target=/assets \
    --mount=type=bind,source=scripts/offline,target=/src/scripts/offline \
    --mount=type=bind,source=runtime,target=/src/runtime \
    RSCRIPT="/opt/R/${R_VERSION}/bin/Rscript" GIT_EXE=/usr/bin/git RESULTS=/tmp/results \
    bash /src/scripts/offline/install_runtime.sh /assets /opt/opncafyr

FROM build AS launcher
RUN --network=none --mount=type=bind,from=wheel,source=/wheel,target=/wheel \
    uv="$(ls /opt/opncafyr/rt/*/uv)" \
 && UV_CACHE_DIR=/tmp/uv-cache UV_OFFLINE=1 "$uv" pip install --quiet --no-deps \
      --python /opt/opncafyr/envs/launcher/.venv/bin/python /wheel/opncafyr-*.whl \
 && rm -rf /tmp/uv-cache \
 && /opt/opncafyr/envs/launcher/.venv/bin/python -c "import opncafyr.cli"

FROM base AS slim
ARG OPNCAFYR_VERSION
ARG PINS_SHA256
ARG GIT_SHA=unknown
ARG R_VERSION
LABEL org.opencontainers.image.title="opncafyr" \
      org.opencontainers.image.description="opncafyr workspace launcher with its pinned tools (research use only)" \
      org.opencontainers.image.source="https://github.com/jprybylski/opncafyr" \
      org.opencontainers.image.version="${OPNCAFYR_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      io.opncafyr.pins.sha256="${PINS_SHA256}" \
      io.opncafyr.variant="slim"
COPY --from=launcher /opt/R/${R_VERSION} /opt/R/${R_VERSION}
COPY --from=launcher /opt/opncafyr /opt/opncafyr
# /state (launcher state) and /workspace (the study folder) are mounted by
# container/opncafyr-container; HOME is a throwaway, since the run-time user may have no
# passwd entry. Every tool gets its own HOME and caches from its adapter anyway.
RUN mkdir -p /state /workspace /tmp/home && chmod 1777 /state /workspace /tmp/home
ENV OPNCAFYR_RUNTIME_FILE=/opt/opncafyr/runtime/runtime.json \
    OPNCAFYR_APP_ROOT=/state \
    HOME=/tmp/home \
    PATH=/opt/opncafyr/envs/launcher/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
USER 65532:65532
WORKDIR /workspace
EXPOSE 8765
ENTRYPOINT ["opncafyr"]
CMD ["launch", "--container", "--port", "8765", "--workspace", "/workspace"]

FROM slim AS full
ARG PLATFORM
ARG APT_MIRROR=
ARG LIBREOFFICE_FILE
USER root
RUN --mount=type=bind,from=assets,target=/assets \
    APT_MIRROR="${APT_MIRROR}" /opt/opncafyr-apt/install.sh "/opt/opncafyr-apt/lock-${PLATFORM}-full.txt" \
 && mkdir /tmp/lo \
 && tar --no-same-owner -xzf "/assets/runtime/${LIBREOFFICE_FILE}" -C /tmp/lo --strip-components=1 \
 && dpkg -i /tmp/lo/DEBS/*.deb \
 && rm -rf /tmp/lo \
 && soffice="$(ls /opt/libreoffice*/program/soffice)" \
 && /opt/opncafyr/rt/python/bin/python3 -c 'import json, sys; p = "/opt/opncafyr/runtime/runtime.json"; r = json.load(open(p)); r["executables"]["soffice"] = sys.argv[1]; json.dump(r, open(p, "w"), indent=2)' "$soffice"
LABEL io.opncafyr.variant="full"
USER 65532:65532
