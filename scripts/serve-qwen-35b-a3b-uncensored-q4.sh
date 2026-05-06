#!/bin/bash
# Serve Qwen3.6-35B-A3B Uncensored (HauhauCS Aggressive) Q4_K_M as OpenAI-compatible API.
# 512K context: 27,139 MiB total / 4,939 MiB headroom on a 32 GB GPU — measured 2026-05-05.
# Per-token KV ~6.3 KB (MoE-efficient), 6 parallel slots (~85K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf" \
  -a "Qwen 35B MoE Uncensored" \
  -c 524288 \
  "$@"
