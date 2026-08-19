#!/bin/bash
# Qwen3.8-27B-Uncensored Q5_K_M interactive chat on RTX 5090 (32GB VRAM)
# Full native 262K context — the hybrid Gated-DeltaNet stack (arch `qwen35`,
# full_attention_interval 4) makes KV ~18 KB/token, so the native ceiling fits
# with ~6.4 GB to spare. See scripts/serve-qwen38-27b-uncensored-q5.sh for the
# measurements and scripts/serve-qwen38-27b-uncensored-mtp-q5.sh for the
# 2.0x-faster MTP launch config that ships as the default.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Uncensored-Q5_K_M.gguf" \
  -c 262144 \
  "$@"
