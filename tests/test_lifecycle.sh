#!/bin/bash
# Stack lifecycle tests: start.sh / stop.sh / healthcheck.sh / doctor.sh /
# ext.sh and the libs they share (lib/net.sh, lib/proc.sh). The 2026-09-29
# review's "lifecycle" findings, each pinned by a case that FAILS on the old
# code.
#
# Same rules as tests/test_scripts.sh: no GPU, no docker, no network, no real
# stack. Everything that would touch one is a stub on PATH or a throwaway
# HTTP server on an ephemeral loopback port; every process this file starts
# exits on its own or is reaped by the EXIT trap. Never `pkill -f` here: the
# harness's own command line would match.
#
# Usage: bash tests/test_lifecycle.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

_T="$(mktemp -d)"
_PIDS=""
cleanup() {
  local p
  for p in $_PIDS; do kill "$p" 2>/dev/null || true; done
  rm -rf "$_T"
}
trap cleanup EXIT

# A sandbox copy of the repo's scripts: the scripts under test resolve
# everything relative to their own location, so a copy runs against a .run/
# and an openbeast.conf that belong to this test and nothing else.
_sandbox() { # _sandbox <dir>
  local d="$1"
  mkdir -p "$d/scripts/lib" "$d/.run" "$d/bin" "$d/home" "$d/extensions"
  cp "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$d/"
  cp "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/doctor.sh" \
     "$REPO_DIR/scripts/ext.sh" "$d/scripts/"
  cp "$REPO_DIR"/scripts/lib/*.sh "$d/scripts/lib/"
  : > "$d/openbeast.conf"
  # Every host tool that could reach the real machine answers "not here".
  local c
  for c in curl docker tailscale nvidia-smi sudo systemctl systemd-run smartctl; do
    printf '#!/bin/bash\nexit 1\n' > "$d/bin/$c"; chmod +x "$d/bin/$c"
  done
}
# Run a sandbox script with a clean environment and the stubs first on PATH.
_run() { # _run <dir> <script> [args...]   (extra env via RUN_ENV array)
  local d="$1"; shift
  env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    bash "$@" 2>&1 || true
}
RUN_ENV=()

# ---------------------------------------------------------------------------
echo "=== Lifecycle tests ==="
echo ""
echo "lib/net.sh — one BIND_HOST -> probe-host mapping:"
_ph() { bash -c "source '$REPO_DIR/scripts/lib/net.sh'; ob_probe_host \"\$1\"" _ "$1"; }
while IFS='|' read -r _in _want; do
  _got="$(_ph "$_in")"
  if [[ "$_got" == "$_want" ]]; then
    pass "ob_probe_host '$_in' -> '$_want'"
  else
    fail "ob_probe_host '$_in' gave '$_got', want '$_want'"
  fi
done <<'CASES'
127.0.0.1|127.0.0.1
0.0.0.0|127.0.0.1
|127.0.0.1
localhost|127.0.0.1
::|[::1]
[::]|[::1]
::1|[::1]
fd7a:115c:a1e0::5|[fd7a:115c:a1e0::5]
[fd7a:115c:a1e0::5]|[fd7a:115c:a1e0::5]
192.168.1.50|192.168.1.50
127.0.0.2|127.0.0.2
CASES
_NA="$(bash -c "set -- keep these; source '$REPO_DIR/scripts/lib/net.sh'; echo \"\$*\"")"
[[ "$_NA" == "keep these" ]] && pass "sourcing lib/net.sh leaves \$@ alone" \
  || fail "sourcing lib/net.sh clobbered \$@: '$_NA'"

# No script keeps a private copy of the mapping (that is how they drifted).
for _f in start.sh scripts/doctor.sh scripts/healthcheck.sh; do
  if grep -qE 'case "\$(BIND_HOST|CHAT_HEALTH_HOST|_chat_host)"' "$REPO_DIR/$_f"; then
    fail "$_f still carries its own BIND_HOST case instead of ob_probe_host"
  elif grep -q 'ob_probe_host' "$REPO_DIR/$_f"; then
    pass "$_f derives its probe host from lib/net.sh"
  else
    fail "$_f does not use ob_probe_host"
  fi
done

# doctor.sh, run for real: which URLs does it ask for?
echo ""
echo "doctor.sh probes (stubbed curl records every URL):"
_D="$_T/doctor"; _sandbox "$_D"
printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == http* ]] && echo "$a" >> "$CURL_LOG"; done\nexit 1\n' > "$_D/bin/curl"
_doctor_urls() { # _doctor_urls <conf-lines...> -> prints the URL log
  printf '%s\n' "$@" > "$_D/openbeast.conf"
  : > "$_D/curl.log"
  RUN_ENV=(CURL_LOG="$_D/curl.log")
  _run "$_D" "$_D/scripts/doctor.sh" >/dev/null
  RUN_ENV=()
  cat "$_D/curl.log"
}
_U="$(_doctor_urls 'BIND_HOST=::')"
if grep -q '^http://\[::1\]:8080/health' <<< "$_U" && ! grep -q ':::' <<< "$_U"; then
  pass "BIND_HOST=:: -> doctor probes http://[::1]:8080 (was http://:::8080, curl rc=3)"
else
  fail "doctor with BIND_HOST=:: built: $(tr '\n' ' ' <<< "$_U")"
fi
_U="$(_doctor_urls 'BIND_HOST=192.0.2.9' 'BEAST_CHAT=true')"
if grep -q '^http://127.0.0.1:3003/api/chat/health' <<< "$_U" \
   && ! grep -q '^http://192.0.2.9:3003' <<< "$_U"; then
  pass "doctor probes beast-chat on OPENBEAST_CHAT_BIND (loopback), not a LAN BIND_HOST"
else
  fail "doctor probed beast-chat at: $(grep 3003 <<< "$_U" | tr '\n' ' ')"
fi
if grep -q '^http://192.0.2.9:8080/health' <<< "$_U"; then
  pass "…while the core services are still probed on BIND_HOST (control)"
else
  fail "doctor stopped probing llama on BIND_HOST: $(tr '\n' ' ' <<< "$_U")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Lifecycle: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
