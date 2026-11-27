#!/bin/bash
# openbeast-scope: mem-limit-pct=90 memlock=infinity host-class=rig min-mem-gib=118
#
# BIG MODE: GLM-5.3-Flash (abliterated) on the one-5090 rig, with the MoE
# experts streamed from host RAM through llama.cpp v1.0.0's expert cache.
# Opt-in only, never the default: `./start.sh -d serve-glm53-flash-abl-q2kxl-big.sh`,
# and `./stop.sh && ./start.sh -d` to go back to the 27B daily driver.
# Runbook, acceptance check and rollback: docs/WEIGHT_STREAMING.md.
#
# WHILE BIG MODE IS ON the rig serves THIS model, not the 27B. beast-hydra's
# rig deployment (upstream qwen38-27b...) then reads MISMATCH and is not
# routable: `routes.beast` falls to its next target (the Ti), or answers
# 503 on a rig-only install with no other node. Clients that pin the 27B by
# name see the same. That is the trade Max accepted (2026-11-15).
#
# The model: huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF, UD-Q2_K_XL
# (unsloth's dynamic 2-bit quant; huihui abliterated attention and shared
# experts of layers 15-35). Uncensored, as every OpenBeast model must be.
# 321B total / ~18B active, arch glm5-next (hybrid KDA + nope MLA with a DSA
# indexer), which needs llama.cpp v1.0.0 (#27773; absent from the old pin).
#
# THE MEMORY PLAN, sized for the rig's 128 GB (122 GiB usable). The GGUF's
# own tensor table (HTTP range reads, 2026-11-15):
#   routed experts 94.68 GiB (43 layers, 2.2-2.9 GiB each, blk 45 = MTP)
#   everything else 6.57 GiB (attention, KDA, 3 dense layers, shared
#   experts, embeddings, head)
#   host RAM: all routed experts, pinned by --load-mode none (-cmoe)
#             94.7 + ~1.5 llama-server buffers + 2 prompt cache ≈ 98 GiB,
#             + the stack's ~3 GiB in the scope ≈ 101 of the 109.8 GiB that
#             THIS profile's 90% scope allows (the header line above; every
#             other profile keeps 75%). ~12 GiB stays outside the scope for
#             the OS, docker (WebUI, SearXNG) and the page cache.
#   VRAM:     ~6.2 GiB non-expert + ~3.6 KV (MLA latent, q8_0, 131072) +
#             ~4 compute (a large prompt batch uploads a whole layer's
#             experts) + 0.5 CUDA ≈ 14.3 GiB, + 14 GiB of expert cache
#             ≈ 28.3 of 31.8 GiB: ~3.5 GiB free.
#
# EXPECTED (UNMEASURED; the rig was offline 2026-11-15): decode 20-30 tok/s
# at short context. Per token 8 of 288 experts x 42 MoE layers = 2.57 GiB of
# expert bytes; a 14 GiB cache holds 15% of them, assumed to serve 50-65%
# of the picks. The misses cross PCIe from pinned RAM or are computed by the
# CPU, both ~40-46 GB/s here. Anchors: Project Maya on the same rig class
# (5090 + 9950X3D + 128 GB DDR5-5600), stock GLM EXL3 3.05 bpw: 25.9-30.1
# tok/s MEASURED (Seppe Gadeyne, 2026-10). Prefill: each 4096-token batch
# reads every expert over PCIe, ~2 s at Gen5 x16: ~2000 tok/s, half that
# if the slot is really x8. Tune with moe-cache-tune.py (runbook step 5).
#
# MTP: off (blk 45 is loaded and unused). --spec-type draft-mtp on this
# file is an experiment for after the acceptance check (runbook).
#
# Do NOT start this while an eval chain holds the GPU (start.sh refuses
# while gpu-lease.sh holds it).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-00001-of-00004.gguf" \
  -a "GLM-5.3-Flash Abliterated UD-Q2_K_XL (big mode)" \
  -c 131072 \
  -np 1 \
  -ctk q8_0 \
  --cpu-moe \
  --moe-cache-mib 14336 \
  --moe-cache-cpu-share 0 \
  --load-mode none \
  --cache-ram 2048 \
  -b 4096 \
  -ub 4096 \
  --reasoning-budget 20480 \
  "$@"
