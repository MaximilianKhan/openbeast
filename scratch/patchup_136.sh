#!/bin/bash
# PATCH-UP: rerun the one unit a review subagent killed with an over-broad pkill
# (2026-09-14 22:54). The row banked agent_exit_code -15 and a bogus FAIL.
# Fails are never cached (PR #35 guard) so the rerun is genuinely live; --no-cache
# is belt-and-braces. Runs BEFORE the IQ3 verdict so the board it reads is honest.
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
S=$OB/weights/research-staging
ALIAS=UD-IQ3S-np1; TASK=136_gf256_b
echo "[$(date +%F' '%T)] PATCHUP start $TASK on $ALIAS"
MAIN=$(ls -t $OB/evals/results/eval-ud-iq3s-np1-*.json 2>/dev/null | head -1)
[ -n "$MAIN" ] || { echo "PATCHUP: no ud-iq3s-np1 results file — SKIP"; exit 0; }
python3 -c "
import json,sys
d=json.load(open('$MAIN'))
t=[x for x in d['tasks'] if x['id']=='$TASK']
sys.exit(0 if (t and t[0].get('agent_exit_code')==-15) else 1)
" || { echo "PATCHUP: $TASK in $MAIN is not the SIGTERM row — nothing to do, SKIP"; exit 0; }
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
nohup scripts/serve.sh -m "$S/Qwen3.8-27B-UD-IQ3_S.gguf" -a "$ALIAS" -c 262144 -np 1 \
  --reasoning-budget 20480 > /tmp/patchup-serve.log 2>&1 &
for i in $(seq 1 90); do curl -sf http://localhost:8080/health >/dev/null 2>&1 && break; sleep 2; done
curl -sf http://localhost:8080/health >/dev/null || { echo "PATCHUP: server failed — SKIP"; exit 0; }
python3 -u evals/run_eval.py --tasks "$TASK" --no-cache > /tmp/patchup-run.log 2>&1
echo "[$(date +%F' '%T)] PATCHUP rerun rc=$? (log /tmp/patchup-run.log)"
RERUN=$(ls -t $OB/evals/results/eval-ud-iq3s-np1-*.json | head -1)
if [ "$RERUN" = "$MAIN" ]; then echo "PATCHUP: no new results file — SKIP"; else
  python3 scratch/patchup_replace.py "$MAIN" "$RERUN" "$TASK" \
    "row killed by a review subagent's pkill 2026-09-14 22:54; rerun live --no-cache"
  # The verdict step resolves its input with `ls -t .../eval-<alias>-*.json | head -1`.
  # The 1-task rerun file is newer than the 112-task board, so it would WIN that race
  # and the verdict would score a single unit. Move it out of the results dir and make
  # the patched board the newest file again.
  mkdir -p "$OB/evals/results/patchup"
  mv "$RERUN" "$OB/evals/results/patchup/$(basename "$RERUN")"
  touch "$MAIN"
  echo "PATCHUP: rerun file parked in evals/results/patchup/; $(basename "$MAIN") is newest again"
fi
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
echo "[$(date +%F' '%T)] PATCHUP done"
