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
# ops F5: with the router on, that endpoint IS the router — say so, and name
# the repair healthcheck.sh now really has (it had no router branch, so the
# old "check the router: healthcheck.sh --restart" did nothing). On a LAN
# BIND_HOST the row used to blame the bind, which the router never follows.
if grep -q "is the agent router, and it is not answering" <<< "$_O" \
   && grep -A1 "is the agent router" <<< "$_O" | grep -qF "fix: ./scripts/healthcheck.sh --restart (relaunches the router)" \
   && ! grep -q "refuses connections: services bind only" <<< "$_O"; then
  pass "…named as a dead agent router, with the healthcheck --restart that now relaunches it"
else
  fail "doctor's router advice: $(grep -A1 -iE 'model endpoint' <<< "$_O" | tr '\n' ' ')"
fi
if grep -qE '^# Agent-spawn router' "$REPO_DIR/scripts/healthcheck.sh" \
   && grep -q 'agents/router.py" >>"\$_rt_log"' "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "…and healthcheck.sh has the router branch that advice depends on (run in test_hydra_instinct_wiring.sh)"
else
  fail "doctor sends the operator to healthcheck.sh --restart, which has no router branch"
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
srv = http.server.HTTPServer(("127.0.0.1", port), H)
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
  echo 'SCRIPT_DIR="$SANDBOX"; RUN_DIR="$SANDBOX/.run"'
  echo 'HEALTH_HOST=127.0.0.1; LLAMA_BASE="http://127.0.0.1:$STUB_PORT"'
  echo 'LLAMA_LOAD_GRACE="${OPENBEAST_LLAMA_LOAD_GRACE:-900}"'
  echo 'reconfigure_webui_for_model() { :; }'
  for _fn in launch_llama wait_llama_health record_last_good launch_and_wait; do
    sed -n "/^${_fn}() {/,/^}/p" "$REPO_DIR/start.sh"
  done
  echo 'rc=0; launch_and_wait || rc=$?'
  echo 'echo "RC=$rc SERVING=$SERVE_SCRIPT LASTGOOD=$(cat "$RUN_DIR/last-good-serve-script" 2>/dev/null) PID=$LLAMA_PID"'
} > "$_L/harness.sh"
_free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
_launch_case() { # _launch_case <serve-script> <last-good or ""> [grace] -> result line
  local port; port="$(_free_port)"
  rm -f "$_L/.run/last-good-serve-script"
  [[ -n "$2" ]] && echo "$2" > "$_L/.run/last-good-serve-script"
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

# ---------------------------------------------------------------------------
# UX-12 / S13 (2026-10-09): a command that only READS must not create
# openbeast.conf. conf.sh minted SEARXNG_SECRET for whoever sourced it first —
# doctor.sh, a report-only healthcheck.sh and the --check-default-admin probe
# included. OB_CONF_READONLY=1 is the caller's way to say "read only".
# ---------------------------------------------------------------------------
echo ""
echo "read-only commands leave openbeast.conf alone (OB_CONF_READONLY):"
_RO="$_T/ro"; _sandbox "$_RO"
cp "$REPO_DIR/scripts/configure-webui.sh" "$_RO/scripts/"
_ro_fresh() { rm -f "$_RO/openbeast.conf"; }
_ro_fresh
_run "$_RO" "$_RO/scripts/doctor.sh" >/dev/null
[[ ! -e "$_RO/openbeast.conf" ]] && pass "doctor.sh on a fresh checkout creates no openbeast.conf" \
  || fail "doctor.sh created openbeast.conf: $(tr '\n' ' ' < "$_RO/openbeast.conf")"
_ro_fresh
_run "$_RO" "$_RO/scripts/healthcheck.sh" >/dev/null
[[ ! -e "$_RO/openbeast.conf" ]] && pass "a report-only healthcheck.sh creates no openbeast.conf" \
  || fail "healthcheck.sh (no --restart) created openbeast.conf"
_ro_fresh
_run "$_RO" "$_RO/scripts/configure-webui.sh" --check-default-admin >/dev/null
[[ ! -e "$_RO/openbeast.conf" ]] && pass "configure-webui.sh --check-default-admin creates no openbeast.conf" \
  || fail "the read-only admin probe created openbeast.conf"
_ro_src() { # _ro_src [VAR=val] — source the sandbox conf.sh, print the secret it resolved
  env -i HOME="$_RO/home" PATH="$_RO/bin:/usr/bin:/bin" REPO_DIR="$_RO" "$@" \
    bash -c 'source "$REPO_DIR/scripts/lib/conf.sh" 2>/dev/null; printf "%s" "$OPENBEAST_SEARXNG_SECRET"'
}
_ro_fresh
_S="$(_ro_src OB_CONF_READONLY=1)"
if [[ -z "$_S" && ! -e "$_RO/openbeast.conf" ]]; then
  pass "OB_CONF_READONLY=1: no file, and no throwaway secret a compose call could run with"
else
  fail "OB_CONF_READONLY=1 still minted a secret ('${_S:0:8}…') or wrote the file"
fi
# Negative controls: without the flag (and with any other value) the secret
# is still minted and persisted 0600 — daemon mode depends on that.
for _v in "" "OB_CONF_READONLY=0" "OB_CONF_READONLY=true"; do
  _ro_fresh
  # shellcheck disable=SC2086  # an empty $_v must vanish, not become an argument
  _S="$(_ro_src $_v)"
  if [[ ${#_S} -eq 64 && "$(stat -c '%a' "$_RO/openbeast.conf" 2>/dev/null)" == "600" ]] \
     && grep -q "^SEARXNG_SECRET=$_S\$" "$_RO/openbeast.conf"; then
    pass "'${_v:-flag unset}': the secret is minted and saved 0600 (control)"
  else
    fail "'${_v:-flag unset}': conf.sh no longer persists SEARXNG_SECRET"
  fi
done
# An existing secret is READ under the flag — read-only is not "blank".
_S2="$(_ro_src OB_CONF_READONLY=1)"
[[ -n "$_S" && "$_S2" == "$_S" ]] && pass "OB_CONF_READONLY=1 still reads a secret that is already there" \
  || fail "OB_CONF_READONLY=1 dropped an existing SEARXNG_SECRET"
# --restart may `docker compose up`, which needs the secret: not read-only.
_ro_fresh
_run "$_RO" "$_RO/scripts/healthcheck.sh" --restart >/dev/null
grep -q '^SEARXNG_SECRET=' "$_RO/openbeast.conf" 2>/dev/null \
  && pass "healthcheck.sh --restart keeps the writable behaviour (compose needs the secret)" \
  || fail "healthcheck.sh --restart ran without a SearXNG secret"

# ---------------------------------------------------------------------------
# UX-17 (2026-10-09): on a stack that is simply not running, doctor printed a
# "not responding" row per service, each with a different fix, and never the
# one sentence that was true; healthcheck ended on a count and no next step.
# ---------------------------------------------------------------------------
echo ""
echo "a stack that is not running is said once, with the one fix:"
_N="$_T/down"; _sandbox "$_N"
# Its own hardware: doctor's GPU rows must not depend on the card (or the lack
# of one) in the box running this test.
printf 'ob_detect_gpu() { OB_GPU_VENDOR=nvidia; OB_GPU_NAME="Stub 32G"; OB_VRAM_MB=32000; }\n' > "$_N/scripts/lib/hardware.sh"
# _rc <dir> <script> [args] — like _run, but stdout+stderr in $_O and the
# script's OWN exit code in $_RC (never `cmd | grep` on it under pipefail).
_rc() {
  local d="$1"; shift
  _RC=0
  _O="$(env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" ${RUN_ENV[@]+"${RUN_ENV[@]}"} bash "$@" 2>&1)" || _RC=$?
}
_per_service='llama.cpp server not responding|identity tool server not responding|Open WebUI not responding|beast-chat enabled but not responding|beast-gate not responding|beast-artifact not responding'
printf '%s\n' BEAST_CHAT=true BEAST_ARTIFACT=true EDGE_GATE=true > "$_N/openbeast.conf"
_rc "$_N" "$_N/scripts/doctor.sh"
if [[ "$(grep -c 'Stack is not running' <<< "$_O")" == "1" ]] \
   && grep -qxF "  ! Stack is not running — start it: ./start.sh -d" <<< "$_O" \
   && ! grep -qE "$_per_service" <<< "$_O"; then
  pass "doctor: one 'Stack is not running — start it: ./start.sh -d' line replaces six per-service rows"
else
  fail "doctor on a stopped stack: $(grep -E "Stack is not|$_per_service" <<< "$_O" | tr '\n' ' ')"
fi
if [[ "$(tail -n1 <<< "$_O")" == "Next: ./start.sh -d" && $_RC -eq 0 ]]; then
  pass "…it ends on 'Next: ./start.sh -d', and a stopped stack is still exit 0 (warnings only)"
else
  fail "doctor's last line / exit on a stopped stack: '$(tail -n1 <<< "$_O")' rc=$_RC"
fi
echo "2026-10-09T08:00:00 ./stop.sh" > "$_N/.run/stopped"
_rc "$_N" "$_N/scripts/doctor.sh"
grep -qxF "  ! Stack is not running (stopped on purpose 2026-10-09T08:00:00) — start it: ./start.sh -d" <<< "$_O" \
  && pass "…with ./stop.sh's marker it says when it was stopped on purpose" \
  || fail "stopped-on-purpose line: $(grep 'Stack is' <<< "$_O")"
echo "2026-10-09T08:05:00 supervisor gave up: llama-server exited 4 times (status 1)" > "$_N/.run/stopped"
_rc "$_N" "$_N/scripts/doctor.sh"
grep -qF "Stack is not running (it gave up 2026-10-09T08:05:00: supervisor gave up: llama-server exited 4 times (status 1) — see .run/stack.log) — start it: ./start.sh -d" <<< "$_O" \
  && pass "…and a supervisor that GAVE UP is not called 'on purpose' (reason + .run/stack.log named)" \
  || fail "gave-up line: $(grep 'Stack is' <<< "$_O")"
rm -f "$_N/.run/stopped"
# Negative control 1: a live supervisor whose services do not answer yet
# (starting, or broken) is NOT "not running" — every row is shown.
bash -c 'sleep 60; :' start.sh &   # `; :` keeps bash (and "start.sh") on the command line
_SUP=$!; _PIDS="$_PIDS $_SUP"
echo "$_SUP" > "$_N/.run/supervisor.pid"
_rc "$_N" "$_N/scripts/doctor.sh"
if ! grep -q "Stack is not running" <<< "$_O" && [[ "$(grep -cE "$_per_service" <<< "$_O")" == "6" ]]; then
  pass "with a live supervisor the per-service rows are all shown (control)"
else
  fail "live supervisor: $(grep -cE "$_per_service" <<< "$_O") rows, headline: $(grep 'Stack is' <<< "$_O")"
fi
kill "$_SUP" 2>/dev/null || true
rm -f "$_N/.run/supervisor.pid"
# Negative control 2: no supervisor, but the core answers (the watchdog
# relaunched it) — a row that is down is a real row.
cat > "$_N/bin/curl" <<'SH'
#!/bin/bash
url=""; w=0
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; [[ "$a" == "%{http_code}" ]] && w=1; done
case "$url" in
  *:8080/*|*:3001/*) [[ $w -eq 1 ]] && { printf '200'; exit 0; }; printf '{"status":"ok"}'; exit 0 ;;
esac
[[ $w -eq 1 ]] && printf '000'
exit 7
SH
_rc "$_N" "$_N/scripts/doctor.sh"
if ! grep -q "Stack is not running" <<< "$_O" && grep -q "Open WebUI not responding (:3000)" <<< "$_O" \
   && grep -q "beast-gate not responding" <<< "$_O"; then
  pass "with the core answering, a dead WebUI / gate is still its own warning (control)"
else
  fail "core up: $(grep -E "Stack is not|$_per_service" <<< "$_O" | tr '\n' ' ')"
fi
if [[ "$(tail -n1 <<< "$_O")" == "Next: "* && "$(tail -n1 <<< "$_O")" != "Next: ./start.sh -d" ]]; then
  pass "…and 'Next:' then names a warning's fix, not ./start.sh"
else
  fail "Next line with the core up: '$(tail -n1 <<< "$_O")'"
fi
# A published surface over a dead server stays a FAILURE on a stopped stack
# (the mount is live and 502s; exit 1 is kept) — with the same one fix.
printf '#!/bin/bash\nexit 1\n' > "$_N/bin/curl"
cat > "$_N/bin/tailscale" <<'SH'
#!/bin/bash
if [[ "$1 $2" == "serve status" ]]; then
  printf 'https://beast.example.ts.net:8446 (tailnet only)\n|-- / proxy http://127.0.0.1:3004\n'; exit 0
fi
exit 1
SH
_rc "$_N" "$_N/scripts/doctor.sh"
if grep -qF "✗ :8446 is published but beast-artifact is NOT responding" <<< "$_O" && [[ $_RC -eq 1 ]] \
   && grep -A1 -F ":8446 is published" <<< "$_O" | grep -qF "fix: ./start.sh -d (the stack is not running" \
   && [[ "$(tail -n1 <<< "$_O")" == "Next: ./start.sh -d (the stack is not running"* ]]; then
  pass "a published :8446 over a stopped stack still FAILs (exit 1), and its fix is ./start.sh -d too"
else
  fail "published surface on a stopped stack: rc=$_RC $(grep -A1 -F ':8446' <<< "$_O" | tr '\n' ' ') last='$(tail -n1 <<< "$_O")'"
fi
printf '#!/bin/bash\nexit 1\n' > "$_N/bin/tailscale"

# The shipped default has no leaderboard row on a fresh install (results are
# not checked in): that was a permanent warning nobody could clear.
echo ""
echo "doctor.sh: 'no leaderboard row' is information, not a warning:"
mkdir -p "$_N/evals"
: > "$_N/openbeast.conf"
printf '#!/bin/bash\nexec true\n' > "$_N/scripts/serve-here.sh"
cat > "$_N/evals/benchmark_all.py" <<'PY'
MODELS = [
    {"slug": "here-q5", "name": "Here 27B Q5", "serve": "scripts/serve-here.sh"},
]
PY
echo '{"entries": []}' > "$_N/evals/leaderboard.json"
RUN_ENV=(OPENBEAST_SERVE_SCRIPT=serve-here.sh)
_rc "$_N" "$_N/scripts/doctor.sh"; _A="$_O"
_rc "$_N" "$_N/scripts/doctor.sh" --quiet; _Q="$_O"
if grep -qF "  - default model 'Here 27B Q5' has no leaderboard row on this host" <<< "$_A" \
   && grep -qF "benchmark_all.py --models here-q5" <<< "$_A" \
   && ! grep -qE '^  ! .*leaderboard' <<< "$_A" && ! grep -q "leaderboard" <<< "$_Q"; then
  pass "an unbenchmarked default is an info row with the optional command (and silent under --quiet)"
else
  fail "leaderboard row: $(grep -i leaderboard <<< "$_A" | tr '\n' ' ') quiet=$(grep -ci leaderboard <<< "$_Q")"
fi
RUN_ENV=(OPENBEAST_SERVE_SCRIPT=serve-elsewhere.sh)
_rc "$_N" "$_N/scripts/doctor.sh"
RUN_ENV=()
grep -qE "^  ! default serve script 'serve-elsewhere.sh' is not registered" <<< "$_O" \
  && pass "…a serve script the eval registry does not know is still a warning (control)" \
  || fail "unregistered serve script: $(grep -i 'registered' <<< "$_O")"

echo ""
echo "healthcheck.sh ends on a next step:"
_K="$_T/hcnext"; _sandbox "$_K"
_rc "$_K" "$_K/scripts/healthcheck.sh"
if [[ "$(tail -n1 <<< "$_O")" == "Stack is not running — start it: ./start.sh -d" && $_RC -eq 1 ]] \
   && grep -q "DOWN llama.cpp server" <<< "$_O" && grep -qE '^[0-9]+ of [0-9]+ services unhealthy\.$' <<< "$_O"; then
  pass "nothing answering: the count is followed by 'Stack is not running — start it: ./start.sh -d' (exit 1 kept)"
else
  fail "healthcheck on a stopped stack: rc=$_RC last='$(tail -n1 <<< "$_O")'"
fi
echo "2026-10-09T08:00:00 ./stop.sh" > "$_K/.run/stopped"
_rc "$_K" "$_K/scripts/healthcheck.sh"
[[ "$(tail -n1 <<< "$_O")" == "Stack is not running (stopped on purpose 2026-10-09T08:00:00) — start it: ./start.sh -d" ]] \
  && pass "…naming ./stop.sh's marker when there is one" || fail "healthcheck with a marker: '$(tail -n1 <<< "$_O")'"
# The watchdog (--restart) has already acted: its output gets no extra line.
_rc "$_K" "$_K/scripts/healthcheck.sh" --restart
if ! grep -qE '^(Next:|Stack is not running)' <<< "$_O" && grep -qE 'services unhealthy\.$' <<< "$_O"; then
  pass "--restart output is unchanged: no next-step line after the count"
else
  fail "--restart grew a next-step line: $(grep -E '^(Next:|Stack is not)' <<< "$_O" | tr '\n' ' ')"
fi
rm -f "$_K/.run/stopped"
# The tool server answers, llama does not: not "stopped" — restart what is down.
printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == http*:3001/* ]] && { printf "{\\"status\\":\\"ok\\"}"; exit 0; }; done\nexit 7\n' > "$_K/bin/curl"
_rc "$_K" "$_K/scripts/healthcheck.sh"
if [[ "$(tail -n1 <<< "$_O")" == "Next: ./scripts/healthcheck.sh --restart"* ]] && ! grep -q "Stack is not running" <<< "$_O"; then
  pass "partly down (tool server up): 'Next: ./scripts/healthcheck.sh --restart' instead (control)"
else
  fail "partly-down next step: '$(tail -n1 <<< "$_O")'"
fi
# Everything answers: no next step at all.
printf '#!/bin/bash\n[[ "$1" == status ]] && { echo "{\\"Self\\":{\\"Online\\":true}}"; exit 0; }\nexit 1\n' > "$_K/bin/tailscale"
printf '#!/bin/bash\nprintf "{\\"status\\":\\"ok\\",\\"version\\":\\"x\\"} searx"\nexit 0\n' > "$_K/bin/curl"
_rc "$_K" "$_K/scripts/healthcheck.sh"
if [[ $_RC -eq 0 ]] && grep -qE '^All [0-9]+ services healthy\.$' <<< "$_O" && ! grep -qE '^(Next:|Stack is not)' <<< "$_O"; then
  pass "all healthy: exit 0 and no next-step line (control)"
else
  fail "healthy stack: rc=$_RC $(tail -n2 <<< "$_O" | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# UX-14 (2026-10-09): doctor's fix for a missing pinned package was a bare
# `pip install --user -r agents/requirements.txt` — refused by PEP 668 on
# Arch / Debian 12+ / Ubuntu 24.04, and outside the hash-pinned lock.
# ---------------------------------------------------------------------------
echo ""
echo "doctor.sh's missing-dependency hint:"
_P="$_T/pydeps"; _sandbox "$_P"
mkdir -p "$_P/agents"
printf 'obnotapackage==1.2.3\n' > "$_P/agents/requirements.txt"
# python3 -m pip show: "not installed" for everything; any other python3 call
# goes to the real interpreter.
printf '#!/bin/bash\n[[ "$1 $2 $3" == "-m pip show" ]] && exit 1\nexec /usr/bin/python3 "$@"\n' > "$_P/bin/python3"
chmod +x "$_P/bin/python3"
_O="$(_run "$_P" "$_P/scripts/doctor.sh")"
if grep -qF "✗ obnotapackage not installed (pinned 1.2.3)" <<< "$_O" \
   && grep -A1 -F "obnotapackage not installed" <<< "$_O" | grep -qF "fix: ./scripts/pydeps.sh install"; then
  pass "a missing pinned package points at ./scripts/pydeps.sh install"
else
  fail "missing-dep hint: $(grep -A1 -F 'obnotapackage' <<< "$_O" | tr '\n' ' ')"
fi
if grep -q 'pip install --user -r' <<< "$_O"; then
  fail "doctor still recommends a bare 'pip install --user -r' (PEP 668 refuses it)"
else
  pass "…and no longer recommends a bare 'pip install --user -r'"
fi

# ---------------------------------------------------------------------------
# UX-13 (2026-10-09): doctor shows conf.sh's lint findings as rows. The
# parsing itself is pinned in tests/test_conf_secrets.sh §7; this is the
# "surfaced in doctor, as a warning, once" half.
# ---------------------------------------------------------------------------
echo ""
echo "doctor.sh surfaces openbeast.conf typos and bad values:"
_L="$_T/lint"; _sandbox "$_L"
cp "$REPO_DIR/openbeast.conf.example" "$_L/"
printf 'SEARXNG_SECRET=s\nEDGE_GTAE=true\nREASONING_BUDGET=lots\nSERVE_SCRIPT=serve-nope.sh\n' > "$_L/openbeast.conf"
chmod 600 "$_L/openbeast.conf"
_O="$(_run "$_L" "$_L/scripts/doctor.sh")"
if grep -qF "! openbeast.conf: unknown key 'EDGE_GTAE' — did you mean EDGE_GATE?" <<< "$_O" \
   && grep -qF "! REASONING_BUDGET='lots' is not an integer" <<< "$_O" \
   && grep -qF "! openbeast.conf: SERVE_SCRIPT='serve-nope.sh' names no file in scripts/" <<< "$_O"; then
  pass "doctor rows: the typo'd key (with its suggestion), the non-integer budget, the missing serve script"
else
  fail "doctor did not surface the conf problems: $(grep -iE 'unknown|REASONING|SERVE_SCRIPT' <<< "$_O" | tr '\n' ' ')"
fi
if [[ "$(grep -c "EDGE_GTAE" <<< "$_O")" == "1" ]] && ! grep -q "^WARNING: openbeast.conf" <<< "$_O"; then
  pass "…each said once, as a row (conf.sh's own stderr copy is switched off under doctor)"
else
  fail "doctor repeated the lint: $(grep -c EDGE_GTAE <<< "$_O") line(s) mention the typo"
fi
_verdict() { sed -n 's/^doctor: [0-9]* ok, \([0-9]*\) warning(s), \([0-9]*\) failure(s).*/\1 \2/p' <<< "$1"; }
read -r _LW _LF <<< "$(_verdict "$_O")"
printf 'SEARXNG_SECRET=s\nEDGE_GATE=false\n' > "$_L/openbeast.conf"
_O="$(_run "$_L" "$_L/scripts/doctor.sh")"
read -r _CW _CF <<< "$(_verdict "$_O")"
if grep -qF "✓ openbeast.conf: no unknown keys" <<< "$_O" && ! grep -q "unknown key '" <<< "$_O"; then
  pass "a clean conf gets one green row and no warning (control)"
else
  fail "clean conf: $(grep -iE 'unknown' <<< "$_O" | tr '\n' ' ')"
fi
# Same sandbox, same everything else: the three findings add exactly three
# warnings and not one failure.
if [[ -n "${_CW:-}" && "${_LW:-}" == "$((_CW + 3))" && "${_LF:-x}" == "$_CF" ]]; then
  pass "…and they are WARNINGS: +3 warnings, the failure count does not move"
else
  fail "conf findings changed doctor's verdict wrongly: ${_LW:-?}w/${_LF:-?}f with them, ${_CW:-?}w/${_CF:-?}f without"
fi

# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Lifecycle: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
