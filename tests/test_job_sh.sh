#!/bin/bash
# Job-session wrapper (scripts/job.sh) — behavior tests.
#
# Usage: ./tests/test_job_sh.sh
#
# Everything runs against a THROWAWAY repo under $TMPDIR: the real
# .run/sessions ledger is never read or written, no service is contacted, and
# no GPU work happens. The CLI is exercised for real rather than grepped,
# because every load-bearing property here is a runtime one:
#
#   * the job outlives the shell that launched it
#   * it lands in its OWN process group, so `stop` reaches a campaign
#     script's grandchildren and nothing else
#   * the terminal state is the truth — exit 0 is not "failed", exit 7 is
#     not "done", and an operator stop is not a crash
#   * a read-only subcommand does not mutate the rig's config
#
# Real jobs on this rig are multi-hour campaigns; the fixtures here are
# sleeps measured in seconds, which is why the whole file runs in ~20s.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== job.sh (beast-chat job sessions) tests ==="
echo ""

# --- 1. The script itself ---
echo "Script:"
if [[ -x "$REPO_DIR/scripts/job.sh" ]]; then
  pass "scripts/job.sh exists and is executable"
else
  fail "scripts/job.sh missing or not executable"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi
if bash -n "$REPO_DIR/scripts/job.sh" 2>/dev/null; then
  pass "scripts/job.sh passes bash -n"
else
  fail "scripts/job.sh has a syntax error"
fi
# Both checks below read CODE, not prose: job.sh documents the rules it holds
# to ("no mapfile/readarray", "deliberately does NOT source lib/conf.sh"), and
# a grep over the raw file would match those very sentences and fail the
# script for explaining itself. Strip whole-line comments first.
_code() { grep -v '^[[:space:]]*#' "$REPO_DIR/scripts/job.sh"; }
# Bash 3.2 (stock macOS) has no mapfile/readarray/associative arrays — job.sh
# sits beside clients.sh in the client-facing CLI and holds the same floor.
if ! _code | grep -qE '(^|[^[:alnum:]_])(mapfile|readarray)([^[:alnum:]_]|$)|declare[[:space:]]+-A'; then
  pass "no bash-4-only constructs (mapfile/readarray/declare -A)"
else
  fail "job.sh uses a bash-4-only construct (breaks stock macOS bash 3.2)"
fi
# Sourcing lib/conf.sh has a SIDE EFFECT (it generates and appends a SearXNG
# secret to openbeast.conf). `job.sh list` must never do that.
if ! _code | grep -qE '(^|[^[:alnum:]_])(source|\.)[[:space:]]+[^;]*lib/conf\.sh'; then
  pass "job.sh does not source lib/conf.sh (no secret-generating side effect)"
else
  fail "job.sh sources lib/conf.sh — a read-only subcommand would mutate openbeast.conf"
fi

if [[ ! -f "$REPO_DIR/agents/sessions.py" ]]; then
  echo ""
  echo "  SKIP: agents/sessions.py is not present — the runtime tests below"
  echo "        need the session ledger. Structural checks above still ran."
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
  exit $?
fi

