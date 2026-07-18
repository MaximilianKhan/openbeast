#!/bin/bash
# Serve Qwen3.6-27B Fable-Fusion-711 (DavidAU) Q6_K as OpenAI-compatible API.
# Model card / source: https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF
#   Highest-fidelity non-MTP quant of this fine-tune (~24 GB). Q6_K is very
#   near-lossless vs the source; pick this over Q5_K_M when you want maximum
#   accuracy and can spend the extra ~3 GB of VRAM (context pays for it).
#   Reasoning/thinking ON by default.
#
# DavidAU's recommended samplers (client-side / per request):
#   thinking, general : temp 1.0  top_p 0.95 top_k 20 min_p 0.0  rep_pen 1.0
#   thinking, coding  : temp 0.6  top_p 0.95 top_k 20 min_p 0.0  rep_pen 1.0
#   non-thinking      : temp 0.7  top_p 0.80 top_k 20 min_p 0.0  presence 1.5
#
# ⚠️ CONTEXT/VRAM IS AN ESTIMATE (2026-07-17) — not yet profiled on the 5090.
# Q6_K (~24 GB) is ~3 GB heavier than Q5_K_M, so context is scaled back from
# the native 262144 ceiling to keep the 2 GB OS-headroom rule: -c 229376
# (224K) with 4 slots is the conservative starting point. Validate and raise
# with scripts/measure-vram.sh once downloaded — it may well hold more.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q6_K.gguf" \
  -a "Fable-Fusion 27B Q6" \
  -c 229376 \
  -np 4 \
  "$@"
