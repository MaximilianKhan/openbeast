#!/bin/bash
# Qwen3.6-27B Q5_K_XL on RTX 5090 (32GB VRAM)
# 350K context (-c 358400): reduced from 416K on 2026-05-22 — the 416K default
# left only ~2 GB headroom and was crashing under sustained OS+KV pressure.
# Historical measurement: 416K = 30,711 MiB / 2,057 MiB headroom (2026-05-05).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/run.sh" \
  -m "$WEIGHTS_DIR/Qwen3.6-27B-UD-Q5_K_XL.gguf" \
  -c 358400 \
  "$@"
