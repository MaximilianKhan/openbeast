#!/bin/bash
# E36-F: capability replication (PLAN.md §F). Three rounds of (GSQ, UD, OURS)
# on v5-fast, single slot, --no-cache, budget 20480. Resume-safe: a row whose
# result JSON is recorded in rows.txt is skipped. Logs stay in the repo tree
# (logs-f/), never /tmp. Run under scripts/gpu-lease.sh, stack DOWN.
set -uo pipefail
OB=/home/max/Documents/openbeast
S=$OB/weights/research-staging
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E36=$OB/research/lowrank/experiments/36-submission
LF=$E36/logs-f; mkdir -p "$LF"
ROWS=$E36/rows-f.txt; touch "$ROWS"
ROUNDS=${ROUNDS:-3}
cd "$OB"

stop_server() {
  pkill -x llama-server 2>/dev/null || true
  for _ in $(seq 1 30); do pgrep -x llama-server >/dev/null || break; sleep 1; done
}

row() { # row <alias> <gguf> [extra serve args]
  local alias="$1" gguf="$2"; shift 2
  if grep -q "^$alias " "$ROWS"; then echo "SKIP $alias"; return 0; fi
  echo "[$(date +%F' '%T)] ROW START $alias"
  stop_server
  nohup scripts/serve.sh -m "$gguf" -a "$alias" -c 262144 -np 1 \
    --reasoning-budget 20480 "$@" > "$LF/serve-$alias.log" 2>&1 &
  for _ in $(seq 1 180); do
    curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2
  done
  if ! curl -sf http://localhost:8080/health >/dev/null; then
    echo "[$(date +%T)] SERVER FAILED $alias"; stop_server; return 1; fi
  local before; before=$(ls evals/results/eval-*.json 2>/dev/null | sort)
  python3 -u evals/run_eval.py --suite v5-fast --jobs 1 --no-cache \
    > "$LF/cap-$alias.log" 2>&1
  local rc=$?
  local after; after=$(ls evals/results/eval-*.json 2>/dev/null | sort)
  local new; new=$(comm -13 <(echo "$before") <(echo "$after") | tail -1)
  local cuda; cuda=$(grep -c "CUDA error" "$LF/serve-$alias.log" 2>/dev/null)
  stop_server
  echo "$alias rc=$rc result=${new:-NONE} cuda_errors=${cuda:-0} sha=$(git rev-parse --short HEAD) ended=$(date +%F' '%T)" >> "$ROWS"
  echo "[$(date +%F' '%T)] ROW END $(tail -1 "$ROWS")"
}

echo "=== E36-F start $(date +%F' '%T), rounds=$ROUNDS ==="
for r in $(seq 1 "$ROUNDS"); do
  row "e36f-GSQ-IQ3S-r$r"  "$S/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf"
  row "e36f-UD-IQ3S-r$r"   "$S/Qwen3.8-27B-UD-IQ3_S.gguf"
  row "e36f-OURS-IQ3S-r$r" "$S/Qwen3.8-27B-UD-IQ3_S.gguf" \
      --lora "$E32/adapter-fc-r128q8-UD-IQ3_S.gguf"
done
echo "=== E36-F complete $(date +%F' '%T) ==="
