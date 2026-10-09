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

# No `.` or other ERE metacharacter in the path: the pkill guard below
# recognises sandbox-anchored patterns by the literal path.
_T="$(mktemp -d "${TMPDIR:-/tmp}/oblifecycleXXXXXX")"
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
  # pkill/pgrep reach the REAL process table. Let through only patterns
  # anchored inside this sandbox (stop.sh also carries an unanchored legacy
  # `pkill -f "mcpo --port"`, which must never run against the host).
  for c in pkill pgrep; do
    printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == *%s* ]] && exec /usr/bin/%s "$@"; done\nexit 1\n' \
      "$d" "$c" > "$d/bin/$c"
    chmod +x "$d/bin/$c"
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

# lifecycle-6: with a specific-address BIND_HOST the services refuse
# localhost, which is exactly what Open WebUI dials. Every probe that follows
# BIND_HOST reads green; doctor must dial what the frontend dials.
# This curl answers ONLY URLs matching $CURL_OK (a server bound to one address).
cat > "$_D/bin/curl" <<'SH'
#!/bin/bash
url=""; w=0
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; [[ "$a" == "%{http_code}" ]] && w=1; done
echo "$url" >> "$CURL_LOG"
if [[ -n "${CURL_OK:-}" && "$url" =~ $CURL_OK ]]; then
  [[ $w -eq 1 ]] && { printf '200'; exit 0; }
  printf '{"status":"ok"}'; exit 0
fi
[[ $w -eq 1 ]] && printf '000'
exit 7
SH
chmod +x "$_D/bin/curl"
_doctor_out() { # _doctor_out <CURL_OK-regex> <conf-lines...>
  local ok="$1"; shift
  printf '%s\n' "$@" > "$_D/openbeast.conf"
  RUN_ENV=(CURL_LOG="$_D/curl.log" CURL_OK="$ok")
  _run "$_D" "$_D/scripts/doctor.sh"
  RUN_ENV=()
}
# Round 2 closed the conf.sh half: OPENBEAST_MODEL_URL (what compose hands
# Open WebUI) now follows the probe host, so on a LAN BIND_HOST the frontend
# dials the address llama binds and doctor's endpoint row stays quiet —
# while it still dials OPENBEAST_MODEL_URL, not a URL of its own.
_O="$(_doctor_out '^http://192\.0\.2\.9:' 'BIND_HOST=192.0.2.9')"
if ! grep -q "model endpoint" <<< "$_O" && grep -q '^http://192.0.2.9:8080/v1/models' "$_D/curl.log"; then
  pass "on a LAN BIND_HOST, WebUI's model URL is http://192.0.2.9:8080/v1 — doctor dials it and it answers"
else
  fail "doctor on a LAN BIND_HOST: $(grep -iE 'llama|model endpoint' <<< "$_O" | tr '\n' ' ') :: $(grep models "$_D/curl.log" | tr '\n' ' ')"
fi
# Negative control: a server that answers ONLY its LAN address, while the
# frontend URL points elsewhere (the router, which hard-binds loopback, is
# down) is still a FAIL.
_O="$(_doctor_out '^http://192\.0\.2\.9:' 'BIND_HOST=192.0.2.9' 'AGENT_ROUTER=true')"
if grep -q "model endpoint (http://localhost:8088/v1)" <<< "$_O"; then
  pass "…and a frontend URL that refuses connections (router down) still FAILs (control)"
else
  fail "doctor missed an unreachable frontend model URL: $(grep -iE 'model endpoint' <<< "$_O" | tr '\n' ' ')"
fi
_O="$(_doctor_out '^http://(127\.0\.0\.1|localhost):' 'BIND_HOST=127.0.0.1')"
if grep -q "llama.cpp server (:8080)" <<< "$_O" && ! grep -q "model endpoint" <<< "$_O"; then
  pass "…and stays quiet on a loopback rig where the frontend reaches the model (control)"
else
  fail "doctor flagged a reachable model endpoint: $(grep -iE 'llama|model endpoint' <<< "$_O" | tr '\n' ' ')"
fi
# The inter-service upstreams follow the probe host too.
if grep -qE 'OPENBEAST_(LLAMA_UPSTREAM|MCPO_URL)="http://127\.0\.0\.1' "$REPO_DIR/start.sh" "$REPO_DIR/scripts/healthcheck.sh"; then
  fail "router/beast-gate upstreams are still hardcoded to 127.0.0.1 (a specific BIND_HOST refuses it)"
else
  pass "router/beast-gate upstreams follow BIND_HOST's probe host"
fi

