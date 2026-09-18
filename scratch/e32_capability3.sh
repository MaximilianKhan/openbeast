#!/bin/bash
# E32 capability chain v3 — IQ2 pair, -np 1 (single slot; the crashes
# appeared under -np 6 concurrent kernels) + --jobs 1.
#
# GUARD FIXED 2026-09-15. The header used to promise "any CUDA error in the
# serve log or >10% zero-token failures marks the row INVALID" and the code
# delivered neither:
#   * the CUDA half was  crashes=$(grep -c ... || echo 0)  — grep -c prints 0
#     AND exits 1 when it matches nothing, so $crashes became "0\n0" and the
#     test died with "[: 0\n0: integer expected". The guard never fired.
#   * the zero-token half was never written.
# Both rows of the 09-14 IQ3 pair thus finished unstamped, and "did this
# 10-hour arm survive?" got answered a day later by hand. Now stamped by
# scratch/row_validity.py at row exit, with the same killed-vs-cached-vs-
# timeout classifier the verdict uses.
#
# LOGS ARE DURABLE. They used to go to /tmp; the 09-15 10:05 power-off erased
# both rows' serve logs, so the CUDA axis of a 19-hour measurement became
# unauditable after the fact. scratch/logs/campaign/ survives a reboot.
set -uo pipefail
OB=/home/max/Documents/openbeast
S=$OB/weights/research-staging
L=$OB/scratch/logs/campaign
mkdir -p "$L"
cd $OB
# openbeast.conf carries BEAST_ASSIST=1 (Max, 2026-09-10). These rows are
# UNTREATED model measurements. run_eval's leak guard already strips it (the
# 09-14 rows recorded diagnostics=False with the key set), but a measurement
# should not depend on one guard alone.
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
row() {
  local alias="$1" gguf="$2"
  local slog="$L/v3-serve-$alias.log" elog="$L/v3-cap-$alias.log"
  echo "[$(date +%F' '%T)] ROW START $alias (np1) logs=$slog"
  pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
  nohup scripts/serve.sh -m "$gguf" -a "$alias" -c 262144 -np 1 \
    --reasoning-budget 20480 > "$slog" 2>&1 &
  for i in $(seq 1 90); do
    curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2
  done
  curl -sf http://localhost:8080/health >/dev/null || { echo "[$(date +%T)] SERVER FAILED $alias"; return 0; }
  python3 -u evals/run_eval.py --suite v5-fast --jobs 1 > "$elog" 2>&1
  local rc=$?
  # Stamp the row NOW, while the serve log still exists and the operator can
  # still act on it, instead of discovering it at verdict time.
  local res; res=$(ls -t $OB/evals/results/eval-$(echo "$alias" | tr 'A-Z' 'a-z')-*.json 2>/dev/null | head -1)
  echo "[$(date +%F' '%T)] ROW EXIT $rc $alias"
  python3 scratch/row_validity.py "$res" --serve-log "$slog" | sed 's/^/    /'
  pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
  return 0
}
echo "=== capability v3 (np1) start $(date +%F' '%T) era=$(python3 -c "import sys; sys.path.insert(0,'evals'); import cache; print(cache.context_hash())" 2>/dev/null) ==="
row GSQ-RCO-IQ2XS-np1 "$S/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf"
row UD-IQ2S-np1 "$S/Qwen3.8-27B-UD-IQ2_S.gguf"
echo "=== capability v3 complete $(date +%F' '%T) ==="
