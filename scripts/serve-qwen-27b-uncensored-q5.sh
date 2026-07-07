#!/bin/bash
# Serve Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090
# 350K context (-c 358400): reduced from 380K on 2026-05-22 — even the 380K
# default was crashing intermittently under sustained OS+KV pressure.
# Historical measurements: 380K = 30,648 MiB / 2,120 MiB headroom (2026-05-05);
# 416K = 31,405 MiB / 1.36 GB headroom (below 2GB rule, OOM-prone).
# Per-token KV at high context is ~20 KB, denser than original 18 KB estimate.
# 6 parallel slots (unified KV — no extra VRAM, ~58K context per slot).
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -a "Qwen 27B Uncensored" \
  -c 358400 \
  "$@"
