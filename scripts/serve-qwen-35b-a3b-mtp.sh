#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) Q4_K_M **with MTP** on RTX 5090
# Model: unsloth/Qwen3.6-35B-A3B-MTP-GGUF — MTP heads baked into the same GGUF
# (~0.7 GB heavier than the non-MTP build).
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide):
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 2      draft 2 tokens ahead per step
#
# **CRITICAL CONSTRAINTS (upstream limitations, 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input.
#
# Context: 384K (-c 393216) — conservative starting point pending measurement.
# The non-MTP 35B-A3B runs at 512K with ~4.3 GB headroom; MTP adds ~0.7 GB of
# head weights plus draft buffers, so we drop ~25% to stay safe. Re-measure
# and raise after a clean launch under real load.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf" \
  -a "Qwen 35B MoE MTP" \
  -c 393216 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
