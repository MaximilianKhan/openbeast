#!/usr/bin/env bash
# tests/test_opencode_sessions.sh — scripts/opencode-sessions.sh, hermetically.
#
# A stub `opencode` (python + sqlite3) implements exactly the four calls the
# script makes — `db path`, `db [--format tsv] SQL`, `session delete ID` with
# opencode 1.18's cascade (sub-agent sessions, messages, parts, todos) — over a
# fixture database. A stub `pgrep` decides whether opencode "is running". No
# real opencode, no real ~/.local/share, no network.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_DIR/scripts/opencode-sessions.sh"
PASS=0 FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/data/opencode" "$T/state/opencode" "$T/x/b_c/sub" "$T/x/bXc" "$T/x/b_cd"

cat > "$T/bin/opencode" <<'STUB'
#!/usr/bin/env python3
import os, sqlite3, sys
db = os.environ["OC_TEST_DB"]
a = sys.argv[1:]
with open(os.environ["OC_TEST_LOG"], "a") as f:
    f.write(" ".join(a) + "\n")
if a[:2] == ["db", "path"]:
    print(db); sys.exit(0)
if a and a[0] == "db":
    q = a[-1]
    c = sqlite3.connect(db, isolation_level=None)
    cur = c.execute(q)
    if cur.description:
        print("\t".join(d[0] for d in cur.description))
        for r in cur.fetchall():
            print("\t".join("" if v is None else str(v) for v in r))
    sys.exit(0)
if a[:2] == ["session", "delete"]:
    c = sqlite3.connect(db, isolation_level=None)
    sid = a[2]
    if not c.execute("select 1 from session where id=?", (sid,)).fetchone():
        print("not found", file=sys.stderr); sys.exit(1)
    todo = [sid]
    while todo:
        s = todo.pop()
        todo += [r[0] for r in c.execute("select id from session where parent_id=?", (s,))]
        c.execute("delete from part where message_id in (select id from message where session_id=?)", (s,))
        c.execute("delete from message where session_id=?", (s,))
        c.execute("delete from todo where session_id=?", (s,))
        c.execute("delete from session where id=?", (s,))
    print("Session", sid, "deleted"); sys.exit(0)
sys.exit(2)
STUB
cat > "$T/bin/pgrep" <<'STUB'
#!/bin/bash
[[ "${OC_TEST_RUNNING:-0}" == 1 && "$*" == "-x opencode" ]] && exit 0
exit 1
STUB
chmod +x "$T/bin/opencode" "$T/bin/pgrep"

DB="$T/data/opencode/opencode.db"
fixture() {   # a fresh database: 4 top-level sessions in 4 dirs, 2 sub-agents
  rm -f "$DB" "$DB".backup-*
  python3 - "$DB" "$T" <<'PY'
import sqlite3, sys
db, t = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
c.executescript("""
create table project(id text primary key);
create table session(id text primary key, parent_id text, directory text, time_created integer);
create table message(id text primary key, session_id text);
create table part(id text primary key, message_id text);
create table todo(session_id text, content text);
insert into project values ('p1');
""")
rows = [("ses_a1", None, f"{t}/x/b_c"), ("ses_a2", "ses_a1", f"{t}/x/b_c"),
        ("ses_b1", None, f"{t}/x/b_c/sub"), ("ses_c1", None, f"{t}/x/bXc"),
        ("ses_d1", None, f"{t}/x/b_cd"), ("ses_d2", "ses_d1", f"{t}/x/b_cd")]
for i, (s, p, d) in enumerate(rows):
    c.execute("insert into session values (?,?,?,?)", (s, p, d, i))
    c.execute("insert into message values (?,?)", ("m_" + s, s))
    c.execute("insert into part values (?,?)", ("p_" + s, "m_" + s))
    c.execute("insert into todo values (?,?)", (s, "t"))
c.commit()
PY
  : > "$T/oc.log"
}
count() { python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute(sys.argv[2]).fetchone()[0])" "$DB" "$1"; }
run() {   # run <args…> — output in $_O, exit code in $_RC
  _RC=0
  _O="$(env PATH="$T/bin:/usr/bin:/bin" OPENCODE_BIN="$T/bin/opencode" OC_TEST_DB="$DB" OC_TEST_LOG="$T/oc.log" \
        XDG_STATE_HOME="$T/state" OC_TEST_RUNNING="${RUNNING:-0}" bash "$SCRIPT" "$@" 2>&1)" || _RC=$?
}

echo "opencode-sessions.sh — Bash 3.2 (stock macOS):"
if ! grep -v '^[[:space:]]*#' "$SCRIPT" | grep -qE '(^|[^[:alnum:]_])(mapfile|readarray)([^[:alnum:]_]|$)|declare[[:space:]]+-A' \
   && head -1 "$SCRIPT" | grep -q '/usr/bin/env bash'; then
  pass "no bash-4-only constructs, env-bash shebang"
else
  fail "bash-4-only construct or non-env shebang (breaks stock macOS)"
fi

