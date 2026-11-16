#!/bin/bash
# setup-tailscale.sh: the tailnet machine name comes from the conf, never a
# hard-coded "beast" on a box that must not be called that.
#
# Usage: ./tests/test_tailscale_hostname.sh
#
#   - TS_HOSTNAME resolves env > openbeast.conf > unset; a bad name is refused
#   - _join_name: the configured name wins; a rig defaults to "beast"; a Spark
#     with no name refuses instead of guessing
#   - _name_note: an already-joined machine whose name differs gets a note
#     (with the rename command), and the script contains no rename of its own
#
# The functions are lifted out of the script with awk and run in a sandbox,
# so nothing here touches tailscale.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/scripts/setup-tailscale.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== setup-tailscale.sh machine-name tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-ts-name.XXXXXX")"
trap 'rm -rf "$T"' EXIT

{
  awk '/^_conf_get\(\) \{/,/^}/' "$SRC"
  awk '/^TS_HOSTNAME="\$\{TS_HOSTNAME:-/,/^fi$/' "$SRC"
  awk '/^_join_name\(\) \{/,/^}/' "$SRC"
  awk '/^_name_note\(\) \{/,/^}/' "$SRC"
} > "$T/fns.sh"
for f in _conf_get _join_name _name_note; do
  grep -q "^$f() {" "$T/fns.sh" || { fail "could not lift $f out of setup-tailscale.sh"; }
done
grep -q '^TS_HOSTNAME=' "$T/fns.sh" || fail "could not lift the TS_HOSTNAME resolution"
[[ $FAIL -eq 0 ]] || { echo "Results: $PASS passed, $FAIL failed"; exit 1; }

# run <env-TS_HOSTNAME|-> <conf-text> <snippet> — stdout+stderr of the snippet
# after the lifted code resolved TS_HOSTNAME against that conf.
run() {
  printf '%s\n' "$2" > "$T/openbeast.conf"
  local envset=()
  [[ "$1" != - ]] && envset=(TS_HOSTNAME="$1")
  env -i PATH="$PATH" ${envset[@]+"${envset[@]}"} CONF="$T/openbeast.conf" FNS="$T/fns.sh" bash -c '
    _conf_file="$CONF"
    . "$FNS" || exit 9
    '"$3" 2>&1
}

[[ "$(run - "" 'echo "[$TS_HOSTNAME]"')" == "[]" ]] \
  && pass "no env, no conf key: TS_HOSTNAME is empty" || fail "unset TS_HOSTNAME was not empty"
[[ "$(run - 'TS_HOSTNAME=spark-1' 'echo "$TS_HOSTNAME"')" == spark-1 ]] \
  && pass "openbeast.conf TS_HOSTNAME is read" || fail "conf TS_HOSTNAME was not read"
[[ "$(run - 'TS_HOSTNAME="spark-2"  # quoted, commented' 'echo "$TS_HOSTNAME"')" == spark-2 ]] \
  && pass "a quoted, commented conf value is unwrapped" || fail "quoted conf value"
[[ "$(run rig-a 'TS_HOSTNAME=spark-1' 'echo "$TS_HOSTNAME"')" == rig-a ]] \
  && pass "env TS_HOSTNAME overrides the conf" || fail "env did not override the conf"
_out="$(run - 'TS_HOSTNAME=Spark_1' 'echo reached')"
if [[ "$_out" != *reached* && "$_out" == *"is not a tailnet machine name"* ]]; then
  pass "an invalid name is refused before anything runs"
else
  fail "invalid name was accepted: $_out"
fi

[[ "$(run - 'TS_HOSTNAME=spark-1' '_join_name spark')" == spark-1 ]] \
  && pass "join: the configured name wins on a Spark" || fail "join name on a configured Spark"
[[ "$(run - '' '_join_name rig')" == beast ]] \
  && pass "join: an unnamed rig defaults to beast" || fail "rig default"
_out="$(run - '' '_join_name spark && echo joined-as-default; echo rc=$?')"
if [[ "$_out" != *joined-as-default* && "$_out" == *"set TS_HOSTNAME in openbeast.conf"* ]]; then
  pass "join: an unnamed Spark refuses instead of becoming 'beast'"
else
  fail "unnamed Spark: $_out"
fi

_out="$(run - 'TS_HOSTNAME=spark-1' '_name_note beast')"
[[ "$_out" == *"joined as 'beast'"* && "$_out" == *"tailscale set --hostname=spark-1"* ]] \
  && pass "joined under another name: a note with the rename command" || fail "note: $_out"
[[ -z "$(run - 'TS_HOSTNAME=spark-1' '_name_note spark-1')" ]] \
  && pass "joined under the configured name: silent" || fail "note printed for a matching name"
[[ -z "$(run - '' '_name_note spark-1')" ]] \
  && pass "no configured name: silent" || fail "note printed with no TS_HOSTNAME"

if grep -vE '^[[:space:]]*(#|echo)' "$SRC" | grep -qE 'tailscale (set|up)[^|]*--hostname'; then
  _n="$(grep -vE '^[[:space:]]*(#|echo)' "$SRC" | grep -cE 'tailscale (set|up)[^|]*--hostname')"
  if [[ "$_n" == 1 ]] && grep -A0 -B12 'sudo tailscale up --hostname="\$_join_as"' "$SRC" | grep -q '^else$'; then
    pass "the only --hostname is the first join's, in the not-joined branch"
  else
    fail "setup-tailscale.sh sets --hostname outside the first join ($_n places)"
  fi
else
  fail "the first join's tailscale up --hostname is missing"
fi

if grep -q '^#TS_HOSTNAME=' "$REPO_DIR/openbeast.conf.example"; then
  pass "TS_HOSTNAME is documented in openbeast.conf.example (so the lint knows it)"
else
  fail "openbeast.conf.example does not document TS_HOSTNAME"
fi

echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
