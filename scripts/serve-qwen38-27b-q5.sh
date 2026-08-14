#!/bin/bash
# Serve Qwen3.8-27B UD-Q5_K_XL as OpenAI-compatible API on RTX 5090
#
# Added 2026-08-14. The GGUF declares `general.architecture = qwen35`, the
# hybrid Gated-DeltaNet stack: 64 trunk layers laid out as 16 x (3 x DeltaNet
# -> 1 x full attention), i.e. `full_attention_interval = 4`. Only 16 of the 64
# layers carry a real KV cache; the other 48 hold a constant-size recurrent
# state, which is why KV is only ~18 KB/token at q4_0.
#
# CORRECTED same day: this header first claimed Qwen3.8 was a NEW architecture
# vs Qwen3.6 and that its KV was cheaper (~18 vs ~28 KB/token). Both wrong.
# Qwen3.6-27B reports the SAME qwen35 arch, same 64 trunk layers, same
# full_attention_interval=4, same head_count_kv/key_length/value_length — and
# therefore the same ~18 KB/token. Qwen3.8 is an architecturally identical,
# newly-trained model. The measurements below were taken empirically and stand.
#
# Requires a llama.cpp with LLM_ARCH_QWEN35. Ours has it (build 2026-08-04,
# b10066-188-g0ef6e55ed) — no upgrade needed.
#
# Context: 262144 = the model's full native ceiling (no YaRN, no rope scaling).
# MEASURED 2026-08-14 on the 32 GB card: 26,702 MiB used / 5,905 MiB free.
# That is the roomiest headroom in the entire lineup. Qwen advertises the
# architecture as extensible to 1M via YaRN; we do NOT enable that here —
# untested, and the native ceiling already exceeds every other model we ship.
#
# Decode MEASURED 2026-08-14: 67.6 tok/s greedy, 193 tok/s prompt.
# For 1.83x that speed at the same context, use serve-qwen38-27b-mtp-q5.sh —
# the MTP draft heads ship INSIDE this very file (see that script).
#
# 6 parallel slots (unified KV — slots SHARE the -c pool, see docs/BEAST_SLOT.md).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-UD-Q5_K_XL.gguf" \
  -a "Qwen3.8 27B Q5" \
  -c 262144 \
  "$@"
