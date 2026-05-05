#!/bin/bash
# Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090 (32GB VRAM)
# 380K context: 30,648 MiB total / 2,120 MiB headroom — measured 2026-05-05.
# 416K previously used 31,405 MiB (1.36GB free, OOM risk).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -c 389120 \
  "$@"
