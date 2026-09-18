#!/bin/bash
# Full verdict sequence for A/B round 2 — run AFTER the chain completes.
# 1. tripwire reruns (--no-cache, one per arm)  2. merge minis
# 3. patched ab_verdict  4. audit deltas printed for the brief.
set -uo pipefail
cd /home/max/Documents/openbeast
S=scratch; R=evals/results

echo "=== 1. TRIPWIRE RERUNS (rerun-once rule, --no-cache) ==="
echo "--- B0 tripwire: 122_gemm_blocked_d (uncensored, diag OFF)"
python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks 122_gemm_blocked_d --no-leaderboard --no-cache --jobs 1 2>&1 | grep -E "PASS|FAIL|Results:" | head -4
echo "--- B1 tripwire: 127_aes_keysched_c (uncensored, diag ON)"
OPENBEAST_DIAGNOSTICS=1 python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --tasks 127_aes_keysched_c --no-leaderboard --no-cache --jobs 1 2>&1 | grep -E "PASS|FAIL|Results:" | head -4

echo "=== 2. MERGE MINIS ==="
a0=$(grep -m1 "^Results:" /tmp/ab2-A0.log | awk '{print $2}')
a0m=$(grep -m1 "^Results:" /tmp/ab2-A0-mini.log | awk '{print $2}')
a1=$(grep -m1 "^Results:" /tmp/ab2-A1.log | awk '{print $2}')
a1m=$(grep -m1 "^Results:" /tmp/ab2-A1-mini.log | awk '{print $2}')
b0=$(grep -m1 "^Results:" /tmp/ab2-B0.log | awk '{print $2}')
b0m=$(grep -m1 "^Results:" /tmp/ab2-B0-mini.log | awk '{print $2}')
b1=$(grep -m1 "^Results:" /tmp/ab2-B1.log | awk '{print $2}')
b1m=$(grep -m1 "^Results:" /tmp/ab2-B1-mini.log | awk '{print $2}')
python3 $S/merge_minis.py "$a0" "$a0m" $S/verdict-A0.json
python3 $S/merge_minis.py "$a1" "$a1m" $S/verdict-A1.json
python3 $S/merge_minis.py "$b0" "$b0m" $S/verdict-B0.json
python3 $S/merge_minis.py "$b1" "$b1m" $S/verdict-B1.json

echo "=== 3. VERDICT (patched ab_verdict: B0 filter includes assumed_failed) ==="
python3 $S/ab_verdict.py $S/verdict-A0.json $S/verdict-A1.json $S/verdict-B1.json $S/verdict-B0.json

echo "=== 4. CELL SUMMARIES ==="
for f in A0 A1 B0 B1; do
  grep -E "passed,|Imputed" /tmp/ab2-$f.log | head -2 | sed "s/^/[$f] /"
done
