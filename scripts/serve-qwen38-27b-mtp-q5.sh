#!/bin/bash
# Serve Qwen3.8-27B UD-Q5_K_XL **with MTP** (Multi-Token Prediction) on RTX 5090
#
# NOTE THE CHANGE FROM QWEN3.6: there is no separate "-MTP-GGUF" repo to
# download. Qwen3.8 ships the MTP head INSIDE the standard GGUF —
# `qwen35.nextn_predict_layers = 1`, tensors at `blk.64.nextn.*` (block_count
# is 65: 64 trunk layers + 1 nextn layer). Served WITHOUT --spec-type they are
# loaded and ignored ("unused tensor" warnings at startup are expected and
# harmless); --spec-type draft-mtp activates them. Same file as
# serve-qwen38-27b-q5.sh — this script is purely a different launch config.
#
# MTP launch flags:
#   --spec-type draft-mtp     enables the MTP draft path
#   --spec-draft-n-max 4      drafts 4 tokens ahead per step  <- TUNED, see below
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
#
# TUNED 2026-08-14 by measured sweep at the native 262K context (greedy,
# 300-token decode, identical prompt):
#   n-max 2  ->  116.5 tok/s  (63% draft acceptance)
#   n-max 4  ->  123.7 tok/s  (48% acceptance)   <-- PEAK, shipped
#   n-max 8  ->  106.6 tok/s  (28% acceptance)
# Baseline without MTP: 67.6 tok/s. So n4 = 1.83x.
# The curve peaks EARLIER than Qwen3.6-27B's (which wanted n8 for 2.75x):
# Qwen3.8's draft acceptance falls off faster with depth, so deep drafting
# spends more on rejected tokens than it wins. Do not copy the 3.6 config here.
# p-min does NOT affect output quality (the target model verifies every draft).
#
# **CRITICAL CONSTRAINT (upstream):** `-np 1` is forced — MTP does not support
# more than one parallel slot. Concurrent requests serialize.
#
# Context: 262144 = full native ceiling, same as the non-MTP script — the
# hybrid DeltaNet KV is cheap enough that MTP costs no context here.
# MEASURED 2026-08-14: 28,852 MiB used / 3,755 MiB free at the shipped n4.
# (n8 costs ~720 MiB more in draft buffers: 29,574 MiB / 3,033 MiB free.)
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-UD-Q5_K_XL.gguf" \
  -a "Qwen3.8 27B MTP Q5" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
