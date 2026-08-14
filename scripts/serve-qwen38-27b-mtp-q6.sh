#!/bin/bash
# Serve Qwen3.8-27B Q6_K **with MTP** (Multi-Token Prediction) on RTX 5090
#
# As with the Q5 pair, the MTP head ships inside the standard GGUF
# (`qwen35.nextn_predict_layers = 1`, tensors at `blk.64.nextn.*`) — this is
# the same weight file serve-qwen38-27b-q6.sh loads, with the draft path on.
# "unused tensor blk.64.*" warnings appear only WITHOUT --spec-type.
#
# TUNED 2026-08-14 by measured sweep (greedy, 300-token decode, same prompt):
#   n-max 2  ->  112.4 tok/s  (67% draft acceptance)
#   n-max 4  ->  113.6 tok/s  (47% acceptance)   <-- PEAK, shipped
#   n-max 8  ->  111.0 tok/s  (32% acceptance)
# Baseline without MTP: 61.5 tok/s. So n4 = 1.85x.
# The Q6 curve is FLAT (112-114 across the whole grid) where Q5's has a real
# peak — at 6-bit the model is bandwidth-bound enough that draft depth barely
# moves the result. n4 is shipped as the nominal best, but anything in 2-8
# performs within ~2%; do not spend tuning effort here.
#
# **CRITICAL CONSTRAINT (upstream):** `-np 1` is forced — MTP does not support
# more than one parallel slot. Concurrent requests serialize.
#
# Context: 196608 (192K) — the ONE config in the Qwen3.8 set that gives up
# native context. MEASURED 2026-08-14: the full 262144 loads and runs
# (113.9 tok/s) but lands at 31,048 MiB / 1,559 MiB free — inside the
# sustained-load crash zone that cost us Gemma at 220K and Qwopus at 352K.
# Backed off to 192K: 29,641 MiB used / 2,966 MiB free. If you want the full
# native window at Q6, run the non-MTP script (3,735 MiB free at 262K) — the
# trade here is 70K of context for 1.85x decode.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Q6_K.gguf" \
  -a "Qwen3.8 27B MTP Q6" \
  -c 196608 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
