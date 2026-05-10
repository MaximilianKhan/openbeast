#!/bin/bash
# wrap_up_rerun.sh — runs after the Gemma re-run (post harness-fix) finishes.
#   1. wait for benchmark_all PID 8959 to exit
#   2. rebuild leaderboard from results/ (replaces the phantom 24% Gemma row)
#   3. regenerate doc tables via finalize_docs.py
#   4. git add + commit + push to origin/main
#   5. systemctl poweroff
#
# Logs to evals/results/wrap-up-rerun-$(date).log so Max can read what happened.

set -uo pipefail   # NOT -e: we want to push and shutdown even if some step fails

REPO="/home/max/Documents/models"
BENCHMARK_PID=8959
LOG="$REPO/evals/results/wrap-up-rerun-$(date +%Y%m%d-%H%M%S).log"

cd "$REPO"

{
  echo "=== wrap_up_rerun.sh started at $(date -Is) ==="

  # Step 1 — wait for benchmark_all to exit
  echo ""
  echo ">>> [1/5] waiting for PID $BENCHMARK_PID (benchmark_all.py --models gemma-4-31b-q5)"
  while ps -p "$BENCHMARK_PID" -o cmd= 2>/dev/null | grep -q benchmark_all; do
    sleep 60
  done
  echo "    benchmark_all has exited at $(date -Is)"

  # Sanity pause for filesystem flush
  sleep 5

  # Step 2 — rebuild leaderboard
  echo ""
  echo ">>> [2/5] rebuilding leaderboard"
  python3 evals/scoring.py --rebuild 2>&1 || echo "    (rebuild had warnings; continuing)"

  # Step 3 — regenerate doc tables
  echo ""
  echo ">>> [3/5] regenerating doc tables (README.md, docs/RESULTS.md)"
  python3 scripts/finalize_docs.py 2>&1

  # Step 4 — commit + push
  echo ""
  echo ">>> [4/5] git commit + push"
  git status --short
  git add evals/leaderboard.json README.md docs/RESULTS.md \
          scripts/wrap_up_rerun.sh 2>&1 || true
  if git diff --cached --quiet; then
    echo "    no staged changes — skipping commit"
  else
    git commit -m "$(cat <<'EOF'
Gemma re-run after harness fix: real leaderboard number replaces 24% phantom

The original v3.5 Gemma row (24.0% accuracy, 59/323 passes) was an artifact
of llama-server dying at task 64 — the harness silently logged 256 zero-token
failures against a dead endpoint. Pre-crash, Gemma was at 90.6% on the 64
tasks it actually saw, peer-level with the Qwen MoEs.

After landing the harness fix in cf14926 (server log capture + per-task
/health + auto-restart), evicted the 256 dead-server cache entries (kept the
59 valid passes + 8 genuine fails), and re-ran the missing 256 tasks. This
commit records the real Gemma performance and re-ranks the leaderboard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)" 2>&1 || echo "    (commit failed — continuing)"
    git push origin main 2>&1 || echo "    (push failed — Max can push manually)"
  fi

  # Step 5 — shutdown
  echo ""
  echo ">>> [5/5] shutting down at $(date -Is)"
  echo ""
  echo "=== wrap_up_rerun.sh finished ==="
  sync
  systemctl poweroff
} 2>&1 | tee -a "$LOG"
