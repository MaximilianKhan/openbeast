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
# Review 2026-11-16: the SAME string goes to chat_server (bound from its cwd)
# and to tailscaled (`unix:<path>`, resolved from ITS cwd, ~ never). So the
# value is exported absolute, with a leading ~/ expanded.
_E="$(conf_env $'CHAT_LOGIN_FROM=unix\nCHAT_SOCKET=~/x/chat.sock')"
if grep -qx "OPENBEAST_CHAT_SOCKET=$T/x/chat.sock" <<< "$_E"; then
  pass "CHAT_SOCKET=~/x/chat.sock is exported with ~ expanded to \$HOME"
else
  fail "~ CHAT_SOCKET: $(grep '^OPENBEAST_CHAT_SOCKET' <<< "$_E")"
fi
_E="$(conf_env $'CHAT_LOGIN_FROM=unix\nCHAT_SOCKET=.run/c.sock')"
if grep -qx "OPENBEAST_CHAT_SOCKET=$T/repo/.run/c.sock" <<< "$_E"; then
  pass "a relative CHAT_SOCKET is anchored to the checkout"
else
  fail "relative CHAT_SOCKET: $(grep '^OPENBEAST_CHAT_SOCKET' <<< "$_E")"
fi
_LONG="/srv/$(printf 'd%.0s' $(seq 1 110))/chat.sock"
_E="$(conf_env $'CHAT_LOGIN_FROM=unix\nCHAT_SOCKET='"$_LONG")"
if grep -q "CHAT_SOCKET=.* cannot be bound" "$T/repo/stderr"; then
  pass "a socket path past the AF_UNIX limit warns at conf time"
else
  fail "long CHAT_SOCKET did not warn: $(cat "$T/repo/stderr")"
fi

# beast-artifact: the same switch (ARTIFACT_LOGIN_FROM / ARTIFACT_SOCKET).
_E="$(conf_env "")"
if ! grep -q '^OPENBEAST_ARTIFACT_LOGIN_FROM=' <<< "$_E" && ! grep -q '^OPENBEAST_ARTIFACT_SOCKET=' <<< "$_E"; then
  pass "artifact default: neither OPENBEAST_ARTIFACT_LOGIN_FROM nor _SOCKET exported"
else
  fail "artifact default exported a login variable"
fi
_E="$(conf_env "ARTIFACT_LOGIN_FROM=unix")"
if grep -qx 'OPENBEAST_ARTIFACT_LOGIN_FROM=unix' <<< "$_E" \
   && grep -qx "OPENBEAST_ARTIFACT_SOCKET=$T/repo/.run/artifact.sock" <<< "$_E"; then
  pass "ARTIFACT_LOGIN_FROM=unix: exports unix and the default .run/artifact.sock"
else
  fail "ARTIFACT_LOGIN_FROM=unix: $(grep '^OPENBEAST_ARTIFACT_' <<< "$_E" | tr '\n' ' ')"
fi
_E="$(conf_env $'ARTIFACT_LOGIN_FROM=UNIX\nARTIFACT_SOCKET=~/a.sock')"
if grep -qx "OPENBEAST_ARTIFACT_SOCKET=$T/a.sock" <<< "$_E"; then
  pass "ARTIFACT_SOCKET overrides the path (~ expanded), case-insensitive mode"
else
  fail "ARTIFACT_SOCKET override: $(grep '^OPENBEAST_ARTIFACT_' <<< "$_E" | tr '\n' ' ')"
fi
_E="$(conf_env "ARTIFACT_LOGIN_FROM=tcp")"
if ! grep -q '^OPENBEAST_ARTIFACT_LOGIN_FROM=' <<< "$_E" && grep -q "ARTIFACT_LOGIN_FROM='tcp' is not loopback or unix" "$T/repo/stderr"; then
  pass "a bad ARTIFACT_LOGIN_FROM warns and falls back to loopback"
else
  fail "bad ARTIFACT_LOGIN_FROM was not refused"
fi
if grep -q '^#ARTIFACT_LOGIN_FROM=' "$REPO_DIR/openbeast.conf.example" && grep -q '^#ARTIFACT_SOCKET=' "$REPO_DIR/openbeast.conf.example"; then
  pass "ARTIFACT_LOGIN_FROM/ARTIFACT_SOCKET are documented in openbeast.conf.example"
else
  fail "openbeast.conf.example does not document ARTIFACT_LOGIN_FROM/ARTIFACT_SOCKET"
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

awk '/^_publish_artifact_mount\(\) \{/,/^}/' "$SRC" > "$T/afns.sh"
if ! grep -q '_publish_artifact_mount "\$UP_HOST"' "$SRC" || [[ "$(grep -c '^}' "$T/afns.sh")" != 1 ]]; then
  fail "could not find _publish_artifact_mount (or its call) in setup-tailscale.sh"
else
  amount() { # amount <login-from> <socket>
    : > "$T/mounts"
    ARTIFACT_LOGIN_FROM="$1" ARTIFACT_SOCKET="$2" MOUNTS="$T/mounts" bash -c '
      set -euo pipefail
      ts_admin() { echo "$*" >> "$MOUNTS"; }
      . "$1"; _publish_artifact_mount 127.0.0.1' _ "$T/afns.sh" >/dev/null 2>&1
  }
  amount unix "$T/artifact.sock"
  if [[ "$(cat "$T/mounts")" == "serve --bg --https=8446 unix:$T/artifact.sock" ]]; then
    pass "artifact unix: :8446 proxies to unix:<socket>"
  else
    fail "artifact unix mount was: $(cat "$T/mounts")"
  fi
  amount loopback ""
  if [[ "$(cat "$T/mounts")" == "serve --bg --https=8446 http://127.0.0.1:3004" ]]; then
    pass "artifact loopback: :8446 proxies to the TCP port, as before"
  else
    fail "artifact loopback mount was: $(cat "$T/mounts")"
  fi
fi

echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
