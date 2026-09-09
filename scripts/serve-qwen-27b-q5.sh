#!/bin/bash
# Serve Qwen3.6-27B Q5_K_XL as OpenAI-compatible API on RTX 5090
# 350K context (-c 358400): reduced from 416K on 2026-05-22 — the 416K default
# left only ~2 GB headroom and was crashing under sustained OS+KV pressure.
# Historical measurement: 416K = 30,711 MiB / 2,057 MiB headroom (2026-05-05).
# 6 parallel slots (unified KV — no extra VRAM; slots SHARE the -c pool,
# they do not each get -c/6 — see docs/BEAST_SLOT.md).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
# Reasoning budget 20480 (2026-09-09, extended to the whole Qwen family per
# Max): per-request thinking cap. Measured on the 27B tails (3.6 champion
# p99.5 = 16.6k, 3.8 p99.5 = 17.8k tokens/request; >20k = 0.2-0.4% of
# requests, runaway/marathon-correlated). Room for every measured productive
# think, ceiling on the spiral. Global conf REASONING_BUDGET (-1 = unlimited)
# overrides — serve.sh appends the global flag after this one, last wins.
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -a "Qwen 27B Q5" \
  -c 358400 \
  --reasoning-budget 20480 \
  "$@"
