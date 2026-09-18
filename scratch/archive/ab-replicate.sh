#!/bin/bash
# Overnight B-pair replicate (run 2 of 2) — Max-authorized 2026-09-09 ~23:10.
# --no-cache is LOAD-BEARING: a replicate must be a fresh measurement, and the
# rb/diag cache-era keys would otherwise replay round-2's banked rows verbatim.
# Same era in every other respect: capped 20480, pristine b10865, same
# toolchain fingerprint, same suite pin.
set -uo pipefail
cd /home/max/Documents/openbeast
MINIS="158_karatsuba_bytes_f,23_sql_injection"
run() { local log="$1"; shift; echo "[$(date +%F' '%T)] START $log"; "$@" > "$log" 2>&1; echo "[$(date +%F' '%T)] EXIT $? $log"; return 0; }
echo "=== B-replicate chain start $(date +%F' '%T) — engine: $(llama.cpp/build/bin/llama-server --version 2>&1 | head -1) ==="
run /tmp/ab3-B0.log      python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --jobs 4 --no-cache
run /tmp/ab3-B0-mini.log python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks "$MINIS" --no-leaderboard --no-cache --jobs 2
run /tmp/ab3-B1.log      env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --jobs 4 --no-cache
run /tmp/ab3-B1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks "$MINIS" --no-leaderboard --no-cache --jobs 2
echo "=== B-replicate chain complete $(date +%F' '%T) ==="
