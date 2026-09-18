#!/bin/bash
# Stage D (roadmap Do-Next #3): after stage C (pid $PREV) — pull main
# (first pull since the GPU queue started; PRs #57 diag2 + #58 pack), then
# the pre-registered Tier-3 zig-only mini-A/B under greedy mode, then the
# verdict + stopping rule. The greedy floor (stage A step 6) is the churn
# calibration it is read against. Main-tree pull is deferred to HERE so
# every earlier row keeps its era.
set -uo pipefail; OB=/home/max/Documents/openbeast; cd $OB
PREV=${PREV:?}; while kill -0 $PREV 2>/dev/null; do sleep 60; done
pkill -f llama-server 2>/dev/null || true; sleep 3
echo "[$(date +%F' '%T)] STEP START pull-main"; git status --short | grep -v '^??' | head; git pull --ff-only origin main 2>&1 | tail -2; echo "[$(date +%F' '%T)] STEP DONE pull-main rc=$? @ $(git rev-parse --short HEAD)"
[ -x scratch/tier3_zig_ab.sh ] || { echo "[$(date +%F' '%T)] tier3_zig_ab.sh MISSING after pull — STEP DONE tier3-ab rc=127"; exit 0; }
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
export MANIFEST=$OB/scratch/tier3_cells-stageD.txt
echo "[$(date +%F' '%T)] STEP START tier3-ab"; bash scratch/tier3_zig_ab.sh > /tmp/tier3-ab.log 2>&1; echo "[$(date +%F' '%T)] STEP DONE tier3-ab rc=$?"
pkill -f llama-server 2>/dev/null || true
echo "[$(date +%F' '%T)] STEP START tier3-verdict"; python3 scratch/tier3_verdict.py --manifest "$MANIFEST" > scratch/tier3-verdict.txt 2>&1; echo "[$(date +%F' '%T)] STEP DONE tier3-verdict rc=$?"; cat scratch/tier3-verdict.txt
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE (stage D)"
echo "[$(date +%F' '%T)] ==== STAGE F (IQ2 pair, demoted last 2026-09-14)"; bash scratch/post_e32_f.sh > scratch/logs/post-e32-f.log 2>&1; echo "[$(date +%F' '%T)] stage F rc=$?"
echo "[$(date +%F' '%T)] ==== STAGE G (tripwire reruns + final IQ3 verdict)"; bash scratch/patchup_tripwires.sh > scratch/logs/stage-g.log 2>&1; echo "[$(date +%F' '%T)] stage G rc=$?"
