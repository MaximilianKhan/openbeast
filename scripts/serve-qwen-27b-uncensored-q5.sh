#!/bin/bash
# Serve Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090
# 380K context: 30,648 MiB total / 2,120 MiB headroom — measured 2026-05-05.
# 416K previously used 31,405 MiB (1.36GB free, OOM risk).
# Per-token KV at high context is ~20 KB, denser than original 18 KB estimate.
# 6 parallel slots (unified KV — no extra VRAM, ~63K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -a "Qwen 27B Uncensored" \
  -c 389120 \
  "$@"
