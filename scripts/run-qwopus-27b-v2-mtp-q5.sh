#!/bin/bash
# Qwopus3.6-27B-v2 Q5_K_M **with MTP** (Jackrong SFT fine-tune) — interactive CLI
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
#
# MTP launch flags:
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 2      draft 2 tokens ahead per step
#
# Context: 256K (-c 262144) — conservative starting point pending measurement
# (see comments in serve-qwopus-27b-v2-mtp-q5.sh for rationale).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf" \
  -c 262144 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  "$@"
