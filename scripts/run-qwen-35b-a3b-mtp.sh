#!/bin/bash
# Qwen3.6-35B-A3B (MoE) Q4_K_M **with MTP** on RTX 5090 (32GB VRAM) — interactive CLI
# Model: unsloth/Qwen3.6-35B-A3B-MTP-GGUF (MTP heads baked into the GGUF,
# ~0.7 GB heavier than the non-MTP build).
#
# MTP launch flags:
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 4      draft 4 tokens ahead per step (tuned 2026-07-07,
#                             1.46x over baseline — see serve script)
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
#
# Context: 512K (-c 524288) — measured ceiling 2026-07-07
# (see comments in serve-qwen-35b-a3b-mtp.sh for the numbers).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf" \
  -c 524288 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
