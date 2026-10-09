#!/bin/bash
# E32 capability chain v2 — IQ3 pair only, STABILITY-HARDENED:
# -np 1 (single slot; the crashes appeared under -np 6 concurrent
# kernels) + --jobs 1, and a per-row validity stamp: any CUDA error in
# the serve log or >10% zero-token failures marks the row INVALID.
set -uo pipefail
OB=/home/max/Documents/openbeast
S=$OB/weights/research-staging
cd $OB
row() {
  local alias="$1" gguf="$2"
  echo "[$(date +%F' '%T)] ROW START $alias (np1)"
  pkill -f llama-server 2>/dev/null || true; sleep 3
  nohup scripts/serve.sh -m "$gguf" -a "$alias" -c 262144 -np 1 \
    --reasoning-budget 20480 > "/tmp/e32v2-serve-$alias.log" 2>&1 &
  for i in $(seq 1 90); do
    curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2
  done
  curl -sf http://localhost:8080/health >/dev/null || { echo "[$(date +%T)] SERVER FAILED $alias"; return 0; }
  python3 -u evals/run_eval.py --suite v5-fast --jobs 1 \
    > "/tmp/e32v2-cap-$alias.log" 2>&1
  local rc=$?
  local crashes; crashes=$(grep -c "CUDA error" "/tmp/e32v2-serve-$alias.log" 2>/dev/null || echo 0)
  echo "[$(date +%F' '%T)] ROW EXIT $rc $alias — serve CUDA errors: $crashes $( [ "$crashes" -gt 0 ] && echo '→ ROW INVALID' || echo '→ row clean so far' )"
  pkill -f llama-server 2>/dev/null || true; sleep 3
  return 0
}
echo "=== capability v2 (np1) start $(date +%F' '%T) ==="
row GSQ-RCO-IQ3S-np1 "$S/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf"
row UD-IQ3S-np1      "$S/Qwen3.8-27B-UD-IQ3_S.gguf"
echo "=== capability v2 complete $(date +%F' '%T) ==="
