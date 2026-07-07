#!/bin/bash
# Serve Gemma 4 31B-it (unsloth UD Q5_K_XL) as OpenAI-compatible API on RTX 5090
# 192K context (reduced from 220K on 2026-05-08 after a v3.5 sweep death between
# tasks 10–11; the 2,080 MiB headroom at 220K was too tight under sustained KV
# pressure). 192K still comfortably exceeds the largest observed eval prompt
# (~47K), and unified KV lets a single sequential request use the whole pool.
# Per-token KV: 128K→200K=20 KB. 6 parallel slots, ~32K per slot at unified-c.
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/gemma-4-31B-it-UD-Q5_K_XL.gguf" \
  -a "Gemma 4 31B" \
  -c 196608 \
  "$@"
