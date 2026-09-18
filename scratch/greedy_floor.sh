#!/bin/bash
# Langaware: re-measure the churn floor ON GREEDY MODE (PR #56 low-churn
# eval era) — two untreated, same-day v5-fast rows of the default model
# (uncensored 3.8 Q5_K_M, capped 20480). Run 2 uses --no-cache (the greedy
# era key would replay run 1). Flips between them = the greedy floor that
# the Tier-3 zig mini-A/B is powered against.
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
# Durable logs: /tmp lost both capability rows' serve logs to the 09-15
# power-off, which made a 19-hour measurement unauditable after the fact.
L=$OB/scratch/logs/campaign; mkdir -p "$L"
source scripts/conf.sh 2>/dev/null || true
# openbeast.conf carries BEAST_ASSIST=1 (Max, 2026-09-10) — this is an UNTREATED measurement: never let it leak into the harness.
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
export OPENBEAST_REASONING_BUDGET=20480
echo "[$(date +%F' '%T)] greedy floor run 1"
python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --greedy --jobs 4 --no-leaderboard > $L/greedy-floor-1.log 2>&1
echo "[$(date +%F' '%T)] run 1 rc=$? ; greedy floor run 2 (--no-cache)"
python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast --greedy --jobs 4 --no-leaderboard --no-cache > $L/greedy-floor-2.log 2>&1
echo "[$(date +%F' '%T)] run 2 rc=$?"
pkill -f llama-server 2>/dev/null || true
