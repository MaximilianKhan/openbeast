#!/bin/bash
# Qwen3.6-27B Q5_K_XL **with MTP** on RTX 5090 (32GB VRAM) — interactive CLI
# Model: unsloth/Qwen3.6-27B-MTP-GGUF (MTP heads baked into the GGUF, ~1.4 GB
# heavier than the non-MTP build).
#
# MTP launch flags:
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 2      draft 2 tokens ahead per step
#
# Context: 256K (-c 262144) — conservative starting point pending measurement
# (see comments in serve-qwen-27b-mtp-q5.sh for rationale).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf" \
  -c 262144 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
