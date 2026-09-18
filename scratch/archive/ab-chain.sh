#!/bin/bash
# Diagnostics A/B relaunch — 2026-09-09, all cells on pristine b10865 (era-consistent).
# Cells per docs/LANG_AWARENESS_PLAN.md §7: A0/A1 champion off/on, B1 uncensored on;
# B0 offline from banked A' merged full run. Each live cell = v5-fast suite + the
# 2 assumed-failed minis (158_karatsuba_bytes_f, 23_sql_injection).
# Pre-swap A0 cache rows quarantined in scratch/ab-quarantine-preswap/ so A0 runs live.
set -uo pipefail
cd /home/max/Documents/openbeast
MINIS="158_karatsuba_bytes_f,23_sql_injection"

run() { # run <log> <env...> -- <cmd...>
  local log="$1"; shift
  echo "[$(date +%F' '%T)] START $log"
  "$@" > "$log" 2>&1
  local rc=$?
  echo "[$(date +%F' '%T)] EXIT $rc  $log"
  return 0
}

echo "=== A/B chain start $(date +%F' '%T) — engine: $(llama.cpp/build/bin/llama-server --version 2>&1 | head -1) ==="

# Cell A0 — champion, diagnostics OFF, fully live (cache quarantined)
run /tmp/ab-A0.log      python3 evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4
run /tmp/ab-A0-mini.log python3 evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

# Cell A1 — champion, diagnostics ON (33 valid post-swap diag-era rows reused from cache)
run /tmp/ab-A1.log      env OPENBEAST_DIAGNOSTICS=1 python3 evals/benchmark_all.py --models qwen-27b-q5 --suite v5-fast --jobs 4
run /tmp/ab-A1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 evals/benchmark_all.py --models qwen-27b-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

# Cell B1 — uncensored, diagnostics ON
run /tmp/ab-B1.log      env OPENBEAST_DIAGNOSTICS=1 python3 evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --jobs 4
run /tmp/ab-B1-mini.log env OPENBEAST_DIAGNOSTICS=1 python3 evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks "$MINIS" --no-leaderboard --jobs 2

echo "=== A/B chain complete $(date +%F' '%T) ==="
echo "Verdict next: merge minis into cell files, then scratch/ab_verdict.py A0 A1 B1 evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-20260908-065247.json"
