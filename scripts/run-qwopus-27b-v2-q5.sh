#!/bin/bash
# Qwopus3.6-27B-v2 Q5_K_M (Jackrong SFT fine-tune) on RTX 5090 — interactive CLI
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-GGUF
#
# Context: 350K (-c 358400) — matches our other 27B variants. See
# serve-qwopus-27b-v2-q5.sh for the YaRN-extension caveat.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwopus3.6-27B-v2-Q5_K_M.gguf" \
  -c 358400 \
  "$@"
