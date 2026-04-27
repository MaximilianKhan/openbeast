#!/bin/bash
# Qwen3.6-35B-A3B (MoE) Q4_K_M on RTX 5090 (32GB VRAM)
# KV cost not yet validated — estimated ~11 KB/token (40 layers vs 27B's 64).
# 512K context: ~22GB model + ~5.6GB KV (est.) = ~27.6GB. Headroom: ~4.4GB.
# TODO: validate KV allocation with a real launch.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" \
  -c 524288 \
  "$@"
