#!/bin/bash
# Fast-boot bridge model — Qwen3-0.6B, loads in ~2-5s so `start.sh` can serve
# chat on :8080 almost immediately while the real (large) model loads behind
# it. NOT a model you pick to work with — it's the placeholder the fast-boot
# path swaps OUT the moment the configured big model is healthy (see start.sh,
# OPENBEAST_FAST_BOOT). Reasoning is forced OFF here so the bridge answers
# snappily; the big model restores OpenBeast's reasoning-ON default on swap.
#
# The 0.6B is already on disk (the agent router uses it too). Small context —
# this is a stopgap, not a work surface.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3-0.6B-Q8_0.gguf" \
  -a "OpenBeast Bootstrap (loading full model…)" \
  -c 16384 \
  -np 4 \
  --reasoning off \
  "$@"
