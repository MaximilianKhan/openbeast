#!/bin/bash
# Serve Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090
# 416K context: ~21GB model + ~7.3GB KV = ~28.3GB. Headroom: ~2GB after OS.
# 7 parallel slots (unified KV — no extra VRAM, ~59K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -a "Qwen 27B Uncensored" \
  -c 425984 \
  "$@"
