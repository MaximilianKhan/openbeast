# The Basalt build-and-serve environment (scripts/engines/basalt.sh builds it;
# docs/ENGINES.md "basalt"). It reproduces the image every measured Inkling
# row ran on (ob-strata-env:2026-11-23, Strata report §3 and §15.3) with its
# inputs pinned:
#   - the base by digest (the same digest as that image);
#   - every Ubuntu package from ONE archive snapshot, the archive as it was
#     when that image was built (2026-11-24 01:31Z = 17:31 PST 11-23), so the
#     whole package set is fixed in time, not just the named ones;
#   - the six packages Basalt's server imports pinned to the exact versions
#     that image held (dpkg-query in it, Strata report §15.3), and checked
#     again after the build by basalt.sh (_ENG_BASALT_PY_PINS).
# Basalt's own requirements.txt (pinned, unhashed, newer versions) and its
# Dockerfile (unpinned nvidia/cuda base, PyPI into a venv) are NOT used: the
# rows were measured on this distro set, and conformance and the thinking
# probes passed on it (§11.2). No venv exists, so `basalt serve` runs the
# system python3 (src/platform/system.cpp:152-154 at 400b411c).
# The network is used only by this build (the base pull and apt). The compile
# and every serve run with no egress. Offline: `engine.sh stage basalt` saves
# the built image (docker save + sha256 + image id), `install --from` loads
# and re-verifies it.
ARG BASE=nvidia/cuda:13.0.2-devel-ubuntu24.04@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6
FROM ${BASE}
# Empty = the live archive (the exact pins below still hold, so a superseded
# version fails the build loudly instead of drifting).
ARG UBUNTU_SNAPSHOT=20261124T013100Z
ENV DEBIAN_FRONTEND=noninteractive
# The CUDA apt repo is set aside for the install: nothing comes from it (the
# toolkit is the base image's), and an unpinned third-party index is one less
# input. Restored afterwards so the image is the base plus packages only.
RUN set -eu; \
    mkdir -p /root/apt-aside; \
    for f in /etc/apt/sources.list.d/cuda*; do [ -e "$f" ] && mv "$f" /root/apt-aside/; done; \
    if [ -n "${UBUNTU_SNAPSHOT}" ]; then \
      printf 'Types: deb\nURIs: http://snapshot.ubuntu.com/ubuntu/%s\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
        "${UBUNTU_SNAPSHOT}" > /etc/apt/sources.list.d/ubuntu.sources; \
      rm -f /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build git ca-certificates libgomp1 libatomic1 \
      python3=3.12.3-0ubuntu2.1 \
      python3-jinja2=3.1.2-1ubuntu1.3 \
      python3-markupsafe=2.1.5-1build2 \
      python3-regex=0.1.20221031-2build1 \
      python3-psutil=5.9.8-2build2 \
      python3-pil=10.2.0-1ubuntu1.5 \
      python3-yaml python3-numpy python3-tqdm; \
    rm -rf /var/lib/apt/lists/*; \
    for f in /root/apt-aside/*; do [ -e "$f" ] && mv "$f" /etc/apt/sources.list.d/; done; \
    rmdir /root/apt-aside; \
    dpkg-query -W -f '${Package}=${Version}\n' > /usr/share/openbeast-basalt-env.dpkg
LABEL org.opencontainers.image.title="openbeast-basalt-env" \
      org.opencontainers.image.description="Basalt build and serve environment for OpenBeast (scripts/engines/basalt.sh)"
