#!/bin/bash
# Serve Qwen3.6-27B Uncensored Heretic v2 (llmfan46) Q6_K **with MTP** as
# OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF
#   Same fine-tune as serve-heretic-v2-27b-mtp-q5.sh at the higher-fidelity
#   Q6_K quant (near-lossless). Reasoning ON. NATIVE MTP PRESERVED (15 original
#   Qwen3.6 MTP heads, KL 0.0021 vs base).
#
# MTP launch flags (same proven pattern):
#   --spec-type draft-mtp / --spec-draft-n-max 4 / --spec-draft-p-min 0.0
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0
#
# n-max 4 is the MEASURED optimum (scripts/profile-heretic-v2-mtp.sh q6,
# 2026-07-17): decode n1 99 / n2 128 / n4 140 / n6 128 / n8 124 / n10 119
# tok/s — a clean peak at n4 (unlike the Q5's flat plateau; same base, but
# the heavier quant shifts the sweet spot). Draft acceptance 0.60 (mean len
# 3.41) at n4.
#
# ⚠️ MTP REQUIREMENTS: temperature ≤ 1.0, repetition_penalty = 1.0 (client-side),
# or acceptance/speed degrade; <50% acceptance → use a non-MTP quant.
#
# CONSTRAINTS (MTP): -np 1 forced; --mmproj unsupported.
#
# MEASURED on the 5090 (2026-07-17, n-max 4): -c 212992 uses 30,360 MiB /
# 2,247 MiB free — safe. The ladder above breached the 2 GB rule (229376 =
# 1,781 free, 245760 = 1,315, 262144 = 847), so 212992 is the ceiling with
# real headroom — more than the NEO Q6 MTP's 176K (this quant is lighter).
# Decode ~139 tok/s greedy (the fastest build measured), acceptance 0.60.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q6_K.gguf" \
  -a "Heretic v2 27B MTP Q6" \
  -c 212992 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
