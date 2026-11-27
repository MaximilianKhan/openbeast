#!/bin/bash
# Review 2026-10-27 (R5 supply chain / CI): scripts/land-dependabot.sh, with
# negative controls. Hermetic: gh and sleep are recording stubs; nothing is
# fetched, approved or merged.
#
#   1. a red check stops the run before the merge (R5-03)
#   2. a PR that does not touch agents/requirements.txt waits for the rebase
#      only, not for a relock that never comes (R5-04)
#
# Usage: bash tests/test_review1027_ci.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0 FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { grep -qF -- "$2" <<< "$1"; }
T="$(mktemp -d "${TMPDIR:-/tmp}/obrev1027ciXXXXXX")"
trap 'rm -rf "$T"' EXIT

# ===========================================================================
echo "1-2. land-dependabot.sh — red checks, and PRs with no relock:"
# ===========================================================================
if ! command -v jq >/dev/null 2>&1; then
  if [[ "${CI:-}" == true ]]; then fail "jq is not installed (the gh stub needs it)"; else echo "  SKIP: jq not installed"; fi
else
  LB="$T/bin_land" S="$T/state"; mkdir -p "$LB" "$S"
  printf '#!/bin/bash\nexit 0\n' > "$LB/sleep"
  # gh answers from fixtures and evaluates -q with real jq. $S/checks_rc
  # decides whether `gh pr checks --watch` ends green (0) or red (1).
  # PR 7 bumps agents/requirements.txt (relock path); PR 8 bumps a workflow.
  cat > "$LB/gh" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "gh $*" >> "$S/gh.log"
q=""; args=()
while [[ $# -gt 0 ]]; do
  case "$1" in -q) q="$2"; shift 2 ;; *) args+=("$1"); shift ;; esac
done
set -- "${args[@]}"
out() { if [[ -n "$q" ]]; then jq -r "$q"; else cat; fi; }
HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
case "$*" in
  "repo view"*)                    echo '{"nameWithOwner": "me/openbeast"}' | out ;;
  "pr view "[78]" --json headRefName"*) echo '{"headRefName": "dependabot/x"}' | out ;;
  "pr view 7 --json commits"*)     echo '{"commits": [{"messageHeadline": "deps: regenerate agents/requirements.lock"}]}' | out ;;
  "pr view 8 --json commits"*)     echo '{"commits": [{"messageHeadline": "Bump actions/checkout from 7.0.0 to 7.0.1"}]}' | out ;;
  "pr view "[78]" --json headRefOid"*) echo "{\"headRefOid\": \"$HEAD\"}" | out ;;
  "pr view "[78]" --json state"*)  if [[ -s "$S/merge.log" ]]; then echo '{"state": "MERGED"}'; else echo '{"state": "OPEN"}'; fi | out ;;
  "pr comment"*) echo ok ;;
  "pr merge"*)  echo "$*" >> "$S/merge.log"; echo ok ;;
  "pr checks"*)
      rc="$(cat "$S/checks_rc" 2>/dev/null || echo 0)"
      if [[ "$rc" == 0 ]]; then
        printf 'test\tpass\t5m\thttps://x/1\nLock matches a fresh resolve\tpass\t1m\thttps://x/2\n'
      else
        printf 'test\tpass\t5m\thttps://x/1\nLock matches a fresh resolve\tfail\t1m\thttps://x/2\n'
      fi
      exit "$rc" ;;
  "pr list"*)
      echo '[{"number": 8, "files": [{"path": ".github/workflows/ci.yml"}]},
             {"number": 7, "files": [{"path": "agents/requirements.txt"}]}]' | out ;;
  "api repos/me/openbeast/commits/main"*)  echo '{"sha": "m"}' | out ;;
  "api repos/me/openbeast/compare/"*)      echo '{"behind_by": 0}' | out ;;
  "api repos/me/openbeast/actions/runs?"*) echo '{"workflow_runs": []}' | out ;;
  "run list"*) echo '[]' | out ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
STUB
  chmod +x "$LB/gh" "$LB/sleep"
  LR="$T/land_repo"; mkdir -p "$LR/scripts"
  install -m 755 "$REPO_DIR/scripts/land-dependabot.sh" "$LR/scripts/"
  land() { # land <checks_rc> [args…] — sets _out/_rc
    echo "$1" > "$S/checks_rc"; shift
    : > "$S/gh.log"; : > "$S/merge.log"
    _out="$(env PATH="$LB:$PATH" OB_STUB_STATE="$S" bash "$LR/scripts/land-dependabot.sh" "$@" 2>&1)"; _rc=$?
  }

  land 1 7
  if [[ $_rc -ne 0 && ! -s "$S/merge.log" ]] && has "$_out" "a check failed (none was merely cancelled) — NOT merging" \
     && has "$_out" "Lock matches a fresh resolve"; then
    pass "R5-03: a red check (gh pr checks exit 1) stops the run before the merge, naming the check"
  else
    fail "R5-03 red check (rc=$_rc, merge=$(cat "$S/merge.log")): $_out"
  fi
  land 0 7
  if [[ $_rc -eq 0 && -s "$S/merge.log" ]] && has "$_out" "PR 7 -> MERGED" && ! has "$_out" "unexpected gh call"; then
    pass "R5-03 control: the same PR with green checks is merged"
  else
    fail "R5-03 control (rc=$_rc): $_out"
  fi

  land 0 8
  _polls="$(grep -c "compare/" "$S/gh.log")"
  if [[ $_rc -eq 0 ]] && has "$_out" "PR 8 -> MERGED" && has "$_out" "rebase only" \
     && [[ "$_polls" -eq 1 ]] && ! grep -q "dependabot-relock" "$S/gh.log"; then
    pass "R5-04: a named github-actions PR waits for the rebase only (1 poll, no relock query) and lands"
  else
    fail "R5-04 non-requirements PR (rc=$_rc, $_polls compare polls): $_out"
  fi
  # Control: the requirements PR still waits for its relock commit.
  land 0 7
  if [[ $_rc -eq 0 ]] && ! has "$_out" "rebase only" && grep -q "pr view 7 --json commits" "$S/gh.log"; then
    pass "R5-04 control: an agents/requirements.txt PR still waits for the relock commit"
  else
    fail "R5-04 control (rc=$_rc): $_out"
  fi
  land 1 8
  [[ $_rc -ne 0 && ! -s "$S/merge.log" ]] && pass "R5-03+04: a red check on a non-requirements PR also stops before the merge" \
    || fail "red check on PR 8 (rc=$_rc): $_out"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
