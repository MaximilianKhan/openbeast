#!/bin/bash
# beast-chat CHAT_LOGIN_FROM=unix: conf.sh exports it, :8445 follows it.
#
# Usage: ./tests/test_chat_unix_login.sh
#
#   - conf.sh: the default (no key) exports neither OPENBEAST_CHAT_LOGIN_FROM
#     nor OPENBEAST_CHAT_SOCKET; CHAT_LOGIN_FROM=unix exports both, the socket
#     defaulting to .run/chat.sock; CHAT_SOCKET overrides it; a bad value warns
#     and falls back to loopback
#   - setup-tailscale.sh _publish_chat_mount (lifted out, against a stub
#     ts_admin): unix mounts :8445 at unix:<socket>, loopback at the TCP port

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/scripts/setup-tailscale.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== beast-chat unix-socket login tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-chat-unix.XXXXXX")"
trap 'rm -rf "$T"' EXIT

# conf_env <conf-text> — the environment conf.sh exports for that conf, in a
# sandbox repo (only lib/ and the example are needed).
mkdir -p "$T/repo/scripts"
cp -R "$REPO_DIR/scripts/lib" "$T/repo/scripts/lib"
cp "$REPO_DIR/openbeast.conf.example" "$T/repo/"
conf_env() {
  printf '%s\n' "$1" > "$T/repo/openbeast.conf"
  env -i HOME="$T" PATH="$PATH" REPO_DIR="$T/repo" bash -c \
    'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>"$REPO_DIR/stderr"; env' 2>/dev/null
}

_E="$(conf_env "")"
if ! grep -q '^OPENBEAST_CHAT_LOGIN_FROM=' <<< "$_E" && ! grep -q '^OPENBEAST_CHAT_SOCKET=' <<< "$_E"; then
  pass "default: neither OPENBEAST_CHAT_LOGIN_FROM nor OPENBEAST_CHAT_SOCKET exported"
else
  fail "default exported a chat login variable"
fi
_E="$(conf_env "CHAT_LOGIN_FROM=unix")"
if grep -qx 'OPENBEAST_CHAT_LOGIN_FROM=unix' <<< "$_E" \
   && grep -qx "OPENBEAST_CHAT_SOCKET=$T/repo/.run/chat.sock" <<< "$_E"; then
  pass "CHAT_LOGIN_FROM=unix: exports unix and the default .run/chat.sock"
else
  fail "CHAT_LOGIN_FROM=unix: $(grep '^OPENBEAST_CHAT_' <<< "$_E" | tr '\n' ' ')"
fi
_E="$(conf_env $'CHAT_LOGIN_FROM=Unix\nCHAT_SOCKET=/srv/chat.sock')"
if grep -qx 'OPENBEAST_CHAT_SOCKET=/srv/chat.sock' <<< "$_E" && grep -qx 'OPENBEAST_CHAT_LOGIN_FROM=unix' <<< "$_E"; then
  pass "CHAT_SOCKET overrides the socket path; the value is case-insensitive"
else
  fail "CHAT_SOCKET override: $(grep '^OPENBEAST_CHAT_' <<< "$_E" | tr '\n' ' ')"
fi
_E="$(conf_env "CHAT_LOGIN_FROM=tcp")"
if ! grep -q '^OPENBEAST_CHAT_LOGIN_FROM=' <<< "$_E" && grep -q "CHAT_LOGIN_FROM='tcp' is not loopback or unix" "$T/repo/stderr"; then
  pass "a bad value warns and falls back to loopback (nothing exported)"
else
  fail "bad CHAT_LOGIN_FROM was not refused"
fi
if grep -q '^#CHAT_LOGIN_FROM=' "$REPO_DIR/openbeast.conf.example" && grep -q '^#CHAT_SOCKET=' "$REPO_DIR/openbeast.conf.example"; then
  pass "both keys are documented in openbeast.conf.example (so the lint knows them)"
else
  fail "openbeast.conf.example does not document CHAT_LOGIN_FROM/CHAT_SOCKET"
fi

awk '/^_publish_chat_mount\(\) \{/,/^}/' "$SRC" > "$T/fns.sh"
if ! grep -q '_publish_chat_mount "\$CHAT_UP_HOST"' "$SRC" || [[ "$(grep -c '^}' "$T/fns.sh")" != 1 ]]; then
  fail "could not find _publish_chat_mount (or its call) in setup-tailscale.sh"
  echo "Results: $PASS passed, $FAIL failed"; exit 1
fi
mount() { # mount <login-from> <socket>
  : > "$T/mounts"
  CHAT_LOGIN_FROM="$1" CHAT_SOCKET="$2" MOUNTS="$T/mounts" bash -c '
    set -euo pipefail
    ts_admin() { echo "$*" >> "$MOUNTS"; }
    . "$1"; _publish_chat_mount 127.0.0.1' _ "$T/fns.sh" >/dev/null 2>&1
}
mount unix "$T/chat.sock"
if [[ "$(cat "$T/mounts")" == "serve --bg --https=8445 unix:$T/chat.sock" ]]; then
  pass "unix: :8445 proxies to unix:<socket>"
else
  fail "unix mount was: $(cat "$T/mounts")"
fi
mount loopback ""
if [[ "$(cat "$T/mounts")" == "serve --bg --https=8445 http://127.0.0.1:3003" ]]; then
  pass "loopback: :8445 proxies to the TCP port, as before"
else
  fail "loopback mount was: $(cat "$T/mounts")"
fi

echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
