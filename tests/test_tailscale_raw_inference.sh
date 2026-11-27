#!/bin/bash
# scripts/setup-tailscale.sh: where a RAW :8443 mount points (no beast-gate).
#
# Usage: ./tests/test_tailscale_raw_inference.sh
#
# The raw mount used to be a fixed http://<bind>:8080, while beast-gate's
# upstream follows INFERENCE_URL: on a TensorFold / vLLM box (:8000) the raw
# :8443 proxied to nothing (502) and still said "published"; an
# INFERENCE_URL on another machine would have been mounted at :8080 here.
# (2026-11-16 review.) _raw_inference_target is lifted out and run with the
# real lib/backend.sh + lib/net.sh — the rest of the script needs sudo and a
# live tailnet, and none of that decides the target. A static check pins
# that every raw mount uses it.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/scripts/setup-tailscale.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== setup-tailscale.sh raw :8443 target ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-raw-8443.XXXXXX")"
trap 'rm -rf "$T"' EXIT
awk '/^_raw_inference_target\(\) \{/,/^}/' "$SRC" > "$T/fns.sh"
if [[ "$(grep -c '^}' "$T/fns.sh")" != 1 ]]; then
  fail "could not find _raw_inference_target in setup-tailscale.sh"
  echo "Results: $PASS passed, $FAIL failed"; exit 1
fi

# target <inference-url> [probe-host] [bind-host] -> prints the target or "REFUSED"
target() {
  bash -c '
    set -euo pipefail
    source "$1/scripts/lib/backend.sh"; source "$1/scripts/lib/net.sh"; source "$2"
    _raw_inference_target "$3" "${4:-127.0.0.1}" "${5:-127.0.0.1}" || echo REFUSED
  ' _ "$REPO_DIR" "$T/fns.sh" "$@" 2>&1
}

check() { # check <want> <args...>
  local want="$1" got; shift
  got="$(target "$@")"
  if [[ "$got" == "$want" ]]; then pass "$* -> $want"; else fail "$* -> '$got' (want '$want')"; fi
}

check "http://127.0.0.1:8080"   "http://127.0.0.1:8080"
check "http://127.0.0.1:8000"   "http://127.0.0.1:8000"            # TensorFold / vLLM
check "http://127.0.0.1:8000"   "http://0.0.0.0:8000/v1"           # wildcard -> loopback, path dropped
check "http://127.0.0.1:8000"   "http://localhost:8000"
check "http://[::1]:8000"       "http://[::1]:8000"
check "http://100.64.0.7:8000"  "http://100.64.0.7:8000" "100.64.0.7" "100.64.0.7"   # this box's own address
check "REFUSED"                 "http://spark-1.example.ts.net:8000"                 # another machine
check "REFUSED"                 "http://10.0.0.9:8080"

# Every raw mount goes through it — no fixed :8080 is left.
if grep -nE 'serve --bg --https=8443 "http://\$UP_HOST:8080"' "$SRC" >/dev/null; then
  fail "setup-tailscale.sh still mounts :8443 at a fixed :8080"
elif [[ "$(grep -c 'serve --bg --https=8443 "\$RAW_TARGET"' "$SRC")" == 2 ]]; then
  pass "both raw :8443 branches (shared key, acknowledged open) mount \$RAW_TARGET"
else
  fail "expected both raw :8443 mounts to use \$RAW_TARGET"
fi

# A refused serve names the one-time operator fix (the rig, 2026-11-22: the
# re-point of :8443 at the gate was denied). ts_admin is lifted out and run
# against a mock tailscale and sudo on PATH.
awk '/^ts_admin\(\) \{/,/^}/' "$SRC" > "$T/ts_admin.sh"
mkdir -p "$T/bin"
cat > "$T/bin/tailscale" <<'STUB'
#!/bin/bash
if [[ "$1 $2" == "debug prefs" ]]; then echo "{\"OperatorUser\": \"${TS_OPERATOR:-}\"}"; exit 0; fi
echo "tailscale $*" >> "$TS_LOG"
if [[ "$1" == serve && -n "${TS_DENY:-}" ]]; then echo "Access denied: serve config denied" >&2; exit 1; fi
exit 0
STUB
cat > "$T/bin/sudo" <<'STUB'
#!/bin/bash
[[ -n "${SUDO_FAIL:-}" ]] && { echo "sudo: a terminal is required to read the password" >&2; exit 1; }
"$@"
STUB
chmod +x "$T/bin/tailscale" "$T/bin/sudo"
admin() { # admin <env...> -- prints "rc=<n>" and the combined output
  env "$@" PATH="$T/bin:$PATH" TS_LOG="$T/ts.log" bash -c 'source "$1"; ts_admin serve --bg --https=8443 http://127.0.0.1:8091; echo "rc=$?"' _ "$T/ts_admin.sh" 2>&1
}
me="$(id -un)"
o="$(admin SUDO_FAIL=1)"
if grep -q "rc=1" <<<"$o" && grep -qF "sudo tailscale set --operator=$me" <<<"$o" && grep -q "was refused" <<<"$o"; then
  pass "ts_admin: a refused sudo serve names the operator fix and keeps the exit status"
else fail "ts_admin sudo refused: $o"; fi
o="$(admin TS_OPERATOR="$me" TS_DENY=1)"
if grep -q "rc=1" <<<"$o" && grep -qF "sudo tailscale set --operator=$me" <<<"$o"; then
  pass "ts_admin: a serve tailscaled denies names the operator fix"
else fail "ts_admin denied: $o"; fi
o="$(admin TS_OPERATOR="$me")"
if grep -q "rc=0" <<<"$o" && ! grep -q "operator" <<<"$o"; then
  pass "ts_admin: a serve that succeeds prints no hint (control)"
else fail "ts_admin ok: $o"; fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
