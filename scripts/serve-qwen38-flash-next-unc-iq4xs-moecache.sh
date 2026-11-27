#!/bin/bash
# Serve Qwen3.8-Flash-Next-Uncensored IQ4_XS WITH llama.cpp's MoE expert
# cache (v1.0.0, #29887 / #30966 / #31233): the un-shelved Flash-Next.
# Same weights as serve-qwen38-flash-next-unc-iq4xs.sh (the static baseline,
# measured 38 tok/s on 2026-09-11); this launch adds a VRAM cache of the hot
# experts so most expert reads no longer cross dual-channel DDR5.
# Runbook, acceptance check and rollback: docs/WEIGHT_STREAMING.md.
#
# UNMEASURED ON OUR RIG (the rig was offline when this was written,
# 2026-11-15). Target: 55-70 tok/s decode at short context. Anchors:
#   llama.cpp #29887, RTX 5090 + EPYC 7742, PCIe 4.0 x16, Flash-Next Q4_0:
#     master --fit 30.8 tok/s; --fit --moe-cache-mib 6544 54.5 (77% hits);
#     -cmoe --moe-cache-mib 19000 67.8 (89% hits).                MEASURED
#   Our static baseline: 38 tok/s, ~40 GB/s effective expert gather. MEASURED
#
# A daily-class profile: it runs in the normal 75% memory scope (~91.5 GiB
# of the rig's 122 GiB), NOT big mode's raised one. That decides the split.
# The GGUF's tensor table (read 2026-11-15 over HTTP range requests):
#   routed experts 60.94 GiB (48 layers x 1.27 GiB), global tables 27.63 GiB
#   (token embeddings + PLE, always host-resident), attention / DeltaNet /
#   norms / shared experts 2.21 GiB.
# The PR's fastest form (-cmoe: every expert in host RAM, the GPU holding
# only the cache) would put 88.6 GiB of weights in host RAM before the
# stack: over the 75% scope. So 8 expert layers stay resident on the GPU:
#   host RAM  40 x 1.27 = 50.8 GiB experts + 27.6 GiB tables + ~1.5 GiB
#             llama-server buffers + 2 GiB prompt cache ≈ 82 GiB, plus the
#             stack's ~3 GiB in the scope ≈ 85 of 91.5 GiB.
#   VRAM      8 x 1.27 = 10.2 GiB experts + 2.2 non-expert + ~3.6 KV at 262K
#             (measured 14 KB/token) + ~2.5 compute + 0.5 CUDA ≈ 19 GiB,
#             + the 8 GiB cache ≈ 27 of 31.8 GiB: ~4.9 GiB free, the daily
#             driver's measured-safe headroom (docs/MODELS.md).
# Pinned host experts (--load-mode none: llama.cpp pins them, docs/moe-cache.md)
# upload at the link's full speed, so the CPU share starts at 0.
#
# TUNE IT ON THE RIG before trusting these two numbers (runbook step 3):
#   python3 llama.cpp/scripts/moe-cache-tune.py --max-mib 9000 -- \
#     llama.cpp/build/bin/llama-server -m <shard 1> -ngl 99 -c 262144 \
#     -np 1 --n-cpu-moe 40 --load-mode none -ctk q4_0 -ctv q4_0
# then write its "Recommended:" --moe-cache-mib / --moe-cache-cpu-share here.
# Each layer moved back to the CPU frees 1.27 GiB of VRAM for the cache and
# costs 1.27 GiB of host RAM; the scope arithmetic above is the limit.
#
# MTP: off. The orcarouter draft head is a separate GGUF that needed an
# unmerged PR on the old pin; v1.0.0 support for it is unverified (runbook).
#
# Do NOT start this while an eval chain holds the GPU (port 8080 + VRAM).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-Flash-Next-Uncensored-IQ4_XS-00001-of-00003.gguf" \
  -a "Qwen3.8 Flash-Next Uncensored IQ4_XS MoE-cache" \
  -c 262144 \
  -np 1 \
  --n-cpu-moe 40 \
  --moe-cache-mib 8192 \
  --moe-cache-cpu-share 0 \
  --load-mode none \
  --cache-ram 2048 \
  --reasoning-budget 20480 \
  "$@"
