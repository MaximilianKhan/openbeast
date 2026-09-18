#!/bin/bash
# Second-stage orchestrator: waits for post_e32.sh (pid $PREV) then runs the
# PPL@2048 sign check. Appended 2026-09-11 10:55 without touching the running
# first stage (never edit a bash script while bash is executing it).
set -uo pipefail; OB=/home/max/Documents/openbeast; cd $OB
PREV=${PREV:?}; while kill -0 $PREV 2>/dev/null; do sleep 60; done
pkill -f llama-server 2>/dev/null || true; sleep 3
echo "[$(date +%F' '%T)] STEP START ppl2048-check"; bash $OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head/ppl2048_check.sh; echo "[$(date +%F' '%T)] STEP DONE ppl2048-check rc=$?"
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE (stage B)"
