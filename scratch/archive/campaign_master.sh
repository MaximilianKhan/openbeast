#!/bin/bash
# CAMPAIGN MASTER 2026-09-11 (post-crash relaunch). ONE sequential process —
# replaces the pid-chained orchestrators (setsid forked → every recorded pid
# was a dead parent → all stages fired at once at 14:01; PR #59 merged early,
# E16 rung-1 completed, rest killed). Stage scripts are reused unchanged: each
# is handed an already-dead pid so its wait loop falls through immediately.
set -uo pipefail; OB=/home/max/Documents/openbeast; cd $OB; L=$OB/scratch/logs
sleep 0 & DEAD=$!; wait $DEAD 2>/dev/null
st() { echo "[$(date +%F' '%T)] ==== $1"; }
st "STAGE A0 chain (IQ3 pair, np1)";      bash scratch/e32_capability2.sh   > $L/e32v2-chain.log 2>&1; st "chain rc=$?"
st "STAGE A post-e32 (steps 1-6)";        CHAIN_PID=$DEAD bash scratch/post_e32.sh > $L/post-e32.log 2>&1; st "post-e32 rc=$?"
st "STAGE B ppl2048 sign check";          PREV=$DEAD bash scratch/post_e32_b.sh > $L/post-e32-b.log 2>&1; st "stage B rc=$?"
st "STAGE C e16 rung-1 — ALREADY COMPLETE 14:02, skipped"
st "STAGE D pull + tier3 mini-A/B + verdict"; PREV=$DEAD bash scratch/post_e32_d.sh > $L/post-e32-d.log 2>&1; st "stage D rc=$?"
st "STAGE E merge #59 — ALREADY MERGED 14:01, skipped"
pkill -f '[l]lama-server' 2>/dev/null || true
st "ALL STAGES COMPLETE"
