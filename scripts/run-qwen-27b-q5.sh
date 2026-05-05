#!/bin/bash
# Qwen3.6-27B Q5_K_XL on RTX 5090 (32GB VRAM)
# 416K context: 30,711 MiB total / 2,057 MiB headroom — measured 2026-05-05.
# Right at the 2GB rule (9 MiB margin). Drop to 408K (-c 417792) if OS spikes cause OOM.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -c 425984 \
  "$@"
