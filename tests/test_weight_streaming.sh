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
echo "llama.cpp v1.0.0 flags (tests/fixtures/llama/arg-options-v1.0.0.txt):"
OPTS="$REPO_DIR/tests/fixtures/llama/arg-options-v1.0.0.txt"
_pin="$(sed -nE 's/^LLAMA_CPP_REF=([0-9a-f]{40})$/\1/p' "$REPO_DIR/scripts/llama.cpp.ref")"
grep -q "commit $_pin" "$OPTS" && pass "the option list is the pinned commit's (${_pin:0:12})" \
  || fail "scripts/llama.cpp.ref pins ${_pin:0:12} but the option fixture is for another commit — regenerate it"
# _flags <file> — every -flag word in the serve.sh exec block of a serve script.
_flags() {
  awk '/exec "\$SCRIPT_DIR\/serve.sh"/ {on = 1} on {print} on && !/\\$/ {on = 0}' "$1" \
    | tr ' \t' '\n\n' | grep -E '^--?[a-zA-Z]' | sed 's/=.*//' || true
}
_unknown=""; _n=0
for _ss in "$REPO_DIR"/scripts/serve-*.sh; do
  while IFS= read -r _f; do
    [[ -n "$_f" ]] || continue
    _n=$((_n + 1))
    grep -qxF -- "$_f" "$OPTS" || _unknown+=" $(basename "$_ss"):$_f"
  done < <(_flags "$_ss")
done
# serve.sh's own fixed flags on the llama-server exec line.
for _f in -m -ngl -c -np --kv-unified -ctk -ctv --metrics --host --port --cache-ram; do
  grep -qxF -- "$_f" "$OPTS" || _unknown+=" serve.sh:$_f"
done
[[ -z "$_unknown" && $_n -gt 50 ]] && pass "all $_n flags the serve scripts pass, and serve.sh's own, exist in v1.0.0" \
  || fail "flags v1.0.0 does not define (or too few found: $_n):$_unknown"
_removed=""
for _f in --defrag-thold -dt --mmap --mlock --direct-io -dio; do
  grep -rqE -- "(^|[[:space:]\"])$_f([[:space:]=\"]|$)" "$REPO_DIR"/scripts/serve*.sh "$REPO_DIR"/scripts/run*.sh \
    "$REPO_DIR"/scripts/profile-*.sh "$REPO_DIR"/scripts/measure-vram.sh 2>/dev/null && _removed+=" $_f"
done
[[ -z "$_removed" ]] && pass "no launcher uses an option v1.0.0 removed (--defrag-thold, --mmap, --mlock, --direct-io)" \
  || fail "removed in v1.0.0 but still passed:$_removed"

echo ""
echo "the MoE-cache and big-mode profiles:"
FN="$REPO_DIR/scripts/serve-qwen38-flash-next-unc-iq4xs-moecache.sh"
_fl="$(_flags "$FN" | tr '\n' ' ')"
if [[ -x "$FN" && "$_fl" == *"--moe-cache-mib"* && "$_fl" == *"--n-cpu-moe"* && "$_fl" == *"--cache-ram"* ]] \
   && [[ -z "$(ob_scope_decl "$FN")" ]]; then
  pass "Flash-Next + cache: host experts, a VRAM cache, a pinned prompt cache, and the daily 75% scope (no declaration)"
else
  fail "Flash-Next MoE-cache profile: flags '$_fl', scope '$(ob_scope_decl "$FN")'"
fi
# Its memory plan must fit the 75% scope: 40 CPU layers x 1.27 GiB + 27.63 GiB
# of tables + 1.5 buffers + the --cache-ram + 3 for the stack, against 75% of 122 GiB.
_ncm="$(awk '$1 == "--n-cpu-moe" {print $2}' "$FN")"; _cr="$(awk '$1 == "--cache-ram" {print $2}' "$FN")"
_need="$(awk -v n="$_ncm" -v c="$_cr" 'BEGIN {printf "%d", n * 1.27 + 27.63 + 1.5 + c / 1024 + 3}')"
_cap="$(awk -v k="$RIG_KB" 'BEGIN {printf "%d", k / 1048576 * 0.75}')"
[[ -n "$_ncm" && "$_need" -lt "$_cap" ]] && pass "…its host RAM plan (~${_need} GiB) fits the 75% scope (${_cap} GiB)" \
  || fail "Flash-Next MoE-cache needs ~${_need:-?} GiB of the ${_cap} GiB scope (n-cpu-moe '${_ncm}', cache-ram '${_cr}')"
grep -q -- '--cache-ram' "$REPO_DIR/scripts/serve-qwen38-flash-next-unc-iq4xs.sh" \
  && pass "…and the static Flash-Next baseline pins --cache-ram too (its experts share the scope)" \
  || fail "serve-qwen38-flash-next-unc-iq4xs.sh leaves the prompt cache unbounded beside 72 GiB of host weights"

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
