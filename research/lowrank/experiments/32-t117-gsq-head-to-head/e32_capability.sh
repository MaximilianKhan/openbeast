#!/bin/bash
# E32 step (e): v5-fast capability rows for the T1.17 artifacts.
# Leaderboard-ineligible research rows (run_eval standalone never touches
# leaderboard.json). Era: capped 20480 explicitly (serve.sh alone doesn't
# bake it), b10865, same as the 09-09/10 campaign cells.
# Order: IQ3 pair first (their card's task-lossless + beats-UD claims),
# IQ2 pair second. ~3.5h/row on 3.8-class verbosity ≈ 14h total.
set -uo pipefail
OB=/home/max/Documents/openbeast
S=$OB/weights/research-staging
cd $OB
row() { # row <alias> <gguf>
  local alias="$1" gguf="$2"
  echo "[$(date +%F' '%T)] ROW START $alias"
  pkill -f llama-server 2>/dev/null; sleep 3
  nohup scripts/serve.sh -m "$gguf" -a "$alias" -c 262144 \
    --reasoning-budget 20480 > "/tmp/e32-serve-$alias.log" 2>&1 &
  for i in $(seq 1 90); do
    curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2
  done
  curl -sf http://localhost:8080/health >/dev/null || { echo "[$(date +%T)] SERVER FAILED $alias"; return 0; }
  python3 -u evals/run_eval.py --suite v5-fast --jobs 4 \
    > "/tmp/e32-cap-$alias.log" 2>&1
  echo "[$(date +%F' '%T)] ROW EXIT $? $alias"
  pkill -f llama-server 2>/dev/null; sleep 3
  return 0
}
echo "=== capability chain start $(date +%F' '%T) ==="
row GSQ-RCO-IQ3S-research "$S/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf"
row UD-IQ3S-research      "$S/Qwen3.8-27B-UD-IQ3_S.gguf"
row GSQ-RCO-IQ2XS-research "$S/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf"
row UD-IQ2S-research      "$S/Qwen3.8-27B-UD-IQ2_S.gguf"
echo "=== capability chain complete $(date +%F' '%T) ==="
