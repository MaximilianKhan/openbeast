#!/bin/bash
# Qwen3.6-27B Q5_K_XL on RTX 5090 (32GB VRAM)
# Real-world KV cost: ~18 KB/token (llama.cpp allocates KV for all 64 layers).
# 416K context: ~19GB model + ~7.3GB KV = ~26.3GB. ~2GB free after OS GPU usage.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -c 425984 \
  "$@"
