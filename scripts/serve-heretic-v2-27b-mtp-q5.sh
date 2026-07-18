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
# ⚠️ n-max 8 IS AN ESTIMATE — chosen because the MTP is NATIVE-PRESERVED
# (base unsloth 27B MTP peaked at n8). But do NOT trust it: our DavidAU NEO
# builds surprised us at n2. Run  ./scripts/profile-heretic-v2-mtp.sh q5
# once downloaded and set the real optimum here.
#
# ⚠️ DavidAU/Qwen MTP REQUIREMENTS: temperature ≤ 1.0, repetition_penalty = 1.0
# (set client-side), else draft acceptance and speed degrade; if acceptance
# stays under ~50% (server log), use a non-MTP quant.
#
# CONSTRAINTS (MTP, upstream): -np 1 forced; --mmproj vision unsupported (the
# repo ships a separate mmproj, incompatible with MTP anyway).
#
# ⚠️ CONTEXT/VRAM IS AN ESTIMATE (2026-07-17). Q5_K_M (~19.7 GB — ~1.5 GB
# lighter than the NEO Q5) should hold the full native 262144 with comfortable
# headroom (cf. the 21.2 GB fable-fusion Q5 MTP fits 262144 at ~2.7 GB free).
# Validate/raise with scripts/measure-vram.sh + the profile script.
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
