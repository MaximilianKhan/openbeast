#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) **NVFP4 + MTP** on RTX 5090 (Blackwell) via llama.cpp.
# Model: neko-legends/Qwen3.6-35B-A3B-NVFP4-MTP-GGUF
#   (file: qwen3.6-35b-a3b-unsloth-nvfp4-mtp-gguf.gguf, 24.34 GiB) — native
#   llama.cpp conversion of unsloth/Qwen3.6-35B-A3B-NVFP4. Packed NVFP4 expert
#   FFNs kept native (Blackwell FP4 tensor cores), source FP8 tensors stored as
#   Q8_0, MTP block preserved for draft-mtp. Arch: qwen35moe (A3B = ~3B active
#   params/token → very fast decode). Requires GGML_TYPE_NVFP4 (build b9690 has
#   it) + a Blackwell card (5090 = sm_120).
#
# LAUNCH FLAGS (tuned empirically on THIS card 2026-07-10 — greedy sweep,
# identical output across n, pure draft-speed isolation):
#   --spec-type draft-mtp    MTP speculative-decoding draft path
#   --spec-draft-n-max 2     draft 2 tokens ahead — MEASURED optimum. Decode
#                            tok/s: n1 280 / **n2 317** / n4 307 / n6 277 /
#                            n8 194 / n10 180. The MoE forward pass is so cheap
#                            (~3B active) that deep-draft verification overhead
#                            dominates fast → the peak is SHALLOW (n2), unlike
#                            the dense 27B NVFP4 which favored n4. Matches the
#                            publisher's shipped n2.
#   --spec-draft-p-min 0.0   draft unconditionally (target verifies every draft)
#   -ngld 99                 draft (MTP) layers on GPU
#   -ctkd/-ctvd q4_0         draft KV cache quant (main KV = q4_0 via serve.sh)
#   -fa on                   flash attention
#
# CONSTRAINTS (MTP, upstream): -np 1 forced (one slot); --mmproj unsupported.
#
# CONTEXT: -c 262144 — the model's FULL native n_ctx_train. MEASURED 2026-07-10
# at n=2: 29527 MiB used / 3080 MiB free after live gen — safe (the MoE's KV is
# smaller than the dense 27B's, so full context fits with MORE headroom despite
# heavier weights). Decode flat ~312 tok/s across ctx sizes. 5090 is the
# reference card → serve.sh's auto-scaler leaves this unchanged.
#
# Reasoning NOT forced off (OpenBeast keeps reasoning ON + per-request toggle).
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-35B-A3B-NVFP4-MTP.gguf" \
  -a "Qwen 35B-A3B NVFP4 MTP" \
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
