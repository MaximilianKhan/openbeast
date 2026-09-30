#!/bin/bash
# beast-chat / beast-artifact operations — behavior tests for the 2026-09-30
# ops round (integration-ops-5..-8 and the notification / verdict plumbing).
#
# Usage: bash tests/test_ops_chat_artifact.sh
#
#   1  lib/portown.sh       who holds a port: ss, and the /proc fallback
#   2  start.sh             _spawn_ready: "ready" only when OUR process holds
#                           the port (a foreign answer is not ready), a held
#                           port is not spawned over, positive control
#   3  healthcheck.sh       a relaunched chat/artifact server's output lands in
#                           .run/stack.log, and a failed relaunch shows it; a
#                           green health answered by a process this stack did
#                           not start is FOREIGN (named, never killed)
#   4  logrotate            job logs are rotated; orphaned old ones pruned
#                           under AGENT_LOG_RETENTION_DAYS; ledger swept
#                           without beast-chat
#   5  doctor.sh            :8446 parity with :8445, empty allowlist, bind
#                           caveats, notification rows, :8447, an OFFLINE
#                           rig's missing extension image
#   6  lib/conf.sh          CHAT_NOTIFY_* keys; the topic URL (a bearer
#                           secret) reaches the chat server's env ONLY
#   7  publish-verdict.sh   stable uuid5 id, sha+era label, .txt wrapped and
#                           escaped, never fails the caller; one real publish
#                           against a real artifact server when its deps exist
#   8  scoring.py --html    self-contained, escaped leaderboard page
#
# Throwaway sandboxes under $TMPDIR, stub binaries that RECORD their calls,
# and every server started here is killed by its recorded pid. No stack, no
# GPU, no docker, no real openbeast.conf. Every positive has a control.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-ops-ca-XXXXXX")"
PIDS=""
cleanup() {
  local p
  for p in $PIDS; do kill "$p" 2>/dev/null || true; done
  rm -rf "$T"
}
trap cleanup EXIT
trap 'exit 143' INT TERM

# A free TCP port on loopback (the kernel picks it; tiny reuse race accepted).
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }

# A health server: `health.py <port> [delay]` — sleeps <delay> (the import
# time that opens the bind-race window), binds, answers every GET with
# {"status":"ok"}. A failed bind exits 1 with "cannot bind", as the real
# servers do.
cat > "$T/health.py" <<'PY'
import http.server, json, sys, time
port = int(sys.argv[1]); time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"status": "ok"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
try:
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
except OSError as e:
    print(f"cannot bind 127.0.0.1:{port} ({e})", file=sys.stderr); sys.exit(1)
srv.serve_forever()
PY
wait_listen() { # wait_listen <port>
  local i
  for i in $(seq 1 50); do
    curl -fsS -m 1 "http://127.0.0.1:$1/" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  return 1
}

echo "=== chat/artifact ops ==="

# ---------------------------------------------------------------------------
echo ""
echo "1. lib/portown.sh:"
P1="$(free_port)"
python3 "$T/health.py" "$P1" & A_PID=$!; PIDS="$PIDS $A_PID"
wait_listen "$P1" || fail "the test health server never listened on $P1"
# shellcheck source=/dev/null
source "$REPO_DIR/scripts/lib/portown.sh"
if ob_port_listening "$P1" && ob_pid_owns_port "$A_PID" "$P1"; then
  pass "a listener is seen, and its own pid owns the port"
else
  fail "ob_port_listening/ob_pid_owns_port missed pid $A_PID on :$P1 ($(ob_port_pids "$P1" | tr '\n' ' '))"
fi
_rc=0; ob_pid_owns_port "$$" "$P1" || _rc=$?
if [[ $_rc -eq 1 ]]; then
  pass "…and a different pid does NOT own it (control)"
else
  fail "ob_pid_owns_port said this shell ($$) owns :$P1 (rc=$_rc)"
fi
P_FREE="$(free_port)"
if ! ob_port_listening "$P_FREE"; then
  pass "a free port is not listening (control)"
else
  fail "ob_port_listening reported a free port :$P_FREE as held"
