#!/bin/bash
# POST-E32 ORCHESTRATOR (Max 2026-09-11: "launch the E32 chain and all our
# remaining tasks to complete in order"). Waits for the v2 capability chain
# (pid $CHAIN_PID) then runs, in order:
#  1 FineWeb-KLD control  2 IQ3 capability verdict  3 IQ2 capability pair
#  4 IQ2 verdict  5 IQ-instability repro  6 greedy churn-floor pair + verdict
#  (3-4 moved to stage F on 2026-09-14 — see below)
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E32R=$OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head
CHAIN_PID=${CHAIN_PID:?}
step() { echo "[$(date +%F' '%T)] STEP START $1"; }
done_() { echo "[$(date +%F' '%T)] STEP DONE $1 rc=$2"; }
latest() { ls -t $OB/evals/results/eval-$1-*.json 2>/dev/null | head -1; }
echo "[$(date +%F' '%T)] waiting on chain pid $CHAIN_PID"
while kill -0 $CHAIN_PID 2>/dev/null; do sleep 60; done
echo "[$(date +%F' '%T)] chain exited"; pkill -f llama-server 2>/dev/null || true; sleep 3
step patchup-136; bash scratch/patchup_136.sh; done_ patchup-136 $?
step fineweb-control; bash $E32R/fineweb_control.sh; done_ fineweb-control $?
step verdict-iq3; python3 $E32R/e32_cap_verdict.py "$(latest gsq-rco-iq3s-np1)" "$(latest ud-iq3s-np1)" --serve-logs '/tmp/e32v2-serve-{alias}.log' > $E32/capability-verdict-iq3.txt 2>&1; done_ verdict-iq3 $?; cat $E32/capability-verdict-iq3.txt
# REORDERED 2026-09-14 14:40 (Max: 'Arm the reorder, IQ2 last'): steps 3-4 (IQ2 pair + verdict) moved to scratch/post_e32_f.sh, invoked at the END of stage D.
step iq-stability-repro; bash scratch/iq_stability_repro.sh; done_ iq-stability-repro $?
step greedy-floor; T0=$(date +%s); bash scratch/greedy_floor.sh; done_ greedy-floor $?
step greedy-verdict; python3 - "$T0" <<'PY' > $OB/scratch/greedy-floor-verdict.txt 2>&1
import glob, json, os, sys, subprocess
t0 = int(sys.argv[1]); fs = [f for f in sorted(glob.glob('/home/max/Documents/openbeast/evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-*.json')) if os.path.getmtime(f) > t0]
fs = [f for f in fs if len(json.load(open(f)).get('tasks', [])) == 112]
print('greedy rows:', fs)
if len(fs) >= 2: os.execvp('python3', ['python3', '/home/max/Documents/openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head/e32_cap_verdict.py', fs[-2], fs[-1]])
print('FEWER THAN 2 FULL GREEDY ROWS — inspect /tmp/greedy-floor-*.log')
PY
done_ greedy-verdict $?; cat $OB/scratch/greedy-floor-verdict.txt
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE"
