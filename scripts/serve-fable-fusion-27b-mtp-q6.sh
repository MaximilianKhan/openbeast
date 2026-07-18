#!/bin/bash
# Serve Qwen3.6-27B Fable-Fusion-711 (DavidAU) Q6_K **with MTP** (Multi-Token
# Prediction) as OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF
#   Highest-fidelity quant (~24 GB) with MTP draft heads — max accuracy AND
#   the lossless MTP speedup, at the cost of context room. Reasoning ON.
#
# MTP launch flags (same proven pattern as serve-qwen-27b-nvfp4-mtp.sh):
#   --spec-type draft-mtp / --spec-draft-n-max 8 / --spec-draft-p-min 0.0
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0
#
# ⚠️ n-max 8 IS AN ESTIMATE — run  ./scripts/profile-fable-fusion-mtp.sh q6
# once downloaded to find this model's optimum, then update -c/-n-max here.
#
# ⚠️ DavidAU's MTP REQUIREMENTS (model card — breaking these degrades MTP):
#   - temperature ≤ 1.0   - repetition_penalty = 1.0 (OFF)
#   Set client-side. If draft acceptance drops below ~50% (server log), fall
#   back to the non-MTP Q6 build.
#
# CONSTRAINTS (MTP, upstream): -np 1 forced; --mmproj vision unsupported.
#
# ⚠️ CONTEXT/VRAM IS AN ESTIMATE (2026-07-17). Q6_K MTP (~24 GB) + draft
# buffers is the tightest of the four — -c 196608 (192K) is the conservative
# start. Validate/raise with the profile script + scripts/measure-vram.sh.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf" \
  -a "Fable-Fusion 27B MTP Q6" \
  -c 196608 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 8 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
