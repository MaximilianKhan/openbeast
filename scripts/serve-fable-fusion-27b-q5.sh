#!/bin/bash
# Serve Qwen3.6-27B Fable-Fusion-711 (DavidAU) Q5_K_M as OpenAI-compatible API.
# Model card / source: https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF
#   DavidAU multi-stage fine-tune of Qwen3.6-27B (arch qwen35, dense): Heretic
#   uncensor + NEO imatrix quant (output tensor kept full 16-bit) + "Fable
#   Fusion". Reasoning/thinking is ON by default (matches OpenBeast's ethos).
#
# Native context: 262144 (n_ctx_train). Going beyond needs YaRN rope scaling
# (DavidAU documents rope_theta 1e7 / factor 4.0 / mrope_section [11,11,10] for
# up to ~1M) — we ship the native ceiling to avoid any long-context quality
# loss; raise via OPENBEAST_CONTEXT + the YaRN flags if you need >256K.
#
# DavidAU's recommended samplers (set client-side / per request — OpenBeast
# keeps sampling per-request, thinking ON):
#   thinking, general : temp 1.0  top_p 0.95 top_k 20 min_p 0.0  rep_pen 1.0
#   thinking, coding  : temp 0.6  top_p 0.95 top_k 20 min_p 0.0  rep_pen 1.0
#   non-thinking      : temp 0.7  top_p 0.80 top_k 20 min_p 0.0  presence 1.5
#
# MEASURED on the 5090 (2026-07-17, q4_0 KV): -c 262144 (native ceiling) uses
# 28,253 MiB / 4,354 MiB free — comfortable. Baseline decode ~66 tok/s
# (greedy); the MTP twin roughly 1.6× that. Native 262144 is the ceiling
# (higher needs YaRN); the generous headroom means a smaller card can hold a
# useful context too.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q5_K_M.gguf" \
  -a "Fable-Fusion 27B Q5" \
  -c 262144 \
  "$@"
