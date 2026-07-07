#!/bin/bash
# Serve Qwopus3.6-27B-v2 Q5_K_M **with MTP** (Jackrong SFT fine-tune) on RTX 5090
# Source: https://huggingface.co/Jackrong/Qwopus3.6-27B-v2-MTP-GGUF
# Reasoning-enhanced fine-tune (Trace Inversion) with MTP draft heads baked
# into the same GGUF.
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide + llama.cpp
# docs/speculative.md):
#   --spec-type draft-mtp     enables the MTP draft path
#   --spec-draft-n-max 4      drafts 4 tokens ahead per step
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
# Tuned empirically 2026-07-07: n4/p0.0 = 147 tok/s vs 68.5 baseline (2.14x),
# 62% draft acceptance — lower than the base Qwen 27B MTP (the SFT fine-tune
# shifted the output distribution relative to the MTP heads), so the optimum
# is shallower (n4, not n8). Gated configs (p-min > 0) all measured slower.
# p-min does NOT affect output quality (target model verifies every draft).
#
# **CRITICAL CONSTRAINTS (upstream MTP limitations, llama.cpp 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input (despite
#     the base Qwopus model supporting vision).
#
# Context: 336K (-c 344064) — measured 2026-07-07 with production tuning:
# 30,027 MiB used / 2,580 MiB headroom on the 32 GB card. 352K measured
# 30,475 MiB / 2,132 MiB — technically above the 2 GB rule, but 2,120 MiB
# is exactly where the uncensored 27B crashed under sustained load, so we
# back off one notch. ~27 KB/token KV. Lower the context further if YaRN
# turns out broken in this conversion — see the non-MTP script's note.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf" \
  -a "Qwopus 27B v2 MTP Q5" \
  -c 344064 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
