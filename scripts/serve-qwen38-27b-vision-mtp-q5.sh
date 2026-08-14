#!/bin/bash
# Serve Qwen3.8-27B UD-Q5_K_XL with **vision AND MTP** on RTX 5090
#
# ** THIS CONFIG WAS SUPPOSED TO BE IMPOSSIBLE. ** Every MTP serve script we
# ship carries the note "--mmproj is not yet supported with MTP", inherited
# from a 2026-05-22 upstream limitation. That is NO LONGER TRUE on our build:
# there is no such guard anywhere in the current llama.cpp source, and the
# combination was verified working end-to-end on 2026-08-14 — correct image
# readback (code string + three shapes/colours, left to right) with the MTP
# draft path live at 64.6% acceptance on the same request.
#
# Scope of that finding: proven for arch `qwen35` (Qwen3.8) on our build only.
# The Qwen3.6-era MTP scripts still carry the old claim and have NOT been
# retested — do not assume it is lifted for them without measuring.
#
# Context: 229376 (224K) — this config gives up 32K of the native ceiling, and
# it is the vision tower plus the MTP draft buffers together that force it.
# MEASURED 2026-08-14 on the 32 GB card:
#   262144 + vision + MTP -> 31,555 MiB / 1,052 MiB free  <-- crash zone, NO
#   229376 + vision + MTP -> 29,116 MiB / 3,491 MiB free  <-- shipped
#   196608 + vision + MTP -> 28,219 MiB / 4,388 MiB free  (extra safety margin)
# The 262144 row loads and answers correctly, but 1,052 MiB of headroom is far
# inside the sustained-load crash zone that cost us Gemma at 220K and Qwopus at
# 352K. Not shipping a config that dies under load.
#
# Want the full native 262K with vision? Drop MTP: serve-qwen38-27b-vision-q5.sh
# holds 262144 at 4,869 MiB free. The trade here is 32K of context for 1.83x
# decode.
#
# Image cost: one token per 32x32 px (patch 16, spatial_merge 2) — a 1024x1024
# image is 1,024 tokens. See serve-qwen38-27b-vision-q5.sh for how the
# projector splices into the embedding stream.
#
# `-np 1` is forced by MTP (no parallel slots). n-max 4 is the tuned peak for
# this quant — see serve-qwen38-27b-mtp-q5.sh for the full sweep.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-UD-Q5_K_XL.gguf" \
  --mmproj "$WEIGHTS_DIR/mmproj-F16.gguf" \
  -a "Qwen3.8 27B Vision MTP Q5" \
  -c 229376 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
