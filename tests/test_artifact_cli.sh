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

# --- 9. identifier validation happens LOCALLY ---
# A typo'd id used to sail into curl, produce a malformed URL, fail with curl
# exit 3, and be reported as "beast-artifact is not answering — restart the
# stack". Wrong diagnosis, wrong repair, healthy stack bounced.
echo ""
echo "Identifier validation:"
_run 2 "show rejects an id with a space" -- show "not an id"
if echo "$OUT" | grep -q "is not an artifact id"; then
  pass "a bad id is a usage error that names the id"
else
  fail "a bad id was not caught locally: $OUT"
fi
if echo "$OUT" | grep -q "not answering"; then
  fail "a bad id is still reported as 'server not answering'"
else
  pass "a bad id is NOT reported as a dead server"
fi
_run 2 "show rejects a traversal id" -- show "../../etc/passwd"
_run 2 "show rejects a URL-ish id" -- show "abc?x=1"
_run 2 "show rejects a bare dot" -- show "."
_run 2 "versions rejects a bad id" -- versions "bad id"
_run 2 "rollback rejects a bad id" -- rollback "bad id" 2
_run 2 "visibility rejects a bad id" -- visibility "bad id" private
_run 2 "remove rejects a bad id" -- remove "bad id" --yes
# A well-formed id must still be allowed through to the network (and then
# fail as "no server", not as a validation error).
_run 4 "a well-formed id reaches the network" -- show "a1b2c3d4-5e6f.7g"

# --- 10. publish argument honesty ---
echo ""
echo "publish honesty:"
_run 2 "--title '' is refused, not silently dropped" -- publish "$PAGE" --title ""
if echo "$OUT" | grep -q "cannot be empty"; then
  pass "an empty --title says why it cannot be honored"
else
  fail "an empty --title was dropped silently: $OUT"
fi
: > "$TMPROOT/app.js"
_run 2 "--file with an absolute published path is refused" \
      -- publish "$PAGE" --file "/opt/app.js=$TMPROOT/app.js"
if echo "$OUT" | grep -q "must be relative"; then
  pass "an absolute published path is refused, not silently relocated"
else
  fail "an absolute published path was accepted: $OUT"
fi
_run 2 "publish rejects a bad --id locally" -- publish "$PAGE" --id "bad id"

# --- 11. transport failures are told apart ---
# A stubbed curl is the only deterministic way to produce "curl failed for a
# reason that is NOT a refused connection" — and the distinction is the whole
# point: one means "start the stack", the other means "do not".
echo ""
echo "Transport error classification:"
STUBDIR="$TMPROOT/stub"
mkdir -p "$STUBDIR"
cat > "$STUBDIR/curl" <<'STUB'
#!/bin/bash
# Test stub for curl: records its own argv and a process-table snapshot,
# then fakes whatever response the test asked for.
out=""; prev=""
for a in "$@"; do
  [[ "$prev" == "-o" ]] && out="$a"
  prev="$a"
done
printf '%s\n' "$*" > "${STUB_ARGV:-/dev/null}"
[[ -n "${STUB_PS:-}" ]] && { ps -ww -eo args= > "$STUB_PS" 2>/dev/null || true; }
[[ -n "$out" && -n "${STUB_RESPONSE:-}" ]] && printf '%s' "$STUB_RESPONSE" > "$out"
printf '%s' "${STUB_CODE:-}"
exit "${STUB_EXIT:-0}"
STUB
chmod +x "$STUBDIR/curl"
export STUB_ARGV="$TMPROOT/argv.txt"

# NB: the assignments go INSIDE the substitution — `A=1 OUT="$(cmd)"` is a
# list of assignments, and cmd would not see A at all.
rc=0
OUT="$(STUB_EXIT=7 PATH="$STUBDIR:$PATH" "$CLI" list 2>&1)" || rc=$?
if [[ $rc -eq 4 ]] && echo "$OUT" | grep -q "not answering"; then
  pass "a refused connection (curl 7) is 'the server is not answering' (exit 4)"
else
  fail "curl 7 was not classified as a dead server (exit $rc): $OUT"
fi
rc=0
OUT="$(STUB_EXIT=3 PATH="$STUBDIR:$PATH" "$CLI" list 2>&1)" || rc=$?
if [[ $rc -eq 5 ]]; then
  pass "any other curl failure is its own outcome (exit 5, not 4)"
else
  fail "curl 3 was not told apart from a refused connection (exit $rc): $OUT"
fi
if echo "$OUT" | grep -q "restarting it will not help"; then
  pass "it does not advise restarting a healthy stack"
else
  fail "a transport error still advises a restart: $OUT"
fi

# --- 12. the locality token never reaches the process table ---
# /proc/<pid>/cmdline is world-readable: a `-H "X-OpenBeast-Local: $TOKEN"`
# argument published the WRITE credential to every uid on the box, on every
# call — read-only ones included.
echo ""
echo "Locality token handling:"
mkdir -p "$SANDBOX/.run"
TESTTOKEN="deadbeefcafe0123456789abcdefTOKEN"
printf '%s' "$TESTTOKEN" > "$SANDBOX/.run/artifact-local.token"
chmod 600 "$SANDBOX/.run/artifact-local.token"
export STUB_PS="$TMPROOT/ps.txt"
: > "$STUB_PS"
STUB_EXIT=0 STUB_CODE=201 \
  STUB_RESPONSE='{"id":"abc","title":"Test page","url":"http://x/a/abc","version":1}' \
  PATH="$STUBDIR:$PATH" "$CLI" publish "$PAGE" --title "T" >/dev/null 2>&1 || true
if [[ -s "$STUB_ARGV" ]] && ! grep -q "$TESTTOKEN" "$STUB_ARGV"; then
  pass "the token is absent from curl's argv on a publish"
else
  fail "the token appears in curl's argv: $(cat "$STUB_ARGV" 2>/dev/null)"
fi
if [[ -s "$STUB_PS" ]] && ! grep -q "$TESTTOKEN" "$STUB_PS"; then
  pass "the token is absent from the process table during the call"
else
  fail "the token is visible in \`ps\` during a publish"
fi
if grep -q -- "--config" "$STUB_ARGV"; then
  pass "curl is handed the header through --config instead"
else
  fail "curl was not given a --config file: $(cat "$STUB_ARGV")"
fi
# A read DOES carry the token — it is our identity, not just a write
# credential: the server refuses anonymous callers on every route, so a GET
# that sent nothing would be indistinguishable from a stranger and `list`
# would report an empty gallery on a rig full of pages. What must still hold
# is that it travels through --config and never through argv.
: > "$STUB_ARGV"
STUB_EXIT=0 STUB_CODE=200 STUB_RESPONSE='{"artifacts":[]}' \
  PATH="$STUBDIR:$PATH" "$CLI" list >/dev/null 2>&1 || true
if grep -q -- "--config" "$STUB_ARGV" && ! grep -q "$TESTTOKEN" "$STUB_ARGV"; then
  pass "a read identifies itself through --config, never through argv"
else
  fail "read did not use --config, or leaked the token into argv: $(cat "$STUB_ARGV")"
fi
unset STUB_PS
rm -f "$SANDBOX/.run/artifact-local.token"

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
