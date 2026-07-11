#!/bin/bash
# Serve Qwen3.6-27B **NVFP4 + MTP** on RTX 5090 (Blackwell) via llama.cpp.
# Model: neko-legends/Qwen3.6-27B-NVFP4-MTP-GGUF
#   (file: qwen3.6-27b-unsloth-nvfp4-mtp-gguf.gguf, 21.58 GiB) — a native
#   llama.cpp conversion of unsloth/Qwen3.6-27B-NVFP4. FFNs stay in packed
#   NVFP4 (4-bit, Blackwell tensor-core native); the source FP8 tensors are
#   stored as Q8_0; the source mtp.* block is preserved for draft-mtp
#   speculative decoding. Requires a build with GGML_TYPE_NVFP4 (ours is
#   b9690 — type 40 present) and a Blackwell card (5090 = sm_120).
#
# WHY NVFP4: the 4-bit FFN weights run on Blackwell's native FP4 tensor
# cores, which our GGUF K-quants can't touch. The publisher measured this
# exact file at 72.8 tok/s @ 10k / 44.1 @ 200k on a Win11 RTX 5090
# (llama.cpp b9851), 26.1 GiB VRAM after load — ~6 GiB headroom on our 32 GB.
#
# LAUNCH FLAGS (tuned empirically on THIS card 2026-07-10 — greedy sweep so
# output is identical across n, isolating pure draft speed):
#   --spec-type draft-mtp    MTP speculative-decoding draft path
#   --spec-draft-n-max 4     draft 4 tokens ahead — MEASURED optimum. Decode
#                            tok/s: n1 88.5 / n2 104 / n4 115 / n6 111 / n8 115
#                            / n10 108. n4 and n8 tie at the peak, but n8 costs
#                            ~650 MiB more draft-buffer VRAM for no speed gain,
#                            so n4 is the efficient frontier (that VRAM buys
#                            context instead). Publisher shipped n2; n4 = +10%.
#                            (Our Q5 MTP uses n8 — different weights; don't copy.)
#   --spec-draft-p-min 0.0   draft unconditionally (target verifies every draft
#                            → no quality impact)
#   -ngld 99                 draft (MTP) layers on GPU
#   -ctkd/-ctvd q4_0         draft KV cache quant (main KV = q4_0 via serve.sh)
#   -fa on                   flash attention (matches the measured config)
#
# CONSTRAINTS (MTP, upstream): -np 1 is forced — one parallel slot only;
# --mmproj vision is unsupported with MTP (this file is text-only anyway).
#
# CONTEXT: -c 262144 — the model's FULL native n_ctx_train (going higher needs
# RoPE scaling → quality loss, so this is the ceiling). MEASURED 2026-07-10 at
# n=4: 30033 MiB used / 2574 MiB free after a live generation — safe headroom
# (more than our Q5 MTP ships with). Decode is flat ~119 tok/s vs smaller -c
# because -c only sizes KV capacity; it doesn't slow decode until filled. The
# 5090 is the reference card, so serve.sh's auto-scaler leaves it unchanged.
#
# Reasoning is intentionally NOT forced off here — OpenBeast keeps reasoning
# ON by default with a per-request enable_thinking toggle.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-NVFP4-MTP.gguf" \
  -a "Qwen 27B NVFP4 MTP" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  -fa on \
  -ngld 99 \
  -ctkd q4_0 \
  -ctvd q4_0 \
  "$@"
