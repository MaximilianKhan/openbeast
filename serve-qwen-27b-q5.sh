#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL as OpenAI-compatible API on RTX 5090
# 416K context: ~19GB model + ~7.3GB KV = ~26.3GB. ~2GB free after OS GPU usage.
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -c 425984 \
  "$@"
