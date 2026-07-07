#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL **with MTP** (Multi-Token Prediction) on RTX 5090
# Model: unsloth/Qwen3.6-27B-MTP-GGUF — MTP heads baked into the same GGUF
# (~1.4 GB heavier than the non-MTP build).
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide + llama.cpp
# docs/speculative.md):
#   --spec-type draft-mtp     enables the MTP draft path
#   --spec-draft-n-max 2      drafts 2 tokens ahead per step (unsloth recommended)
#
# **CRITICAL CONSTRAINTS (upstream limitations, 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input.
#
# Context: 256K (-c 262144) — conservative starting point pending real
# measurement. The non-MTP 27B Q5_K_XL runs at 350K with ~3.2 GB headroom;
# MTP adds ~1.4 GB of head weights plus draft buffers, so we drop ~25% to
# stay well above the 2 GB OS-headroom rule. Re-measure and raise after a
# clean launch under real load.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf" \
  -a "Qwen 27B MTP Q5" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
