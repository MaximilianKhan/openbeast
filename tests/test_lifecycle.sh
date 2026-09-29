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
_O="$(_doctor_out '^http://192\.0\.2\.9:' 'BIND_HOST=192.0.2.9')"
if grep -q "model endpoint (http://localhost:8080/v1) refuses connections" <<< "$_O"; then
  pass "doctor FAILs when llama answers on a LAN BIND_HOST but WebUI's localhost model URL is refused"
else
  fail "doctor reported green while WebUI cannot reach the model: $(grep -iE 'llama|model endpoint' <<< "$_O" | tr '\n' ' ')"
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
echo ""
echo "================================"
echo "Lifecycle: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
