#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) Q4_K_M as OpenAI-compatible API on RTX 5090
# 512K context: 27,807 MiB total / 4,271 MiB headroom on a 32 GB GPU — measured 2026-05-05.
# Per-token KV ~6.3 KB (MoE-efficient).
# 6 parallel slots (unified KV — no extra VRAM, ~85K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" \
  -a "Qwen 35B MoE" \
  -c 524288 \
  "$@"
