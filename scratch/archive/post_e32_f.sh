#!/bin/bash
# Stage F — IQ2 capability pair + verdict, DEMOTED TO LAST (Max 2026-09-14
# 14:40: "Arm the reorder, IQ2 last"). Was post_e32.sh steps 3-4. Invoked by
# post_e32_d.sh after the Tier-3 verdict so the product-relevant stages
# (greedy floor, Tier-3 mini-A/B) land ~10h sooner. Era note: stage D pulls
# main before this runs; the IQ2 pair is compared WITHIN itself (both rows
# post-pull, same era) so the pairing stays clean, but IQ2-vs-IQ3 rows are
# cross-era — disclose if ever tabled together. Cancel = kill this script
# (pid in scratch/logs/post-e32-f.log header) + pkill -9 -x llama-server.
set -uo pipefail
OB=/home/max/Documents/openbeast; cd $OB
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
E32R=$OB/../openbeast-research/research/lowrank/experiments/32-t117-gsq-head-to-head
step() { echo "[$(date +%F' '%T)] STEP START $1"; }
done_() { echo "[$(date +%F' '%T)] STEP DONE $1 rc=$2"; }
latest() { ls -t $OB/evals/results/eval-$1-*.json 2>/dev/null | head -1; }
echo "[$(date +%F' '%T)] stage F pid $$"
pkill -f '[l]lama-server' 2>/dev/null || true; sleep 3
step capability-iq2; bash scratch/e32_capability3.sh; done_ capability-iq2 $?
step verdict-iq2; python3 $E32R/e32_cap_verdict.py "$(latest gsq-rco-iq2xs-np1)" "$(latest ud-iq2s-np1)" --serve-logs '/tmp/e32v3-serve-{alias}.log' > $E32/capability-verdict-iq2.txt 2>&1; done_ verdict-iq2 $?; cat $E32/capability-verdict-iq2.txt
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE (stage F)"