echo "summary + dry run change nothing:"
fixture
run
if [[ $_RC -eq 0 ]] && grep -q "4 top-level, 2 sub-agent" <<< "$_O"; then pass "summary counts top-level and sub-agent sessions"; else fail "summary: $_O"; fi
run clear
if [[ $_RC -eq 0 ]] && grep -q "DRY RUN" <<< "$_O" && [[ "$(count 'select count(*) from session')" == 6 ]] \
   && ! grep -q "session delete" "$T/oc.log" && ! ls "$DB".backup-* >/dev/null 2>&1; then
  pass "clear without --go deletes nothing and writes no backup"
else
  fail "dry run changed something: $_O"
fi

echo "refuses while opencode is running:"
fixture
RUNNING=1 run clear --go
if [[ $_RC -ne 0 ]] && grep -q "opencode is running" <<< "$_O" && [[ "$(count 'select count(*) from session')" == 6 ]] \
   && ! ls "$DB".backup-* >/dev/null 2>&1; then
  pass "a running opencode blocks --go before any backup or delete"
else
  fail "running opencode not refused (rc=$_RC): $_O"
fi

echo "--dir is an exact path prefix (underscore is not a wildcard):"
fixture
run clear --go --dir "$T/x/b_c" --no-backup
left="$(count "select group_concat(id) from (select id from session order by id)")"
if [[ $_RC -eq 0 && "$left" == "ses_c1,ses_d1,ses_d2" ]]; then
  pass "only b_c and b_c/sub went; bXc and b_cd kept (was a LIKE wildcard hazard)"
else
  fail "--dir deleted the wrong set, left=[$left] rc=$_RC: $_O"
fi

echo "full clear: backup first, cascade, nothing orphaned, projects kept:"
fixture
run clear --go
bk="$(ls "$DB".backup-* 2>/dev/null | head -1)"
if [[ $_RC -eq 0 && -n "$bk" ]] \
   && [[ "$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select count(*) from session').fetchone()[0])" "$bk")" == 6 ]] \
   && [[ "$(stat -c %a "$bk" 2>/dev/null || stat -f %Lp "$bk")" == 600 ]]; then
  pass "backup holds every session and is mode 600"
else
  fail "backup missing/incomplete/wrong mode ($bk) rc=$_RC: $_O"
fi
if [[ "$(count 'select count(*) from session')" == 0 && "$(count 'select count(*) from message')" == 0 \
      && "$(count 'select count(*) from part')" == 0 && "$(count 'select count(*) from todo')" == 0 \
      && "$(count 'select count(*) from project')" == 1 ]]; then
  pass "sessions, messages, parts, todos gone; the project row kept"
else
  fail "cascade incomplete or project touched: $(count 'select count(*) from session') sessions left"
fi
if grep -q "^db VACUUM$" "$T/oc.log" && grep -q "wal_checkpoint(TRUNCATE)" "$T/oc.log" \
   && [[ "$(grep -c '^session delete' "$T/oc.log")" == 4 ]] && ! grep -q '^session delete ses_[ad]2' "$T/oc.log"; then
  pass "deletes top-level sessions only (children cascade), then VACUUM + truncating checkpoint"
else
  fail "unexpected call sequence: $(tr '\n' ';' < "$T/oc.log")"
fi

echo "--history:"
fixture
mkdir -p "$T/data/opencode/snapshot/p" "$T/data/opencode/storage/session_diff" "$T/data/opencode/tool-output" "$T/data/opencode/log"
echo '{"input":"x"}' > "$T/state/opencode/prompt-history.jsonl"; echo keep > "$T/data/opencode/log/a.log"
run clear --go --history --dir "$T/x"
if [[ $_RC -ne 0 ]] && grep -q "cannot be combined with --dir" <<< "$_O" && [[ -f "$T/state/opencode/prompt-history.jsonl" ]]; then
  pass "--history with --dir is refused (it is device-wide)"
else
  fail "--history --dir not refused: $_O"
fi
run clear --go --history --no-backup
if [[ $_RC -eq 0 && ! -e "$T/state/opencode/prompt-history.jsonl" && ! -e "$T/data/opencode/snapshot" \
      && ! -e "$T/data/opencode/storage/session_diff" && -f "$T/data/opencode/log/a.log" ]]; then
  pass "clears prompt history, diffs, tool output, snapshots — keeps logs"
else
  fail "--history cleared the wrong things (rc=$_RC): $_O"
fi

echo "a malformed id is never passed to opencode:"
fixture
python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"insert into session values ('ses_x; rm -rf ~', null, '/q', 99)\"); c.commit()" "$DB"
run clear --go --no-backup
if [[ $_RC -ne 0 ]] && grep -q "skip: unexpected id" <<< "$_O" && ! grep -q "rm -rf" "$T/oc.log"; then
  pass "an id outside ^ses_[A-Za-z0-9]+\$ is skipped and the run exits nonzero"
else
  fail "malformed id handling (rc=$_RC): $_O / $(tr '\n' ';' < "$T/oc.log")"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
