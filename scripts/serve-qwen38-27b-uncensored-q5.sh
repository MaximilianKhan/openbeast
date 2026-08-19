#!/bin/bash
# Serve Qwen3.8-27B-Uncensored Q5_K_M as OpenAI-compatible API on RTX 5090.
#
# Added 2026-08-19. JonathanColetti/Qwen3.8-27B-Uncensored-GGUF — Qwen3.8-27B
# with the refusal direction abliterated out of self_attn.o_proj and
# mlp.down_proj, then imatrix-quantized. sha256 matches the published HF LFS
# oid byte-for-byte; pinned in scripts/weights.registry.
#
# Architecture is stock Qwen3.8: `general.architecture = qwen35`, the hybrid
# Gated-DeltaNet stack — 64 trunk layers as 16 x (3 x DeltaNet -> 1 x full
# attention), `full_attention_interval = 4`. Only 16 of the 64 layers carry a
# real KV cache; the other 48 hold a constant-size recurrent state, which is
# why KV is ~18 KB/token at q4_0 and the full native context fits with room to
# spare. Abliteration edits weight values, not shapes — nothing about the KV
# math changes.
#
# Requires a llama.cpp with LLM_ARCH_QWEN35. Ours has it (build 10254,
# 0ef6e55ed) — no upgrade needed.
#
# Context: 262144 = the model's full native ceiling (no YaRN, no rope scaling).
# MEASURED 2026-08-19 on the 32 GB card at the shipped 6 slots: 26,211 MiB used
# / 6,396 MiB free — the roomiest config in the whole lineup. (The same load at
# -np 1 sits at 25,476 MiB; the six slots cost ~735 MiB in per-slot buffers,
# not in KV, which --kv-unified pools.) Qwen advertises
# the architecture as extensible to 1M via YaRN; we do NOT enable that here
# (untested, and the native ceiling already exceeds every other model we ship).
#
# Decode MEASURED 2026-08-19: 69.9 tok/s greedy at -np 1.
# For 2.0x that speed at the same context, use
# serve-qwen38-27b-uncensored-mtp-q5.sh — that is the shipped DEFAULT, and the
# MTP draft head ships INSIDE this very file (see that script). This non-MTP
# config exists for the case MTP cannot serve: concurrent requests. MTP forces
# -np 1, so a multi-user rig wants this script instead.
#
# 6 parallel slots (unified KV — slots SHARE the -c pool, see docs/BEAST_SLOT.md).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Uncensored-Q5_K_M.gguf" \
  -a "Qwen3.8 27B Uncensored Q5" \
  -c 262144 \
  "$@"
