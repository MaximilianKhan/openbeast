#!/bin/bash
# Serve Qwopus3.6-27B-v2 Q5_K_M (Jackrong SFT fine-tune of Qwen3.6-27B) on RTX 5090
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-GGUF
# Reasoning-enhanced fine-tune using Trace Inversion (Claude Opus 4.6/4.7
# traces). Vision + tool-use + agentic focus. Standard (non-MTP) build.
#
# Context: 416K (-c 425984) — measured 2026-07-07: 29,980 MiB used /
# 2,627 MiB headroom on the 32 GB card (350K measured 28,460 MiB / 4,147 MiB;
# 440K would land right at the 2 GB rule — not worth it). Note: Jackrong's
# README cites "32K/128K native context"; the YaRN extension inherited from
# Qwen3.6-27B may or may not be intact in their GGUF conversion. If outputs
# degrade past ~128K in practice, back off the context here and validate
# against the base Qwen3.6-27B at the same context for comparison.
# 6 parallel slots (unified KV — no extra VRAM, ~58K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwopus3.6-27B-v2-Q5_K_M.gguf" \
  -a "Qwopus 27B v2 Q5" \
  -c 425984 \
  "$@"
