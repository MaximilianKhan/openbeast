#!/bin/bash
# A-pair (champion) replicate — Max-authorized 2026-09-10. Tests L7 and the
# un-replicated champion signal (+13/-5, p=0.096). --no-cache load-bearing.
set -uo pipefail
cd /home/max/Documents/openbeast
MINIS="158_karatsuba_bytes_f,23_sql_injection"
run() { local log="$1"; shift; echo "[$(date +%F' '%T)] START $log"; "$@" > "$log" 2>&1; echo "[$(date +%F' '%T)] EXIT $? $log"; return 0; }
echo "=== A-replicate chain start $(date +%F' '%T) — engine: $(llama.cpp/build/bin/llama-server --version 2>&1 | head -1) ==="
run /tmp/ab4-A0.log      python3 -u evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4 --no-cache
run /tmp/ab4-A0-mini.log python3 -u evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --no-cache --jobs 2
run /tmp/ab4-A1.log      env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4 --no-cache
run /tmp/ab4-A1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --no-cache --jobs 2
echo "=== A-replicate chain complete $(date +%F' '%T) ==="
