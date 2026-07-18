#!/bin/bash
# Serve Qwen3.6-27B Fable-Fusion-711 (DavidAU) Q6_K **with MTP** (Multi-Token
# Prediction) as OpenAI-compatible API on RTX 5090.
# Model card / source: https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF
#   Highest-fidelity quant (~24 GB) with MTP draft heads — max accuracy AND
#   the lossless MTP speedup, at the cost of context room. Reasoning ON.
#
# MTP launch flags (same proven pattern as serve-qwen-27b-nvfp4-mtp.sh):
#   --spec-type draft-mtp / --spec-draft-n-max 2 / --spec-draft-p-min 0.0
#   -fa on / -ngld 99 / -ctkd q4_0 / -ctvd q4_0
#
# n-max 2 is the MEASURED optimum (scripts/profile-fable-fusion-mtp.sh q6,
# 2026-07-17): decode n1 87 / n2 97 / n4 96 / n8 72 / n10 67 tok/s (n2 and n4
# nearly tie; n2 wins and uses less draft VRAM). Same shallow-peak acceptance
# curve as the Q5 MTP build — re-profile, never copy n across models.
#
# ⚠️ DavidAU's MTP REQUIREMENTS (model card — breaking these degrades MTP):
#   - temperature ≤ 1.0   - repetition_penalty = 1.0 (OFF)
#   Set client-side. If draft acceptance drops below ~50% (server log), fall
#   back to the non-MTP Q6 build.
#
# CONSTRAINTS (MTP, upstream): -np 1 forced; --mmproj vision unsupported.
#
# MEASURED on the 5090 (2026-07-17, n-max 2): the tightest of the four —
# ~24 GB weights + MTP draft buffers. -c 180224 uses 30,116 MiB / 2,491 MiB
# free (safe). Ladder above it all breached the 2 GB rule: 196608 = 2,025 free,
# 212992 = 1,562, 229376 = 1,096, 245760 = 630 — so 180224 is the ceiling with
# real headroom. Decode ~103 tok/s greedy (1.8× the non-MTP Q6's ~57), draft
# acceptance 0.67 (mean len 2.33).
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf" \
  -a "Fable-Fusion 27B MTP Q6" \
  -c 180224 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  --reasoning-budget 4096 \
  "$@"
