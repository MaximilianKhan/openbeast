#!/bin/bash
# =============================================================================
# CAMPAIGN MASTER 3 — resumes master 2 where Max's 2026-09-15 12:43 GPU
# hand-back stopped it: Tier-3 (P0a banked, 5 cells left) -> Tier-3 verdict ->
# greedy floor -> stage F (IQ2, last by Max's standing order).
#
# Written 2026-09-17 after the Fable review merge. That merge touched NONE of
# the six files evals/cache.py hashes, so the era is still 3b7c2adb8da7968d
# and the banked P0a row pairs with everything below. The preflight ASSERTS
# that rather than trusting this comment (scripts/eval-era.sh).
#
# Same rules as master 2: ONE sequential process, plain nohup, never
# setsid-wrapped, never pid-chained; logs in scratch/logs/campaign/, not /tmp.
# NEW: it runs under scripts/gpu-lease.sh, so the watchdog and stop.sh now
# KNOW the card is taken (both consult the lease since the 09-17 review), and
# an operator SIGTERM to the lease wrapper is forwarded here instead of being
# swallowed.
#
#   LAUNCH:  nohup scripts/gpu-lease.sh run "campaign master3" -- \
#              bash scratch/campaign_master3.sh > scratch/logs/campaign/master3.log 2>&1 &
#   CANCEL (let the running cell bank its row, Max's 09-15 method):
#            kill "$(cat scratch/logs/campaign/master3.pid)"   # plain kill, NEVER the group
#            then wait for the cell, then: pkill -9 -x llama-server; watch VRAM drain.
#
# tier3_zig_ab.sh re-runs P0a first; it is served from the eval cache (same
# era), so that cell costs minutes, not 93.
# =============================================================================
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
L=$OB/scratch/logs/campaign; mkdir -p "$L"
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E32R=$OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head
WANT_ERA=3b7c2adb8da7968d
echo $$ > "$L/master3.pid"

st()   { echo "[$(date +%F' '%T)] ==== $*"; }
latest() { ls -t $OB/evals/results/eval-$1-*.json 2>/dev/null | head -1; }

# --- preflight: right era, idle GPU ------------------------------------------
ERA="$(python3 -c "import sys; sys.path.insert(0,'evals'); import cache; print(cache.context_hash())" 2>/dev/null || true)"
if [ "$ERA" != "$WANT_ERA" ]; then
  st "ABORT: eval era is '${ERA:-?}', the banked rows are $WANT_ERA. Something touched a hashed file."; exit 1
fi
if pgrep -x llama-server >/dev/null 2>&1; then
  st "ABORT: a llama-server is already running. Stop it first."; exit 1
fi
_vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1 || echo 0)
st "preflight: GPU ${_vram} MiB used, era=$ERA, HEAD=$(git rev-parse --short HEAD)"
if [ "${_vram:-0}" -gt 4000 ]; then
  st "ABORT: ${_vram} MiB already on the GPU — something is holding it."; exit 1
fi

# --- TIER-3 — pre-registered zig-only mini-A/B + verdict ---------------------
st "TIER-3 zig mini-A/B (P0a from cache, then P1a P0b P1b C0 C1)"
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
export MANIFEST=$OB/scratch/tier3_cells-20260917.txt
bash scratch/tier3_zig_ab.sh > "$L/tier3-ab-master3.log" 2>&1; st "tier3 A/B rc=$?"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 5
st "TIER-3 verdict"
python3 scratch/tier3_verdict.py --manifest "$MANIFEST" > $OB/scratch/tier3-verdict.txt 2>&1
st "tier3 verdict rc=$? -> scratch/tier3-verdict.txt"
cat $OB/scratch/tier3-verdict.txt

# --- GREEDY FLOOR — churn calibration, 2 untreated rows (~7 h) --------------
st "GREEDY FLOOR (2 rows)"
T0=$(date +%s)
bash scratch/greedy_floor.sh > "$L/greedy-floor.log" 2>&1; st "greedy floor rc=$?"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 5
st "GREEDY FLOOR verdict"
python3 - "$T0" <<'PY' > $OB/scratch/greedy-floor-verdict.txt 2>&1
import glob, json, os, sys
t0 = int(sys.argv[1])
fs = [f for f in sorted(glob.glob('/home/max/Documents/openbeast/evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-*.json'))
      if os.path.getmtime(f) > t0]
fs = [f for f in fs if len(json.load(open(f)).get('tasks', [])) == 112]
print('greedy rows:', fs)
if len(fs) >= 2:
    os.execvp('python3', ['python3',
        '/home/max/Documents/openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head/e32_cap_verdict.py',
        fs[-2], fs[-1]])
print('FEWER THAN 2 FULL GREEDY ROWS — inspect scratch/logs/campaign/greedy-floor-*.log')
PY
st "greedy verdict rc=$?"; cat $OB/scratch/greedy-floor-verdict.txt

# --- STAGE F — IQ2 capability pair + verdict (~19 h, last by Max's order) ---
st "STAGE F IQ2 pair"
bash scratch/e32_capability3.sh > "$L/stage-f-chain.log" 2>&1; st "stage F chain rc=$?"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 5
st "STAGE F verdict"
python3 $E32R/e32_cap_verdict.py "$(latest gsq-rco-iq2xs-np1)" "$(latest ud-iq2s-np1)" \
  --serve-logs "$L/v3-serve-{alias}.log" > $E32/capability-verdict-iq2.txt 2>&1
st "stage F verdict rc=$? -> $E32/capability-verdict-iq2.txt"
cat $E32/capability-verdict-iq2.txt

pkill -f '[l]lama-server' 2>/dev/null || true
st "ALL STAGES COMPLETE"
