#!/bin/bash
# Serve Qwopus3.6-27B-v2 Q5_K_M **with MTP** (Jackrong SFT fine-tune) on RTX 5090
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
# Reasoning-enhanced fine-tune (Trace Inversion) with MTP draft heads baked
# into the same GGUF.
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide + llama.cpp
# docs/speculative.md):
#   --spec-type draft-mtp     enables the MTP draft path
#   --spec-draft-n-max 2      drafts 2 tokens ahead per step (safe default)
#
# **CRITICAL CONSTRAINTS (upstream MTP limitations, llama.cpp 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input (despite
#     the base Qwopus model supporting vision).
#
# Context: 256K (-c 262144) — conservative starting point pending real
# measurement, matches our other 27B MTP variant. MTP heads + draft buffers
# eat into the headroom we'd otherwise have at 350K. Re-measure and raise
# (or lower, if YaRN is broken in this conversion) after a clean launch.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf" \
  -a "Qwopus 27B v2 MTP Q5" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
