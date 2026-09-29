#!/bin/bash
# BEAST_ESCALATE in openbeast.conf reaches the tool servers (review open-prs-5).
#
# Usage: bash tests/test_escalate_conf.sh
#
# agents/tools.py reads BEAST_ESCALATE per-call from the process env and
# compares it against exactly "1". Before this, lib/conf.sh never forwarded
# the key, so a user who put BEAST_ESCALATE=1 next to BEAST_ASSIST=1 in
# openbeast.conf got nothing, with no warning.
#
# Runs the REAL conf.sh against a throwaway conf in a clean env (env -i): no
# stack, no real openbeast.conf. The final check pipes the exported value into
# the REAL tools.escalation_enabled(), so "forwarded" means "the reader agrees".

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-esc-conf-XXXXXX")"
trap 'rm -rf "$T"' EXIT

SB="$T/rig"
mkdir -p "$SB/scripts/lib"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$SB/scripts/lib/"

# conf_eval <conf body> <env assignments…> -- <snippet>; stderr → $T/conf.err
conf_eval() {
  local body="$1"; shift
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  printf '%s\n' "$body" > "$SB/openbeast.conf"
  env -i HOME="$T" PATH="$PATH" REPO_DIR="$SB" ${envs[@]+"${envs[@]}"} \
    bash -c "source '$SB/scripts/lib/conf.sh'; $1" 2>"$T/conf.err"
}

echo "=== BEAST_ESCALATE forwarding (lib/conf.sh) ==="

OUT="$(conf_eval 'BEAST_ASSIST=1
BEAST_ESCALATE=1' -- 'printf %s "${BEAST_ESCALATE-ABSENT}"')"
if [[ "$OUT" == "1" ]]; then
  pass "BEAST_ESCALATE=1 in openbeast.conf is exported to the stack"
else
  fail "BEAST_ESCALATE=1 in the conf was not exported (got '$OUT')"
fi

OUT="$(conf_eval 'BEAST_ASSIST=1
BEAST_ESCALATE=true   # cards on' -- 'printf %s "${BEAST_ESCALATE-ABSENT}"')"
if [[ "$OUT" == "1" ]]; then
  pass "true + an inline comment is exported as the exact '1' tools.py compares against"
else
  fail "BEAST_ESCALATE=true # … not canonicalised (got '$OUT')"
fi

# Negative controls: absent stays absent (nothing exported), off is 0.
OUT="$(conf_eval 'BEAST_ASSIST=1' -- 'printf %s "${BEAST_ESCALATE-ABSENT}"')"
if [[ "$OUT" == "ABSENT" ]]; then
  pass "negative control: no key, nothing exported"
else
  fail "BEAST_ESCALATE exported without being configured (got '$OUT')"
fi
OUT="$(conf_eval 'BEAST_ESCALATE=off' -- 'printf %s "${BEAST_ESCALATE-ABSENT}"')"
if [[ "$OUT" == "0" ]]; then
  pass "negative control: off is exported as 0"
else
  fail "BEAST_ESCALATE=off not exported as 0 (got '$OUT')"
fi

# The launching shell still wins over the conf, as for BEAST_ASSIST.
OUT="$(conf_eval 'BEAST_ASSIST=1
BEAST_ESCALATE=1' BEAST_ESCALATE=0 -- 'printf %s "$BEAST_ESCALATE"')"
if [[ "$OUT" == "0" ]]; then
  pass "an exported BEAST_ESCALATE=0 overrides the conf"
else
  fail "env override ignored (got '$OUT')"
fi

# On without the checker does nothing — that must be visible.
conf_eval 'BEAST_ESCALATE=1' -- 'true' >/dev/null
if grep -q 'no effect without BEAST_ASSIST=1' "$T/conf.err"; then
  pass "BEAST_ESCALATE=1 without BEAST_ASSIST=1 warns"
else
  fail "escalation-without-assist was silent (stderr: $(cat "$T/conf.err"))"
fi
conf_eval 'BEAST_ASSIST=1
BEAST_ESCALATE=1' -- 'true' >/dev/null
if ! grep -q 'BEAST_ESCALATE' "$T/conf.err"; then
  pass "negative control: with BEAST_ASSIST=1 there is no warning"
else
  fail "spurious warning: $(cat "$T/conf.err")"
fi

# Review r2: the warning must agree with the READER. BEAST_ASSIST is
# forwarded verbatim and tools.diagnostics_enabled() wants exactly "1", so
# `true` (or `1  # on`) leaves the checker off — the warning used _ob_bool and
# stayed quiet. Cross-check against the real tools.py, not an assumption.
for spelling in 'true' '1  # on'; do
  OUT="$(conf_eval "BEAST_ASSIST=$spelling
BEAST_ESCALATE=1" -- "cd '$REPO_DIR/agents' && python3 -c 'import tools; print(tools.diagnostics_enabled())'")"
  if [[ "$OUT" == "False" ]] && grep -q 'no effect without BEAST_ASSIST=1' "$T/conf.err"; then
    pass "BEAST_ASSIST='$spelling' (checker off per tools.py) warns"
  else
    fail "BEAST_ASSIST='$spelling': diagnostics_enabled=$OUT, stderr: $(cat "$T/conf.err")"
  fi
done
# Negative control: the internal spelling does turn the checker on.
OUT="$(conf_eval 'BEAST_ESCALATE=1' OPENBEAST_DIAGNOSTICS=1 -- "cd '$REPO_DIR/agents' && python3 -c 'import tools; print(tools.diagnostics_enabled())'")"
if [[ "$OUT" == "True" ]] && ! grep -q 'BEAST_ESCALATE' "$T/conf.err"; then
  pass "negative control: OPENBEAST_DIAGNOSTICS=1 (checker on) does not warn"
else
  fail "OPENBEAST_DIAGNOSTICS=1: diagnostics_enabled=$OUT, stderr: $(cat "$T/conf.err")"
fi

# End to end with the real reader: what conf.sh exports, tools.py honours.
OUT="$(conf_eval 'BEAST_ASSIST=1
BEAST_ESCALATE=yes' -- "cd '$REPO_DIR/agents' && python3 -c 'import tools; print(tools.escalation_enabled())'")"
if [[ "$OUT" == "True" ]]; then
  pass "agents/tools.escalation_enabled() is True under the forwarded value"
else
  fail "tools.py does not see escalation as enabled (got '$OUT'; $(tail -3 "$T/conf.err"))"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
