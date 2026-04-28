#!/bin/bash
# Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090 (32GB VRAM)
# ~21GB model + ~7.3GB KV at 416K = ~28.3GB. Headroom: ~2GB after OS.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -c 425984 \
  "$@"
