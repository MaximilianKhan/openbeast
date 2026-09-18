#!/bin/bash
# Stage E: after stage D (pid $PREV) — merge PR #59 (harness agentics) on green CI.
# Held until here so the Tier-3 A/B cells stay in the diag2+pack era the greedy floor calibrated.
set -uo pipefail; OB=/home/max/Documents/openbeast; cd $OB
PREV=${PREV:?}; while kill -0 $PREV 2>/dev/null; do sleep 120; done
echo "[$(date +%F' '%T)] STEP START merge-pr59"
if gh pr checks 59 --watch --fail-fast --interval 60 > /tmp/pr59-checks.log 2>&1; then
  gh pr merge 59 --squash --delete-branch >> /tmp/pr59-checks.log 2>&1 && echo "[$(date +%F' '%T)] STEP DONE merge-pr59 rc=0 MERGED" || echo "[$(date +%F' '%T)] STEP DONE merge-pr59 rc=1 (merge failed)"
else echo "[$(date +%F' '%T)] STEP DONE merge-pr59 rc=1 (checks failed)"; fi
echo "[$(date +%F' '%T)] ALL STEPS COMPLETE (stage E)"
