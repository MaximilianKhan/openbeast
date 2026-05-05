#!/bin/bash
# Gemma 4 31B-it (unsloth UD Q5_K_XL) on RTX 5090 (32GB VRAM)
# 220K context: 30,688 MiB total / 2,080 MiB headroom — measured ceiling for the 2GB rule.
# Per-token KV: 128K→200K=20 KB, 200K→250K=25 KB. Measured 2026-05-05.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/gemma-4-31B-it-UD-Q5_K_XL.gguf" \
  -c 225280 \
  "$@"
