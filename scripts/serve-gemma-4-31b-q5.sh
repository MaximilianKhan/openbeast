#!/bin/bash
# Serve Gemma 4 31B-it (unsloth UD Q5_K_XL) as OpenAI-compatible API on RTX 5090
# 200K context: ~20.4GB model + KV (TBD at this length). Measured 28,680 MiB at 128K.
# 6 parallel slots (unified KV — no extra VRAM, ~33K context per slot at 200K).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/gemma-4-31B-it-UD-Q5_K_XL.gguf" \
  -a "Gemma 4 31B" \
  -c 204800 \
  "$@"
