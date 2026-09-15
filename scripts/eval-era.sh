#!/bin/bash
# The eval cache era: print it, or assert it has not moved.
#
#   ./scripts/eval-era.sh                 # print the current era hash
#   ./scripts/eval-era.sh --files         # and the six files that produce it
#   ./scripts/eval-era.sh --check <hash>  # exit 1 if the era has moved
#
# WHY (docs/BEAST_CAMPAIGN_PLAN.md §1). Rows measured either side of a change
# to any of the six files evals/cache.py hashes are NOT comparable, and until
# now nothing enforced that — era was a property people remembered. The
# 2026-09-08 plan scheduled the greedy churn floor before a `git pull` and the
# Tier-3 cells it calibrates after it, a cross-era mismatch that survived
# review precisely because it was nobody's job to check.
#
# `--check` turns that into a precondition a campaign can assert at start and
# between stages, and turns a silent comparability error into an exit code.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

era() {
  python3 - "$SCRIPT_DIR" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "evals"))
import cache
print(cache.context_hash())
PY
}

files() {
  python3 - "$SCRIPT_DIR" <<'PY'
import hashlib, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "evals"))
import cache
for p in cache.CONTEXT_FILES:
    try:
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
        print(f"  {h}  {os.path.relpath(p, sys.argv[1])}")
    except OSError as e:
        print(f"  MISSING           {p} ({e.__class__.__name__})")
PY
}

case "${1:-}" in
  --check)
    want="${2:?--check needs an era hash}"
    got="$(era)" || { echo "Error: could not compute the era" >&2; exit 2; }
    if [[ "$got" == "$want" ]]; then
      echo "era $got — unchanged"
      exit 0
    fi
    echo "ERA MOVED: expected $want, tree is $got" >&2
    echo "Rows measured under these two eras are NOT comparable. The inputs:" >&2
    files >&2
    exit 1 ;;
  --files) era; files ;;
  -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//' ;;
  "") era ;;
  *) echo "Error: unknown option: $1" >&2; exit 2 ;;
esac
