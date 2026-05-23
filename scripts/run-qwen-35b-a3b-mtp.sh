#!/bin/bash
# Qwen3.6-35B-A3B (MoE) Q4_K_M **with MTP** on RTX 5090 (32GB VRAM) — interactive CLI
# Model: unsloth/Qwen3.6-35B-A3B-MTP-GGUF (MTP heads baked into the GGUF,
# ~0.7 GB heavier than the non-MTP build).
#
# MTP launch flags:
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 2      draft 2 tokens ahead per step
#
# Context: 384K (-c 393216) — conservative starting point pending measurement
# (see comments in serve-qwen-35b-a3b-mtp.sh for rationale).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf" \
  -c 393216 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
