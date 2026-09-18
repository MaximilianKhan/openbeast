#!/bin/bash
# Diagnostics A/B round 2 — 2026-09-09, ALL cells capped (reasoning budget
# 20480 baked into qwen serve scripts, PR #45) on pristine b10865.
# Round 1 (uncapped) was stopped mid-A0 per Max: the ship decision must be
# measured under the go-forward config, and B0' doubles as the budget
# validation row. rb cache-era component (PR #45) isolates capped rows —
# every cell runs live-fresh by construction.
# Cells: B0' (unc, diag OFF — capped baseline + budget validation),
#        B1' (unc, diag ON), A0' (champ, diag OFF), A1' (champ, diag ON);
# each + the 2 assumed-failed minis. Verdict:
#   merge minis into cell files, then
#   python3 scratch/ab_verdict.py A0' A1' B1' B0'
set -uo pipefail
cd /home/max/Documents/openbeast
MINIS="158_karatsuba_bytes_f,23_sql_injection"

run() {
  local log="$1"; shift
  echo "[$(date +%F' '%T)] START $log"
  "$@" > "$log" 2>&1
  echo "[$(date +%F' '%T)] EXIT $? $log"
  return 0
}

echo "=== A/B round-2 chain start $(date +%F' '%T) — engine: $(llama.cpp/build/bin/llama-server --version 2>&1 | head -1) — budget: $(grep -c 'reasoning-budget 20480' scripts/serve-qwen38-27b-uncensored-q5.sh)/1 baked ==="

run /tmp/ab2-B0.log      python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --jobs 4
run /tmp/ab2-B0-mini.log python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

run /tmp/ab2-B1.log      env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --jobs 4
run /tmp/ab2-B1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

run /tmp/ab2-A0.log      python3 -u evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4
run /tmp/ab2-A0-mini.log python3 -u evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

run /tmp/ab2-A1.log      env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4
run /tmp/ab2-A1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

echo "=== A/B round-2 chain complete $(date +%F' '%T) ==="
