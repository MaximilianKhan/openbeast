#!/bin/bash
# Qwen3.8-27B Q6_K interactive chat on RTX 5090 (32GB VRAM)
# Full native 262K context — holds even at 6-bit thanks to the hybrid
# Gated-DeltaNet KV. See scripts/serve-qwen38-27b-q6.sh for measurements.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Q6_K.gguf" \
  -c 262144 \
  "$@"
