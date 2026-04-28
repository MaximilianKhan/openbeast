#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) Q4_K_M as OpenAI-compatible API on RTX 5090
# 512K context: ~20GB model + ~3.1GB KV = ~23.1GB. Headroom: ~8.9GB.
# Measured 2026-04-27: KV cost ~6.3 KB/token (very efficient MoE).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$SCRIPT_DIR/weights/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" \
  -a "Qwen 35B MoE" \
  -c 524288 \
  "$@"