fi
# The /proc fallback: a PATH with the needed tools but no ss / lsof.
mkdir -p "$T/noss"
for _t in awk grep sort cut readlink printf tr cat; do
  _p="$(command -v "$_t" 2>/dev/null)" && [[ "$_p" == /* ]] && ln -sf "$_p" "$T/noss/$_t"
done
if [[ -r /proc/net/tcp ]]; then
  _o="$(PATH="$T/noss" "$BASH" -c "source '$REPO_DIR/scripts/lib/portown.sh'; command -v ss >/dev/null && echo HAS_SS; ob_port_pids $P1; ob_pid_owns_port $A_PID $P1 && echo OWNS")"
  if ! has "$_o" "HAS_SS" && has "$_o" "$A_PID" && has "$_o" "OWNS"; then
    pass "without ss/lsof, /proc/net/tcp + fd inodes still name the owner"
  else
    fail "/proc fallback: $(tr '\n' ' ' <<< "$_o")"
  fi
else
  echo "  SKIP: no /proc/net/tcp on this OS"
fi

# ---------------------------------------------------------------------------
echo ""
echo "2. start.sh _spawn_ready (integration-ops-6):"
SR="$T/sr"; mkdir -p "$SR/.run"
# The function under test, lifted out of start.sh verbatim.
_fn="$(sed -n '/^_spawn_ready() {/,/^}/p' "$REPO_DIR/start.sh")"
if [[ -z "$_fn" ]]; then
  fail "start.sh has no _spawn_ready() — the readiness check still trusts any answer on the port"
else
  cat > "$SR/harness.sh" <<EOF
set -euo pipefail
RUN_DIR="$SR/.run"
source "$REPO_DIR/scripts/lib/portown.sh"
$_fn
EOF
  # (a) THE BUG: a foreign server holds the port; our spawn loses the bind
  # after 0.3 s. The preflight is bypassed (as in the race where the orphan
  # binds between the check and the spawn), so the spawn happens and the
  # foreign server answers health while ours is still alive.
  _O="$(bash -c "source '$SR/harness.sh'; ob_port_listening() { return 1; }
        rc=0; _spawn_ready beast-artifact artifact $P1 http://127.0.0.1:$P1/health \
          python3 '$T/health.py' $P1 0.3 || rc=\$?; echo RC=\$rc SPAWN=\${SPAWN_PID:-none}" 2>&1)"
  if has "$_O" "RC=1" && has "$_O" "SPAWN=none" && [[ ! -e "$SR/.run/artifact.pid" ]] \
     && has "$_O" "DIFFERENT process"; then
    pass "a foreign server answering health is NOT 'ready'; the dead pid is not left on record"
  else
    fail "foreign answer accepted: $(tr '\n' ' ' <<< "$_O") pidfile=$(cat "$SR/.run/artifact.pid" 2>/dev/null || echo none)"
  fi
  # (b) With the preflight in place, a held port is not spawned over at all.
  rm -f "$SR/.run/artifact.pid"
  _O="$(bash -c "source '$SR/harness.sh'
        rc=0; _spawn_ready beast-artifact artifact $P1 http://127.0.0.1:$P1/health \
          python3 '$T/health.py' $P1 || rc=\$?; echo RC=\$rc" 2>&1)"
  if has "$_O" "RC=2" && has "$_O" "already in use by pid $A_PID" && [[ ! -e "$SR/.run/artifact.pid" ]]; then
    pass "a port already held by an unrecorded process is named, and nothing is spawned or recorded"
  else
    fail "held port: $(tr '\n' ' ' <<< "$_O")"
  fi
  # (c) Control: a free port, a server that binds → ready, pid recorded, ours.
  P2="$(free_port)"
  # To a file, not $(…): the server it leaves running holds the pipe open.
  bash -c "source '$SR/harness.sh'
        rc=0; _spawn_ready beast-chat chat $P2 http://127.0.0.1:$P2/health \
          python3 '$T/health.py' $P2 0.2 || rc=\$?; echo RC=\$rc SPAWN=\$SPAWN_PID" >"$T/sr.out" 2>&1
  _O="$(cat "$T/sr.out")"
  _sp="$(grep -o 'SPAWN=[0-9]*' <<< "$_O" | cut -d= -f2)"
  [[ -n "$_sp" ]] && PIDS="$PIDS $_sp"
  if has "$_O" "RC=0" && [[ -n "$_sp" && "$(cat "$SR/.run/chat.pid" 2>/dev/null)" == "$_sp" ]] \
     && ob_pid_owns_port "$_sp" "$P2"; then
    pass "…a free port: ready, and the recorded pid is the one holding the port (control)"
  else
    fail "free-port spawn: $(tr '\n' ' ' <<< "$_O")"
  fi
  [[ -n "$_sp" ]] && kill "$_sp" 2>/dev/null
  # Both call sites use it (no second copy of the old loop left behind).
  if [[ "$(grep -c '_spawn_ready beast-' "$REPO_DIR/start.sh")" -ge 2 ]] \
     && ! grep -q 'kill -0 "$ARTIFACT_PID" 2>/dev/null || break' "$REPO_DIR/start.sh"; then
    pass "start.sh starts both beast-chat and beast-artifact through _spawn_ready"
  else
    fail "start.sh still has a private readiness loop for chat or artifact"
  fi
fi

# ---------------------------------------------------------------------------
# Sandbox rig for healthcheck / doctor / conf: copies + recording stubs.
SB="$T/rig"
mkdir -p "$SB/scripts/lib" "$SB/.run" "$SB/bin" "$SB/home" "$SB/agents"
cp "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/doctor.sh" "$SB/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$SB/scripts/lib/"
for c in docker nvidia-smi sudo systemd-run smartctl pkill pgrep systemctl; do
  printf '#!/bin/bash\nexit 1\n' > "$SB/bin/$c"; chmod +x "$SB/bin/$c"
done
# curl stub: a healthy core stack; the chat/artifact/ntfy/notify answers are
# steered by env (ART_UP, CHAT_UP, NTFY_UP, NOTIFY_CODE).
cat > "$SB/bin/curl" <<'STUB'
#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_DIR"] + "/curl.argv", "a") as f:
    f.write(" ".join(args) + "\n")
url, w, i = "", False, 0
while i < len(args):
    a = args[i]
    if a in ("--config", "-K"): i += 2; continue
    if a == "%{http_code}": w = True
    if a.startswith("http"): url = a
    i += 1
def out(o):
    sys.stdout.write(o if isinstance(o, str) else json.dumps(o, separators=(",", ":")))
    sys.exit(0)
if "/api/artifacts/health" in url:
    if os.environ.get("ART_UP", "1") != "1": sys.exit(7)
    out({"status": "ok", "artifacts": 2})
if "/api/chat/health" in url:
    if os.environ.get("CHAT_UP", "1") != "1": sys.exit(7)
    out({"status": "ok", "reads": "allowlist", "running": 0})
if url.endswith("/v1/health"):
    if ":3005/" in url and os.environ.get("NTFY_UP", "1") != "1": sys.exit(7)
    code = os.environ.get("NOTIFY_CODE", "200")
    if code == "000": sys.exit(7)
    out(code if w else {"healthy": True})
if w: out("200")
if url.endswith(":8080/health"): out({"status": "ok"})
if url.endswith("/slots"): out([])
if ":3001/health" in url: out({"status": "ok", "auth": "open", "identity": "headers"})
if url.endswith("/api/version"): out({"version": "stub"})
if url.endswith("/api/config"): out({"features": {"auth": True}})
if url.endswith(":8888") or "/search" in url: out("<html></html>")
sys.exit(7)
STUB
cat > "$SB/bin/tailscale" <<'STUB'
#!/bin/bash
[[ "$1 $2" == "serve status" ]] && { printf '%b' "${TS_SERVE:-}"; exit 0; }
[[ "$1" == "status" ]] && { echo '{}'; exit 0; }
exit 0
STUB
chmod +x "$SB/bin/curl" "$SB/bin/tailscale"
RUN_ENV=()
run_sb() {
  env -i HOME="$SB/home" PATH="$SB/bin:/usr/bin:/bin" STUB_DIR="$T" \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} bash "$@" 2>&1
}
: > "$T/curl.argv"

# ---------------------------------------------------------------------------
echo ""
echo "3. healthcheck.sh --restart keeps the relaunched server's output (integration-ops-8):"
# Stub servers that fail the way a real one does on a bad conf.
printf 'import sys\nprint("artifact-boom: bad ARTIFACT_PORT in openbeast.conf", file=sys.stderr)\nsys.exit(1)\n' \
  > "$SB/agents/artifact_server.py"
printf 'import sys\nprint("chat-boom: ModuleNotFoundError: fastapi", file=sys.stderr)\nsys.exit(1)\n' \
  > "$SB/agents/chat_server.py"
printf 'SEARXNG_SECRET=x\nBEAST_ARTIFACT=true\nBEAST_CHAT=true\n' > "$SB/openbeast.conf"
rm -f "$SB/.run/stack.log"
RUN_ENV=(ART_UP=0 CHAT_UP=0)
_O="$(run_sb "$SB/scripts/healthcheck.sh" --restart)"
if grep -q "artifact-boom: bad ARTIFACT_PORT" "$SB/.run/stack.log" 2>/dev/null \
   && grep -q "chat-boom: ModuleNotFoundError" "$SB/.run/stack.log" \
   && grep -q "healthcheck --restart: relaunching beast-artifact" "$SB/.run/stack.log"; then
  pass "both relaunched servers' stderr is appended to .run/stack.log, under a dated marker"
else
  fail "restart output lost: stack.log=[$(tr '\n' ' ' < "$SB/.run/stack.log" 2>/dev/null)] :: $(grep -E 'restart' <<< "$_O" | tr '\n' ' ')"
fi
if has "$_O" "restart FAILED: the relaunched beast-artifact exited" && has "$_O" "| artifact-boom" \
   && has "$_O" "| chat-boom"; then
  pass "…and the FAILED line says it exited and quotes the reason (the watchdog journal has it)"
else
  fail "failed-restart report: $(grep -E -A3 'restart' <<< "$_O" | tr '\n' ' ')"
fi
if [[ "$(stat -c %a "$SB/.run/stack.log" 2>/dev/null)" == 600 ]]; then
  pass "…a stack.log it creates is 0600"
else
  fail "stack.log mode $(stat -c %a "$SB/.run/stack.log" 2>/dev/null)"
fi
if [[ ! -e "$SB/.run/artifact.pid" && ! -e "$SB/.run/chat.pid" ]]; then
  pass "…and the dead replacements' pids are not left on record"
else
  fail "a dead replacement's pid stayed on record"
fi
RUN_ENV=(ART_UP=1 CHAT_UP=1)
: > "$SB/.run/stack.log"
_O="$(run_sb "$SB/scripts/healthcheck.sh" --restart)"
if ! has "$_O" "restarting beast-artifact" && [[ ! -s "$SB/.run/stack.log" ]]; then
  pass "healthy servers are not relaunched and write nothing (control)"
else
  fail "healthy control: $(grep -E 'restart' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=()

# The steady-state half of integration-ops-6: health answers, but NOT from the
# process this stack recorded (a sibling worktree's server, or an orphan that
# made start.sh refuse to spawn). The curl stub says "ok" for both; the ports
# are real listeners, so the owner check is real.
PFA="$(free_port)"; PFC="$(free_port)"
python3 "$T/health.py" "$PFA" & FOREIGN_PID=$!; PIDS="$PIDS $FOREIGN_PID"
python3 "$T/health.py" "$PFC" & OURS_PID=$!; PIDS="$PIDS $OURS_PID"
wait_listen "$PFA"; wait_listen "$PFC"
printf 'SEARXNG_SECRET=x\nBEAST_ARTIFACT=true\nBEAST_CHAT=true\nARTIFACT_PORT=%s\nCHAT_PORT=%s\n' \
  "$PFA" "$PFC" > "$SB/openbeast.conf"
echo "$OURS_PID" > "$SB/.run/chat.pid"
rm -f "$SB/.run/artifact.pid"
RUN_ENV=(ART_UP=1 CHAT_UP=1)
_rc=0; _O="$(run_sb "$SB/scripts/healthcheck.sh" --restart)" || _rc=$?
if has "$_O" "FOREIGN beast-artifact answers, but from pid $FOREIGN_PID" && has "$_O" "recorded: none" \
   && ! has "$_O" "FOREIGN beast-chat" && [[ $_rc -ne 0 ]] && has "$_O" "services unhealthy" \
   && kill -0 "$FOREIGN_PID" 2>/dev/null && ! has "$_O" "restarting beast-artifact"; then
  pass "a foreign server answering health is reported FOREIGN (holder named, exit non-zero) and NOT killed"
else
  fail "foreign steady state (rc=$_rc): $(grep -E 'FOREIGN|beast-|unhealthy|healthy' <<< "$_O" | tr '\n' ' ')"
fi
echo "$FOREIGN_PID" > "$SB/.run/artifact.pid"
_rc=0; _O="$(run_sb "$SB/scripts/healthcheck.sh")" || _rc=$?
if ! has "$_O" "FOREIGN" && has "$_O" "OK   beast-artifact" && has "$_O" "OK   beast-chat console"; then
  pass "…the recorded pid holding the port is plain OK (control)"
else
  fail "own-listener control: $(grep -E 'FOREIGN|beast-' <<< "$_O" | tr '\n' ' ')"
fi
kill "$FOREIGN_PID" "$OURS_PID" 2>/dev/null
rm -f "$SB/.run/artifact.pid" "$SB/.run/chat.pid"
printf 'SEARXNG_SECRET=x\nBEAST_ARTIFACT=true\nBEAST_CHAT=true\n' > "$SB/openbeast.conf"
RUN_ENV=()

# ---------------------------------------------------------------------------
echo ""
echo "4. logrotate — job logs (integration-ops-7):"
LR="$T/lr"
mkdir -p "$LR/scripts" "$LR/agents" "$LR/.run/sessions"
cp "$REPO_DIR/scripts/logrotate.sh" "$REPO_DIR/scripts/logrotate-openbeast.conf" "$LR/scripts/"
cp "$REPO_DIR/agents/sessions.py" "$LR/agents/"
S="$LR/.run/sessions"
old="$(date -d '40 days ago' '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || python3 -c 'import datetime;print((datetime.datetime.now()-datetime.timedelta(days=40)).isoformat(timespec="seconds"))')"
# orphan, old → pruned; orphan, fresh → kept; old but recorded → kept;
# rotated old orphan copies → pruned; a dotfile → ignored.
for f in orphan-old.log orphan-old.log.1 orphan-old.log.2.gz orphan-new.log kept.log .hidden.log; do
  echo "data $f" > "$S/$f"
done
printf '{"id":"kept","state":"running","started_at":"%s","updated_at":"%s"}\n' "$old" "$old" > "$S/kept.json"
touch -d '40 days ago' "$S/orphan-old.log" "$S/orphan-old.log.1" "$S/orphan-old.log.2.gz" "$S/kept.log" "$S/.hidden.log"
# A terminal record 40 days old whose log must SURVIVE the ledger sweep when
# no retention is configured (keep_logs).
printf '{"id":"done-old","state":"done","started_at":"%s","updated_at":"%s"}\n' "$old" "$old" > "$S/done-old.json"
echo "job output" > "$S/done-old.log"
_O="$(cd "$LR" && env -i HOME="$T" PATH="/usr/bin:/bin" OPENBEAST_LOGROTATE=/nonexistent bash scripts/logrotate.sh 2>&1)"
if [[ ! -e "$S/done-old.json" && -e "$S/done-old.log" && -e "$S/orphan-old.log" ]]; then
  pass "without beast-chat the ledger is swept (30d terminal record gone), and no log is deleted without retention"
else
  fail "ledger sweep / keep-forever default: $(ls -A "$S" | tr '\n' ' ') :: $_O"
fi
_O="$(cd "$LR" && env -i HOME="$T" PATH="/usr/bin:/bin" OPENBEAST_LOGROTATE=/nonexistent AGENT_LOG_RETENTION_DAYS=30 bash scripts/logrotate.sh 2>&1)"
if [[ ! -e "$S/orphan-old.log" && ! -e "$S/orphan-old.log.1" && ! -e "$S/orphan-old.log.2.gz" ]] \
   && has "$_O" "pruned 3 job log(s)"; then
  pass "AGENT_LOG_RETENTION_DAYS=30 prunes an old orphaned job log and its rotated copies"
else
  fail "orphan prune: $(ls -A "$S" | tr '\n' ' ') :: $_O"
fi
if [[ -e "$S/orphan-new.log" && -e "$S/kept.log" && -e "$S/.hidden.log" ]]; then
  pass "…a fresh orphan, an old log whose record exists, and a dotfile all survive (controls)"
else
  fail "retention removed a protected file: $(ls -A "$S" | tr '\n' ' ')"
fi
# Captured first: `… | grep -q` under pipefail measures the writer's SIGPIPE.
_conf="$(bash "$LR/scripts/logrotate.sh" --print-conf)"
if has "$_conf" "\"$LR/.run/sessions/*.log\""; then
  pass "the rotation policy covers .run/sessions/*.log"
else
  fail "no sessions/*.log entry in the rendered logrotate conf"
fi
# The built-in rotator (no logrotate binary) on an oversized job log. Sparse:
# the size is metadata, no 51 MB is written.
truncate -s 51M "$S/big.log"
printf '{"id":"big","state":"running","started_at":"%s","updated_at":"%s"}\n' "$old" "$old" > "$S/big.json"
_O="$(cd "$LR" && env -i HOME="$T" PATH="/usr/bin:/bin" OPENBEAST_LOGROTATE=/nonexistent bash scripts/logrotate.sh 2>&1)"
if [[ -e "$S/big.log.1" && "$(stat -c %s "$S/big.log")" == 0 ]] && has "$_O" "rotated $S/big.log"; then
  pass "an oversized job log is copytruncated by the built-in rotator"
else
  fail "big job log not rotated: $_O"
fi
rm -f "$S"/big.log*

# ---------------------------------------------------------------------------
echo ""
echo "5. doctor.sh (integration-ops-5, notifications):"
printf '%s' "art-tok" > "$SB/.run/artifact-local.token"
doctor() { # doctor <conf lines…> — output in $_O
  printf '%s\n' "SEARXNG_SECRET=x" "$@" > "$SB/openbeast.conf"
  _O="$(run_sb "$SB/scripts/doctor.sh")"
}
row() { grep -E "$1" <<< "$_O" | tr '\n' ' '; }

doctor BEAST_ARTIFACT=true
if has "$_O" "beast-artifact has no operator allowlist — private pages (the default) cannot be opened from a phone"; then
  pass "BEAST_ARTIFACT=true with both allowlists empty WARNs that private pages open for nobody"
else
  fail "empty allowlist: $(row 'artifact')"
fi
doctor BEAST_ARTIFACT=true ARTIFACT_OPERATORS=me@example.com
_a="$_O"; doctor BEAST_ARTIFACT=true CHAT_OPERATORS=me@example.com
if ! has "$_a" "no operator allowlist" && ! has "$_O" "no operator allowlist"; then
  pass "…ARTIFACT_OPERATORS, or the CHAT_OPERATORS fallback, silences it (controls)"
else
  fail "allowlist control: $(row 'allowlist')"
fi
doctor BEAST_ARTIFACT=true ARTIFACT_ADMINS=me@example.com
if ! has "$_O" "no operator allowlist"; then
  pass "…ARTIFACT_ADMINS alone (an admin may open the rig's private pages) silences it too"
else
  fail "admins control: $(row 'allowlist')"
fi
# The server now honours a login from a peer on the bind address itself (how
# tailscale serve reaches a LAN BIND_HOST), so the old "not honoured through
# :8446" WARN described a rule it no longer has — it must be gone.
doctor BEAST_ARTIFACT=true ARTIFACT_OPERATORS=me@example.com BIND_HOST=192.168.1.20
if ! has "$_O" "not honoured through :8446" && ! has "$_O" "beast-artifact binds 192.168.1.20"; then
  pass "a LAN BIND_HOST no longer WARNs that logins are not honoured through :8446"
else
  fail "stale bind caveat: $(row 'binds')"
fi
S8446='https://beast.example.ts.net:8446 (tailnet only)\n|-- / proxy http://127.0.0.1:3004\n'
RUN_ENV=(TS_SERVE="$S8446" ART_UP=0)
doctor BEAST_ARTIFACT=true ARTIFACT_OPERATORS=me@example.com
if has "$_O" ":8446 is published but beast-artifact is NOT responding"; then
  pass ":8446 published over a dead server FAILs (parity with :8445)"
else
  fail ":8446 dead: $(row '8446|artifact')"
fi
doctor ARTIFACT_OPERATORS=me@example.com
if has "$_O" ":8446 is published but beast-artifact is NOT responding"; then
  pass "…including when BEAST_ARTIFACT is off (the mount outlived the feature)"
else
  fail ":8446 with BEAST_ARTIFACT off: $(row '8446')"
fi
RUN_ENV=(TS_SERVE="$S8446" ART_UP=1)
doctor BEAST_ARTIFACT=true ARTIFACT_OPERATORS=me@example.com
if has "$_O" "beast-artifact published on :8446 (tailnet-only)" && ! has "$_O" "NOT responding"; then
  pass "…a live server behind :8446 passes (control)"
else
  fail ":8446 live: $(row '8446')"
fi
RUN_ENV=(TS_SERVE='https://beast.example.ts.net:8445 (tailnet only)\n|-- / proxy http://127.0.0.1:3003\n')
doctor BEAST_ARTIFACT=true ARTIFACT_OPERATORS=me@example.com
if has "$_O" "BEAST_ARTIFACT=true but :8446 is not published"; then
  pass "BEAST_ARTIFACT=true with other surfaces published but not :8446 WARNs"
else
  fail ":8446 unpublished: $(row '8446')"
fi
RUN_ENV=()

doctor
if ! has "$_O" "Notifications"; then
  pass "nothing configured: no Notifications section (control)"
else
  fail "a Notifications section appeared unconfigured"
fi
TOPIC="obtopic-$RANDOM$RANDOM"
TOK="$SB/home/ntfy.token"; printf 'tk_x' > "$TOK"; chmod 600 "$TOK"
doctor BEAST_CHAT=true "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC" "CHAT_NOTIFY_TOKEN_FILE=$TOK"
if has "$_O" "notifications configured → http://127.0.0.1:3005 (on: failed,lost,done)" \
   && has "$_O" "notification endpoint reachable" && has "$_O" "token file is private (mode 600)" \
   && ! has "$_O" "$TOPIC"; then
  pass "a configured, reachable endpoint passes — and the topic (the secret) is never printed"
else
  fail "notify rows: $(row 'notif|token')"
fi
chmod 644 "$TOK"
RUN_ENV=(NOTIFY_CODE=000)
doctor BEAST_CHAT=true "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC" "CHAT_NOTIFY_TOKEN_FILE=$TOK"
if has "$_O" "notification endpoint not reachable" && has "$_O" "readable by other users (mode 644)"; then
  pass "an unreachable endpoint and a group/world-readable token file WARN"
else
  fail "notify failure rows: $(row 'notif|token')"
fi
RUN_ENV=()
doctor "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC"
if has "$_O" "CHAT_NOTIFY_URL is set but BEAST_CHAT is off"; then
  pass "a notify URL without beast-chat WARNs (nothing would send it)"
else
  fail "notify without chat: $(row 'notif')"
fi
doctor BEAST_CHAT=true "CHAT_NOTIFY_URL=ntfy.example/$TOPIC"
if has "$_O" "CHAT_NOTIFY_URL is not an http(s) URL"; then
  pass "a malformed notify URL FAILs"
else
  fail "bad URL: $(row 'notif')"
fi
doctor BEAST_CHAT=true "EXTENSIONS=ntfy"
if has "$_O" "the ntfy extension is enabled but CHAT_NOTIFY_URL is empty"; then
  pass "the ntfy extension without a CHAT_NOTIFY_URL WARNs"
else
  fail "ntfy w/o URL: $(row 'ntfy')"
fi
doctor BEAST_CHAT=true "EXTENSIONS=ntfy" "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC" OFFLINE=true \
       NTFY_UPSTREAM_BASE_URL=https://ntfy.sh
if has "$_O" "OFFLINE=true but NTFY_UPSTREAM_BASE_URL is set"; then
  pass "OFFLINE=true with an iOS upstream relay set WARNs"
else
  fail "offline+upstream: $(row 'OFFLINE|UPSTREAM')"
fi
RUN_ENV=(TS_SERVE='https://beast.example.ts.net:8447 (tailnet only)\n|-- / proxy http://127.0.0.1:3005\n' NTFY_UP=0)
doctor BEAST_CHAT=true "EXTENSIONS=ntfy" "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC"
if has "$_O" ":8447 is published but ntfy is NOT responding"; then
  pass ":8447 published over a dead ntfy FAILs"
else
  fail ":8447 dead: $(row '8447')"
fi
RUN_ENV=(TS_SERVE='https://beast.example.ts.net:8447 (tailnet only)\n|-- / proxy http://127.0.0.1:3005\n' NTFY_UP=1)
doctor BEAST_CHAT=true "EXTENSIONS=ntfy" "CHAT_NOTIFY_URL=http://127.0.0.1:3005/$TOPIC"
if has "$_O" "ntfy published on :8447 (tailnet-only)"; then
  pass "…a live ntfy behind :8447 passes (control)"
else
  fail ":8447 live: $(row '8447')"
fi
RUN_ENV=()
# OFFLINE + a compose-kind extension: its image rides no bundle, and a missing
# one aborts the whole `compose up --pull never` (WebUI + SearXNG with it).
mkdir -p "$SB/extensions"; cp -R "$REPO_DIR/extensions/ntfy" "$SB/extensions/"
cp "$SB/bin/docker" "$T/docker.orig"
cat > "$SB/bin/docker" <<'STUB'
#!/bin/bash
[[ "$1 $2" == "image inspect" && -n "${DOCKER_HAVE:-}" && "$3" == "$DOCKER_HAVE" ]] && exit 0
exit 1
STUB
chmod +x "$SB/bin/docker"
NTFY_REF="$(grep -oE 'binwiederhier/ntfy:[^@[:space:]]+' "$REPO_DIR/extensions/ntfy/compose.yaml" | head -1)"
doctor "EXTENSIONS=ntfy" OFFLINE=true
if has "$_O" "the ntfy extension's image $NTFY_REF is not on this box" && has "$_O" "ext.sh disable ntfy"; then
  pass "OFFLINE with the ntfy extension on and its image absent FAILs (it would abort the frontend up)"
else
  fail "offline ext image absent: $(row 'offline')"
fi
RUN_ENV=(DOCKER_HAVE="$NTFY_REF")
doctor "EXTENSIONS=ntfy" OFFLINE=true
if has "$_O" "the ntfy extension's image is present ($NTFY_REF)" && ! has "$_O" "is not on this box"; then
  pass "…a docker-loaded image (repo:tag, digest dropped) passes (control)"
else
  fail "offline ext image present: $(row 'offline')"
fi
RUN_ENV=()
doctor "EXTENSIONS=ntfy"
if ! has "$_O" "extension's image"; then
  pass "…and a connected rig gets no image row (control)"
else
  fail "online ext image row: $(row 'image')"
fi
cp "$T/docker.orig" "$SB/bin/docker"

# ---------------------------------------------------------------------------
echo ""
echo "6. lib/conf.sh — notification keys; the topic URL stays out of every env but the chat server's:"
conf_env() { # conf_env <conf lines…> — prints the OPENBEAST_CHAT_NOTIFY_* / NTFY env
  printf '%s\n' "SEARXNG_SECRET=x" "$@" > "$SB/openbeast.conf"
  env -i HOME="$SB/home" PATH="/usr/bin:/bin" REPO_DIR="$SB" \
    bash -c 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; env' \
    | grep -E '^OPENBEAST_(CHAT_NOTIFY|NTFY|CHAT_PUBLIC)_' | sort
}
_E="$(conf_env "CHAT_NOTIFY_URL=http://127.0.0.1:3005/t" "CHAT_NOTIFY_ON=failed,done" \
               "CHAT_NOTIFY_TOKEN_FILE=~/ntfy.token" NTFY_PORT=3999)"
if has "$_E" "OPENBEAST_CHAT_NOTIFY_ON=failed,done" \
   && has "$_E" "OPENBEAST_CHAT_NOTIFY_TOKEN_FILE=$SB/home/ntfy.token" \
   && has "$_E" "OPENBEAST_NTFY_PORT=3999"; then
  pass "CHAT_NOTIFY_ON / _TOKEN_FILE (~ expanded) and NTFY_PORT are exported"
else
  fail "conf exports: $(tr '\n' ' ' <<< "$_E")"
fi
_E="$(conf_env "CHAT_PUBLIC_URL=https://beast.example.ts.net:8445")"
if has "$_E" "OPENBEAST_CHAT_PUBLIC_URL=https://beast.example.ts.net:8445"; then
  pass "CHAT_PUBLIC_URL in openbeast.conf reaches the chat server's env"
else
  fail "CHAT_PUBLIC_URL export: $(tr '\n' ' ' <<< "$_E")"
fi
_E="$(conf_env)"
if ! has "$_E" "OPENBEAST_CHAT_PUBLIC_URL="; then
  pass "…unset: no CHAT_PUBLIC_URL exported, so the console name is detected (control)"
else
  fail "CHAT_PUBLIC_URL exported while unset: $(tr '\n' ' ' <<< "$_E")"
fi
_E="$(conf_env)"
if has "$_E" "OPENBEAST_CHAT_NOTIFY_ON=failed,lost,done" \
   && ! has "$_E" "OPENBEAST_CHAT_NOTIFY_TOKEN_FILE=" && has "$_E" "OPENBEAST_NTFY_PORT=3005"; then
  pass "…unset: no token file exported (not an empty 'configured'), ON defaults to failed,lost,done"
else
  fail "conf defaults: $(tr '\n' ' ' <<< "$_E")"
fi

# The ntfy topic URL is a bearer secret with an innocent name (review
# 2026-09-30, major): exported, it rode `./start.sh -d`'s systemd-run --setenv
# onto argv + the unit env, and reached every model-authored bash command via
# tools._scrubbed_env. It must reach the chat server's process and NOTHING else.
TOPIC_URL="http://127.0.0.1:3005/openbeast-s3cr3t-topic"
# start.sh's -d setenv loop, lifted verbatim.
_setenv="$(sed -n '/^    SETENV_ARGS=()$/,/^    done < <(compgen -e/p' "$REPO_DIR/start.sh")"
[[ -n "$_setenv" ]] || fail "could not lift start.sh's SETENV loop"
printf '%s\n' \
  'import os, sys' \
  'open(sys.argv[0] + ".env", "w").write(os.environ.get("OPENBEAST_CHAT_NOTIFY_URL", "<unset>"))' \
  'p = "/proc/self/cmdline"' \
  'open(sys.argv[0] + ".argv", "w").write(open(p).read().replace("\0", " ") if os.path.exists(p) else " ".join(sys.argv))' \
  > "$T/notify_stub.py"
cat > "$T/notify_probe.sh" <<EOF
source "\$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1
$_setenv
echo "SETENV: \${SETENV_ARGS[*]}"
echo "SHELLVAR: \$CHAT_NOTIFY_URL"
echo "ENV:"; env
python3 -c 'import sys; sys.path.insert(0, sys.argv[1])
try:
    import tools
    print("SCRUBBED:", tools._scrubbed_env())
except Exception as e:
    print("SCRUBBED-UNAVAILABLE:", type(e).__name__)' "\$AGENTS"
ob_exec_chat_server "\$STUB" & wait \$!
EOF
for _src in conf envoverride; do
  if [[ $_src == envoverride ]]; then
    printf '%s\n' "SEARXNG_SECRET=x" > "$SB/openbeast.conf"
    _pre=(OPENBEAST_CHAT_NOTIFY_URL="$TOPIC_URL")
  else
    printf '%s\n' "SEARXNG_SECRET=x" "CHAT_NOTIFY_URL=$TOPIC_URL" > "$SB/openbeast.conf"
    _pre=()
  fi
  rm -f "$T/notify_stub.py.env" "$T/notify_stub.py.argv"
  _O="$(env -i HOME="$SB/home" PATH="/usr/bin:/bin" REPO_DIR="$SB" OPENBEAST_BIND=127.0.0.1 \
          ${_pre[@]+"${_pre[@]}"} AGENTS="$REPO_DIR/agents" STUB="$T/notify_stub.py" \
          bash "$T/notify_probe.sh" 2>&1)"
  _leaks="$(grep -v '^SHELLVAR:' <<< "$_O" | grep -c 's3cr3t' || true)"
  if [[ "$_leaks" == 0 ]] && has "$_O" "SHELLVAR: $TOPIC_URL" \
     && has "$_O" "--setenv=OPENBEAST_BIND=127.0.0.1"; then
    pass "[$_src] the notify URL is in no exported env, no -d --setenv, no scrubbed tool env (control: BIND forwarded)"
  else
    fail "[$_src] notify URL leaked ($_leaks): $(grep 's3cr3t\|SETENV' <<< "$_O" | head -c 600)"
  fi
  has "$_O" "SCRUBBED-UNAVAILABLE" && echo "  NOTE: tools.py not importable here — the plain env check covers that leg"
  if [[ "$(cat "$T/notify_stub.py.env" 2>/dev/null)" == "$TOPIC_URL" ]] \
     && ! grep -q 's3cr3t' "$T/notify_stub.py.argv" 2>/dev/null; then
    pass "[$_src] …and ob_exec_chat_server hands it to the chat server's env, not its argv"
  else
    fail "[$_src] chat server env=$(cat "$T/notify_stub.py.env" 2>/dev/null) argv=$(cat "$T/notify_stub.py.argv" 2>/dev/null)"
  fi
done
# Control: no URL configured → the chat server sees none (not an empty string).
printf '%s\n' "SEARXNG_SECRET=x" > "$SB/openbeast.conf"
rm -f "$T/notify_stub.py.env"
env -i HOME="$SB/home" PATH="/usr/bin:/bin" REPO_DIR="$SB" STUB="$T/notify_stub.py" \
  bash -c 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; ob_exec_chat_server "$STUB" & wait $!' >/dev/null 2>&1
if [[ "$(cat "$T/notify_stub.py.env" 2>/dev/null)" == "<unset>" ]]; then
  pass "…no URL configured: the chat server's env has none (control)"
else
  fail "unconfigured notify env: $(cat "$T/notify_stub.py.env" 2>/dev/null)"
fi
# Both launchers go through the helper — no bare `python3 …chat_server.py`.
if grep -q 'ob_exec_chat_server "\$SCRIPT_DIR/agents/chat_server.py"' "$REPO_DIR/start.sh" \
   && grep -q 'ob_exec_chat_server "\$REPO_DIR/agents/chat_server.py"' "$REPO_DIR/scripts/healthcheck.sh" \
   && ! grep -qE '^[^#]*python3 "\$(SCRIPT_DIR|REPO_DIR)/agents/chat_server.py"' "$REPO_DIR/start.sh" "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "start.sh and healthcheck.sh both launch beast-chat through ob_exec_chat_server"
else
  fail "a chat_server launcher bypasses ob_exec_chat_server"
fi

# ---------------------------------------------------------------------------
echo ""
echo "7. publish-verdict.sh:"
PV="$T/pv"; mkdir -p "$PV/scripts" "$PV/.run"
cp "$REPO_DIR/scripts/publish-verdict.sh" "$PV/scripts/"
# artifact.sh stub: records its argv and the page it was handed; exit code
# steered by ART_RC.
cat > "$PV/scripts/artifact.sh" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" > "$STUB_DIR/art.argv"
cp "$2" "$STUB_DIR/art.page" 2>/dev/null
[[ "${ART_RC:-0}" == 0 ]] && echo "Published: http://localhost:3004/a/$4 (v1)"
[[ "${ART_RC:-0}" == 4 ]] && echo "ERROR: beast-artifact is not answering" >&2
exit "${ART_RC:-0}"
STUB
printf '#!/bin/bash\necho era-abc123\n' > "$PV/scripts/eval-era.sh"
chmod +x "$PV/scripts/"*.sh
printf 'BEAST_ARTIFACT=true\n' > "$PV/openbeast.conf"
printf 'verdict: <b>SHIP</b> & p=0.019\n' > "$T/verdict.txt"
pv() { env -i HOME="$T" PATH="/usr/bin:/bin" STUB_DIR="$T" ${PV_ENV[@]+"${PV_ENV[@]}"} \
         bash "$PV/scripts/publish-verdict.sh" "$@" 2>&1; }
PV_ENV=()
WANT_ID="$(python3 -c 'import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, "openbeast:verdict:tier3-zig"))')"
rm -f "$T/art.argv"
_O="$(pv tier3-zig "$T/verdict.txt")"; _rc=$?
_argv="$(tr '\n' '|' < "$T/art.argv" 2>/dev/null)"
if [[ $_rc -eq 0 ]] && has "$_argv" "--id|$WANT_ID|" && has "$_argv" "--visibility|private|" \
   && has "$_argv" "--label|nogit era=era-abc123|" && has "$_O" "Published:"; then
  pass "uuid5 id, private, label '<sha> era=<era>', artifact.sh's URL passed through"
else
  fail "publish argv: [$_argv] rc=$_rc :: $_O"
fi
if grep -qF '&lt;b&gt;SHIP&lt;/b&gt; &amp; p=0.019' "$T/art.page" 2>/dev/null \
   && ! grep -qF '<b>SHIP' "$T/art.page" && grep -q '<title>Verdict: tier3-zig</title>' "$T/art.page"; then
  pass "a .txt verdict is wrapped in a page with its text HTML-escaped inside <pre>"
else
  fail "txt wrap: $(head -c 400 "$T/art.page" 2>/dev/null)"
fi
_O2="$(pv tier3-zig "$T/verdict.txt" --label "rerun 2")"
_argv2="$(tr '\n' '|' < "$T/art.argv")"
if has "$_argv2" "--id|$WANT_ID|" && has "$_argv2" "--label|rerun 2|"; then
  pass "…the same slug republishes to the SAME id (a new version, not a new URL); --label wins"
else
  fail "republish: [$_argv2]"
fi
printf '<!doctype html><title>Board</title><p>x</p>' > "$T/board.html"
pv leaderboard "$T/board.html" >/dev/null
if cmp -s "$T/board.html" "$T/art.page"; then
  pass "…an .html file is published as-is (control)"
else
  fail "html was rewritten"
fi
rm -f "$T/art.argv"
PV_ENV=(OPENBEAST_BEAST_ARTIFACT=false)
_O="$(pv tier3-zig "$T/verdict.txt")"; _rc=$?
if [[ $_rc -eq 0 && ! -e "$T/art.argv" && "$(wc -l <<< "$_O")" == 1 ]] && has "$_O" "beast-artifact is off"; then
  pass "BEAST_ARTIFACT off: exit 0, one stderr line, artifact.sh never called"
else
  fail "off: rc=$_rc argv=$(test -e "$T/art.argv" && echo called) :: $_O"
fi
PV_ENV=(ART_RC=4)
_O="$(pv tier3-zig "$T/verdict.txt")"; _rc=$?
if [[ $_rc -eq 0 && "$(wc -l <<< "$_O")" == 1 ]] && has "$_O" "not answering"; then
  pass "artifact server down: exit 0 and one line — a campaign under set -e keeps going"
else
  fail "server down: rc=$_rc :: $_O"
fi
PV_ENV=()
_O="$(pv 'bad slug!' "$T/verdict.txt")"; _rc=$?
_O2="$(pv tier3-zig "$T/missing.txt")"; _rc2=$?
if [[ $_rc -eq 0 && $_rc2 -eq 0 ]] && has "$_O" "not published" && has "$_O2" "not published"; then
  pass "a bad slug or a missing file is one line and exit 0 too"
else
  fail "usage errors: $_rc/$_rc2 :: $_O :: $_O2"
fi
# A separate process whose status is NOT tested by || or && — bash turns
# errexit off inside such a context, so the old `(set -e; …) || rc=$?` form
# passed even with no script at all.
sete_caller() { # sete_caller <script> <marker> — a campaign-shaped set -e caller
  rm -f "$2"
  env -i HOME="$T" PATH="/usr/bin:/bin" STUB_DIR="$T" ART_RC=4 \
    bash -c 'set -e; bash "$0" tier3-zig "$1" >/dev/null 2>&1; echo STILL-HERE > "$2"' \
    "$1" "$T/verdict.txt" "$2"
  return 0
}
sete_caller "$PV/scripts/publish-verdict.sh" "$T/sete"
printf '#!/bin/bash\nexit 1\n' > "$T/fails.sh"
sete_caller "$T/fails.sh" "$T/sete-control"
if [[ -e "$T/sete" && ! -e "$T/sete-control" ]]; then
  pass "…verified under the caller's set -e (control: a script that exits 1 does stop it)"
else
  fail "set -e caller: publish-verdict=$(test -e "$T/sete" && echo survived || echo KILLED) control=$(test -e "$T/sete-control" && echo 'survived (test is blind)' || echo stopped)"
fi

# One real round trip: the real artifact.sh against a real artifact server.
# The real interpreter, not a version-manager shim (a shim re-resolves under
# the sandbox HOME and may pick a python without the server's deps).
PY3="$(python3 -c 'import sys; print(sys.executable)')"
# …and its user site-packages, which a sandbox HOME would otherwise hide.
PYSITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
if "$PY3" -c 'import fastapi, uvicorn' 2>/dev/null; then
  RR="$T/real"; mkdir -p "$RR/scripts" "$RR/agents" "$RR/.run" "$RR/files"
  cp "$REPO_DIR/scripts/publish-verdict.sh" "$REPO_DIR/scripts/artifact.sh" "$RR/scripts/"
  printf '#!/bin/bash\necho era-real\n' > "$RR/scripts/eval-era.sh"; chmod +x "$RR/scripts/"*.sh
  PR="$(free_port)"
  printf 'BEAST_ARTIFACT=true\nARTIFACT_PORT=%s\n' "$PR" > "$RR/openbeast.conf"
  # BASE_URL pinned so the server never asks the real `tailscale serve`.
  (cd "$RR" && exec env -i HOME="$T" PATH="/usr/bin:/bin" PYTHONPATH="$PYSITE" OPENBEAST_REPO_DIR="$RR" \
      OPENBEAST_RUN_DIR="$RR/.run" OPENBEAST_ARTIFACT_PORT="$PR" OPENBEAST_BIND=127.0.0.1 \
      OPENBEAST_FILES_DIR="$RR/files" OPENBEAST_ARTIFACT_BASE_URL="http://localhost:$PR" \
      "$PY3" "$REPO_DIR/agents/artifact_server.py" >"$T/artsrv.log" 2>&1) &
  RS_PID=$!; PIDS="$PIDS $RS_PID"
  for _i in $(seq 1 60); do
    curl -fsS -m 1 "http://127.0.0.1:$PR/api/artifacts/health" >/dev/null 2>&1 && [[ -s "$RR/.run/artifact-local.token" ]] && break
    sleep 0.25
  done
  _O="$(env -i HOME="$T" PATH="/usr/bin:/bin" bash "$RR/scripts/publish-verdict.sh" tier3-zig "$T/verdict.txt" 2>&1)"
  _O="$_O$(env -i HOME="$T" PATH="/usr/bin:/bin" bash "$RR/scripts/publish-verdict.sh" tier3-zig "$T/verdict.txt" --label second 2>&1)"
  _show="$(env -i HOME="$T" PATH="/usr/bin:/bin" bash "$RR/scripts/artifact.sh" show "$WANT_ID" --json 2>&1)"
  if has "$_show" "$WANT_ID" && has "$_show" "second" && has "$_show" "era=era-real" \
     && has "$_show" '"private"'; then
    pass "real server: two publishes of one slug are two versions of ONE private artifact, labels kept"
  else
    fail "real round trip: $_O :: $(head -c 600 <<< "$_show") :: $(tail -n 5 "$T/artsrv.log")"
  fi
  kill "$RS_PID" 2>/dev/null
else
  echo "  SKIP: fastapi/uvicorn not importable — real artifact-server round trip skipped"
fi

# ---------------------------------------------------------------------------
echo ""
echo "8. scoring.py --html:"
_H="$(cd "$REPO_DIR/evals" && python3 - <<'PY'
import scoring
e = {"model": "<script>alert(1)</script>", "suite_version": scoring.current_suite_version(),
     "problem_solving": 98.2, "language_breadth": 96.1, "capability": 97.7,
     "tasks_passed": 258, "tasks_total": 291, "gpu": {"host_id": "rig&1"}}
print(scoring.format_leaderboard_html([e]))
PY
)"
if has "$_H" "&lt;script&gt;alert(1)&lt;/script&gt;" && ! has "$_H" "<script>alert" \
   && has "$_H" "rig&amp;1" && has "$_H" "97.7%" \
   && ! grep -qiE '<script|<link|src=|https?://' <<< "$_H"; then
  pass "a self-contained page (no script, no external request) with every value escaped"
else
  fail "scoring html: $(head -c 400 <<< "$_H")"
fi
_out="$T/board-out.html"
if (cd "$T" && python3 "$REPO_DIR/evals/scoring.py" --html "$_out" >/dev/null 2>&1) && grep -q '<table' "$_out"; then
  pass "--html PATH writes the page"
else
  fail "--html PATH did not write a table: $(head -c 200 "$_out" 2>/dev/null)"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
