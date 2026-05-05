#!/bin/bash
# Serve Gemma 4 31B-it (unsloth UD Q5_K_XL) as OpenAI-compatible API on RTX 5090
# 220K context: 30,688 MiB total / 2,080 MiB headroom — measured ceiling for the 2GB rule.
# Per-token KV: 128K→200K=20 KB, 200K→250K=25 KB. Heavier slots above 200K.
# 6 parallel slots, ~37K context per slot. Measured 2026-05-05.
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/gemma-4-31B-it-UD-Q5_K_XL.gguf" \
  -a "Gemma 4 31B" \
  -c 225280 \
  "$@"
