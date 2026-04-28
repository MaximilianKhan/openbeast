#!/bin/bash
# Qwen3.6-27B Q4_K_M on RTX 5090 (32GB VRAM)
# Real-world KV cost: ~18 KB/token (llama.cpp allocates KV for all 64 layers).
# 512K context: ~16GB model + ~9GB KV = ~25GB. Headroom: ~7GB.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-Q4_K_M.gguf" \
  -c 524288 \
  "$@"
