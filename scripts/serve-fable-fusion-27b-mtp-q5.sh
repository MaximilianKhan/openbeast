#!/bin/bash
# Serve Qwen3.6-27B Fable-Fusion-711 (DavidAU) Q5_K_M **with MTP** (Multi-Token
# Prediction) as OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF
#   Same fine-tune as serve-fable-fusion-27b-q5.sh, with MTP draft heads baked
#   into the GGUF (~0.5 GB heavier). Reasoning/thinking ON by default.
#
# MTP launch flags (same proven pattern as our other Qwen3.6 MTP builds —
# serve-qwen-27b-nvfp4-mtp.sh):
#   --spec-type draft-mtp     enable the MTP draft path
#   --spec-draft-n-max 8      draft 8 tokens ahead per step  ← ESTIMATE
#   --spec-draft-p-min 0.0    draft unconditionally (target verifies every
#                             draft → zero quality impact)
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0   flash-attn + draft on GPU
#
# ⚠️ n-max 8 IS AN ESTIMATE — the optimum is model-specific (our unsloth Q5
# MTP peaked at n8, but the NVFP4 27B peaked at n4). Run
#   ./scripts/profile-fable-fusion-mtp.sh q5
# once downloaded to find THIS model's optimum, then update -c/-n-max here.
#
# ⚠️ DavidAU's MTP REQUIREMENTS (from the model card — breaking these silently
# degrades MTP acceptance and speed):
#   - temperature ≤ 1.0   (higher temps hurt MTP)
#   - repetition_penalty = 1.0  (OFF — raising it hurts MTP)
#   Set these client-side (WebUI/OpenCode). If token-acceptance drops below
#   ~50% (see the server log's "draft acceptance"), use the non-MTP Q5 build.
#
# CONSTRAINTS (MTP, upstream): -np 1 forced (one slot; concurrent requests
# serialize); --mmproj vision unsupported.
#
# ⚠️ CONTEXT/VRAM IS AN ESTIMATE (2026-07-17). -c 262144 (native ceiling) at
# Q5_K_M MTP (~21.2 GB) should fit the 32 GB card (cf. 21.6 GB NVFP4-MTP fits
# 262144 with ~2.5 GB free). Validate with scripts/measure-vram.sh / the
# profile script once downloaded.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M.gguf" \
  -a "Fable-Fusion 27B MTP Q5" \
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
