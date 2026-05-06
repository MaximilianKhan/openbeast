#!/bin/bash
# Qwen3.6-35B-A3B Uncensored (HauhauCS Aggressive) Q4_K_M on RTX 5090 (32GB VRAM)
# 512K context: 27,139 MiB total / 4,939 MiB headroom — measured 2026-05-05.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf" \
  -c 524288 \
  "$@"
