#!/bin/bash
# Serve Qwen3.8-27B Q6_K as OpenAI-compatible API on RTX 5090
#
# The higher-fidelity half of the Qwen3.8 pair (22.9 GB vs the Q5_K_XL's
# 20.2 GB). Same `qwen35` hybrid Gated-DeltaNet architecture — see
# serve-qwen38-27b-q5.sh for the architecture notes and why KV is cheap here.
#
# Context: 262144 = full native ceiling, and it STILL fits at Q6.
# MEASURED 2026-08-14 on the 32 GB card: 28,872 MiB used / 3,735 MiB free.
# Worth pausing on: every other 27B-class Q6 we ship had to give up context
# (Fable-Fusion Q6 = 240K, Heretic-v2 Q6 MTP = 208K). The DeltaNet KV saving
# buys the full native window back even at 6-bit.
#
# Decode MEASURED 2026-08-14: 61.5 tok/s greedy, 117 tok/s prompt — ~9% slower
# than the Q5_K_XL (67.6), the usual bandwidth cost of the larger file.
#
# 6 parallel slots (unified KV — slots SHARE the -c pool, see docs/BEAST_SLOT.md).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Q6_K.gguf" \
  -a "Qwen3.8 27B Q6" \
  -c 262144 \
  "$@"
