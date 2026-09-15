#!/bin/bash
# beast-artifact CLI (scripts/artifact.sh) — behavior tests.
#
# Usage: ./tests/test_artifact_cli.sh
#
# No server is required and none is started: everything here is the CLI's
# OWN behavior — argument validation, the refusals, and the two properties
# that are load-bearing on a rig where the stack may be down:
#   1. a read-only subcommand must never mutate openbeast.conf (sourcing
#      lib/conf.sh would, via its SearXNG-secret bootstrap — the clients.sh
#      lesson), and
#   2. "server not running" must be a named, actionable error, not a stray
#      curl exit code or an empty parse.
# The wire format itself is covered by the sibling store/server tests.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLI_SRC="$REPO_DIR/scripts/artifact.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== artifact.sh (beast-artifact CLI) tests ==="
echo ""

# --- 1. The script itself ---
echo "Script:"
if [[ -x "$CLI_SRC" ]]; then
  pass "scripts/artifact.sh exists and is executable"
else
  fail "scripts/artifact.sh missing or not executable"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi
if bash -n "$CLI_SRC" 2>/dev/null; then
  pass "scripts/artifact.sh passes bash -n"
else
  fail "scripts/artifact.sh has a syntax error"
fi
# Bash 3.2 (stock macOS) has no mapfile/readarray/associative arrays — the
# CLI is copied to client Macs alongside clients.sh.
if ! grep -qE '(^|[^[:alnum:]_])(mapfile|readarray)([^[:alnum:]_]|$)|declare[[:space:]]+-A' \
     "$CLI_SRC"; then
  pass "no bash-4-only constructs (mapfile/readarray/declare -A)"
else
  fail "artifact.sh uses a bash-4-only construct (breaks stock macOS bash 3.2)"
fi
# The whole reason this CLI re-implements _ob_conf_value instead of sourcing.
if ! grep -qE '^[[:space:]]*(source|\.)[[:space:]].*lib/conf\.sh' "$CLI_SRC"; then
  pass "does not source lib/conf.sh (no secret-generating side effect)"
else
  fail "artifact.sh sources lib/conf.sh — a read-only command would mutate openbeast.conf"
fi

# --- 2. Isolated sandbox ---
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-artifact-test.XXXXXX")"
cleanup() { rm -rf "$TMPROOT"; }
trap cleanup EXIT

SANDBOX="$TMPROOT/repo"
mkdir -p "$SANDBOX/scripts/lib" "$TMPROOT/home"
cp "$CLI_SRC" "$SANDBOX/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"
export HOME="$TMPROOT/home"
CLI="$SANDBOX/scripts/artifact.sh"
CONF="$SANDBOX/openbeast.conf"
# A port nothing is listening on: every network-touching case must fail as
# "server not running", never hang or half-succeed.
export OPENBEAST_ARTIFACT_PORT=39199

