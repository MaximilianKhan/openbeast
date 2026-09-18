#!/bin/bash
# IQ-artifact CUDA launch-timeout minimal repro (upstream-report candidate).
# 2026-09-10: UD-IQ3_S + GSQ-RCO-IQ3_S (qwen35/GDN arch) under -np 6 on
# stock b10865 + RTX 5090 died ~98 min in with
#   ggml-cuda.cu:108: CUDA error: the launch timed out and was terminated
# (coredump 2404908: ggml_backend_cuda_synchronize ← server decode loop),
# while Q5-class K-quants ran 14h+ crash-free under the same flags.
# Variants (each ≤ MIN minutes of 6-stream load, stops at first crash):
#   A) UD-IQ3_S  -np 6            (positive control — reproduce)
#   B) UD-IQ3_S  -np 6 + GGML_CUDA_DISABLE_GRAPHS=1 (graph-capture suspect)
#   C) UD-Q5_K_XL -np 6           (negative control, K-quant same arch)
set -uo pipefail
OB=/home/max/Documents/openbeast; S=$OB/weights/research-staging; cd $OB
source scripts/conf.sh 2>/dev/null || true
# openbeast.conf carries BEAST_ASSIST=1 (Max, 2026-09-10) — this is an UNTREATED measurement: never let it leak into the harness.
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
MIN=${MIN:-100}
OUT=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head/iq-stability-repro.md
echo "# IQ-artifact stability repro — $(date +%F' '%T)" > $OUT
echo "engine: $(llama.cpp/build/bin/llama-server --version 2>&1 | head -1); GPU: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)" >> $OUT
variant() {
  local name="$1" gguf="$2" np="$3"; shift 3
  local log="/tmp/iqrepro-serve-$name.log" load="/tmp/iqrepro-load-$name.log"
  echo "[$(date +%F' '%T)] VARIANT START $name ($gguf, -np $np, env: $*)"
  pkill -f llama-server 2>/dev/null || true; sleep 3
  env "$@" nohup scripts/serve.sh -m "$gguf" -a "iqrepro-$name" -c 262144 -np $np --reasoning-budget 20480 > "$log" 2>&1 &
  for i in $(seq 1 120); do curl -sf localhost:8080/health >/dev/null 2>&1 && break; sleep 2; done
  curl -sf localhost:8080/health >/dev/null || { echo "SERVER FAILED $name"; echo "- $name: server failed to start" >> $OUT; return; }
  python3 -u scratch/iq_load.py --concurrency $np --minutes $MIN --max-tokens 4096 > "$load" 2>&1
  local crash; crash=$(grep -m1 -n "CUDA error\|GGML_ASSERT" "$log" || true)
  local summ; summ=$(grep SUMMARY "$load" | tail -1)
  echo "[$(date +%F' '%T)] VARIANT END $name — crash: ${crash:-none}; $summ"
  { echo; echo "## $name — $gguf, -np $np, env: $*"; echo "- $summ"; echo "- crash line: ${crash:-NONE within $MIN min}";
    [ -n "$crash" ] && { echo '```'; grep -B5 -A25 -m1 "CUDA error\|GGML_ASSERT" "$log" | cut -c1-220; echo '```'; }; } >> $OUT
  pkill -f llama-server 2>/dev/null || true; sleep 3
}
variant A-udiq3s-np6        "$S/Qwen3.8-27B-UD-IQ3_S.gguf"   6
variant B-udiq3s-np6-nograph "$S/Qwen3.8-27B-UD-IQ3_S.gguf"  6 GGML_CUDA_DISABLE_GRAPHS=1
variant C-udq5kxl-np6       "$OB/weights/Qwen3.8-27B-UD-Q5_K_XL.gguf" 6
echo "[$(date +%F' '%T)] repro complete → $OUT"
