# syntax=docker/dockerfile:1.7
#
# The Open-Jev loader image for beast-instinct's openjev_head engine
# (scripts/serve-openjev.sh). It holds CODE ONLY — torch, transformers, peft
# and the Open-Jev loader at a pinned commit. The base weights and the
# Open-Jev-27B-v1.1 checkpoint are mounted READ-ONLY at run time, and the
# container runs with HF_HUB_OFFLINE=1: it never downloads anything.
#
# This is deliberately NOT part of the rig's Python lock (agents/requirements*):
# a heavy CUDA stack lives here, on the dedicated GPU host, and nowhere else.
#
# Build ON the GPU host (a Spark is arm64/GB10; the 5090 host is x86_64):
#   docker build -t openbeast-openjev:1 scripts/instinct/openjev
#   docker image inspect --format '{{index .RepoDigests 0}}' openbeast-openjev:1   # or push+pull
# then run with OPENJEV_IMAGE=<repo>@sha256:<digest> (serve-openjev.sh refuses a tag).
#
# [HW] VERIFY on the build host: that torch 2.8.0's cu128 wheel exists for the
# host arch (aarch64 on a Spark) and runs on its GPU (sm_121 GB10 / sm_120
# 5090); then freeze the resolved set WITH hashes (pip-compile
# --generate-hashes requirements.txt) and switch the install below to
# --require-hashes. Pin BASE_IMAGE by digest the same way.

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# Open-Jev loader, pinned: github.com/Zefan-Cai/Open-Jev (MIT).
ARG OPENJEV_COMMIT=3308a15ccd7eea1df7a37d6ddc39b023b801ba16
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

COPY requirements.txt /tmp/openjev-requirements.txt
RUN pip install --no-cache-dir --index-url "${TORCH_INDEX_URL}" torch==2.8.0 \
 && pip install --no-cache-dir -r /tmp/openjev-requirements.txt

ADD --keep-git-dir=false https://github.com/Zefan-Cai/Open-Jev.git#${OPENJEV_COMMIT} /app
WORKDIR /app
# --no-deps: the pinned set above stays authoritative.
RUN pip install --no-cache-dir --no-deps -e . \
 && useradd --create-home --uid 10001 jev

USER jev
EXPOSE 8791
LABEL org.openbeast.role="instinct-engine" \
      org.openbeast.openjev.commit="${OPENJEV_COMMIT}" \
      org.openbeast.package="ZefanCai/Open-Jev-27B-v1.1@28cf73067d5b337860bbef3c85b8b82ba8730956"
