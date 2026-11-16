#!/bin/bash
# Weight streaming (docs/WEIGHT_STREAMING.md): the per-profile memory scope,
# the MoE-cache and big-mode serve profiles, and the llama.cpp v1.0.0 flags
# they pass. Hermetic: no GPU, no network, no systemd; portable bash (macOS
# and Linux). The rig itself is checked by the runbook, not here.
#
# Usage: bash tests/test_weight_streaming.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
T="$(mktemp -d "${TMPDIR:-/tmp}/obwsXXXXXX")"
trap 'rm -rf "$T"' EXIT

# 122 GiB usable on the 128 GB rig (MemTotal 127,926,272 kB, test_llama_serving.sh).
RIG_KB=127926272
source "$REPO_DIR/scripts/lib/memscope.sh"

echo ""
echo "per-profile memory scope (scripts/lib/memscope.sh):"
printf '#!/bin/bash\n# a daily profile\nexec serve.sh -m x\n' > "$T/serve-daily.sh"
printf '#!/bin/bash\n# openbeast-scope: mem-limit-pct=90 memlock=infinity host-class=rig min-mem-gib=118\n' > "$T/serve-big.sh"
_s() { _RC=0; _OUT="$(ob_mem_scope "$@" 2>&1)" || _RC=$?; }
_s "$T/serve-daily.sh" rig "$RIG_KB" 75
[[ $_RC -eq 0 && "$_OUT" == "75 - conf" ]] && pass "a profile with no declaration keeps MEM_LIMIT_PCT (75), no memlock" \
  || fail "daily profile: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 75
[[ $_RC -eq 0 && "$_OUT" == "90 infinity profile:serve-big.sh" ]] && pass "the big profile on the rig gets 90% and memlock infinity" \
  || fail "big profile on rig: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 60
[[ "$_OUT" == "90 infinity profile:serve-big.sh" ]] && pass "…whatever the conf's daily MEM_LIMIT_PCT is (60 here)" \
  || fail "big profile ignores conf pct: '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 75 85
[[ "$_OUT" == "85 infinity env" ]] && pass "…an explicit OPENBEAST_MEM_LIMIT_PCT for this launch still wins (memlock kept)" \
  || fail "env override: '$_OUT'"
_s "$T/serve-big.sh" spark "$RIG_KB" 75
[[ $_RC -eq 2 && "$_OUT" == *"rig-only profile"* ]] && pass "the big profile refuses a Spark (the raise is rig-only)" \
  || fail "big profile on spark: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig 65000000 75
[[ $_RC -eq 2 && "$_OUT" == *"at least 118 GiB"* ]] && pass "…and a 64 GB box (it could never hold the experts)" \
  || fail "big profile on 64 GB: rc=$_RC '$_OUT'"
printf '# openbeast-scope: mem-limit-pct=99\n' > "$T/serve-bad1.sh"
printf '# openbeast-scope: memlock=yes\n' > "$T/serve-bad2.sh"
printf '# openbeast-scope: mem-limt-pct=90\n' > "$T/serve-bad3.sh"
_bad=""
for _b in 1 2 3; do _s "$T/serve-bad$_b.sh" rig "$RIG_KB" 75; [[ $_RC -eq 2 ]] || _bad+=" bad$_b"; done
[[ -z "$_bad" ]] && pass "malformed declarations (pct 99, memlock=yes, a misspelt key) are refused, never ignored" \
  || fail "accepted:$_bad"
_s "$T/missing.sh" rig "$RIG_KB" 75
[[ "$_OUT" == "75 - conf" ]] && pass "a serve script that is not there declares nothing (start.sh reports it later)" \
  || fail "missing script: '$_OUT'"

# start.sh wires it into the daemon launch, and only there.
_st="$(sed -n '/>>> per-profile memory scope/,/<<< per-profile memory scope/p' "$REPO_DIR/start.sh")"
if [[ "$_st" == *'ob_mem_scope "$_scope_file"'* && "$_st" == *'LimitMEMLOCK=infinity'* \
      && "$_st" == *'export OPENBEAST_MEM_LIMIT_PCT="$MEM_LIMIT_PCT"'* ]] \
   && grep -q '${_SCOPE_PROPS\[@\]+"${_SCOPE_PROPS\[@\]}"}' "$REPO_DIR/start.sh"; then
  pass "start.sh -d sizes MemoryMax from ob_mem_scope, passes LimitMEMLOCK and carries the percent into the daemon"
else
  fail "start.sh's per-profile scope hook is missing or incomplete"
fi
if grep -q 'MEM_MAX_BYTES=$(( MEM_TOTAL_KB \* 1024 / 100 \* MEM_LIMIT_PCT ))' "$REPO_DIR/start.sh" \
   && [[ "$(grep -n 'per-profile memory scope (scripts' "$REPO_DIR/start.sh" | cut -d: -f1)" -lt \
         "$(grep -n 'MEM_MAX_BYTES=\$(( MEM_TOTAL_KB' "$REPO_DIR/start.sh" | cut -d: -f1)" ]]; then
  pass "…before MemoryMax is computed from the percent"
else
  fail "the scope hook runs after MEM_MAX_BYTES is computed"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
