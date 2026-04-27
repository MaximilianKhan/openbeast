#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) Q4_K_M as OpenAI-compatible API on RTX 5090
# 512K context: ~22GB model + ~5.6GB KV (est.) = ~27.6GB. Headroom: ~4.4GB.
# TODO: validate KV allocation with a real launch.
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" \
  -c 524288 \
  "$@"
