#!/bin/bash
# Qwen3.6-35B-A3B (MoE) Q4_K_M on RTX 5090 (32GB VRAM)
# 512K context: 27,807 MiB total / 4,271 MiB headroom — measured 2026-05-05.
# Per-token KV ~6.3 KB (MoE-efficient).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" \
  -c 524288 \
  "$@"
