#!/usr/bin/env bash
# E36 master queue 3 (B5 oracle, then Llama-8B, then capability): every GPU block after B/C, in order, each under its own
# lease. Waits for the card to be free first. A failed block is logged and
# the queue moves on (each block is resume-safe; rerun it by hand).
set -uo pipefail
OB=/home/max/Documents/openbeast
E36=$OB/research/lowrank/experiments/36-submission
cd "$OB"
log() { echo "[$(date +%F' '%T)] $*"; }
wait_free() { until ./scripts/gpu-lease.sh check >/dev/null 2>&1; [ $? -eq 3 ]; do sleep 60; done; }
block() { # name -- cmd...
  local name=$1; shift 2
  wait_free; log "BLOCK START $name"
  ./scripts/gpu-lease.sh run "E36 $name" -- "$@" > "$E36/run-$name.log" 2>&1
  log "BLOCK END $name rc=$?"
}


block b5-oracle -- bash "$E36/b5_oracle.sh"

block llama8b -- bash "$E36/llama8b/pipeline.sh"
block f-cap   -- bash "$E36/f_capability.sh"
log "MASTER QUEUE DONE"
