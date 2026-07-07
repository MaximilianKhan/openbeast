#!/bin/bash
# Qwopus3.6-27B-v2 Q5_K_M **with MTP** (Jackrong SFT fine-tune) — interactive CLI
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
#
# MTP launch flags:
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 4      draft 4 tokens ahead per step (tuned 2026-07-07,
#                             2.14x over baseline — see serve script)
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
#
# Context: 336K (-c 344064) — measured ceiling 2026-07-07
# (see comments in serve-qwopus-27b-v2-mtp-q5.sh for the numbers).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf" \
  -c 344064 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
