#!/bin/bash
# =============================================================================
# CAMPAIGN MASTER 2 — post-merge era (f7e81a5), written 2026-09-15 after the
# waybar/coredump power-off killed master 1 mid-flight.
#
# ONE sequential process, plain nohup. Do NOT setsid-wrap it and do not
# pid-chain the stages: setsid forks, so every recorded pid became a dead
# parent and on 2026-09-11 all stages fired at once (PR #59 merged early, the
# rest were killed). Stages are called directly, in order, here.
#
# WHAT CHANGED vs master 1
#   * The IQ3 capability pair is NOT re-run. Its "CRASH SIGNATURE, ROW INVALID"
#     was a verdict-script bug: 4 cache hits + 1 timeout counted as kills. Both
#     rows are valid (research cbf3cd8), so re-running them would have burned
#     ~19 GPU-hours to reproduce a result we already hold.
#   * STAGE G runs FIRST, not last. It is ~40 min and it is the only thing
#     standing between us and a final IQ3 verdict; its original "run last"
#     reasoning was that it must not contend with another sweep, which a
#     sequential master satisfies wherever it sits.
#   * Tier-3 before the greedy floor. The verdict is READ against the floor but
#     does not consume it, so the 1-hour job goes ahead of the 7-hour one: if
#     the harness is broken we learn in an hour.
#   * Every log lands in scratch/logs/campaign/, not /tmp. The 09-15 power-off
#     erased both capability rows' serve logs and with them the CUDA axis of a
#     19-hour measurement.
#
# ERA: one era for everything below (context_hash 3b7c2adb8da7968d). Of the six
# files evals/cache.py hashes, only agents/runner.py moved in #61/#62, and
# every line it gained is gated behind _steering_enabled(), which is False
# whenever OPENBEAST_EVAL is set — run_eval.py sets it in every child env.
# Inertness with steering off was proven differentially against main. Stage F's
# IQ2 rows are internally paired in this era; IQ2-vs-IQ3 is cross-era, disclose.
#
# CANCEL: kill the pid in scratch/logs/campaign/master2.pid, then
#         pkill -9 -x llama-server  (and wait for VRAM to actually drain).
# =============================================================================
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
L=$OB/scratch/logs/campaign; mkdir -p "$L"
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E32R=$OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head
echo $$ > "$L/master2.pid"

st()   { echo "[$(date +%F' '%T)] ==== $*"; }
latest() { ls -t $OB/evals/results/eval-$1-*.json 2>/dev/null | head -1; }

# --- preflight: never start on a busy GPU ------------------------------------
if pgrep -x llama-server >/dev/null 2>&1; then
  st "ABORT: a llama-server is already running. Stop it first."; exit 1
fi
_vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null || echo 0)
st "preflight: GPU ${_vram} MiB used, era=$(python3 -c "import sys; sys.path.insert(0,'evals'); import cache; print(cache.context_hash())" 2>/dev/null), HEAD=$(git rev-parse --short HEAD)"
if [ "${_vram:-0}" -gt 4000 ]; then
  st "ABORT: ${_vram} MiB already on the GPU — something is holding it."; exit 1
fi

# --- STAGE G — tripwire reruns + FINAL IQ3 verdict (~40 min) ----------------
# Repairs the five UD units whose SETUP died of OpenBLAS thread exhaustion
# while six build agents ran on this box inside row B's window. Asymmetric
# contamination that flatters row A, so it must land before any number ships.
st "STAGE G tripwire reruns + final IQ3 verdict"
bash scratch/patchup_tripwires.sh > "$L/stage-g.log" 2>&1; st "stage G rc=$?"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 5

# --- STAGE B — PPL@2048 sign check ------------------------------------------
st "STAGE B ppl2048 sign check"
bash $E32R/ppl2048_check.sh > "$L/stage-b.log" 2>&1; st "stage B rc=$?"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 5

# --- TIER-3 — pre-registered zig-only mini-A/B + verdict (~1.25 h) ----------
st "TIER-3 zig mini-A/B"
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
export MANIFEST=$OB/scratch/tier3_cells-20260915.txt
bash scratch/tier3_zig_ab.sh > "$L/tier3-ab.log" 2>&1; st "tier3 A/B rc=$?"
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