# --- 2. Isolated sandbox ---
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-job-test.XXXXXX")"
cleanup() {
  # Never leave a fixture job running, whatever happened above.
  if [[ -d "$TMPROOT/repo/.run/sessions" ]]; then
    for _f in "$TMPROOT"/repo/.run/sessions/*.json; do
      [[ -e "$_f" ]] || continue
      _pg="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("pgid") or 0)
except Exception:
    print(0)' "$_f" 2>/dev/null || echo 0)"
      [[ "$_pg" =~ ^[0-9]+$ ]] && [[ "$_pg" -gt 1 ]] && kill -KILL -- "-$_pg" 2>/dev/null
    done
  fi
  rm -rf "$TMPROOT"
}
trap cleanup EXIT

SANDBOX="$TMPROOT/repo"
mkdir -p "$SANDBOX/scripts" "$SANDBOX/agents"
cp "$REPO_DIR/scripts/job.sh" "$SANDBOX/scripts/"
cp "$REPO_DIR/agents/sessions.py" "$SANDBOX/agents/"
CLI="$SANDBOX/scripts/job.sh"
LEDGER="$SANDBOX/.run/sessions"

_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
# Query one record with python (the test never hand-parses JSON either).
_q() { # _q <id> <python-expr over `rec`>
  python3 -c '
import json, sys
rec = json.load(open(sys.argv[1]))
print(eval(sys.argv[2]))
' "$LEDGER/$1.json" "$2"
}
# The id of the most recently started job.
_latest() {
  OPENBEAST_SESSIONS_DIR="$LEDGER" python3 -c '
import json, os, sys
d = sys.argv[1]
rows = []
for name in os.listdir(d):
    if name.endswith(".json"):
        try:
            rows.append(json.load(open(os.path.join(d, name))))
        except Exception:
            pass
rows.sort(key=lambda r: r.get("started_at") or "")
print(rows[-1]["id"] if rows else "")
' "$LEDGER"
}
# Poll the ledger until a session leaves "running" (or the budget runs out).
_await_terminal() { # _await_terminal <id> <seconds>
  local i=0
  while [[ $i -lt $2 ]]; do
    [[ "$(_q "$1" 'rec["state"]')" != "running" ]] && return 0
    sleep 1
    i=$((i + 1))
  done
  return 1
}

# --- 3. Empty state ---
echo ""
echo "Empty state:"
if out="$("$CLI" list 2>&1)" && echo "$out" | grep -q "No jobs registered"; then
  pass "list with no ledger prints the friendly 'register one' hint"
else
  fail "list with no ledger unfriendly: $out"
fi
if [[ ! -f "$SANDBOX/openbeast.conf" ]]; then
  pass "a read-only subcommand did NOT generate openbeast.conf"
else
  fail "job.sh list created openbeast.conf (it sourced lib/conf.sh)"
fi
if ! "$CLI" show ghost >/dev/null 2>&1; then
  pass "show of an unknown id exits non-zero"
else
  fail "show of an unknown id exited 0"
fi
if ! "$CLI" run --title "no command" >/dev/null 2>&1; then
  pass "run without a '-- <command>' is refused"
else
  fail "run accepted an empty command"
fi

# --- 4. A job that succeeds ---
echo ""
echo "run (exit 0):"
RUN_OUT="$("$CLI" run --title "demo job" -- bash -c 'echo hi; sleep 2; echo bye' 2>&1)"
OK_ID="$(_latest)"
if [[ -n "$OK_ID" && -f "$LEDGER/$OK_ID.json" ]]; then
  pass "run registered a ledger record at .run/sessions/<id>.json"
else
  fail "run left no ledger record: $RUN_OUT"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi
if echo "$RUN_OUT" | grep -q "$OK_ID" && echo "$RUN_OUT" | grep -q "Stop:"; then
  pass "run prints the id and how to watch/stop it"
else
  fail "run output missing the id or the follow-up commands"
fi
if [[ "$(_q "$OK_ID" 'rec["kind"]')" == "job" ]]; then
  pass "record kind is 'job'"
else
  fail "record kind is $(_q "$OK_ID" 'rec["kind"]'), want 'job'"
fi
if [[ "$(_q "$OK_ID" 'rec["title"]')" == "demo job" ]]; then
  pass "--title lands in the record"
else
  fail "title is $(_q "$OK_ID" 'repr(rec["title"])')"
fi
# THE property `stop` depends on: both pid AND pgid, and the pgid is the
# job's own group (== the supervisor pid), not the caller's.
if [[ "$(_q "$OK_ID" 'type(rec["pid"]).__name__')" == "int" \
   && "$(_q "$OK_ID" 'type(rec["pgid"]).__name__')" == "int" \
   && "$(_q "$OK_ID" 'rec["pgid"] == rec["pid"]')" == "True" ]]; then
  pass "pid and pgid are recorded as ints, and the job leads its own group"
else
  fail "pid/pgid wrong: pid=$(_q "$OK_ID" 'repr(rec["pid"])') pgid=$(_q "$OK_ID" 'repr(rec["pgid"])')"
fi
if [[ "$(_q "$OK_ID" 'rec["pgid"]')" != "$(ps -o pgid= -p $$ | tr -d " ")" ]]; then
  pass "the job's process group is NOT this shell's (stop can't hit the caller)"
else
  fail "the job shares the test shell's process group — stop would kill the caller"
fi
LOG="$(_q "$OK_ID" 'rec["transcript"]')"
if [[ -f "$LOG" && "$(_mode "$LOG")" == "600" ]]; then
  pass "combined-output log exists at mode 600"
else
  fail "log missing or mode is $(_mode "$LOG" 2>/dev/null), want 600"
fi
if _await_terminal "$OK_ID" 20; then
  pass "the job reached a terminal state"
else
  fail "the job never left 'running'"
fi
if [[ "$(_q "$OK_ID" 'rec["state"]')" == "done" ]]; then
  pass "exit 0 => state 'done'"
else
  fail "exit 0 gave state $(_q "$OK_ID" 'rec["state"]'), want 'done'"
fi
if grep -q "^hi$" "$LOG" && grep -q "^bye$" "$LOG"; then
  pass "stdout was captured to the log"
else
  fail "the job's stdout is not in the log"
fi

# --- 5. A job that fails, and one that writes only to stderr ---
echo ""
echo "run (exit 7):"
"$CLI" run --title "failing job" -- bash -c 'echo boom >&2; exit 7' >/dev/null 2>&1
BAD_ID="$(_latest)"
_await_terminal "$BAD_ID" 15
if [[ "$(_q "$BAD_ID" 'rec["state"]')" == "failed" ]]; then
  pass "a non-zero exit => state 'failed'"
else
  fail "exit 7 gave state $(_q "$BAD_ID" 'rec["state"]'), want 'failed'"
fi
if [[ "$(_q "$BAD_ID" 'rec["summary"]')" == "exit 7" ]]; then
  pass "the exit code survives as the summary"
else
  fail "summary is $(_q "$BAD_ID" 'repr(rec.get("summary"))'), want 'exit 7'"
fi
if grep -q "boom" "$(_q "$BAD_ID" 'rec["transcript"]')"; then
  pass "stderr was merged into the same log as stdout"
else
  fail "stderr did not reach the log"
fi

# --- 6. Surviving the launching shell ---
echo ""
echo "detachment:"
bash -c "cd '$SANDBOX' && ./scripts/job.sh run --title 'orphan job' -- bash -c 'sleep 4; echo survived' >/dev/null 2>&1"
ORPHAN_ID="$(_latest)"
sleep 1
if [[ "$(_q "$ORPHAN_ID" 'rec["state"]')" == "running" ]]; then
  pass "the job is still running after the launching shell exited"
else
  fail "the job died with its launching shell (state $(_q "$ORPHAN_ID" 'rec["state"]'))"
fi
if _await_terminal "$ORPHAN_ID" 20 && [[ "$(_q "$ORPHAN_ID" 'rec["state"]')" == "done" ]] \
   && grep -q "survived" "$(_q "$ORPHAN_ID" 'rec["transcript"]')"; then
  pass "the orphaned job ran to completion and recorded its own terminal state"
else
  fail "the orphaned job did not finish cleanly"
fi

# --- 7. stop signals the whole process group ---
echo ""
echo "stop:"
# A campaign script is a shell with children. Killing only the supervisor
# would leave the real work running and the operator lied to, so the fixture
# has a grandchild and the assertion is that the GROUP is gone.
"$CLI" run --title "long job" -- bash -c 'sleep 300 & wait' >/dev/null 2>&1
LONG_ID="$(_latest)"
sleep 1
LONG_PG="$(_q "$LONG_ID" 'rec["pgid"]')"
GROUP_BEFORE="$(ps -eo pgid= -o pid= | awk -v g="$LONG_PG" '$1==g' | wc -l | tr -d ' ')"
if [[ "$GROUP_BEFORE" -ge 2 ]]; then
  pass "the job's group holds the supervisor and its children ($GROUP_BEFORE procs)"
else
  fail "expected a multi-process group, saw $GROUP_BEFORE"
fi
STOP_OUT="$("$CLI" stop "$LONG_ID" --timeout 15 2>&1)"
if [[ "$(_q "$LONG_ID" 'rec["state"]')" == "stopped" ]]; then
  pass "stop => state 'stopped' (an operator stop is not a crash, not a failure)"
else
  fail "stop gave state $(_q "$LONG_ID" 'rec["state"]'): $STOP_OUT"
fi
sleep 1
GROUP_AFTER="$(ps -eo pgid= -o pid= | awk -v g="$LONG_PG" '$1==g' | wc -l | tr -d ' ')"
if [[ "$GROUP_AFTER" -eq 0 ]]; then
  pass "the entire process group is gone — no orphaned grandchildren"
else
  fail "$GROUP_AFTER process(es) survived the stop"
fi
if "$CLI" stop "$LONG_ID" 2>&1 | grep -q "already 'stopped'"; then
  pass "stopping an already-stopped job is a no-op, not an error"
else
  fail "a second stop did not report the job as already stopped"
fi

# --- 8. list / show ---
echo ""
echo "list / show:"
LIST_OUT="$("$CLI" list)"
if echo "$LIST_OUT" | grep -q "$OK_ID" && echo "$LIST_OUT" | grep -q "demo job" \
   && echo "$LIST_OUT" | grep -q "done"; then
  pass "list shows the job, its title and its state"
else
  fail "list output missing a registered job"
fi
if "$CLI" list --state failed | grep -q "$BAD_ID" \
   && ! "$CLI" list --state failed | grep -q "$OK_ID"; then
  pass "--state filters the listing"
else
  fail "--state did not filter"
fi
if "$CLI" list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d["sessions"], list); assert all(s["kind"]=="job" for s in d["sessions"])' 2>/dev/null; then
  pass "list --json parses and contains only job-kind sessions"
else
  fail "list --json malformed or leaking agent sessions"
fi
SHOW_OUT="$("$CLI" show "$OK_ID")"
if echo "$SHOW_OUT" | grep -q "demo job" && echo "$SHOW_OUT" | grep -q "^  command:" \
   && echo "$SHOW_OUT" | grep -q "hi"; then
  pass "show prints the record, the command, and a tail of the log"
else
  fail "show output incomplete"
fi
if "$CLI" show "$OK_ID" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["state"]=="done"; assert d["meta"]["wrapper"]=="scripts/job.sh"' 2>/dev/null; then
  pass "show --json parses and stamps the wrapper in meta"
else
  fail "show --json malformed"
fi

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
