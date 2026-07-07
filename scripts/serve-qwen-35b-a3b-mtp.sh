#!/bin/bash
# Serve Qwen3.6-35B-A3B (MoE) Q4_K_M **with MTP** on RTX 5090
# Model: unsloth/Qwen3.6-35B-A3B-MTP-GGUF — MTP heads baked into the same GGUF
# (~0.7 GB heavier than the non-MTP build).
#
# MTP launch flags (per unsloth's official Qwen3.6 MTP guide):
#   --spec-type draft-mtp     enable MTP draft path
#   --spec-draft-n-max 4      draft 4 tokens ahead per step
#   --spec-draft-p-min 0.0    draft unconditionally (no probability gate)
# Tuned empirically 2026-07-07: n4/p0.0 = 379 tok/s vs 259 baseline (1.46x),
# 65% draft acceptance. The MoE decodes fast enough that deeper drafts (n8+)
# fall BELOW baseline — verification batches cost more than they save.
# p-min does NOT affect output quality (target model verifies every draft).
#
# **CRITICAL CONSTRAINTS (upstream limitations, 2026-05-22):**
#   - **`-np 1` is forced** — MTP does not yet support more than one parallel
#     slot. Concurrent requests serialize.
#   - **`--mmproj` is not yet supported with MTP** — no vision input.
#
# Context: 512K (-c 524288) — measured 2026-07-07 with production tuning:
# 29,449 MiB used / 3,158 MiB headroom on the 32 GB card. Matches the
# non-MTP 35B-A3B ceiling; the MoE's light per-token KV (~12 KB) absorbs
# the MTP head weights easily. (384K at n-max 2: 27,833 MiB / 4,774 MiB.)
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf" \
  -a "Qwen 35B MoE MTP" \
  -c 524288 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
