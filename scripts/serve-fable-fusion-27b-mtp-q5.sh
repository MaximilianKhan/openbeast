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
#   --spec-draft-n-max 2      draft 2 tokens ahead per step  ← MEASURED optimum
#   --spec-draft-p-min 0.0    draft unconditionally (target verifies every
#                             draft → zero quality impact)
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0   flash-attn + draft on GPU
#
# n-max 2 is the MEASURED optimum (scripts/profile-fable-fusion-mtp.sh q5,
# 2026-07-17): decode n1 96 / n2 108 / n4 98 / n8 76 / n10 69 tok/s. This
# model's draft head accepts shallow drafts well (65% at n2) but deep ones
# poorly (19% at n10), so the peak is SHALLOW — unlike our unsloth Q5 MTP
# (n8) and NVFP4 27B (n4). Do NOT copy n from another model; re-profile.
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
# MEASURED on the 5090 (2026-07-17): -c 262144 (native ceiling) at n-max 2
# uses 29,885 MiB / 2,722 MiB free — above the 2 GB rule. Decode ~108 tok/s
# greedy (1.6× the non-MTP Q5's ~66), draft acceptance 0.65 (mean len 2.30) —
# well above DavidAU's 50% keep-MTP threshold.
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
  --spec-draft-n-max 2 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
