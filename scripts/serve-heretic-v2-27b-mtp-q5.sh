#!/bin/bash
# Serve Qwen3.6-27B Uncensored Heretic v2 (llmfan46) Q5_K_M **with MTP** as
# OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF
#   Qwen3.6-27B (arch qwen35, 64 layers, hybrid Gated DeltaNet + Attention),
#   reasoning ON by default. "Heretic v2" = Heretic v1.3.0 + Magnitude-
#   Preserving Orthogonal Ablation (MPOA) uncensoring (94% fewer refusals).
#   NATIVE MTP PRESERVED: all 15 original Qwen3.6 MTP heads kept (KL 0.0021 vs
#   base — NOT retrained), so the draft path should behave like the base
#   unsloth 27B MTP, not like DavidAU's modified NEO head.
#
# MTP launch flags (same proven pattern as our other Qwen3.6 MTP builds):
#   --spec-type draft-mtp / --spec-draft-n-max 8 / --spec-draft-p-min 0.0
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0
#
# n-max 8 is the MEASURED peak (scripts/profile-heretic-v2-mtp.sh q5,
# 2026-07-17): decode n1 109 / n2 134 / n4 135 / n6 120 / n8 138 / n10 133
# tok/s. The native-preserved head accepts drafts well at depth (mean len
# 4.1 @ n8), so unlike DavidAU's NEO builds (n2) this stays fast deep —
# n2/n4/n8 are a flat plateau within run-to-run noise; n8 is the top and Q5
# has the VRAM to spare, so we ship it. (Confirms the native-MTP hypothesis:
# preserved heads behave like the base unsloth 27B MTP, which also peaked n8.)
#
# ⚠️ DavidAU/Qwen MTP REQUIREMENTS: temperature ≤ 1.0, repetition_penalty = 1.0
# (set client-side), else draft acceptance and speed degrade; if acceptance
# stays under ~50% (server log), use a non-MTP quant.
#
# CONSTRAINTS (MTP, upstream): -np 1 forced; --mmproj vision unsupported (the
# repo ships a separate mmproj, incompatible with MTP anyway).
#
# MEASURED on the 5090 (2026-07-17, n-max 8): -c 262144 (native ceiling) uses
# 29,633 MiB / 2,974 MiB free — safe. Decode ~136 tok/s greedy — the FASTEST
# MTP build in the lineup (native-preserved heads beat the NEO Q5's ~108).
# Draft acceptance 0.39 (mean len 4.13) at n8.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf" \
  -a "Heretic v2 27B MTP Q5" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 8 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
