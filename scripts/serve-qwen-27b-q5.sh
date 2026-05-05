#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL as OpenAI-compatible API on RTX 5090
# 416K context: 30,711 MiB total / 2,057 MiB headroom — measured 2026-05-05.
# Tightest of our 32GB-card configs (only 9 MiB above the 2GB rule). If OS spikes
# cause OOMs, drop to 408K (-c 417792) for ~2.1GB headroom.
# 6 parallel slots (unified KV — no extra VRAM, ~69K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -a "Qwen 27B Q5" \
  -c 425984 \
  "$@"
