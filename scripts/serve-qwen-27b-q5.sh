#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL as OpenAI-compatible API on RTX 5090
# 350K context (-c 358400): reduced from 416K on 2026-05-22 — the 416K default
# left only ~2 GB headroom and was crashing under sustained OS+KV pressure.
# Historical measurement: 416K = 30,711 MiB / 2,057 MiB headroom (2026-05-05).
# 6 parallel slots (unified KV — no extra VRAM, ~58K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -a "Qwen 27B Q5" \
  -c 358400 \
  "$@"