# ---------------------------------------------------------------------------
# lifecycle-1: a model is healthy when /health says so — not when it binds.
# llama-server binds before it loads and answers 503 "Loading model" for the
# whole load; `curl -s` exits 0 on that 503. The stub below is a real HTTP
# server on an ephemeral port that behaves the same way.
# ---------------------------------------------------------------------------
echo ""
echo "start.sh model readiness + rollback (stub llama-server, real HTTP):"
_L="$_T/load"; mkdir -p "$_L/scripts" "$_L/.run"
cat > "$_L/scripts/stub_llama.py" <<'PY'
import http.server, json, sys, threading, time, os
mode, port = sys.argv[1], int(sys.argv[2])
host = os.environ.get("STUB_HOST", "127.0.0.1")
t0 = time.time()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        ok = mode == "ok"
        body = json.dumps({"status": "ok"} if ok else
                          {"error": {"code": 503, "message": "Loading model"}}).encode()
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
srv = http.server.HTTPServer((host, port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
# loading-then-die: the OOM-mid-load shape. Everything else has a hard
# lifetime so nothing outlives the test even if the harness is killed.
life = {"loading-then-die": 3, "ok": 20, "loading-forever": 60}[mode]
time.sleep(life)
os._exit(1 if mode != "ok" else 0)
PY
for _m in loading-then-die ok loading-forever; do
  _n="serve-${_m}.sh"
  printf '#!/bin/bash\nexec python3 "$(dirname "$0")/stub_llama.py" %s "$STUB_PORT" >/dev/null 2>&1\n' "$_m" > "$_L/scripts/$_n"
  chmod +x "$_L/scripts/$_n"
done
# The functions under test, lifted out of start.sh verbatim.
{
  echo 'set -euo pipefail'
  echo "source '$REPO_DIR/scripts/lib/net.sh'"
  echo "source '$REPO_DIR/scripts/lib/portown.sh'"
  echo 'SCRIPT_DIR="$SANDBOX"; RUN_DIR="$SANDBOX/.run"'
  echo 'HEALTH_HOST=127.0.0.1; LLAMA_BASE="http://127.0.0.1:$STUB_PORT"; LLAMA_PORT="$STUB_PORT"'
  echo 'LLAMA_LOAD_GRACE="${OPENBEAST_LLAMA_LOAD_GRACE:-900}"'
  echo 'reconfigure_webui_for_model() { :; }'
  echo 'LLAMA_PID=""'
  # (_port_busy & co. arrived with the 2026-10-09 review; absent on an older
  # start.sh, where sed simply prints nothing for them.)
  for _fn in _port_busy _port_holder _port_refuse launch_llama _llama_port_ours \
             wait_llama_health record_last_good launch_and_wait; do
    sed -n "/^${_fn}() {/,/^}/p" "$REPO_DIR/start.sh"
  done
  echo 'rc=0; launch_and_wait || rc=$?'
  echo 'echo "RC=$rc SERVING=$SERVE_SCRIPT LASTGOOD=$(cat "$RUN_DIR/last-good-serve-script" 2>/dev/null) FAIL=${LLAMA_FAIL:-} PID=$LLAMA_PID"'
} > "$_L/harness.sh"
_free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
# [foreign]: somebody ELSE's healthy server on the same port — "pre" is up
# before the launch, "late" binds one second after it (the bind race).
_launch_case() { # _launch_case <serve-script> <last-good or ""> [grace] [foreign] -> result line
  local port; port="$(_free_port)"
  rm -f "$_L/.run/last-good-serve-script"
  [[ -n "$2" ]] && echo "$2" > "$_L/.run/last-good-serve-script"
  case "${4:-}" in
    pre)  python3 "$_L/scripts/stub_llama.py" ok "$port" >/dev/null 2>&1 & _PIDS="$_PIDS $!"
          for _ in 1 2 3 4 5 6 7 8 9 10; do
            (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.2
          done ;;
    late) ( sleep 1; exec python3 "$_L/scripts/stub_llama.py" ok "$port" >/dev/null 2>&1 ) & _PIDS="$_PIDS $!" ;;
  esac
  SANDBOX="$_L" STUB_PORT="$port" SERVE_SCRIPT="$1" MODEL_ROLLBACK=true \
    OPENBEAST_LLAMA_LOAD_GRACE="${3:-900}" \
    timeout 40 bash "$_L/harness.sh" > "$_L/out" 2>&1 || true
  local line; line="$(grep '^RC=' "$_L/out" || echo "RC=hung $(tail -n 2 "$_L/out" | tr '\n' ' ')")"
  # Reap whatever stub is still up (by its recorded pid, never a pattern).
  local p="${line##*PID=}"; [[ "$p" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $p"
  echo "$line"
}
_R="$(_launch_case serve-loading-then-die.sh serve-ok.sh)"
if [[ "$_R" == "RC=0 SERVING=serve-ok.sh LASTGOOD=serve-ok.sh "* ]]; then
  pass "a model that 503s 'Loading model' then dies is NOT recorded last-good; rollback serves the real last-good"
else
  fail "load-then-die was treated as healthy (MODEL_ROLLBACK dead, last-good overwritten): $_R"
fi
_T0=$SECONDS
_R="$(_launch_case serve-loading-forever.sh "" 2)"
_WEDGED="${_R##*PID=}"
if [[ "$_R" == "RC=1 "* ]] && (( SECONDS - _T0 < 35 )); then
  pass "a load wedged on 'Loading model' fails after OPENBEAST_LLAMA_LOAD_GRACE (was: waited forever)"
else
  fail "wedged load did not fail at the grace: $_R"
fi
if [[ "$_WEDGED" =~ ^[0-9]+$ ]] && ! kill -0 "$_WEDGED" 2>/dev/null; then
  pass "…and the wedged server is stopped, freeing the port/VRAM for a rollback"
else
  fail "the wedged llama-server (pid $_WEDGED) was left running past the grace"
fi
_R="$(_launch_case serve-ok.sh "")"
if [[ "$_R" == "RC=0 SERVING=serve-ok.sh LASTGOOD=serve-ok.sh "* ]]; then
  pass "a model answering 200 {\"status\":\"ok\"} is healthy and recorded last-good (control)"
else
  fail "a healthy stub was not accepted: $_R"
fi
# ops F1 (2026-10-09): READY also means OURS. A healthy server that is not
# the one this start launched — a campaign's llama-server on the same port —
# used to make a start whose own server died on the bind report "ready".
printf '#!/bin/bash\necho launched >> "%s/launched.log"\nexit 1\n' "$_L" > "$_L/scripts/serve-die.sh"
printf '#!/bin/bash\nexec sleep 5\n' > "$_L/scripts/serve-sleeper.sh"
chmod +x "$_L/scripts/serve-die.sh" "$_L/scripts/serve-sleeper.sh"
rm -f "$_L/launched.log"
_R="$(_launch_case serve-die.sh "" 900 pre)"
if [[ "$_R" == "RC=1 SERVING=serve-die.sh LASTGOOD= FAIL=port "* && ! -e "$_L/launched.log" ]] \
   && grep -q "is already in use by pid" "$_L/out"; then
  pass "a foreign server already on the port: nothing is launched, not 'ready', not last-good, and the holder is named"
else
  fail "a foreign llama-server was accepted as ours: $_R :: $(tr '\n' ' ' < "$_L/out")"
fi
_R="$(_launch_case serve-sleeper.sh "" 900 late)"
if [[ "$_R" == "RC=1 SERVING=serve-sleeper.sh LASTGOOD= "* ]] && grep -q "not the one this stack launched" "$_L/out"; then
  pass "a foreign server that wins the bind race is not ours either: our child must HOLD the listener"
else
  fail "health from a server we did not launch counted as ready: $_R :: $(tr '\n' ' ' < "$_L/out")"
fi
for _p in $_PIDS; do kill "$_p" 2>/dev/null || true; done
# The -d launcher's readiness probe uses the same helper, not `curl -s`.
if grep -q 'curl -s -m 2 "http://$HEALTH_HOST:8080/health"' "$REPO_DIR/start.sh"; then
  fail "start.sh -d still treats any /health answer (a 503 included) as ready"
else
  pass "start.sh -d readiness requires ob_llama_ready, not any /health answer"
fi

# ---------------------------------------------------------------------------
# lifecycle-11 / lifecycle-5 / extensions-client-5: a pidfile is a number on
# disk, and .run/ survives a reboot. What it names must be the process that
# was RECORDED, not merely something alive whose command line looks right.
# ---------------------------------------------------------------------------
echo ""
echo "Recorded pids are identities (supervisor + extensions):"
_P="$_T/pids"; _sandbox "$_P"
# A stranger that merely MENTIONS start.sh — another project's dev server.
bash -c 'exec -a "bash /home/someone/proj2/start.sh" sleep 300' & _STRANGER=$!; _PIDS="$_PIDS $!"
sleep 0.3
_proc() { bash -c "source '$REPO_DIR/scripts/lib/proc.sh'; $1"; }
echo "$_STRANGER" > "$_P/.run/supervisor.pid"; echo "1" > "$_P/.run/supervisor.start"
if _proc "ob_recorded_pid_ours '$_P/.run/supervisor.pid' 'start\\.sh'"; then
  fail "a recycled pid running some other start.sh passed as our supervisor"
else
  pass "a recycled pid whose command line mentions start.sh is NOT our supervisor"
fi
_proc "ob_pid_record '$_P/.run/supervisor.pid' $_STRANGER"
if _proc "ob_recorded_pid_ours '$_P/.run/supervisor.pid' 'start\\.sh'"; then
  pass "…while the process actually recorded (pid + start time) is (control)"
else
  fail "ob_recorded_pid_ours rejected the very process ob_pid_record wrote"
fi
# stop.sh, run for real: it must not SIGTERM (then SIGKILL) the stranger.
echo "$_STRANGER" > "$_P/.run/supervisor.pid"; echo "1" > "$_P/.run/supervisor.start"
# An extension whose recorded pid is ALSO the stranger (no sidecar: the old
# format, so only the path fallback can judge it), plus a real extension
# process whose record was lost — the orphan the path sweep exists for.
mkdir -p "$_P/extensions/fake"
printf 'import time\ntime.sleep(300)\n' > "$_P/extensions/fake/server.py"
python3 "$_P/extensions/fake/server.py" & _EXT_ORPHAN=$!; _PIDS="$_PIDS $!"
echo "$_STRANGER" > "$_P/.run/ext-fake.pid"
mkdir -p "$_P/extensions/real"
printf 'import time\ntime.sleep(300)\n' > "$_P/extensions/real/server.py"
python3 "$_P/extensions/real/server.py" & _EXT_REAL=$!; _PIDS="$_PIDS $!"
sleep 0.3
_proc "ob_pid_record '$_P/.run/ext-real.pid' $_EXT_REAL"
_O="$(timeout 60 bash -c "$(declare -f _run); RUN_ENV=(); _run '$_P' '$_P/stop.sh'")"
sleep 0.3
if kill -0 "$_STRANGER" 2>/dev/null && [[ "$_O" != *"Stopping supervisor"* ]]; then
  pass "stop.sh leaves a stranger holding a stale supervisor.pid alone (was: TERM, then KILL)"
else
  fail "stop.sh signalled a stranger via a stale supervisor.pid: $(grep -i supervisor <<< "$_O" | tr '\n' ' ')"
fi
if kill -0 "$_STRANGER" 2>/dev/null && ! grep -q 'extension stopped (fake)\.' <<< "$_O"; then
  pass "stop.sh does not SIGTERM a stranger that inherited a stale ext-*.pid"
else
  fail "stop.sh killed a stranger via ext-fake.pid: $(grep extension <<< "$_O" | tr '\n' ' ')"
fi
if ! kill -0 "$_EXT_ORPHAN" 2>/dev/null && grep -q 'extension stopped (fake, by path' <<< "$_O"; then
  pass "…and still reaps the real extension orphan by its path"
else
  fail "the extension orphan survived stop.sh: $(grep extension <<< "$_O" | tr '\n' ' ')"
fi
if ! kill -0 "$_EXT_REAL" 2>/dev/null && grep -q 'extension stopped (real)\.' <<< "$_O"; then
  pass "a correctly recorded extension is stopped by its pid (control)"
else
  fail "stop.sh did not stop a recorded extension: $(grep extension <<< "$_O" | tr '\n' ' ')"
fi
if [[ ! -e "$_P/.run/ext-fake.pid" && ! -e "$_P/.run/ext-real.start" ]]; then
  pass "…and the extension records (pid + start time) are removed"
else
  fail "stop.sh left extension records behind: $(ls "$_P/.run")"
fi
kill "$_STRANGER" "$_EXT_ORPHAN" "$_EXT_REAL" 2>/dev/null || true
if [[ -s "$_P/.run/stopped" ]]; then
  pass "stop.sh records that the stack was stopped on purpose (.run/stopped)"
else
  fail "stop.sh left no .run/stopped marker — the watchdog will bring the stack back"
fi

# ---------------------------------------------------------------------------
# lifecycle-2: the watchdog must not undo ./stop.sh, and must not relaunch a
# crash-looping model forever once the supervisor has given up.
# ---------------------------------------------------------------------------
echo ""
echo "healthcheck.sh --restart vs a stack stopped on purpose:"
_W="$_T/wd"; _sandbox "$_W"
# llama-server is down; every other core service answers.
cat > "$_W/bin/curl" <<'SH'
#!/bin/bash
url=""; for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
case "$url" in
  *:3001/*) printf '{"status":"ok"}' ;;
  *:3000/*) printf '{"version":"x"}' ;;
  *:8888*)  printf '<title>searxng</title>' ;;
  *) exit 7 ;;
esac
SH
chmod +x "$_W/bin/curl"
printf '#!/bin/bash\necho "$*" >> "$DOCKER_LOG"\nexit 0\n' > "$_W/bin/docker"; chmod +x "$_W/bin/docker"
# The serve script the stack was started with: records that it ran, exits.
printf '#!/bin/bash\necho ran >> "%s/serve.log"\nexit 1\n' "$_W" > "$_W/scripts/serve-stub.sh"
chmod +x "$_W/scripts/serve-stub.sh"
echo "serve-stub.sh" > "$_W/.run/serve-script"
_wd() { # run one watchdog tick; prints its output
  : > "$_W/serve.log"; : > "$_W/docker.log"
  RUN_ENV=(DOCKER_LOG="$_W/docker.log")
  timeout 60 bash -c "$(declare -f _run); RUN_ENV=(${RUN_ENV[*]}); _run '$_W' '$_W/scripts/healthcheck.sh' --restart"
  RUN_ENV=()
}
echo "2026-09-29T10:00:00 ./stop.sh" > "$_W/.run/stopped"
_O="$(_wd)"
if [[ ! -s "$_W/serve.log" && "$_O" == *"stopped on purpose"* ]]; then
  pass "after ./stop.sh the watchdog relaunches nothing (was: the model within 5 minutes)"
else
  fail "the watchdog relaunched a stack stopped on purpose: serve ran $(wc -l < "$_W/serve.log")x"
fi
rm -f "$_W/.run/stopped" "$_W/.run/watchdog-relaunches"
_O="$(_wd)"
if [[ -s "$_W/serve.log" && "$_O" == *"relaunch FAILED"* ]]; then
  pass "with no marker a crashed model IS relaunched, and a relaunch that dies is reported at once (control)"
else
  fail "the watchdog did not relaunch a crashed stack: $(grep -F '→' <<< "$_O" | tr '\n' ' ')"
fi
_wd >/dev/null; _wd >/dev/null      # relaunches 2 and 3 of the hour
_O="$(_wd)"
if [[ ! -s "$_W/serve.log" && -s "$_W/.run/stopped" && "$_O" == *"crash-looping"* ]]; then
  pass "a 4th relaunch within the hour is refused and the stack is marked stopped"
else
  fail "the watchdog relaunched a crash-looping model without limit: $(grep -F '→' <<< "$_O" | tr '\n' ' ')"
fi
if grep -q 'rm -f "$RUN_DIR/stopped"' "$REPO_DIR/start.sh" && grep -q '_mark_gave_up' "$REPO_DIR/start.sh"; then
  pass "start.sh clears the marker on start and sets it when the supervisor gives up"
else
  fail "start.sh does not manage .run/stopped"
fi

# ---------------------------------------------------------------------------
# extensions-client-4: an extension name is a directory name, nothing else.
# ---------------------------------------------------------------------------
echo ""
echo "ext.sh names + start.sh's extension launch:"
_X="$_T/ext"; _sandbox "$_X"
for _e in dashboard other; do
  mkdir -p "$_X/extensions/$_e"
  printf 'NAME=%s\nKIND=process\n' "$_e" > "$_X/extensions/$_e/manifest"
  printf 'import time\ntime.sleep(300)\n' > "$_X/extensions/$_e/server.py"
  printf '#!/bin/bash\nexec python3 "$(cd "$(dirname "$0")" && pwd)/server.py"\n' > "$_X/extensions/$_e/run.sh"
  chmod +x "$_X/extensions/$_e/run.sh"
done
_ext_conf() { grep '^EXTENSIONS=' "$_X/openbeast.conf" || true; }
_ext() { _run "$_X" "$_X/scripts/ext.sh" "$@"; }
echo 'EXTENSIONS="dashboard other"' > "$_X/openbeast.conf"
for _bad in 'dashboard/' 'dash/' '.*' '../x'; do
  _verb=disable; [[ "$_bad" == dashboard/ ]] && _verb=enable
  _O="$(_ext "$_verb" "$_bad")"
  if [[ "$(_ext_conf)" == 'EXTENSIONS="dashboard other"' && "$_O" == *"Invalid extension name"* ]]; then
    pass "ext.sh $_verb '$_bad' is refused and leaves EXTENSIONS alone"
  else
    fail "ext.sh $_verb '$_bad' -> $(_ext_conf) ($(tr '\n' ' ' <<< "$_O"))"
    echo 'EXTENSIONS="dashboard other"' > "$_X/openbeast.conf"
  fi
done
_O="$(_ext disable nope)"
if [[ "$(_ext_conf)" == 'EXTENSIONS="dashboard other"' && "$_O" == *"not enabled"* ]]; then
  pass "disabling an extension that is not enabled says so and changes nothing"
else
  fail "ext.sh disable nope -> $(_ext_conf) ($(tr '\n' ' ' <<< "$_O"))"
fi
_O="$(_ext disable dashboard)"
if [[ "$(_ext_conf)" == 'EXTENSIONS="other"' ]]; then
  pass "disable removes exactly the named extension (control)"
else
  fail "ext.sh disable dashboard -> $(_ext_conf) ($(tr '\n' ' ' <<< "$_O"))"
fi
# start.sh's launch loop, lifted verbatim, under set -euo pipefail.
python3 - "$REPO_DIR/start.sh" "$_X/launch.sh" <<'PY'
import sys
src = open(sys.argv[1]).read()
a = src.index("while IFS= read -r _ext; do")
b = src.index("done < <(ob_ext_processes)", a) + len("done < <(ob_ext_processes)")
open(sys.argv[2], "w").write(
    'set -euo pipefail\nREPO_DIR="$SANDBOX"; RUN_DIR="$SANDBOX/.run"\n'
    'source "$SANDBOX/scripts/lib/proc.sh"; source "$SANDBOX/scripts/lib/extensions.sh"\n'
    + src[a:b] + '\necho LAUNCH-LOOP-DONE\n')
PY
_launch() { SANDBOX="$_X" EXTENSIONS="$1" timeout 30 bash "$_X/launch.sh" 2>&1 || true; }
_O="$(_launch "dashboard/ other")"
_OTHER_PID="$(cat "$_X/.run/ext-other.pid" 2>/dev/null || true)"
[[ "$_OTHER_PID" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $_OTHER_PID"
# (The warning comes from start.sh's own guard or, since round 2, from
# lib/extensions.sh's ob_ext_enabled filtering it first — either wording.)
if [[ "$_O" == *LAUNCH-LOOP-DONE* && "$_O" == *"invalid extension name 'dashboard/'"* ]]; then
  pass "start.sh skips EXTENSIONS=\"dashboard/\" instead of dying on .run/ext-dashboard/.pid"
else
  fail "start.sh's extension loop aborted on 'dashboard/': $(tr '\n' ' ' <<< "$_O")"
fi
sleep 0.3
if [[ "$_OTHER_PID" =~ ^[0-9]+$ ]] && kill -0 "$_OTHER_PID" 2>/dev/null && [[ -s "$_X/.run/ext-other.start" ]]; then
  pass "…and still launches the valid one, recorded with its start time"
else
  fail "the valid extension was not launched/recorded: $(ls "$_X/.run")"
fi
_O="$(_launch "other")"
if [[ "$_O" == *"already running"* && "$(cat "$_X/.run/ext-other.pid")" == "$_OTHER_PID" ]]; then
  pass "a live extension is not spawned over (its record is kept)"
else
  fail "start.sh spawned over a live extension: $(tr '\n' ' ' <<< "$_O")"
  _p="$(cat "$_X/.run/ext-other.pid" 2>/dev/null || true)"; [[ "$_p" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $_p"
fi
[[ "$_OTHER_PID" =~ ^[0-9]+$ ]] && kill "$_OTHER_PID" 2>/dev/null || true

# extensions-client-9: `ext.sh disable x` then `./stop.sh` (as ext.sh says)
# must still bring x's containers down.
echo ""
echo "stop.sh brings down compose extensions, enabled or just disabled:"
_C="$_T/compose"; _sandbox "$_C"
mkdir -p "$_C/extensions/cx"
printf 'NAME=cx\nKIND=compose\n' > "$_C/extensions/cx/manifest"
printf 'services: {}\n' > "$_C/extensions/cx/compose.yaml"
printf '#!/bin/bash\necho "$*" >> "$DOCKER_LOG"\nexit 0\n' > "$_C/bin/docker"; chmod +x "$_C/bin/docker"
: > "$_C/openbeast.conf"   # cx NOT enabled: it was just disabled
RUN_ENV=(DOCKER_LOG="$_C/docker.log")
_run "$_C" "$_C/stop.sh" >/dev/null
RUN_ENV=()
if grep -q -- "down" "$_C/docker.log" && grep -q -- "-f $_C/extensions/cx/compose.yaml" "$_C/docker.log"; then
  pass "a disabled compose extension's fragment is still passed to 'docker compose down'"
else
  fail "stop.sh left a just-disabled compose extension running: $(tr '\n' ' ' < "$_C/docker.log")"
fi
if grep -q -- "-f $_C/docker-compose.yml" "$_C/docker.log"; then
  pass "…alongside the core compose file (control)"
else
  fail "stop.sh no longer passes the core compose file: $(tr '\n' ' ' < "$_C/docker.log")"
fi

# ===========================================================================
# 2026-10-09 review: the entry points themselves (start.sh / stop.sh).
# ===========================================================================
echo ""
echo "start.sh --help describes the stack that ships (UX-19):"
# --help exits inside the argument loop, before conf.sh or anything else runs.
_H="$(bash "$REPO_DIR/start.sh" --help 2>&1)" && _HRC=0 || _HRC=$?
_DEF="$(sed -n 's/^DEFAULT_SERVE_SCRIPT=.*|| echo \([A-Za-z0-9._-]*\)).*/\1/p' "$REPO_DIR/scripts/lib/conf.sh")"
if [[ $_HRC -eq 0 && -n "$_DEF" && "$_H" == *"$_DEF"* ]]; then
  pass "the help names the default serve script conf.sh resolves ($_DEF)"
else
  fail "start.sh --help (rc=$_HRC) does not name the default '$_DEF'"
fi
if [[ "$_H" != *MCPO* && "$_H" != *"MemoryMax=96G"* && "$_H" != *"Qwen3.6-27B Uncensored Q5_K_P"* ]]; then
  pass "…and no longer describes the MCPO proxy, a 96G cap or the old default"
else
  fail "start.sh --help still carries stale facts: $(grep -E 'MCPO|96G|Q5_K_P' <<< "$_H" | tr '\n' ' ')"
fi
if [[ "$_H" == *"Usage:"* && "$_H" == *MEM_LIMIT_PCT* && "$_H" != *"set -euo"* && "$_H" != *'SCRIPT_DIR='* ]]; then
  pass "…and prints the whole header, usage included, with no code leaking in (control)"
else
  fail "start.sh --help is cut short or leaks code: $(tail -n 3 <<< "$_H" | tr '\n' ' ')"
fi
if grep -qE 'echo .*MCPO (tools|proxy)' "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh"; then
  fail "a runtime banner still says MCPO: $(grep -nE 'echo .*MCPO (tools|proxy)' "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" | tr '\n' ' ')"
else
  pass "runtime banners say 'Tool server', not MCPO"
fi

# Run the sandbox's start.sh for real. 127.0.0.2 is where nothing of a live
# stack on this box binds, so every probe start.sh makes is against an address
# this test owns. Sets _SO (output) and _SRC (exit code).
_start_rc() { # _start_rc <dir> <timeout-s> [args...]   (extra env via RUN_ENV)
  local d="$1" t="$2"; shift 2
  _SRC=0
  _SO="$(env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x \
    OPENBEAST_BIND=127.0.0.2 OPENBEAST_LOGROTATE_AUTOINSTALL=false \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} timeout "$t" bash "$d/start.sh" "$@" 2>&1)" || _SRC=$?
}

echo ""
echo "start.sh takes commands as words (UX-20):"
_CW="$_T/words"; _sandbox "$_CW"
printf '#!/bin/bash\necho "$*" >> "%s/docker.log"\nexit 0\n' "$_CW" > "$_CW/bin/docker"
_start_rc "$_CW" 30 status
if [[ $_SRC -eq 0 && "$_SO" == *"OpenBeast stack status:"* && "$_SO" == *"tool server: not running"* ]]; then
  pass "'./start.sh status' is --status (was: \"scripts/status not found or not executable\")"
else
  fail "start.sh status (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi
_start_rc "$_CW" 60 stop
if [[ $_SRC -eq 0 && -s "$_CW/.run/stopped" ]] && grep -q -- "down" "$_CW/docker.log"; then
  pass "'./start.sh stop' runs stop.sh"
else
  fail "start.sh stop (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi
rm -f "$_CW/.run/stopped" "$_CW/docker.log"
_start_rc "$_CW" 30 help
[[ $_SRC -eq 0 && "$_SO" == *"Usage:"* ]] && pass "'./start.sh help' prints the usage" \
  || fail "start.sh help (rc=$_SRC): $(head -n 2 <<< "$_SO" | tr '\n' ' ')"
_start_rc "$_CW" 30 frobnicate
if [[ $_SRC -eq 2 && "$_SO" == *"Unknown command 'frobnicate'"* && "$_SO" == *"status | stop | restart | doctor | help"* \
      && ! -e "$_CW/.run/supervisor.pid" ]]; then
  pass "an unknown word exits 2 with the valid commands, and starts nothing"
else
  fail "start.sh frobnicate (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi
_start_rc "$_CW" 30 stat
[[ $_SRC -eq 2 && "$_SO" == *"did you mean: ./start.sh status"* ]] \
  && pass "…and a near miss names the nearest command ('stat' -> status)" \
  || fail "start.sh stat (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
_start_rc "$_CW" 30 serve-nope.sh
if [[ $_SRC -eq 1 && "$_SO" == *"scripts/serve-nope.sh not found or not executable"* ]]; then
  pass "a word spelled like a serve script is still a serve script (control)"
else
  fail "start.sh serve-nope.sh (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi
# restart = stop.sh, then a -d start. The start half is cut short on purpose
# (a serve script that does not exist), so nothing is launched here.
rm -f "$_CW/docker.log"
_start_rc "$_CW" 60 restart serve-nope.sh
if [[ $_SRC -eq 1 && "$_SO" == *"not found or not executable"* ]] && grep -q -- "down" "$_CW/docker.log"; then
  pass "'./start.sh restart' stops the stack first, then goes on to start"
else
  fail "start.sh restart (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi

echo ""
echo "start.sh refuses to run as root (UX-11):"
_RT="$_T/root"; _sandbox "$_RT"
rm -f "$_RT/openbeast.conf"
# "root" is a stub `id` — the only thing the guard asks.
printf '#!/bin/bash\n[[ "$1" == -u ]] && { echo "${FAKE_UID:-1000}"; exit 0; }\nexec /usr/bin/id "$@"\n' > "$_RT/bin/id"
chmod +x "$_RT/bin/id"
RUN_ENV=(FAKE_UID=0)
_start_rc "$_RT" 30 serve-nope.sh
if [[ $_SRC -eq 1 && "$_SO" == *"do not run ./start.sh as root"* && "$_SO" == *"usermod -aG docker"* \
      && ! -e "$_RT/openbeast.conf" && ! -e "$_RT/.run/supervisor.pid" ]]; then
  pass "as root: refused with the reason and the docker-group fix, before openbeast.conf is created"
else
  fail "start.sh as root (rc=$_SRC, conf=$([[ -e "$_RT/openbeast.conf" ]] && echo created || echo absent)): $(tr '\n' ' ' <<< "$_SO")"
fi
_start_rc "$_RT" 30 status
[[ $_SRC -eq 1 && "$_SO" == *"as root"* ]] && pass "…for every command that reads the config ('status' too)" \
  || fail "start.sh status as root (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
RUN_ENV=(FAKE_UID=1000)
_start_rc "$_RT" 30 serve-nope.sh
RUN_ENV=()
if [[ "$_SO" != *"as root"* && "$_SO" == *"scripts/serve-nope.sh not found"* ]]; then
  pass "a normal user gets past the guard (control)"
else
  fail "the root guard fired for uid 1000: $(tr '\n' ' ' <<< "$_SO")"
fi

echo ""
echo "start.sh preflight: the port and the GPU lease, before anything is launched (ops F1):"
_PF="$_T/preflight"; _sandbox "$_PF"
cp "$REPO_DIR/scripts/gpu-lease.sh" "$_PF/scripts/"
# The serve script leaves a marker and dies: "was the model launched?"
printf '#!/bin/bash\necho launched >> "%s/launched.log"\nexit 1\n' "$_PF" > "$_PF/scripts/serve-mark.sh"
chmod +x "$_PF/scripts/serve-mark.sh"
_PFPORT="$(_free_port)"
_pf_env() { RUN_ENV=(OPENBEAST_SERVE_SCRIPT=serve-mark.sh "OPENBEAST_INFERENCE_URL=http://127.0.0.2:$_PFPORT" "$@"); }
# Somebody else's healthy llama-server on the address+port ours would bind.
STUB_HOST=127.0.0.2 python3 "$_L/scripts/stub_llama.py" ok "$_PFPORT" >/dev/null 2>&1 & _FOREIGN=$!; _PIDS="$_PIDS $!"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  (exec 3<>"/dev/tcp/127.0.0.2/$_PFPORT") 2>/dev/null && break; sleep 0.2
done
for _mode in "" -d; do
  rm -f "$_PF/launched.log"; rm -rf "$_PF/.run"; mkdir -p "$_PF/.run"
  _pf_env
  _start_rc "$_PF" 40 $_mode
  if [[ $_SRC -eq 1 && "$_SO" == *"port $_PFPORT (the model server) is already in use by pid $_FOREIGN"* \
        && "$_SO" == *"Nothing was started"* && ! -e "$_PF/launched.log" && ! -e "$_PF/.run/supervisor.pid" \
        && ! -e "$_PF/.run/last-good-serve-script" ]]; then
    pass "./start.sh ${_mode:-(foreground)}: a foreign server on the model port is refused up front, naming its pid"
  else
    fail "start.sh $_mode with the model port held (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
  fi
done
kill "$_FOREIGN" 2>/dev/null || true; wait "$_FOREIGN" 2>/dev/null || true
rm -f "$_PF/launched.log"; rm -rf "$_PF/.run"; mkdir -p "$_PF/.run"
_pf_env
_start_rc "$_PF" 40
if [[ $_SRC -eq 1 && -s "$_PF/launched.log" && "$_SO" != *"already in use"* && "$_SO" == *"llama-server did not come up"* ]]; then
  pass "…and with the port free the model IS launched (control)"
else
  fail "start.sh with the port free (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
# The GPU lease, held by a live process that is not our ancestor.
sleep 300 & _HOLDER=$!; _PIDS="$_PIDS $!"
_lease_write() { # _lease_write <pid>
  printf 'pid=%s\nstart=%s\nlabel=%s\nsince=%s\n' "$1" \
    "$(_proc "ob_pid_start $1")" "T1.17 pair" "2026-10-09T10:00:00" > "$_PF/.run/gpu.lease"
}
rm -f "$_PF/launched.log"; rm -rf "$_PF/.run"; mkdir -p "$_PF/.run"; _lease_write "$_HOLDER"
_pf_env
_start_rc "$_PF" 40
if [[ $_SRC -eq 1 && "$_SO" == *"the GPU is leased"* && "$_SO" == *"HELD by pid $_HOLDER"* && "$_SO" == *"T1.17 pair"* \
      && "$_SO" == *"gpu-lease.sh status"* && ! -e "$_PF/launched.log" && ! -e "$_PF/.run/supervisor.pid" ]]; then
  pass "a GPU lease held by someone else refuses the start (holder, label and the status command named)"
else
  fail "start.sh under a foreign GPU lease (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
# Unmanaged backend: no local model, so neither the lease nor the port is ours
# to ask about. The start goes on to wait for the remote server.
rm -f "$_PF/launched.log"
_pf_env OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=http://127.0.0.2:$_PFPORT" OPENBEAST_LLAMA_LOAD_GRACE=1
_start_rc "$_PF" 40
if [[ "$_SO" != *"GPU is leased"* && "$_SO" != *"already in use"* && "$_SO" == *"Waiting for the vLLM server"* \
      && ! -e "$_PF/launched.log" ]]; then
  pass "an unmanaged backend (INFERENCE_MANAGED=false) is not held up by the lease or the port check"
else
  fail "the preflight fired on an unmanaged stack (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
# A lease whose holder is gone is stale: free.
kill "$_HOLDER" 2>/dev/null || true; wait "$_HOLDER" 2>/dev/null || true
rm -f "$_PF/launched.log"; rm -rf "$_PF/.run"; mkdir -p "$_PF/.run"; _lease_write "$_HOLDER"
_pf_env
_start_rc "$_PF" 40
RUN_ENV=()
if [[ "$_SO" != *"GPU is leased"* && -s "$_PF/launched.log" ]]; then
  pass "…and a stale lease (holder gone) does not block the start (control)"
else
  fail "a stale lease blocked the start (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi

echo ""
echo "start.sh preflight: the fixed core ports (UX-04):"
# On 127.0.0.3 — an address no stack, and no other suite, binds. The port
# NUMBERS are the real ones (they are not configurable); the address is ours.
_PP="$_T/ports"; _sandbox "$_PP"
cat > "$_PP/listen.py" <<'PY'
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((sys.argv[1], int(sys.argv[2]))); s.listen(4)
time.sleep(60)
PY
_listen() { # _listen <port> -> sets _LP (pid), returns once it accepts
  python3 "$_PP/listen.py" 127.0.0.3 "$1" & _LP=$!; _PIDS="$_PIDS $!"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    (exec 3<>"/dev/tcp/127.0.0.3/$1") 2>/dev/null && return 0; sleep 0.2
  done
}
_unlisten() { kill "$_LP" 2>/dev/null || true; wait "$_LP" 2>/dev/null || true; }
# Unmanaged, so the only thing between the preflight and the (stubbed-out)
# tool server is a one-second wait for a backend that is not there.
_pp_env() { RUN_ENV=(OPENBEAST_BIND=127.0.0.3 OPENBEAST_INFERENCE_BACKEND=vllm
                     OPENBEAST_INFERENCE_URL=http://127.0.0.3:9 OPENBEAST_LLAMA_LOAD_GRACE=1 "$@"); }
_listen 3001
_pp_env; _start_rc "$_PP" 40
if [[ $_SRC -eq 1 && "$_SO" == *"port 3001 (the tool server) is already in use by pid $_LP ("*"listen.py"* \
      && "$_SO" == *"Nothing was started"* && "$_SO" != *"Waiting for"* && ! -e "$_PP/.run/supervisor.pid" ]]; then
  pass "port 3001 held: refused before anything is waited for, naming the pid and its command"
else
  fail "start.sh with 3001 held (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
_unlisten
_listen 3000
_pp_env; _start_rc "$_PP" 40
if [[ $_SRC -eq 1 && "$_SO" == *"port 3000 (Open WebUI) is already in use by pid $_LP"* && "$_SO" != *"Waiting for"* ]]; then
  pass "port 3000 held by a process that is not our container: refused, naming it"
else
  fail "start.sh with 3000 held (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
# The same listener, but docker says our open-webui container is running
# (left up by the last Ctrl+C, as designed): not a conflict.
printf '#!/bin/bash\n[[ "$1" == inspect && "$*" == *State.Running* ]] && { echo true; exit 0; }\nexit 1\n' > "$_PP/bin/docker"
_pp_env; _start_rc "$_PP" 40
if [[ "$_SO" != *"already in use"* && "$_SO" == *"Waiting for the vLLM server"* ]]; then
  pass "…but our own still-running container on 3000 is not a conflict"
else
  fail "the preflight refused our own WebUI container (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
# A holder this user cannot name (ss shows no pid) and no container docker
# will admit to: warn, do not refuse — it is most likely ours behind a docker
# daemon this user cannot reach.
printf '#!/bin/bash\nexit 1\n' > "$_PP/bin/docker"
printf '#!/bin/bash\nexit 0\n' > "$_PP/bin/ss"; chmod +x "$_PP/bin/ss"
_pp_env; _start_rc "$_PP" 40
if [[ "$_SO" == *"Warning: port 3000 (Open WebUI) is already in use"* && "$_SO" == *"Waiting for the vLLM server"* ]]; then
  pass "…and an unnameable holder is a warning, not a refusal"
else
  fail "unnameable 3000 holder (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi
rm -f "$_PP/bin/ss"
_unlisten
_listen 8888
_pp_env; _start_rc "$_PP" 40
[[ $_SRC -eq 1 && "$_SO" == *"port 8888 (SearXNG) is already in use by pid $_LP"* ]] \
  && pass "port 8888 held: refused, naming it" \
  || fail "start.sh with 8888 held (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
_unlisten
_pp_env; _start_rc "$_PP" 40
RUN_ENV=()
if [[ "$_SO" != *"already in use"* && "$_SO" == *"Waiting for the vLLM server"* ]]; then
  pass "with all four free the start goes ahead (control)"
else
  fail "the preflight fired with every port free (rc=$_SRC): $(tail -n 6 <<< "$_SO" | tr '\n' ' ')"
fi

echo ""
echo "start.sh cleanup keeps a replacement's pidfile (ops F9):"
# cleanup(), lifted verbatim. "Ours" are pids above any kernel's pid_max (so
# the kills in cleanup can never land on a real process); the "replacement"
# is a live process healthcheck --restart recorded.
_CU="$_T/cleanup"; mkdir -p "$_CU/.run"
{
  echo 'set -euo pipefail'
  echo 'RUN_DIR="$SANDBOX/.run"; SCRIPT_DIR="$SANDBOX"; REPO_DIR="$SANDBOX"; CLEANED=0'
  echo 'ob_ext_reap() { :; }'
  sed -n '/^_rm_own_pidfile() {/,/^}/p' "$REPO_DIR/start.sh"
  sed -n '/^cleanup() {/,/^}/p' "$REPO_DIR/start.sh"
  echo 'LLAMA_PID="$1"; MCPO_PID="$2"; ROUTER_PID="$3"; EDGE_PID="$4"'
  echo 'cleanup >/dev/null 2>&1'
} > "$_CU/harness.sh"
_OURS=(2147483001 2147483002 2147483003 2147483004)
sleep 300 & _REPL=$!; _PIDS="$_PIDS $!"
for _n in llama mcpo router edge; do echo "$_REPL" > "$_CU/.run/$_n.pid"; done
echo x > "$_CU/.run/supervisor.pid"
SANDBOX="$_CU" bash "$_CU/harness.sh" "${_OURS[@]}" || true
_kept=""; for _n in llama mcpo router edge; do [[ "$(cat "$_CU/.run/$_n.pid" 2>/dev/null)" == "$_REPL" ]] && _kept+="$_n "; done
if [[ "$_kept" == "llama mcpo router edge " ]] && kill -0 "$_REPL" 2>/dev/null; then
  pass "pidfiles naming a watchdog replacement survive the supervisor's exit (tool server, router, gate, model)"
else
  fail "cleanup erased a replacement's record: kept only '$_kept' of llama mcpo router edge"
fi
[[ ! -e "$_CU/.run/supervisor.pid" ]] && pass "…the supervisor's own pidfile is always removed" \
  || fail "cleanup left supervisor.pid behind"
_i=0; for _n in llama mcpo router edge; do echo "${_OURS[$_i]}" > "$_CU/.run/$_n.pid"; _i=$((_i + 1)); done
SANDBOX="$_CU" bash "$_CU/harness.sh" "${_OURS[@]}" || true
if ! ls "$_CU/.run"/*.pid >/dev/null 2>&1; then
  pass "…and pidfiles that still name OUR children are removed (control)"
else
  fail "cleanup left its own children's pidfiles: $(ls "$_CU/.run")"
fi
kill "$_REPL" 2>/dev/null || true

echo ""
echo "Ctrl+C on a foreground start is a stop on purpose (ops F4):"
_IN="$_T/intr"; _sandbox "$_IN"
printf '#!/bin/bash\nexec sleep 60\n' > "$_IN/scripts/serve-sleep.sh"       # never healthy, never dies
printf '#!/bin/bash\nexit 1\n' > "$_IN/scripts/serve-dies.sh"
chmod +x "$_IN/scripts/serve-sleep.sh" "$_IN/scripts/serve-dies.sh"
# A foreground start.sh, parked in its model wait, then signalled. Background
# jobs of a script inherit SIGINT ignored, and bash cannot trap a signal it
# was born ignoring — `env --default-signal` hands start.sh a normal one.
_signal_case() { # _signal_case <INT|TERM> -> sets _SRC, leaves $_IN/.run to inspect
  local sig="$1" p i
  rm -rf "$_IN/.run"; mkdir -p "$_IN/.run"
  env -i --default-signal=INT HOME="$_IN/home" PATH="$_IN/bin:/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x \
    OPENBEAST_BIND=127.0.0.2 OPENBEAST_LOGROTATE_AUTOINSTALL=false OPENBEAST_SERVE_SCRIPT=serve-sleep.sh \
    "OPENBEAST_INFERENCE_URL=http://127.0.0.2:$(_free_port)" \
    bash "$_IN/start.sh" > "$_IN/out" 2>&1 & p=$!; _PIDS="$_PIDS $p"
  for i in $(seq 1 100); do [[ -s "$_IN/.run/llama.pid" ]] && break; sleep 0.1; done
  _SLEEPER="$(cat "$_IN/.run/llama.pid" 2>/dev/null || true)"
  [[ "$_SLEEPER" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $_SLEEPER"
  kill "-$sig" "$p" 2>/dev/null || true
  _SRC=0; wait "$p" 2>/dev/null || _SRC=$?
}
if env --default-signal=INT true 2>/dev/null; then
  _signal_case INT
  if [[ $_SRC -eq 143 ]] && grep -q "Ctrl+C on a foreground" "$_IN/.run/stopped" 2>/dev/null; then
    pass "SIGINT writes .run/stopped (reason: Ctrl+C) — the watchdog will not resurrect the stack"
  else
    fail "Ctrl+C left no stopped-on-purpose marker (rc=$_SRC): $(ls "$_IN/.run" | tr '\n' ' ') :: $(tail -n 3 "$_IN/out" | tr '\n' ' ')"
  fi
  if [[ "$_SLEEPER" =~ ^[0-9]+$ ]] && ! kill -0 "$_SLEEPER" 2>/dev/null && [[ ! -e "$_IN/.run/supervisor.pid" ]]; then
    pass "…and the shutdown itself is unchanged: the model is stopped, the pidfiles are gone"
  else
    fail "the INT trap no longer cleans up: sleeper=$_SLEEPER $(ls "$_IN/.run" | tr '\n' ' ')"
  fi
else
  echo "  SKIP: this env(1) has no --default-signal; SIGINT cannot be delivered to a background start.sh"
fi
_signal_case TERM
if [[ $_SRC -eq 143 ]] && grep -q "SIGTERM to a foreground" "$_IN/.run/stopped" 2>/dev/null; then
  pass "SIGTERM to a foreground start writes the marker too"
else
  fail "SIGTERM left no marker (rc=$_SRC): $(ls "$_IN/.run" | tr '\n' ' ')"
fi
# A start that FAILS was not stopped on purpose: no marker.
RUN_ENV=(OPENBEAST_SERVE_SCRIPT=serve-dies.sh "OPENBEAST_INFERENCE_URL=http://127.0.0.2:$(_free_port)")
rm -rf "$_IN/.run"; mkdir -p "$_IN/.run"
_start_rc "$_IN" 40
RUN_ENV=()
if [[ $_SRC -eq 1 && ! -e "$_IN/.run/stopped" ]]; then
  pass "a start that fails on its own leaves no marker (control)"
else
  fail "a failed start wrote a stopped-on-purpose marker (rc=$_SRC): $(cat "$_IN/.run/stopped" 2>/dev/null)"
fi
# The detached supervisor must not write it: stop.sh does, with its own reason.
if grep -q '\[\[ \$DAEMONIZED -eq 0 && ! -e "\$RUN_DIR/stopped" \]\] || return 0' "$REPO_DIR/start.sh"; then
  pass "the marker is written by a foreground start only, and never over stop.sh's"
else
  fail "_mark_stopped lost its foreground-only / do-not-overwrite guard"
fi

echo ""
echo "stop.sh parses its arguments before it stops anything (UX-01):"
_SA="$_T/stopargs"; _sandbox "$_SA"
printf '#!/bin/bash\necho "$*" >> "%s/docker.log"\nexit 0\n' "$_SA" > "$_SA/bin/docker"
_stop_rc() { # _stop_rc <args...> -> sets _SO (output) and _SRC (exit code)
  _SRC=0
  _SO="$(env -i HOME="$_SA/home" PATH="$_SA/bin:/usr/bin:/bin" timeout 60 bash "$_SA/stop.sh" "$@" 2>&1)" || _SRC=$?
}
for _a in --help -h; do
  rm -f "$_SA/.run/stopped" "$_SA/docker.log"
  _stop_rc "$_a"
  if [[ $_SRC -eq 0 && "$_SO" == *"Usage:"* && ! -e "$_SA/.run/stopped" && ! -e "$_SA/docker.log" ]]; then
    pass "stop.sh $_a prints usage, exits 0 and stops nothing (no marker, no compose down)"
  else
    fail "stop.sh $_a (rc=$_SRC) acted: marker=$([[ -e "$_SA/.run/stopped" ]] && echo yes || echo no) docker=$(cat "$_SA/docker.log" 2>/dev/null | tr '\n' ' ')"
  fi
done
rm -f "$_SA/.run/stopped" "$_SA/docker.log"
_stop_rc --stauts
if [[ $_SRC -eq 2 && "$_SO" == *"Unknown option: --stauts"* && ! -e "$_SA/.run/stopped" && ! -e "$_SA/docker.log" ]]; then
  pass "an unknown option exits 2, names it, and stops nothing"
else
  fail "stop.sh --stauts (rc=$_SRC): $(tr '\n' ' ' <<< "$_SO")"
fi
_stop_rc
if [[ $_SRC -eq 0 && -s "$_SA/.run/stopped" ]] && grep -q -- "down" "$_SA/docker.log"; then
  pass "…and a bare ./stop.sh still stops the stack and writes the marker (control)"
else
  fail "bare stop.sh (rc=$_SRC) no longer stops: $(tr '\n' ' ' <<< "$_SO")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Lifecycle: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
