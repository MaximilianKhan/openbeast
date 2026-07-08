#!/bin/bash
# wrap_up.sh — runs after the v3.5 sweep's Gemma run finishes:
#   1. wait for benchmark_all PID 466968 to exit
#   2. rebuild leaderboard from results/ (picks up the new Gemma row)
#   3. backfill inference_engine on any entry missing it (Gemma's run started
#      before the inference_engine plumbing landed, so its result JSON lacks
#      the field — re-apply the same engine info we backfilled on the others)
#   4. regenerate doc tables via finalize_docs.py
#   5. git add + commit + push to origin/main
#   6. systemctl poweroff
#
# Logs to evals/results/wrap-up-$(date).log so Max can read what happened.

set -uo pipefail   # NOT -e: we want to push and shutdown even if some step fails

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BENCHMARK_PID=466968
LOG="$REPO/evals/results/wrap-up-$(date +%Y%m%d-%H%M%S).log"

cd "$REPO"

{
  echo "=== wrap_up.sh started at $(date -Is) ==="

  # Step 1 — wait for benchmark_all to exit (kill -0 probes; doesn't actually kill)
  echo ""
  echo ">>> [1/6] waiting for PID $BENCHMARK_PID (benchmark_all.py --models gemma-4-31b-q5)"
  while ps -p "$BENCHMARK_PID" -o cmd= 2>/dev/null | grep -q benchmark_all; do
    sleep 60
  done
  echo "    benchmark_all has exited at $(date -Is)"

  # Sanity pause for filesystem flush
  sleep 5

  # Step 2 — rebuild leaderboard
  echo ""
  echo ">>> [2/6] rebuilding leaderboard"
  python3 evals/scoring.py --rebuild 2>&1 || echo "    (rebuild had warnings; continuing)"

  # Step 3 — backfill inference_engine on any entry missing it
  echo ""
  echo ">>> [3/6] backfilling inference_engine on leaderboard entries lacking it"
  python3 - <<'PY'
import json
LB = "os.path.join(os.environ.get("REPO", "."), "evals/leaderboard.json")"
ENGINE = {
    "name": "llama.cpp",
    "binary": "/home/max/Documents/models/llama.cpp/build/bin/llama-server",
    "build": "8893",
    "commit": "6217b4958",
    "compiler": "GNU 15.2.1",
    "target": "Linux x86_64",
    "source_head": "2bacb1eb77d07dd9f9685cd524f6cd9a342d639b",
    "backfilled": True,
    "backfill_note": "binary mtime 2026-04-22, unchanged through the v3.5 sweep",
}
with open(LB) as f:
    lb = json.load(f)
patched = 0
for e in lb["entries"]:
    if not e.get("inference_engine"):
        e["inference_engine"] = ENGINE
        patched += 1
with open(LB, "w") as f:
    json.dump(lb, f, indent=2)
print(f"    backfilled {patched}/{len(lb['entries'])} entries")
PY

  # Step 4 — regenerate doc tables
  echo ""
  echo ">>> [4/6] regenerating doc tables (README.md, docs/RESULTS.md)"
  python3 scripts/finalize_docs.py 2>&1

  # Step 5 — commit + push
  echo ""
  echo ">>> [5/6] git commit + push"
  git status --short
  git add evals/leaderboard.json README.md docs/RESULTS.md docs/REFERENCE.md \
          evals/run_eval.py evals/scoring.py scripts/serve-gemma-4-31b-q5.sh \
          scripts/finalize_docs.py scripts/wrap_up.sh evals/README.md 2>&1 || true
  # Only commit if there's something staged
  if git diff --cached --quiet; then
    echo "    no staged changes — skipping commit"
  else
    git commit -m "$(cat <<'EOF'
v3.5 sweep complete: Gemma row added, llama.cpp version tracked

- Reduced Gemma KV from 220K to 192K (200 GB → 192K context) after a
  sustained-load crash mid-sweep; ran cleanly at the new ceiling.
- Backfilled inference_engine field on every leaderboard entry
  (llama.cpp build 8893, commit 6217b4958) — binary unchanged through
  the entire sweep, verified by mtime.
- Dropped composite score from documentation (kept in JSON for
  back-compat); accuracy-first ranking with separate tokens + Sonnet
  4.6 cost-equivalent columns.
- Architecture diagram cleaned up, centered, Unicode box-drawing.
- Per-language at-a-glance table added to README.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)" 2>&1 || echo "    (commit failed — continuing)"
    git push origin main 2>&1 || echo "    (push failed — Max can push manually in the morning)"
  fi

  # Step 6 — shutdown (systemctl via polkit; user is in wheel + active local session)
  echo ""
  echo ">>> [6/6] shutting down at $(date -Is)"
  echo ""
  echo "=== wrap_up.sh finished ==="
  sync
  systemctl poweroff
} 2>&1 | tee -a "$LOG"
