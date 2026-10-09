#!/bin/bash
# Operator scripts — the 2026-10-09 review fixes, each run for real.
#
# Usage: ./tests/test_operator_scripts.sh
#
# Every script runs from a THROWAWAY copy of scripts/ under $TMPDIR with a
# throwaway HOME and stubbed externals (tailscale, sudo, gh, git, cargo…):
# the real openbeast.conf, the real tailnet, GitHub and the GPU are never
# touched, and nothing is installed.
#
#   1  setup-mcpo-keys.sh   --help / unknown options never run it; an EMPTY
#                           key is not "already set"            (UX-10, S7)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo "=== operator script tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-operator-test.XXXXXX")"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home"

# fresh_repo <name> — a sandbox checkout holding only scripts/ (+ lib/).
fresh_repo() {
  local r="$T/$1"
  mkdir -p "$r/scripts/lib"
  cp "$REPO_DIR"/scripts/*.sh "$r/scripts/"
  cp "$REPO_DIR"/scripts/lib/* "$r/scripts/lib/" 2>/dev/null
  printf '%s\n' "$r"
}
# run <cmd…> — sets OUT (stdout+stderr) and RC. No terminal on stdin.
run() { OUT="$("$@" </dev/null 2>&1)"; RC=$?; }
_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
_val() { sed -n "s/^$2=//p" "$1" | tail -n1; }   # _val <conf> <KEY>

# ---------------------------------------------------------------------------
echo ""
echo "1. setup-mcpo-keys.sh (UX-10, supply S7):"
K="$(fresh_repo keys)"
KEYS=(env -i HOME="$T/home" PATH="/usr/bin:/bin" bash "$K/scripts/setup-mcpo-keys.sh")
run "${KEYS[@]}" --help
if [[ $RC -eq 0 ]] && has "$OUT" "--rotate" && has "$OUT" "MCPO_GUEST_KEY" \
   && ! has "$OUT" "set -euo pipefail" && [[ ! -e "$K/openbeast.conf" ]]; then
  pass "--help prints the header and writes nothing (it used to generate both keys)"
else
  fail "--help (rc=$RC, conf exists: $([[ -e "$K/openbeast.conf" ]] && echo yes || echo no)): $OUT"
fi
run "${KEYS[@]}" --rotat
if [[ $RC -eq 2 ]] && has "$OUT" "Unknown option: --rotat" && has "$OUT" "--help" \
   && [[ ! -e "$K/openbeast.conf" ]]; then
  pass "an unknown option is refused (exit 2) before anything is written"
else
  fail "typo'd option (rc=$RC): $OUT"
fi
run "${KEYS[@]}" --with-jwt --bogus
if [[ $RC -eq 2 && ! -e "$K/openbeast.conf" ]]; then
  pass "…even when it follows a valid one"
else
  fail "valid + unknown option (rc=$RC): $OUT"
fi
# The S7 scenario: both lines uncommented from openbeast.conf.example, empty.
printf '# mine\nMCPO_ADMIN_KEY=\n  MCPO_GUEST_KEY = ""   \nOTHER=keep\n' > "$K/openbeast.conf"
run "${KEYS[@]}"
if [[ $RC -eq 0 ]] && [[ "$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY | wc -c)" -eq 65 ]] \
   && [[ "$(_val "$K/openbeast.conf" MCPO_GUEST_KEY | wc -c)" -eq 65 ]] \
   && has "$OUT" "EMPTY" && ! has "$OUT" "already set"; then
  pass "an empty key line is NOT 'already set': both get a generated value"
else
  fail "empty keys (rc=$RC): $OUT :: $(tr '\n' '|' < "$K/openbeast.conf")"
fi
if [[ "$(grep -c 'MCPO_ADMIN_KEY' "$K/openbeast.conf")" == 1 ]] && grep -qx 'OTHER=keep' "$K/openbeast.conf" \
   && grep -qx '# mine' "$K/openbeast.conf" && [[ "$(_mode "$K/openbeast.conf")" == 600 ]]; then
  pass "…written in place (no duplicate line), other lines kept, conf 0600"
else
  fail "conf after filling empty keys: $(tr '\n' '|' < "$K/openbeast.conf")"
fi
# Negative control: real values are left alone without --rotate.
A1="$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY)"
run "${KEYS[@]}"
if [[ $RC -eq 0 ]] && has "$OUT" "MCPO_ADMIN_KEY: already set" \
   && [[ "$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY)" == "$A1" ]]; then
  pass "control: a key that HAS a value is left untouched and reported as set"
else
  fail "re-run clobbered or misreported a real key (rc=$RC): $OUT"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
