#!/usr/bin/env bash
# Wait for F's next row to be recorded, stop F cleanly at that boundary,
# run A-full under its own lease, then resume F (resume-safe via rows-f.txt).
set -uo pipefail
OB=/home/max/Documents/openbeast
E36=$OB/research/lowrank/experiments/36-submission
ROWS=$E36/rows-f.txt
MASTER_PID=$1 LEASE_PID=$2 FCAP_PID=$3
cd "$OB"
log() { echo "[$(date +%F' '%T)] $*"; }
n0=$(wc -l < "$ROWS"); log "waiting for row $((n0+1)) of F (now $n0 recorded)"
until [ "$(wc -l < "$ROWS")" -gt "$n0" ]; do sleep 3; done
log "row recorded: $(tail -1 "$ROWS") — stopping F at the boundary"
kill "$MASTER_PID" 2>/dev/null; kill "$FCAP_PID" 2>/dev/null
pkill -f '[e]vals/run_eval.py --suite v5-fast' 2>/dev/null
pkill -x llama-server 2>/dev/null
pkill -f '[s]cripts/serve.sh -m' 2>/dev/null
for _ in $(seq 1 60); do pgrep -x llama-server >/dev/null || break; sleep 1; done
kill "$LEASE_PID" 2>/dev/null
until ./scripts/gpu-lease.sh check >/dev/null 2>&1; [ $? -eq 3 ]; do sleep 10; done
log "card free; partial cap log of the interrupted row (will be rerun):"; ls -t "$E36"/logs-f/cap-*.log | head -1
log "A-FULL START"
./scripts/gpu-lease.sh run "E36 a-fullwiki" -- bash "$E36/a_fullwiki.sh" > "$E36/run-a-fullwiki.log" 2>&1
log "A-FULL END rc=$?"
until ./scripts/gpu-lease.sh check >/dev/null 2>&1; [ $? -eq 3 ]; do sleep 10; done
log "F RESUME"
./scripts/gpu-lease.sh run "E36 f-cap" -- bash "$E36/f_capability.sh" > "$E36/run-f-cap-resume.log" 2>&1
log "F END rc=$?"
