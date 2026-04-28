#!/bin/bash
# Serve Qwen3.6-27B Q4_K_M as OpenAI-compatible API on RTX 5090
# 512K context: ~16GB model + ~9GB KV = ~25GB. Headroom: ~7GB.
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-27B-Q4_K_M.gguf" \
  -a "Qwen 27B Q4" \
  -c 524288 \
  "$@"
