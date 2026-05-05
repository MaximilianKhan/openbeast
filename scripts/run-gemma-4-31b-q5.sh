#!/bin/bash
# Gemma 4 31B-it (unsloth UD Q5_K_XL) on RTX 5090 (32GB VRAM)
# 200K context: ~20.4GB model + KV (TBD at this length). Measured 28,680 MiB at 128K.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/gemma-4-31B-it-UD-Q5_K_XL.gguf" \
  -c 204800 \
  "$@"
