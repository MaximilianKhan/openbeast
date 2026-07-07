#!/bin/bash
# Qwopus3.6-27B-v2 Q5_K_M (Jackrong SFT fine-tune) on RTX 5090 — interactive CLI
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-GGUF
#
# Context: 416K (-c 425984) — measured ceiling 2026-07-07. See
# serve-qwopus-27b-v2-q5.sh for the numbers and the YaRN-extension caveat.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwopus3.6-27B-v2-Q5_K_M.gguf" \
  -c 425984 \
  "$@"
