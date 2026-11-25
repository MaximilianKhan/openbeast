#!/bin/bash
# The local merge gate (CI is off since 2026-11-17: CONTRIBUTING.md "CI is off").
#
#   scripts/gate.sh [--ref REF] [--linux] [--quick] [--keep]
#
# Runs the checks the CI workflows defined, in a PRISTINE tree: a fresh
# `git worktree add --detach` of REF (default HEAD), so only committed files
# count. Untracked or ignored files in your checkout (an `.env` profile that
# .gitignore hid, a generated file, a stray patch) cannot make a run pass:
# #314 merged a main where 17 Basalt tests failed because the author's
# checkout held a file the commit did not (fixed in #316).
#
#   1. ruff E9,F                (python -m ruff: no need for ruff on PATH)
#   2. shellcheck -S error      over every tracked *.sh (skipped, said so, if not installed)
#   3. evals/suite_stats.py --check and evals/v5_board.py --check
#   4. pytest tests/ -q         (--quick: only the doc-pinning tests)
# --linux: the same tree in the Linux test image (docker, $OB_GATE_IMAGE,
# default ob-review1116-test) with --init (pytest must not be a child of PID
# 1) and --network none, re-committed as a git repo inside, then
# tests/run_tests.sh (the shell suites that need GNU tools, and pytest).
#
# Prints the tree's commit and one PASS/FAIL line per step; exit 0 only when
# every step passed. Paste that block into the PR body.
#   PY=~/.local/opt/openbeast-eval-venv/bin/python scripts/gate.sh     (the Mac)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REF=HEAD LINUX=0 QUICK=0 KEEP=0
while (( $# )); do
  case "$1" in
    --ref) REF="${2:?--ref needs a commit}"; shift ;;
    --linux) LINUX=1 ;;
    --quick) QUICK=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "gate.sh: unknown argument $1 (--help)" >&2; exit 2 ;;
  esac
  shift
done
PY="${PY:-python3}"
SHA="$(git -C "$REPO_DIR" rev-parse --verify "$REF^{commit}")"
WT="$(mktemp -d "${TMPDIR:-/tmp}/ob-gate.XXXXXX")"
cleanup() { (( KEEP )) && { echo "kept: $WT"; return; }; git -C "$REPO_DIR" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"; git -C "$REPO_DIR" worktree prune 2>/dev/null || true; }
trap cleanup EXIT
rmdir "$WT"
git -C "$REPO_DIR" worktree add -q --detach "$WT" "$SHA"
# shellcheck disable=SC2034  # read in the summary
declare -a RESULTS=()
FAILED=0
step() {  # name command...
  local name="$1"; shift
  echo "=== $name"
  if ( cd "$WT" && "$@" ); then RESULTS+=("PASS  $name"); else RESULTS+=("FAIL  $name"); FAILED=1; fi
}

if (( LINUX )); then
  IMG="${OB_GATE_IMAGE:-ob-review1116-test}"
  docker image inspect "$IMG" >/dev/null 2>&1 || { echo "gate.sh: no docker image $IMG (OB_GATE_IMAGE)" >&2; exit 2; }
  # shellcheck disable=SC2016  # expands in the container
  step "linux: tests/run_tests.sh in $IMG (--init, --network none)" \
    docker run --rm --init --network none -v "$WT:/src:ro" "$IMG" bash -c '
      set -e; cp -a /src /tmp/ob; cd /tmp/ob; rm -f .git
      git init -q; git add -A; git -c user.email=gate@local -c user.name=gate commit -qm gate
      ./tests/run_tests.sh'
else
  step "ruff E9,F" "$PY" -m ruff check agents evals tests scripts extensions --select E9,F
  if command -v shellcheck >/dev/null 2>&1; then
    # shellcheck disable=SC2016
    step "shellcheck -S error (tracked *.sh)" bash -c 'git ls-files -z "*.sh" | xargs -0 shellcheck -S error'
  else
    RESULTS+=("SKIP  shellcheck -S error (not installed)")
  fi
  step "suite_stats --check" "$PY" evals/suite_stats.py --check
  step "v5_board --check" "$PY" evals/v5_board.py --check
  if (( QUICK )); then
    step "pytest (doc-pinning)" "$PY" -m pytest -q -p no:cacheprovider tests/test_suite_stats.py tests/test_beast_docs.py \
      tests/test_beast_slot_ports_doc.py tests/test_v5_board.py tests/test_profiles_tracked.py
  else
    step "pytest tests/" "$PY" -m pytest -q -p no:cacheprovider tests/
  fi
fi

echo
echo "gate: $SHA ($(git -C "$REPO_DIR" log -1 --format=%s "$SHA" | cut -c1-72)) in a clean worktree, $(uname -s)$( (( LINUX )) && echo " + ${OB_GATE_IMAGE:-ob-review1116-test}")"
printf '  %s\n' "${RESULTS[@]}"
(( FAILED )) && { echo "gate: FAILED"; exit 1; }
echo "gate: PASSED"
