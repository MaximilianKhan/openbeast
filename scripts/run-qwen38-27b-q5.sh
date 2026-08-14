#!/bin/bash
# Qwen3.8-27B UD-Q5_K_XL interactive chat on RTX 5090 (32GB VRAM)
# Full native 262K context — the hybrid Gated-DeltaNet stack (arch `qwen35`,
# full_attention_interval 4) makes KV ~18 KB/token, so the native ceiling fits
# with 5.9 GB to spare. See scripts/serve-qwen38-27b-q5.sh for measurements.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-UD-Q5_K_XL.gguf" \
  -c 262144 \
  "$@"
