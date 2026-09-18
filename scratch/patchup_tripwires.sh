#!/bin/bash
# STAGE G — rerun every tripwire failure on the IQ3 pair, then re-verdict.
#
# A tripwire is a unit the suite asserts every reference model passes; a fail
# there means the imputed score's saturation assumption is broken, and both
# rows printed "the imputed score above is NOT trustworthy". The standing rule
# (memory: Phase I, 2026-09-09) is rerun-once WITH --no-cache at verdict time.
# Fails are never cached (PR #35 guard), so the rerun is live regardless.
#
# Runs LAST, after stage F, because every earlier stage needs the GPU and a
# contended rerun would be its own measurement artifact.
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
S=$OB/weights/research-staging
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E32R=$OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head
L=$OB/scratch/logs/campaign; mkdir -p "$L"   # durable: /tmp does not survive a reboot
say() { echo "[$(date +%F' '%T)] $*"; }

rerun() {                       # rerun <alias> <gguf> <comma-separated task ids>
  local alias="$1" gguf="$2" tasks="$3"
  local main; main=$(ls -t $OB/evals/results/eval-$(echo "$alias" | tr 'A-Z' 'a-z')-*.json 2>/dev/null | head -1)
  [ -n "$main" ] || { say "STAGE G: no results file for $alias — SKIP"; return 0; }
  say "STAGE G rerun $tasks on $alias"
  pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
  nohup scripts/serve.sh -m "$gguf" -a "$alias" -c 262144 -np 1 \
    --reasoning-budget 20480 > "$L/tripwire-serve-$alias.log" 2>&1 &
  for i in $(seq 1 90); do curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2; done
  curl -sf http://localhost:8080/health >/dev/null || { say "STAGE G: server failed for $alias — SKIP"; return 0; }
  python3 -u evals/run_eval.py --tasks "$tasks" --no-cache > "$L/tripwire-run-$alias.log" 2>&1
  say "STAGE G rerun rc=$? ($alias)"
  local new; new=$(ls -t $OB/evals/results/eval-$(echo "$alias" | tr 'A-Z' 'a-z')-*.json | head -1)
  if [ "$new" != "$main" ]; then
    local t
    for t in ${tasks//,/ }; do
      python3 scratch/patchup_replace.py "$main" "$new" "$t" \
        "tripwire rerun-once --no-cache at verdict time (standing rule)" || true
    done
    mkdir -p "$OB/evals/results/patchup"
    mv "$new" "$OB/evals/results/patchup/$(basename "$new")"
    touch "$main"
  fi
  pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
}

rerun GSQ-RCO-IQ3S-np1 "$S/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf" "65_miller_rabin_f"
# 122_gemm_blocked_{b,c,d,e}: NOT model failures. Their setup died with
# "OpenBLAS blas_thread_init: pthread_create failed ... Resource temporarily
# unavailable" — the harness could not spawn threads to generate expected
# output. Row 1 (11:19-20:11) has ZERO setup failures; row 2 started 20:11 and
# these four landed while six parallel build agents were running on this box
# from 20:55. Coordinator-caused, and asymmetric: it penalises row 2 only, in
# the direction that flatters the result. Three of the four are A-only wins in
# the paired contrast, so they must be rerun before any number is published.
rerun UD-IQ3S-np1      "$S/Qwen3.8-27B-UD-IQ3_S.gguf"      "115_fft_b,122_gemm_blocked_b,122_gemm_blocked_c,122_gemm_blocked_d,122_gemm_blocked_e"

# Row 1's own gemm units ran clean (real tokens, real wall), so they are NOT
# rerun: rerunning a clean unit to "match" a repaired one would be the same
# error in the other direction.
say "STAGE G re-verdict"
latest() { ls -t $OB/evals/results/eval-$1-*.json 2>/dev/null | head -1; }
python3 $E32R/e32_cap_verdict.py "$(latest gsq-rco-iq3s-np1)" "$(latest ud-iq3s-np1)" \
  --serve-logs "$L/v2-serve-{alias}.log" > $E32/capability-verdict-iq3-final.txt 2>&1
say "STAGE G re-verdict rc=$? -> $E32/capability-verdict-iq3-final.txt"
cat $E32/capability-verdict-iq3-final.txt
say "STAGE G COMPLETE"