PAGE="$TMPROOT/page.html"
cat > "$PAGE" <<'HTML'
<title>Test page</title>
<style>:root{--bg:#fff}body{background:var(--bg)}</style>
<p>hello</p>
HTML

# _run <expected-exit> <label> -- <args...>   (captures stdout+stderr in $OUT)
OUT=""
_run() {
  local want="$1" label="$2"; shift 3
  local rc=0
  OUT="$("$CLI" "$@" 2>&1)" || rc=$?
  if [[ $rc -eq $want ]]; then
    pass "$label (exit $rc)"
  else
    fail "$label: expected exit $want, got $rc — $(echo "$OUT" | head -1)"
  fi
}

# --- 3. help + dispatch ---
echo ""
echo "Help and dispatch:"
_run 0 "--help exits 0" -- --help
if echo "$OUT" | grep -q "artifact.sh publish" && echo "$OUT" | grep -q "artifact.sh list"; then
  pass "--help lists the subcommands"
else
  fail "--help output does not name the subcommands: $OUT"
fi
_run 0 "-h exits 0" -- -h
_run 2 "unknown subcommand exits non-zero" -- frobnicate
if echo "$OUT" | grep -q "Unknown command: frobnicate" && echo "$OUT" | grep -q "artifact.sh publish"; then
  pass "unknown subcommand prints the name and the usage"
else
  fail "unknown subcommand gave no usage: $OUT"
fi

# --- 4. publish argument validation (no server contacted) ---
echo ""
echo "publish validation:"
_run 2 "publish with no file" -- publish
_run 2 "publish with a missing file" -- publish "$TMPROOT/nope.html"
if echo "$OUT" | grep -q "no such file"; then
  pass "missing file names the problem"
else
  fail "missing file gave an unclear error: $OUT"
fi
: > "$TMPROOT/empty.html"
_run 2 "publish with an empty file" -- publish "$TMPROOT/empty.html"
_run 2 "publish rejects a flag where the file belongs" -- publish --title X
_run 2 "publish rejects an unknown option" -- publish "$PAGE" --frobnicate
_run 2 "publish rejects a bad --visibility" -- publish "$PAGE" --visibility public
_run 2 "publish rejects --title with no value" -- publish "$PAGE" --title
_run 2 "publish rejects --file without published=source" -- publish "$PAGE" --file ./only-a-path
_run 2 "publish rejects an unreadable --file source" -- publish "$PAGE" --file "a.css=$TMPROOT/nope.css"

# --- 5. the other subcommands ---
echo ""
echo "Subcommand validation:"
_run 2 "show with no id" -- show
_run 2 "versions with no id" -- versions
_run 2 "rollback with no version" -- rollback abc
_run 2 "rollback rejects a non-numeric version" -- rollback abc one
_run 2 "rollback rejects v0" -- rollback abc 0
_run 2 "visibility rejects an unknown value" -- visibility abc public
_run 2 "list rejects an unknown option" -- list --frobnicate
_run 2 "remove without --yes refuses" -- remove abc
if echo "$OUT" | grep -q -- "--yes"; then
  pass "remove refusal names the flag that confirms it"
else
  fail "remove refusal does not say how to confirm: $OUT"
fi

# --- 6. server-down path ---
echo ""
echo "Server not running:"
_run 4 "list against a dead port fails cleanly" -- list
if echo "$OUT" | grep -q "beast-artifact is not answering" \
   && echo "$OUT" | grep -q "BEAST_ARTIFACT"; then
  pass "the error names the service and the conf key that enables it"
else
  fail "server-down error is not actionable: $OUT"
fi
if echo "$OUT" | grep -q "start.sh"; then
  pass "the error names the command that starts it"
else
  fail "server-down error does not name a start command: $OUT"
fi
# Writes need the locality token; with none on disk that must be its own
# message, and it must never reach the network.
_run 4 "publish with no locality token fails cleanly" -- publish "$PAGE"
if echo "$OUT" | grep -q "locality token"; then
  pass "a missing locality token is reported as such"
else
  fail "missing token gave the wrong error: $OUT"
fi

# --- 7. read-only commands must not mutate the config ---
echo ""
echo "No side effects:"
if [[ ! -f "$CONF" ]]; then
  pass "a read-only run on a conf-less repo created no openbeast.conf"
else
  fail "artifact.sh created $CONF (the lib/conf.sh side effect leaked in)"
fi
printf '# fixture\nARTIFACT_PORT=39199\n' > "$CONF"
cp "$CONF" "$TMPROOT/conf.before"
"$CLI" list >/dev/null 2>&1 || true
"$CLI" show abc >/dev/null 2>&1 || true
"$CLI" --help >/dev/null 2>&1 || true
if diff -q "$CONF" "$TMPROOT/conf.before" >/dev/null; then
  pass "list/show/--help leave openbeast.conf byte-identical"
else
  fail "a read-only subcommand modified openbeast.conf"
fi

# --- 8. port resolution: env > conf > default ---
echo ""
echo "Port resolution:"
printf '# fixture\nARTIFACT_PORT=39198\n' > "$CONF"
out="$(env -u OPENBEAST_ARTIFACT_PORT "$CLI" list 2>&1 || true)"
if echo "$out" | grep -q "127.0.0.1:39198"; then
  pass "ARTIFACT_PORT is read from openbeast.conf"
else
  fail "conf ARTIFACT_PORT ignored: $out"
fi
out="$(OPENBEAST_ARTIFACT_PORT=39197 "$CLI" list 2>&1 || true)"
if echo "$out" | grep -q "127.0.0.1:39197"; then
  pass "\$OPENBEAST_ARTIFACT_PORT overrides the conf value"
else
  fail "env override ignored: $out"
fi
rm -f "$CONF"
out="$(env -u OPENBEAST_ARTIFACT_PORT "$CLI" list 2>&1 || true)"
if echo "$out" | grep -q "127.0.0.1:3004"; then
  pass "falls back to the documented default port 3004"
else
  fail "default port is not 3004: $out"
fi
printf '# fixture\nARTIFACT_PORT=not-a-port\n' > "$CONF"
out="$(env -u OPENBEAST_ARTIFACT_PORT "$CLI" list 2>&1 || true)"
if echo "$out" | grep -q "not a number"; then
  pass "a non-numeric ARTIFACT_PORT is rejected up front"
else
  fail "a garbage port was not caught: $out"
fi

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
