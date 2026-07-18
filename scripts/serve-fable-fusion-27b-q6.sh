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
# MEASURED on the 5090 (2026-07-17, q4_0 KV, 4 slots): -c 245760 uses
# 30,144 MiB / 2,463 MiB free — safe. The full native -c 262144 also loads
# (30,511 MiB / 2,096 MiB free) but sits right on the 2 GB line — the
# sustained-load crash zone (cf. the Qwopus 352K note), so we ship one notch
# down for real headroom. Set OPENBEAST_CONTEXT=262144 if you want the ceiling
# and accept the thin margin. Baseline decode ~57 tok/s greedy (Q6 is heavier
# than Q5; the MTP twin is ~1.8× faster).
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q6_K.gguf" \
  -a "Fable-Fusion 27B Q6" \
  -c 245760 \
  -np 4 \
  --reasoning-budget 4096 \
  "$@"
