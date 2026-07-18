#!/bin/bash
# Serve Qwen3.6-27B Uncensored Heretic v2 (llmfan46) Q6_K **with MTP** as
# OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF
#   Same fine-tune as serve-heretic-v2-27b-mtp-q5.sh at the higher-fidelity
#   Q6_K quant (near-lossless). Reasoning ON. NATIVE MTP PRESERVED (15 original
#   Qwen3.6 MTP heads, KL 0.0021 vs base).
#
# MTP launch flags (same proven pattern):
#   --spec-type draft-mtp / --spec-draft-n-max 8 / --spec-draft-p-min 0.0
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0
#
# ⚠️ n-max 8 IS AN ESTIMATE (native-preserved MTP → base-like). Confirm with
#   ./scripts/profile-heretic-v2-mtp.sh q6   and set the real optimum here.
#
# ⚠️ MTP REQUIREMENTS: temperature ≤ 1.0, repetition_penalty = 1.0 (client-side),
# or acceptance/speed degrade; <50% acceptance → use a non-MTP quant.
#
# CONSTRAINTS (MTP): -np 1 forced; --mmproj unsupported.
#
# ⚠️ CONTEXT/VRAM IS AN ESTIMATE (2026-07-17). Q6_K (~22.8 GB — ~1.2 GB lighter
# than the NEO Q6 MTP, which measured 180224 at 2.5 GB free) should hold MORE
# context: -c 229376 is the conservative start and it will likely take 245760.
# Validate/raise with scripts/measure-vram.sh + the profile script.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q6_K.gguf" \
  -a "Heretic v2 27B MTP Q6" \
  -c 229376 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 8 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
