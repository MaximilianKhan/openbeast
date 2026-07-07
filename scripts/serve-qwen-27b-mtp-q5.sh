#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL **with MTP** (Multi-Token Prediction) on RTX 5090
# Model: unsloth/Qwen3.6-27B-MTP-GGUF — MTP heads baked into the same GGUF
# (~1.4 GB heavier than the non-MTP build).
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide + llama.cpp
# docs/speculative.md):
#   --spec-type draft-mtp     enables the MTP draft path
#   --spec-draft-n-max 8      drafts 8 tokens ahead per step
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
# Tuned empirically 2026-07-07: n8/p0.0 = 184 tok/s vs 66.8 baseline (2.75x),
# 55% draft acceptance. Deeper (n12+) or gated (p-min 0.5/0.75) configs all
# measured slower — MTP drafts are nearly free, so aggressive drafting wins.
# p-min does NOT affect output quality (target model verifies every draft).
#
# **CRITICAL CONSTRAINTS (upstream limitations, 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input.
#
# Context: 288K (-c 294912) — measured 2026-07-07 at the tuned n-max 8:
# 30,063 MiB used / 2,544 MiB headroom on the 32 GB card. n8's draft buffers
# cost ~600 MiB over n4, so 320K went TIGHT (30,959 MiB / 1,648 MiB); we
# trade 32K of context for the 2.75x decode speed. ~28 KB/token KV.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf" \
  -a "Qwen 27B MTP Q5" \
  -c 294912 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 8 \
  --spec-draft-p-min 0.0 \
  "$@"
