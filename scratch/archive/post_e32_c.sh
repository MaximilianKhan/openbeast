#!/bin/bash
# Stage C: after stage B (pid $PREV) → E16 rung-1 0.8B KLD measurements (GPU, ~10 min).
set -uo pipefail; OB=/home/max/Documents/openbeast; cd $OB
PREV=${PREV:?}; while kill -0 $PREV 2>/dev/null; do sleep 60; done
pkill -f llama-server 2>/dev/null || true; sleep 3
echo "[$(date +%F' '%T)] STEP START e16-08b-measure"; bash $OB/../openbeast-research/research/lowrank/experiments/34-e16-nvfp4-08b/e16_08b.sh measure; echo "[$(date +%F' '%T)] STEP DONE e16-08b-measure rc=$?"
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE (stage C)"
