#!/bin/bash
# Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P on RTX 5090 (32GB VRAM)
# 350K context (-c 358400): reduced from 380K on 2026-05-22 — even the 380K
# default was crashing intermittently under sustained OS+KV pressure.
# Historical measurements: 380K = 30,648 MiB / 2,120 MiB headroom (2026-05-05);
# 416K = 31,405 MiB / 1.36 GB headroom (below 2GB rule, OOM-prone).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$SCRIPT_DIR/run.sh" \
  -m "$REPO_DIR/weights/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf" \
  -c 358400 \
  "$@"
