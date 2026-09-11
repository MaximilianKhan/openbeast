#!/bin/bash
# Serve Qwen3.8-Flash-Next-Uncensored IQ4_XS (orcarouter abliteration of
# Qwen/Qwen3.8-Flash-Next) on RTX 5090 + 122 GB RAM.  Baseline-tuned
# 2026-09-11 (table below); MTP + vision untested.
#
# What this model is: 177B-total / 10-of-512-experts MoE, arch `qwen4_exp`
# (Gated DeltaNet + Qwen Sparse Attention hybrid).  Our llama.cpp b10865 has
# LLM_ARCH_QWEN4EXP, so no rebuild is needed.
#
# Why three files: ONE model, split into GGUF shards (-00001-of-00003 …).
# llama.cpp opens the FIRST shard and follows the split metadata to the other
# two automatically — all three must sit in the same directory under their
# original names.  Never rename or concatenate them.
#
# Why --n-cpu-moe: the IQ4_XS shards total ~97 GB (65 GB experts at 1.36 GB
# per layer x 48 layers, 30 GB PLE/embedding tables, 2.4 GB attention/DeltaNet),
# three times the 5090's 32 GB.  Experts of the first N layers stay in system
# RAM; the rest, plus attention, DeltaNet, norms and KV, go to the GPU.
# Why --load-mode none: with mmap (the default) the CPU experts sit in the page
# cache and prompt processing pays page-fault cost; copying them into process
# RAM doubled PP.  Costs ~65 GB of "used" RAM (fine on 122 GB) and ~40 s load.
#
# BASELINE MEASURED 2026-09-11 (Max + Claude, thinking off, 1 slot):
#   config                        decode    PP(1.6k)  VRAM
#   --cpu-moe (mmap)              30 tok/s  250 tok/s  6.3 GB
#   --n-cpu-moe 32 (mmap)         39 tok/s  374 tok/s 32.0 GB  (no headroom)
#   --n-cpu-moe 35 (mmap)         38 tok/s  334 tok/s 23.1 GB
#   --n-cpu-moe 35 --load-mode none  38-39   714 tok/s 23.3 GB  <- this script
# Lower N = more expert layers on GPU = ~+0.6 tok/s per layer, ~3 GB VRAM each.
# N=35 leaves ~9 GB for the MTP draft head and longer contexts.
# CONTEXT COST MEASURED 2026-09-11 (N=35, q4_0 KV, idle VRAM): 32k 23.3 GB,
# 64k 23.8, 128k 24.5, 256k 26.4 (26.7 after a 29k-token prompt) = ~14 KB/token;
# RAM unchanged (KV lives on the GPU: 12 full-attention layers x 2 KV heads x
# 256 dim, DeltaNet state is fixed-size).  The full 262144 training context
# fits under a 30 GB cap with ~3 GB spare.  Decode at 29k tokens in context
# dropped to 25 tok/s (attention cost) vs 38 at short context.  An earlier
# "silent death at 64k" was a kill/relaunch race, not the context.
#
# Optional extras from the same HF repo (drop next to the shards):
#   Qwen3.8-Flash-Next-Uncensored-MTP-draft.gguf (4.1 GB)  → speculative
#     decoding: add  -md <that file> --spec-type draft-mtp --spec-draft-n-max 3
#     (author reports 1.3-2x decode, ~67% acceptance)
#   mmproj-Qwen3.8-Flash-Next-Uncensored-F16.gguf (0.9 GB) → vision:
#     add  --mmproj <that file>
#
# Do NOT start this while an eval chain holds the GPU (port 8080 + VRAM).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-Flash-Next-Uncensored-IQ4_XS-00001-of-00003.gguf" \
  -a "Qwen3.8 Flash-Next Uncensored IQ4_XS" \
  -c 262144 \
  -np 1 \
  --n-cpu-moe 35 \
  --load-mode none \
  --reasoning-budget 20480 \
  "$@"
