#!/bin/bash
# Inference-backend tests (docs/DGX_SPARK_PLAN.md): the INFERENCE_* conf keys,
# lib/backend.sh readiness per server, the unmanaged paths of start.sh /
# healthcheck.sh / stop.sh / doctor.sh, the llama-only tool guards, and the
# Spark launch scaffolds (--print and refusals; docker is a recording stub).
#
# Same rules as tests/test_lifecycle.sh: no GPU, no docker, no real stack.
# The only network is a throwaway HTTP stub on an ephemeral 127.0.0.1 port;
# the stack's own probes are pointed at 127.0.0.2 (BIND_HOST), where nothing
# of the real stack listens. pkill/pgrep/docker are stubs that RECORD their
# calls and never touch the host process table. Every process started here
# is reaped by the EXIT trap. Tests that touch hardware detection pin
# OPENBEAST_GPU_BACKEND=cpu.
#
# Usage: bash tests/test_backends.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

_T="$(mktemp -d "${TMPDIR:-/tmp}/obbackendsXXXXXX")"
_PIDS=""
cleanup() {
  local p
  for p in $_PIDS; do kill "$p" 2>/dev/null || true; done
  rm -rf "$_T"
}
trap cleanup EXIT

_free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# ---------------------------------------------------------------------------
# One stub HTTP server, many personalities: GET /<mode>/<path>. Hard lifetime
# so nothing outlives the test even if the harness is killed.
# ---------------------------------------------------------------------------
cat > "$_T/stub.py" <<'PY'
import http.server, json, sys, threading, time, os
port = int(sys.argv[1])
AUTH = {}
MODELS = {"object": "list", "data": [{"id": "Qwen3.8 27B NVFP4 (vLLM TP2)",
                                      "object": "model", "max_model_len": 262144}]}
def answer(mode, path):
    if mode == "llama-ok":        return 200, json.dumps({"status": "ok"})
    if mode == "llama-loading":   return 503, json.dumps({"error": {"code": 503, "message": "Loading model"}})
    if mode == "vllm":
        if path == "health":      return 200, ""
        if path == "v1/models":   return 200, json.dumps(MODELS)
    if mode == "vllm-dead":       return 503, ""
    if mode == "tf":
        if path == "health":      return 200, json.dumps({"ok": True})
        if path == "v1/models":   return 200, json.dumps({"object": "list", "data": [{"id": "local-model"}]})
    if mode == "tf-mlx":          return 200, json.dumps({"status": "ok", "memory": {}})
    if mode == "plain200":        return 200, "hello"
    if mode == "authecho":        # the served id reports the Authorization header
        return 200, json.dumps({"data": [{"id": "AUTH:" + (AUTH.get("h") or "none")}]})
    return 404, json.dumps({"error": "not found"})
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        AUTH["h"] = self.headers.get("Authorization")
        mode, _, path = self.path.lstrip("/").partition("/")
        st, body = answer(mode, path.rstrip("/"))
        b = body.encode()
        self.send_response(st)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(120)
os._exit(0)
PY
_PORT="$(_free_port)"
python3 "$_T/stub.py" "$_PORT" & _PIDS="$_PIDS $!"
_S="http://127.0.0.1:$_PORT"
for _i in $(seq 1 50); do
  curl -s -o /dev/null "$_S/llama-ok/health" 2>/dev/null && break
  sleep 0.1
done
# A port with nothing on it: bound, then released.
_DEAD="http://127.0.0.1:$(_free_port)"

echo "=== Inference backend tests ==="
echo ""

# ---------------------------------------------------------------------------
echo "lib/backend.sh readiness, per server:"
_ready() { # _ready <backend> <base-url> -> prints yes/no
  bash -c "source '$REPO_DIR/scripts/lib/backend.sh'; if ob_backend_ready \"\$1\" \"\$2\"; then echo yes; else echo no; fi" _ "$2" "$1"
}
_expect_ready() { # _expect_ready <want yes|no> <backend> <mode-or-url> <label>
  local url="$3"; [[ "$url" == http* ]] || url="$_S/$3"
  local got; got="$(_ready "$2" "$url")"
  if [[ "$got" == "$1" ]]; then pass "$4"; else fail "$4 (got $got)"; fi
}
_expect_ready yes llama      llama-ok      "llama: 200 {\"status\":\"ok\"} is ready"
_expect_ready no  llama      llama-loading "llama: 503 'Loading model' is NOT ready (negative control — today's semantics kept)"
_expect_ready no  llama      vllm          "llama: an empty 200 is NOT ready under the llama rule (why the adapter exists)"
_expect_ready yes vllm       vllm          "vllm: 200 with an EMPTY body is ready"
_expect_ready no  vllm       vllm-dead     "vllm: 503 (EngineDeadError) is not ready"
_expect_ready no  vllm       "$_DEAD"      "vllm: nothing listening is not ready"
_expect_ready yes tensorfold tf            "tensorfold: 200 {\"ok\": true} (CUDA) is ready"
_expect_ready yes tensorfold tf-mlx        "tensorfold: 200 {\"status\":\"ok\"} (MLX) is ready"
_expect_ready no  tensorfold plain200      "tensorfold: a 200 that does not say ok is not ready"
_expect_ready no  tensorfold "$_DEAD"      "tensorfold: nothing listening (the port stays closed during load) is not ready"
# The backend argument defaults to INFERENCE_BACKEND, then llama.
_got="$(INFERENCE_BACKEND=vllm bash -c "source '$REPO_DIR/scripts/lib/backend.sh'; ob_backend_ready '$_S/vllm' && echo yes || echo no")"
[[ "$_got" == yes ]] && pass "ob_backend_ready follows INFERENCE_BACKEND when no backend is passed" \
  || fail "ob_backend_ready ignored INFERENCE_BACKEND ($_got)"
_got="$(env -u INFERENCE_BACKEND bash -c "source '$REPO_DIR/scripts/lib/backend.sh'; ob_backend_ready '$_S/vllm' && echo yes || echo no")"
[[ "$_got" == no ]] && pass "…and is the llama rule when nothing is set (control)" \
  || fail "unset INFERENCE_BACKEND did not mean llama ($_got)"
_got="$(bash -c "source '$REPO_DIR/scripts/lib/backend.sh'; ob_backend_models '$_S/vllm'")"
[[ "$_got" == "Qwen3.8 27B NVFP4 (vLLM TP2)" ]] && pass "ob_backend_models lists the served id" \
  || fail "ob_backend_models: '$_got'"
if bash -c "source '$REPO_DIR/scripts/lib/backend.sh'; ob_backend_models '$_DEAD'" >/dev/null 2>&1; then
  fail "ob_backend_models succeeded against a dead port"
else
  pass "ob_backend_models fails (rc!=0) when the server is unreachable"
fi
_NA="$(bash -c "set -- keep these; source '$REPO_DIR/scripts/lib/backend.sh'; echo \"\$*\"")"
_bm() { # _bm <backend> -> the id ob_backend_models reports from /authecho
  LLAMA_API_KEY=k-123 INFERENCE_BACKEND="$1" bash -c "source '$REPO_DIR/scripts/lib/curl_auth.sh'; source '$REPO_DIR/scripts/lib/backend.sh'; ob_backend_models '$_S/authecho'"
}
_got="$(_bm vllm)"; [[ "$_got" == "AUTH:Bearer k-123" ]] && pass "ob_backend_models presents LLAMA_API_KEY to vLLM" \
  || fail "vLLM models probe did not send the key: $_got"
_got="$(_bm tensorfold)"; [[ "$_got" == "AUTH:none" ]] && pass "…and never to TensorFold (it has no auth)" \
  || fail "the key was sent to TensorFold: $_got"
[[ "$_NA" == "keep these" ]] && pass "sourcing lib/backend.sh leaves \$@ alone" \
  || fail "sourcing lib/backend.sh clobbered \$@: '$_NA'"

# ---------------------------------------------------------------------------
echo ""
echo "lib/conf.sh INFERENCE_* resolution:"
_SB="$_T/conf"; mkdir -p "$_SB/scripts/lib"
cp "$REPO_DIR"/scripts/lib/*.sh "$_SB/scripts/lib/"
_conf() { # _conf <conf-file-body> <expr> [env...]
  printf '%s\n' "$1" > "$_SB/openbeast.conf"
  local expr="$2"; shift 2
  env -i HOME="$_SB" PATH=/usr/bin:/bin OPENBEAST_SEARXNG_SECRET=x "$@" \
    bash -c "REPO_DIR='$_SB'; source '$_SB/scripts/lib/conf.sh' 2>'$_T/conf.err'; $expr"
}
_c="$(_conf "" 'echo "$INFERENCE_BACKEND|$INFERENCE_URL|$INFERENCE_MANAGED|$MODEL_URL|${OPENBEAST_INFERENCE_URL-unset}|${OPENBEAST_AGENT_INFERENCE_URL-unset}"')"
if [[ "$_c" == "llama|http://127.0.0.1:8080|true|http://localhost:8080/v1|unset|unset" ]]; then
  pass "defaults: llama, local :8080, managed, MODEL_URL unchanged, nothing extra exported"
else
  fail "defaults drifted: $_c"
fi
_c="$(_conf "" 'echo "$INFERENCE_URL|$MODEL_URL"' OPENBEAST_BIND=192.168.1.20)"
[[ "$_c" == "http://192.168.1.20:8080|http://192.168.1.20:8080/v1" ]] \
  && pass "default INFERENCE_URL follows the probe host (== today's LLAMA_BASE)" || fail "probe-host default: $_c"
_c="$(_conf $'INFERENCE_BACKEND=vllm   # the sparks\nINFERENCE_URL=http://10.0.0.5:8000/v1/' \
  'echo "$INFERENCE_BACKEND|$INFERENCE_URL|$INFERENCE_MANAGED|$MODEL_URL|$OPENBEAST_INFERENCE_URL|$OPENBEAST_AGENT_INFERENCE_URL|$OPENBEAST_INFERENCE_BACKEND"')"
if [[ "$_c" == "vllm|http://10.0.0.5:8000|false|http://10.0.0.5:8000/v1|http://10.0.0.5:8000|http://10.0.0.5:8000/v1|vllm" ]]; then
  pass "vllm: comment + trailing /v1/ stripped, unmanaged by default, MODEL_URL and agents follow INFERENCE_URL"
else
  fail "vllm resolution: $_c"
fi
_c="$(_conf $'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nAGENT_INFERENCE_URL=http://worker:8443/v1' 'echo "$OPENBEAST_AGENT_INFERENCE_URL"')"
[[ "$_c" == "http://worker:8443/v1" ]] && pass "an explicit AGENT_INFERENCE_URL still wins" || fail "AGENT_INFERENCE_URL override: $_c"
_c="$(_conf $'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nAGENT_ROUTER=true' 'echo "$MODEL_URL"')"
[[ "$_c" == "http://localhost:8088/v1" ]] && pass "AGENT_ROUTER=true keeps WebUI on the router (its upstream follows INFERENCE_URL)" \
  || fail "router MODEL_URL: $_c"
_c="$(_conf 'INFERENCE_BACKEND=Ollama' 'echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED"')"
if [[ "$_c" == "llama|true" ]] && grep -q "INFERENCE_BACKEND='Ollama' is not one of" "$_T/conf.err"; then
  pass "junk INFERENCE_BACKEND warns and falls back to llama (managed)"
else
  fail "junk backend: $_c / $(cat "$_T/conf.err")"
fi
_c="$(_conf $'INFERENCE_BACKEND=tensorfold\nINFERENCE_URL=http://10.0.0.5:8080\nINFERENCE_MANAGED=true' 'echo "$INFERENCE_MANAGED"')"
if [[ "$_c" == "false" ]] && grep -q "INFERENCE_MANAGED=true is not supported" "$_T/conf.err"; then
  pass "INFERENCE_MANAGED=true on tensorfold warns and is forced false"
else
  fail "managed tensorfold: $_c"
fi
_c="$(_conf $'INFERENCE_URL=http://10.9.9.9:8080\nINFERENCE_MANAGED=no' 'echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED|$MODEL_URL"')"
[[ "$_c" == "llama|false|http://10.9.9.9:8080/v1" ]] && pass "llama on another box: INFERENCE_MANAGED=no is honoured" \
  || fail "external llama: $_c"
_c="$(_conf 'INFERENCE_URL=http://10.9.9.9:8080' 'echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED"')"
if [[ "$_c" == "llama|false" ]] && grep -q "is another machine — treating its llama-server as not managed" "$_T/conf.err"; then
  pass "llama + a REMOTE INFERENCE_URL defaults to unmanaged, with a notice"
else
  fail "remote llama URL stayed managed: $_c / $(cat "$_T/conf.err")"
fi
_c="$(_conf 'INFERENCE_URL=http://[fd7a::9]:8080' 'echo "$INFERENCE_MANAGED"')"
[[ "$_c" == "false" ]] && pass "…an IPv6 remote host too" || fail "IPv6 remote: $_c"
_c="$(_conf $'INFERENCE_URL=http://10.9.9.9:8080\nINFERENCE_MANAGED=true' 'echo "$INFERENCE_MANAGED"')"
if [[ "$_c" == "true" ]] && grep -q "CONFLICTING CONFIG — INFERENCE_MANAGED=true" "$_T/conf.err"; then
  pass "explicit INFERENCE_MANAGED=true with a remote URL is kept but warned about loudly"
else
  fail "no conflict warning: $_c / $(cat "$_T/conf.err")"
fi
for _local in http://127.0.0.1:8080 http://localhost:9000 "http://[::1]:8080"; do
  _c="$(_conf "INFERENCE_URL=$_local" 'echo "$INFERENCE_MANAGED"')"
  if [[ "$_c" == "true" && ! -s "$_T/conf.err" ]]; then
    pass "a local INFERENCE_URL ($_local) stays managed, silently (control)"
  else
    fail "local URL $_local: $_c / $(cat "$_T/conf.err")"
  fi
done
_c="$(_conf 'INFERENCE_URL=http://192.168.1.20:8080' 'echo "$INFERENCE_MANAGED"' OPENBEAST_BIND=192.168.1.20)"
[[ "$_c" == "true" ]] && pass "an INFERENCE_URL on this box's own BIND_HOST stays managed" || fail "BIND_HOST URL: $_c"
_c="$(_conf 'INFERENCE_URL=10.0.0.5:8000' 'echo "$INFERENCE_URL"')"
if [[ "$_c" == "http://127.0.0.1:8080" ]] && grep -q "is not an http(s):// URL" "$_T/conf.err"; then
  pass "a scheme-less INFERENCE_URL warns and falls back to the local default"
else
  fail "bad URL: $_c"
fi
_c="$(_conf 'INFERENCE_SLOTS=eight' 'echo "[$INFERENCE_SLOTS]|${OPENBEAST_INFERENCE_SLOTS-unset}"')"
if [[ "$_c" == "[]|unset" ]] && grep -q "INFERENCE_SLOTS='eight'" "$_T/conf.err"; then
  pass "a non-integer INFERENCE_SLOTS warns and is ignored"
else
  fail "bad slots: $_c"
fi
_c="$(_conf 'INFERENCE_SLOTS=8  # max-num-seqs' 'echo "$OPENBEAST_INFERENCE_SLOTS"')"
[[ "$_c" == "8" ]] && pass "INFERENCE_SLOTS=8 (with a comment) is exported" || fail "slots: $_c"
_c="$(_conf 'INFERENCE_BACKEND=vllm' 'echo "$INFERENCE_URL"')"
grep -q "INFERENCE_URL is not set" "$_T/conf.err" && pass "vllm without INFERENCE_URL warns" \
  || fail "no warning for vllm without INFERENCE_URL ($_c)"
_c="$(_conf 'INFERENCE_BACKEND=vllm' 'echo "$INFERENCE_BACKEND"' OPENBEAST_INFERENCE_BACKEND=llama)"
[[ "$_c" == "llama" ]] && pass "env OPENBEAST_INFERENCE_BACKEND beats the conf" || fail "env precedence: $_c"

# ---------------------------------------------------------------------------
# Sandbox rigs for the lifecycle scripts. Real curl (probes go to the stub on
# 127.0.0.1 or to 127.0.0.2, where nothing listens); every other host tool is
# a stub. pkill/pgrep/docker RECORD their argv to calls.log and fail.
# ---------------------------------------------------------------------------
_rig() { # _rig <dir>
  local d="$1" c
  mkdir -p "$d/scripts/lib" "$d/.run" "$d/bin" "$d/home" "$d/extensions"
  cp "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$d/"
  cp "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/doctor.sh" "$d/scripts/"
  cp "$REPO_DIR"/scripts/lib/*.sh "$d/scripts/lib/"
  : > "$d/openbeast.conf"
  : > "$d/calls.log"
  for c in docker tailscale nvidia-smi sudo systemctl systemd-run smartctl pkill pgrep killall; do
    printf '#!/bin/bash\necho "%s $*" >> "%s/calls.log"\nexit 1\n' "$c" "$d" > "$d/bin/$c"
    chmod +x "$d/bin/$c"
  done
  # The serve script start.sh / healthcheck would launch: it only leaves a
  # marker. Its execution in an unmanaged run is the failure under test.
  printf '#!/bin/bash\necho "serve-marker $0" >> "%s/calls.log"\nexit 7\n' "$d" > "$d/scripts/serve-marker.sh"
  chmod +x "$d/scripts/serve-marker.sh"
}
_run() { # _run <dir> <timeout-s> <script> [args...]   (extra env via RUN_ENV)
  local d="$1" t="$2"; shift 2
  env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x \
    OPENBEAST_BIND=127.0.0.2 OPENBEAST_GPU_BACKEND=cpu OPENBEAST_LOGROTATE_AUTOINSTALL=false \
    OPENBEAST_SERVE_SCRIPT=serve-marker.sh \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} timeout "$t" bash "$@" 2>&1 || true
}
_llama_kills() { grep -E '^(pkill|pgrep|killall) .*llama-server' "$1/calls.log" || true; }

echo ""
echo "start.sh with an unmanaged backend:"
_R="$_T/start-dead"; _rig "$_R"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_DEAD" OPENBEAST_LLAMA_LOAD_GRACE=3 OPENBEAST_FAST_BOOT=true)
_t0=$SECONDS
_O="$(_run "$_R" 40 "$_R/start.sh")"
if grep -q "inference backend NOT ready at $_DEAD after 3s" <<< "$_O"; then
  pass "a remote backend that never answers is reported NOT ready after the grace, naming INFERENCE_URL"
else
  fail "no NOT-ready warning: $(tail -n 5 <<< "$_O")"
fi
if grep -q "identity tool server" <<< "$_O"; then
  pass "…and start.sh brings the rest of the stack up anyway (tool server next), not exit"
else
  fail "start.sh stopped at an unready remote backend: $(tail -n 5 <<< "$_O")"
fi
(( SECONDS - _t0 < 30 )) && pass "…within the grace (OPENBEAST_LLAMA_LOAD_GRACE), not the 900 s default" \
  || fail "unmanaged wait ignored the grace ($((SECONDS - _t0)) s)"
if grep -q serve-marker "$_R/calls.log"; then
  fail "start.sh executed a serve script on an unmanaged backend"
else
  pass "no serve script is executed (nothing launched)"
fi
[[ -z "$(_llama_kills "$_R")" ]] && pass "no pkill/pgrep of llama-server" \
  || fail "unmanaged start.sh reached for llama-server: $(_llama_kills "$_R")"
grep -q "FAST_BOOT: not applicable for INFERENCE_BACKEND=vllm" <<< "$_O" \
  && pass "FAST_BOOT prints 'not applicable' instead of launching the bridge" || fail "no FAST_BOOT n/a line"
grep -q "Model rollback: not applicable" <<< "$_O" && pass "rollback prints 'not applicable'" || fail "no rollback n/a line"
[[ ! -f "$_R/.run/serve-script" ]] && pass "no .run/serve-script record for a server this stack does not run" \
  || fail ".run/serve-script written on an unmanaged stack"

_R="$_T/start-ok"; _rig "$_R"
echo "serve-stale.sh" > "$_R/.run/serve-script"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_S/vllm" OPENBEAST_LLAMA_LOAD_GRACE=3)
_O="$(_run "$_R" 40 "$_R/start.sh")"
if grep -q "vLLM ready at $_S/vllm" <<< "$_O"; then
  pass "an empty-200 vLLM passes readiness and the stack carries on (tool server next)"
else
  fail "vLLM readiness in start.sh: $(tail -n 5 <<< "$_O")"
fi
grep -q "KV-cache warming: not applicable" <<< "$_O" && pass "KV warming prints 'not applicable'" || fail "no KV warming n/a line"
grep -q "identity tool server" <<< "$_O" && pass "…and goes on to bring up the tool server" || fail "stopped before the tool server"
grep -q serve-marker "$_R/calls.log" && fail "serve script executed on the ready path" \
  || pass "still nothing launched on the ready path"
[[ ! -f "$_R/.run/serve-script" ]] && pass "a stale serve-script record is cleared" || fail "stale serve-script kept"

# start.sh -d against a DOWN remote backend: the launcher must report the
# stack up with inference NOT ready (exit 0), never claim readiness. The
# sandbox's "tool server" is a stub that answers /health on 127.0.0.2:3001
# (where nothing of the real stack binds); the detached supervisor is
# stopped afterwards through its own recorded pid.
_R="$_T/start-daemon"; _rig "$_R"; mkdir -p "$_R/agents"
cat > "$_R/agents/openapi_tools.py" <<'PY'
import http.server, threading, time, os
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = b'{"status":"ok"}'
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
srv = http.server.HTTPServer(("127.0.0.2", 3001), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(60); os._exit(0)
PY
# start.sh checks `import fastapi, uvicorn` first; HOME is the sandbox's, so
# the user site-packages are invisible — empty stand-ins satisfy the check.
mkdir -p "$_R/pylib"; : > "$_R/pylib/fastapi.py"; : > "$_R/pylib/uvicorn.py"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_DEAD" OPENBEAST_LLAMA_LOAD_GRACE=2
  "PYTHONPATH=$_R/pylib")
_rc=0
_O="$(env -i HOME="$_R/home" PATH="$_R/bin:/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x \
  OPENBEAST_BIND=127.0.0.2 OPENBEAST_GPU_BACKEND=cpu OPENBEAST_LOGROTATE_AUTOINSTALL=false \
  "${RUN_ENV[@]}" timeout 60 bash "$_R/start.sh" -d 2>&1)" || _rc=$?
_sup="$(cat "$_R/.run/supervisor.pid" 2>/dev/null || true)"
_tool="$(cat "$_R/.run/mcpo.pid" 2>/dev/null || true)"
[[ "$_tool" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $_tool"
if [[ $_rc -eq 0 ]] && grep -q "Stack is up — but inference is NOT ready" <<< "$_O" \
   && grep -q "NOT READY at $_DEAD" <<< "$_O"; then
  pass "start.sh -d with the Sparks down: 'stack up, inference NOT ready', exit 0"
else
  fail "-d launcher (rc=$_rc): $(tail -n 8 <<< "$_O")"
fi
grep -qE "^Stack is up:$" <<< "$_O" && fail "-d claimed a plain 'Stack is up' with inference down" \
  || pass "…and never prints the plain 'Stack is up:' claim"
# Stop the detached supervisor: it is OURS (recorded, and its command line
# names this sandbox), and its trap reaps the stub tool server.
if [[ "$_sup" =~ ^[0-9]+$ ]] && tr '\0' ' ' < "/proc/$_sup/cmdline" 2>/dev/null | grep -qF "$_R/start.sh"; then
  kill "$_sup" 2>/dev/null || true
  for _i in $(seq 1 20); do kill -0 "$_sup" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$_sup" 2>/dev/null && fail "detached supervisor $_sup did not stop" || pass "the detached supervisor stops cleanly on TERM"
else
  fail "no live sandbox supervisor recorded ($_sup)"
fi

# llama on ANOTHER box (192.0.2.1 = TEST-NET-1, never routed): unmanaged by
# default, so no local llama-server is launched while waiting on it.
_R="$_T/start-remote-lm"; _rig "$_R"
RUN_ENV=(OPENBEAST_INFERENCE_URL=http://192.0.2.1:8080 OPENBEAST_LLAMA_LOAD_GRACE=2)
_O="$(_run "$_R" 40 "$_R/start.sh")"
if ! grep -q serve-marker "$_R/calls.log" && [[ -z "$(_llama_kills "$_R")" ]] \
   && grep -q "not managed here" <<< "$_O"; then
  pass "llama at a remote INFERENCE_URL: start.sh launches no local llama-server"
else
  fail "remote llama URL launched/killed locally: $(cat "$_R/calls.log") / $(tail -n 4 <<< "$_O")"
fi

_R="$_T/start-managed"; _rig "$_R"
RUN_ENV=(OPENBEAST_LLAMA_LOAD_GRACE=3)
_O="$(_run "$_R" 40 "$_R/start.sh")"
grep -q serve-marker "$_R/calls.log" && pass "control: the default (llama, managed) still executes the serve script" \
  || fail "control: managed llama did not launch its serve script: $(tail -n 5 <<< "$_O")"

# ---------------------------------------------------------------------------
echo ""
echo "healthcheck.sh --restart never touches an unmanaged backend:"
_R="$_T/hc"; _rig "$_R"
echo "serve-marker.sh" > "$_R/.run/serve-script"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_DEAD")
_O="$(_run "$_R" 60 "$_R/scripts/healthcheck.sh" --restart)"
grep -q "DOWN vLLM (vllm @ $_DEAD)" <<< "$_O" && pass "a down vLLM is reported DOWN" || fail "no DOWN line: $(head -n 8 <<< "$_O")"
grep -q "not restarting: INFERENCE_MANAGED=false" <<< "$_O" && pass "…and not restarted" || fail "no not-restarting line"
[[ -z "$(_llama_kills "$_R")" ]] && pass "no pkill/pgrep of llama-server" || fail "healthcheck reached for llama: $(_llama_kills "$_R")"
grep -q serve-marker "$_R/calls.log" && fail "healthcheck --restart launched a serve script for an unmanaged backend" \
  || pass "no serve script launched"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_S/vllm")
_O="$(_run "$_R" 60 "$_R/scripts/healthcheck.sh")"
grep -q "OK   vLLM (vllm @ $_S/vllm)" <<< "$_O" && pass "a healthy vLLM (empty 200) reads OK, not DOWN" || fail "healthy vLLM: $(head -n 8 <<< "$_O")"
grep -q "Slots:" <<< "$_O" && fail "llama /slots line printed for vLLM" || pass "no llama /slots line for vLLM"

_R="$_T/hc-remote-lm"; _rig "$_R"
echo "serve-marker.sh" > "$_R/.run/serve-script"
RUN_ENV=(OPENBEAST_INFERENCE_URL=http://192.0.2.1:8080)
_O="$(_run "$_R" 60 "$_R/scripts/healthcheck.sh" --restart)"
if grep -q "not restarting: INFERENCE_MANAGED=false" <<< "$_O" && ! grep -q serve-marker "$_R/calls.log" \
   && [[ -z "$(_llama_kills "$_R")" ]]; then
  pass "llama at a remote INFERENCE_URL that is down: --restart kills and relaunches nothing locally"
else
  fail "remote llama down → local restart: $(cat "$_R/calls.log") / $(head -n 8 <<< "$_O")"
fi

_R="$_T/hc-managed"; _rig "$_R"
echo "serve-marker.sh" > "$_R/.run/serve-script"
RUN_ENV=()
_O="$(_run "$_R" 60 "$_R/scripts/healthcheck.sh" --restart)"
grep -q serve-marker "$_R/calls.log" && pass "control: a managed llama that is down IS relaunched (the path the unmanaged case must avoid)" \
  || fail "control: managed relaunch did not happen: $(head -n 10 <<< "$_O")"

# ---------------------------------------------------------------------------
echo ""
echo "stop.sh leaves an unmanaged backend alone:"
_R="$_T/stop"; _rig "$_R"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=tensorfold "OPENBEAST_INFERENCE_URL=$_S/tf")
_O="$(_run "$_R" 60 "$_R/stop.sh")"
grep -q "Leaving the inference server alone: TensorFold at $_S/tf" <<< "$_O" && pass "stop.sh says it leaves TensorFold alone" \
  || fail "no leave-alone line: $(tail -n 5 <<< "$_O")"
[[ -z "$(_llama_kills "$_R")" ]] && pass "no llama-server pkill" || fail "stop.sh swept llama-server: $(_llama_kills "$_R")"
_R="$_T/stop-managed"; _rig "$_R"
RUN_ENV=()
_run "$_R" 60 "$_R/stop.sh" >/dev/null
[[ -n "$(_llama_kills "$_R")" ]] && pass "control: a managed stop still sweeps this repo's llama-server" \
  || fail "control: managed stop.sh did not sweep llama-server"

# ---------------------------------------------------------------------------
echo ""
echo "doctor.sh on an unmanaged backend:"
_R="$_T/doc"; _rig "$_R"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_S/vllm")
_O="$(_run "$_R" 120 "$_R/scripts/doctor.sh")"
grep -q "vLLM at $_S/vllm (not managed here) — serving: Qwen3.8 27B NVFP4" <<< "$_O" \
  && pass "doctor reports the remote vLLM ready and what it serves" || fail "doctor vLLM row: $(grep -i vllm <<< "$_O" || true)"
grep -q "the GGUF weight registry / WEIGHT_ENFORCE: not applicable for INFERENCE_BACKEND=vllm" <<< "$_O" \
  && pass "weight registry row says 'not applicable'" || fail "no weight-registry n/a row"
grep -q "llama.cpp server" <<< "$_O" && fail "doctor still probes llama.cpp on a vLLM stack" || pass "no llama.cpp row"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=tensorfold "OPENBEAST_INFERENCE_URL=http://10.66.0.1:1")
_O="$(_run "$_R" 120 "$_R/scripts/doctor.sh")"
grep -q "TensorFold has no API key" <<< "$_O" && pass "doctor warns that a remote TensorFold is unauthenticated" \
  || fail "no TensorFold no-key warning"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=http://[::1]:1")
_O="$(_run "$_R" 120 "$_R/scripts/doctor.sh")"
grep -q "reached without a key" <<< "$_O" && fail "doctor called http://[::1] a remote keyless backend" \
  || pass "doctor treats an http://[::1] INFERENCE_URL as loopback (no keyless warning)"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=http://10.66.0.1:1")
_O="$(_run "$_R" 120 "$_R/scripts/doctor.sh")"
grep -q "reached without a key" <<< "$_O" && pass "…while a remote keyless vLLM is warned about (control)" \
  || fail "no keyless warning for a remote vLLM"

echo ""
echo "start.sh --status on an unmanaged stack:"
_R="$_T/status"; _rig "$_R"
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_S/vllm")
_O="$(_run "$_R" 30 "$_R/start.sh" --status)"
if grep -q "inference: vLLM at $_S/vllm — ready (not managed here)" <<< "$_O" && ! grep -q "llama: not running" <<< "$_O"; then
  pass "--status shows the backend and its readiness, not 'llama: not running'"
else
  fail "--status unmanaged: $_O"
fi
RUN_ENV=(OPENBEAST_INFERENCE_BACKEND=vllm "OPENBEAST_INFERENCE_URL=$_DEAD")
_O="$(_run "$_R" 30 "$_R/start.sh" --status)"
grep -q "inference: vLLM at $_DEAD — NOT ready" <<< "$_O" && pass "…and NOT ready when it is down" || fail "--status down: $_O"
RUN_ENV=()
_O="$(_run "$_R" 30 "$_R/start.sh" --status)"
grep -q "llama: not running" <<< "$_O" && pass "control: a managed stack still reports its llama pid line" || fail "--status managed: $_O"

# ---------------------------------------------------------------------------
echo ""
echo "llama-only tools say 'not applicable':"
_R="$_T/tools"; mkdir -p "$_R/scripts/lib"
cp "$REPO_DIR/scripts/measure-vram.sh" "$REPO_DIR"/scripts/profile-*.sh "$_R/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$_R/scripts/lib/"
printf 'INFERENCE_BACKEND=vllm\n' > "$_R/openbeast.conf"
for _tool in measure-vram.sh profile-qwen38-uncensored-mtp.sh profile-heretic-v2-mtp.sh profile-fable-fusion-mtp.sh; do
  _rc=0
  _O="$(env -i HOME="$_R" PATH=/usr/bin:/bin bash "$_R/scripts/$_tool" serve-x.sh 2>&1)" || _rc=$?
  if [[ $_rc -eq 0 && "$_O" == "$_tool: not applicable for INFERENCE_BACKEND=vllm"* ]]; then
    pass "$_tool: one 'not applicable' line, exit 0"
  else
    fail "$_tool (rc=$_rc): $(head -n 2 <<< "$_O")"
  fi
done
_O="$(env -i HOME="$_R" PATH=/usr/bin:/bin INFERENCE_BACKEND=llama bash "$_R/scripts/measure-vram.sh" 2>&1 || true)"
grep -q "Usage: scripts/measure-vram.sh" <<< "$_O" && pass "INFERENCE_BACKEND=llama in the env still runs the tool (control)" \
  || fail "env override did not reach the tool: $(head -n 2 <<< "$_O")"

# gpu-lease on a unified-memory GPU (GB10): nvidia-smi answers [N/A] for
# memory.used. The busy check used to error into /dev/null (fail-open, silent).
mkdir -p "$_R/bin" "$_R/run"
printf '#!/bin/bash\necho "[N/A]"\n' > "$_R/bin/nvidia-smi"; chmod +x "$_R/bin/nvidia-smi"
cp "$REPO_DIR/scripts/gpu-lease.sh" "$_R/scripts/"
_O="$(env -i HOME="$_R" PATH="$_R/bin:/usr/bin:/bin" OPENBEAST_RUN_DIR="$_R/run" bash "$_R/scripts/gpu-lease.sh" status 2>&1 || true)"
if grep -q "GPU memory is not reported (unified-memory GPU" <<< "$_O" && ! grep -q "integer expression" <<< "$_O"; then
  pass "gpu-lease status on a GB10-style [N/A] GPU says the busy check is not applicable"
else
  fail "gpu-lease [N/A]: $_O"
fi
printf '#!/bin/bash\necho "123"\n' > "$_R/bin/nvidia-smi"
_O="$(env -i HOME="$_R" PATH="$_R/bin:/usr/bin:/bin" OPENBEAST_RUN_DIR="$_R/run" bash "$_R/scripts/gpu-lease.sh" status 2>&1 || true)"
if grep -q "GPU: 123 MiB in use" <<< "$_O" && ! grep -q "not reported" <<< "$_O"; then
  pass "…while a card that reports memory is unchanged (control)"
else
  fail "gpu-lease numeric: $_O"
fi
printf '#!/bin/bash\nexit 9\n' > "$_R/bin/nvidia-smi"
_O="$(env -i HOME="$_R" PATH="$_R/bin:/usr/bin:/bin" OPENBEAST_RUN_DIR="$_R/run" bash "$_R/scripts/gpu-lease.sh" status 2>&1 || true)"
if grep -q "nvidia-smi returned no data" <<< "$_O" && ! grep -q "unified-memory" <<< "$_O"; then
  pass "…an nvidia-smi that answers nothing says 'returned no data', not the GB10 note"
else
  fail "gpu-lease no-data: $_O"
fi
# No nvidia-smi at all: a PATH of just the tools the lease uses (the host
# may well have a real nvidia-smi in /usr/bin).
mkdir -p "$_R/bin-nogpu"
for _c in bash head tr awk cat date mkdir mv rm sed grep id flock sleep printf env; do
  _p="$(command -v "$_c" 2>/dev/null || true)"
  [[ -n "$_p" && "$_p" == /* ]] && ln -sf "$_p" "$_R/bin-nogpu/$_c"
done
_O="$(env -i HOME="$_R" PATH="$_R/bin-nogpu" OPENBEAST_RUN_DIR="$_R/run" "$_R/bin-nogpu/bash" "$_R/scripts/gpu-lease.sh" status 2>&1 || true)"
if grep -q "GPU: ? MiB in use" <<< "$_O" && ! grep -qE "not reported|no data" <<< "$_O"; then
  pass "…and with no nvidia-smi at all: 'GPU: ? MiB', no note"
else
  fail "gpu-lease without nvidia-smi: $_O"
fi

# ---------------------------------------------------------------------------
echo ""
echo "Spark launch scaffolds with model profiles (docker stubbed):"
_K="$_T/spark"; mkdir -p "$_K/bin" "$_K/home" "$_K/profiles" "$_K/models"
# docker records argv AND whether VLLM_API_KEY reached its ENVIRONMENT.
cat > "$_K/bin/docker" <<EOF
#!/bin/bash
echo "ARGV \$*" >> "$_K/docker.log"
echo "ENVKEY \${VLLM_API_KEY:-<unset>}" >> "$_K/docker.log"
exit 0
EOF
chmod +x "$_K/bin/docker"
(umask 077; printf 'sk-test-SECRET-4242\n' > "$_K/key")
sed -e "s|^SPARK_SERVE_HOST=.*|SPARK_SERVE_HOST=100.64.1.2|" \
    -e "s|^VLLM_API_KEY_FILE=.*|VLLM_API_KEY_FILE=$_K/key|" \
    -e "s|^MODELS_DIR=.*|MODELS_DIR=$_K/models|" \
    "$REPO_DIR/scripts/backends/spark.env.example" > "$_K/spark.env"
_REV=0123456789abcdef0123456789abcdef01234567
_DREV=89abcdef0123456789abcdef0123456789abcdef
# Profiles: data files, by path. The names are NOT any real model.
cat > "$_K/profiles/vtest.env" <<EOF
BACKEND=vllm
SOURCE=acme/Brand-New-Model-FP8
REVISION=$_REV
SERVED_MODEL_NAME=My Model (test)
TENSOR_PARALLEL_SIZE=2
MAX_MODEL_LEN=131072
GPU_MEMORY_UTILIZATION=0.80
MAX_NUM_SEQS=8
REASONING_PARSER=qwen3
TOOL_CALL_PARSER=hermes
SPECULATIVE_CONFIG={"method":"mtp","num_speculative_tokens":3}
EXTRA_ARGS=["--kv-cache-dtype","fp8","--enable-prefix-caching"]
EOF
sed -e 's/^TENSOR_PARALLEL_SIZE=2/TENSOR_PARALLEL_SIZE=1/' "$_K/profiles/vtest.env" > "$_K/profiles/vtp1.env"
sed -e 's/^SOURCE=.*/SOURCE=acme\/Unfetched-Model/' "$_K/profiles/vtest.env" > "$_K/profiles/vnofetch.env"
{ cat "$_K/profiles/vnofetch.env"; echo "TRUST_REMOTE_CODE=true"; echo "TRUST_REMOTE_CODE_ACK=$_REV"; } > "$_K/profiles/vtrc.env"
sed -e 's/^REVISION=.*/REVISION=main/' "$_K/profiles/vtest.env" > "$_K/profiles/vbranch.env"
cat > "$_K/profiles/tftest.env" <<EOF
BACKEND=tensorfold
SOURCE=acme/Brand-New-Model-MLX-4bit
REVISION=$_REV
SERVED_MODEL_NAME=local-model
TENSOR_PARALLEL_SIZE=2
TENSORFOLD_PARALLEL=auto
EOF
{ cat "$_K/profiles/tftest.env"; echo "DRAFTER_SOURCE=acme/Brand-New-Drafter"; echo "DRAFTER_REVISION=$_DREV"; } > "$_K/profiles/tfd.env"
sed -e 's/^SOURCE=.*/SOURCE=acme\/Unfetched-MLX/' "$_K/profiles/tftest.env" > "$_K/profiles/tfnofetch.env"
# _mkfetched <profile> <artifact:dir:repo:rev>... — a directory + lock exactly
# as model-fetch.sh leaves them (content hashed, marker written).
_mkfetched() {
  python3 - "$_K/models" "$@" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
mdir, prof = Path(sys.argv[1]), Path(sys.argv[2])
arts = {}
for spec in sys.argv[3:]:
    key, d, repo, rev = spec.split(":")
    root = mdir / d
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"model_type": "brand_new"}))
    (root / "model.safetensors").write_bytes(b"\0" * 64)
    (root / ".openbeast-model.json").write_text(json.dumps({"source": repo, "revision": rev}))
    files = {}
    for f in ("config.json", "model.safetensors"):
        b = (root / f).read_bytes()
        files[f] = {"size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "hub_oid": None, "lfs": False}
    arts[key] = {"source": repo, "revision": rev, "dir": d, "files": files}
prof.with_suffix(".lock").write_text(json.dumps({"schema": 1, "artifacts": arts}))
PY
}
_mkfetched "$_K/profiles/vtest.env" "model:vtest:acme/Brand-New-Model-FP8:$_REV"
cp "$_K/profiles/vtest.lock" "$_K/profiles/vtp1.lock"
mkdir -p "$_K/models/vtp1"; cp -r "$_K/models/vtest/." "$_K/models/vtp1/"
_mkfetched "$_K/profiles/tftest.env" "model:tftest:acme/Brand-New-Model-MLX-4bit:$_REV"
_mkfetched "$_K/profiles/tfd.env" "model:tfd:acme/Brand-New-Model-MLX-4bit:$_REV" "drafter:tfd.drafter:acme/Brand-New-Drafter:$_DREV"
_DIGEST="sha256:$(printf 'a%.0s' $(seq 1 64))"
_VN="$REPO_DIR/scripts/backends/vllm/spark-node.sh"
_TN="$REPO_DIR/scripts/backends/tensorfold/spark-node.sh"
_P="$_K/profiles"
_sp() { # _sp <script> [env...] -- [args...]  -> sets _O (output) and SPRC (rc)
  local s="$1"; shift
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift || true
  SPRC=0
  env -i HOME="$_K/home" PATH="$_K/bin:/usr/bin:/bin" ${envs[@]+"${envs[@]}"} \
    bash "$s" "$@" > "$_K/out" 2>&1 || SPRC=$?
  _O="$(cat "$_K/out")"
}
_has() { grep -qF -- "$2" <<< "$1"; }

: > "$_K/docker.log"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--nnodes 2 --node-rank 0 --master-addr 192.168.100.10 --master-port 29501" \
   && _has "$_O" "--tensor-parallel-size 2 --gpu-memory-utilization 0.80" \
   && _has "$_O" "--max-model-len 131072" && _has "$_O" "--max-num-seqs 8" \
   && _has "$_O" "--reasoning-parser qwen3" \
   && _has "$_O" "--enable-auto-tool-choice --tool-call-parser hermes" \
   && _has "$_O" "--served-model-name My\\ Model\\ \\(test\\)" \
   && _has "$_O" "--host 100.64.1.2 --port 8000" \
   && _has "$_O" "--kv-cache-dtype fp8 --enable-prefix-caching"; then
  pass "vllm rank 0 --print: every model setting comes from the PROFILE (parsers, context, seqs, extra args, served name)"
else
  fail "vllm rank 0 --print flags (rc=$SPRC): $_O"
fi
if _has "$_O" "-v $_K/models/vtest:/models/vtest:ro" && _has "$_O" "serve /models/vtest " \
   && ! _has "$_O" "--revision" && ! _has "$_O" "acme/Brand-New-Model-FP8 --"; then
  pass "…serves the fetched, lock-matching copy from MODELS_DIR, mounted READ-ONLY"
else
  fail "fetched model not mounted/served: $_O"
fi
if _has "$_O" "-e NCCL_SOCKET_IFNAME=enp1s0f1np1" && _has "$_O" "-e GLOO_SOCKET_IFNAME=enp1s0f1np1" \
   && _has "$_O" "-e TP_SOCKET_IFNAME=enp1s0f1np1" && _has "$_O" "-e UCX_NET_DEVICES=enp1s0f1np1" \
   && _has "$_O" "-e NCCL_IB_HCA=rocep1s0f1" && _has "$_O" "-e VLLM_SKIP_MODEL_NAME_VALIDATION=1"; then
  pass "…pins every NCCL/Gloo/TP/UCX interface to the ConnectX link, and skips model-name validation"
else
  fail "vllm env flags: $_O"
fi
_has "$_O" '--speculative-config \{\"method\":\"mtp\"\,\"num_speculative_tokens\":3\}' \
  && pass "…passes SPECULATIVE_CONFIG JSON through intact" || fail "speculative config mangled: $_O"
if _has "$_O" "-e VLLM_API_KEY " && ! _has "$_O" "sk-test-SECRET" && ! _has "$_O" "--api-key"; then
  pass "…forwards VLLM_API_KEY by NAME only: the key is not on argv, and --api-key is never used"
else
  fail "API key handling in --print: $_O"
fi
_has "$_O" "--trust-remote-code" && fail "--trust-remote-code passed by default" || pass "…no --trust-remote-code by default"
[[ ! -s "$_K/docker.log" ]] && pass "--print runs nothing (docker never called)" || fail "--print called docker: $(cat "$_K/docker.log")"

_sp "$_VN" -- --profile "$_P/vtest.env" --rank 1 --head-addr 10.0.0.9 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--node-rank 1 --master-addr 10.0.0.9" && _has "$_O" "--headless" \
   && ! _has "$_O" "--host " && ! _has "$_O" "VLLM_API_KEY" && ! _has "$_O" "--served-model-name" \
   && ! _has "$_O" "--tool-call-parser" && _has "$_O" "--max-model-len 131072"; then
  pass "vllm rank 1: --headless worker, --head-addr overrides, no HTTP/parser flags, no key, same engine settings"
else
  fail "vllm rank 1 --print (rc=$SPRC): $_O"
fi
_sp "$_VN" -- --profile "$_P/vnofetch.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "serve acme/Unfetched-Model --revision $_REV --tokenizer-revision $_REV" \
   && _has "$_O" "UNVERIFIED" && ! _has "$_O" "--code-revision"; then
  pass "an unfetched Hub profile is served AT ITS PINNED REVISION, with a loud 'unverified — run model-fetch' warning"
else
  fail "unfetched hub profile (rc=$SPRC): $_O"
fi
_sp "$_VN" -- --profile "$_P/vtrc.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--trust-remote-code" && _has "$_O" "--code-revision $_REV"; then
  pass "TRUST_REMOTE_CODE with an ACK equal to REVISION passes --trust-remote-code, code pinned by --code-revision"
else
  fail "trust_remote_code profile (rc=$SPRC): $_O"
fi
sed -e '/^TRUST_REMOTE_CODE_ACK/d' "$_P/vtrc.env" > "$_P/vtrcnoack.env"
_sp "$_VN" -- --profile "$_P/vtrcnoack.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 1 ]] && _has "$_O" "executes Python shipped in the model repo" && _has "$_O" "Refusing to start"; then
  pass "…and without the ACK the launcher refuses, saying why"
else
  fail "trust_remote_code without ack (rc=$SPRC): $_O"
fi
_sp "$_VN" -- --profile "$_P/vbranch.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "is not a full 40-hex commit SHA" && pass "a profile pinned to a BRANCH is refused" \
  || fail "branch revision accepted (rc=$SPRC): $_O"
_sp "$_VN" -- --profile "$_P/tftest.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "the vllm launcher cannot serve it" && pass "the vLLM launcher refuses a tensorfold profile" \
  || fail "cross-backend profile (rc=$SPRC): $_O"
# A lock that no longer matches the disk: refuse, never serve it.
printf 'x' >> "$_K/models/vtest/model.safetensors"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 1 ]] && _has "$_O" "does not match its lock" && _has "$_O" "MISMATCH"; then
  pass "a fetched copy that no longer matches its lock is refused (not silently served)"
else
  fail "lock mismatch served (rc=$SPRC): $_O"
fi
truncate -s 64 "$_K/models/vtest/model.safetensors"
_sp "$_VN" -- --profile "$_P/vtp1.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--tensor-parallel-size 1" && ! _has "$_O" "--nnodes" \
   && ! _has "$_O" "NCCL_SOCKET_IFNAME" && _has "$_O" "--host 100.64.1.2"; then
  pass "a TP 1 profile runs on one Spark: no multi-node flags, no ConnectX env"
else
  fail "TP1 (rc=$SPRC): $_O"
fi
_sp "$_VN" -- --profile "$_P/vtp1.env" --rank 1 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "runs on ONE Spark" && pass "…and refuses a rank 1 for it" || fail "TP1 rank 1 (rc=$SPRC): $_O"
_sp "$_VN" "SPARK_PROFILE=$_P/vtest.env" -- --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 0 ]] && _has "$_O" "serve /models/vtest" && pass "SPARK_PROFILE supplies the default --profile" \
  || fail "SPARK_PROFILE (rc=$SPRC): $_O"
_sp "$_VN" -- --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 2 ]] && _has "$_O" "--profile NAME is required" && pass "no profile: refused (the launcher knows no model)" \
  || fail "no profile (rc=$SPRC): $_O"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --model org/Other --env "$_K/spark.env" --print
[[ $SPRC -eq 2 ]] && _has "$_O" "--model is gone" && pass "--model is refused with a pointer to profiles" || fail "--model (rc=$SPRC): $_O"
{ cat "$_K/spark.env"; echo "MODEL=org/Old-Default"; echo "TOOL_CALL_PARSER=qwen3_xml"; } > "$_K/legacy.env"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/legacy.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "still sets MODEL TOOL_CALL_PARSER" && _has "$_O" "IGNORED" \
   && ! _has "$_O" "org/Old-Default" && _has "$_O" "--tool-call-parser hermes"; then
  pass "an old spark.env with model keys warns they are IGNORED; the profile wins"
else
  fail "legacy spark.env (rc=$SPRC): $_O"
fi

_sp "$_VN" SPARK_SERVE_HOST=0.0.0.0 -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "SPARK_SERVE_HOST='0.0.0.0'" && pass "refuses a wildcard bind (env beats the file)" \
  || fail "wildcard bind not refused (rc=$SPRC): $_O"
_sp "$_VN" SPARK_SERVE_HOST=0.0.0.0 SPARK_ALLOW_WILDCARD_BIND=true -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 0 ]] && pass "…unless SPARK_ALLOW_WILDCARD_BIND=true acknowledges it (control)" || fail "ack not honoured: $_O"
chmod 644 "$_K/key"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "is mode 644" && pass "refuses a group/world-readable API key file" \
  || fail "0644 key accepted (rc=$SPRC): $_O"
chmod 600 "$_K/key"
_sp "$_VN" VLLM_API_KEY_FILE="$_K/nope" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "does not exist" && pass "refuses a missing API key file" || fail "missing key file: $_O"
_sp "$_VN" -- --profile "$_P/vnofetch.env" --rank 0 --env "$_K/absent.env" --print
if [[ $SPRC -eq 1 ]] && _has "$_O" "VLLM_IMAGE is not set" && _has "$_O" "SPARK_IFACE is not set" && _has "$_O" "Refusing to start"; then
  pass "refuses to start without required host settings, naming each"
else
  fail "no settings file not refused (rc=$SPRC): $_O"
fi
_sp "$_VN" -- --profile "$_P/vtest.env" --env "$_K/spark.env" --print
[[ $SPRC -eq 2 ]] && pass "--rank is required" || fail "missing --rank (rc=$SPRC)"

: > "$_K/docker.log"
_sp "$_VN" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env"
if [[ $SPRC -eq 1 ]] && _has "$_O" "image digest is not pinned" && [[ ! -s "$_K/docker.log" ]]; then
  pass "a real run refuses the placeholder image digest (docker not called)"
else
  fail "unpinned image ran (rc=$SPRC): $_O / $(cat "$_K/docker.log")"
fi
_sp "$_VN" VLLM_IMAGE_DIGEST="$_DIGEST" -- --profile "$_P/vtest.env" --rank 0 --env "$_K/spark.env"
if [[ $SPRC -eq 0 ]] && grep -q "^ARGV run -d --rm --name openbeast-vllm-rank0 " "$_K/docker.log" \
   && grep -qF "nvcr.io/nvidia/vllm:26.05-py3@$_DIGEST serve /models/vtest " "$_K/docker.log" \
   && grep -q "^ENVKEY sk-test-SECRET-4242$" "$_K/docker.log" \
   && ! grep "^ARGV" "$_K/docker.log" | grep -q "sk-test-SECRET"; then
  pass "a pinned run hands docker the key through its ENVIRONMENT, never argv"
else
  fail "pinned run (rc=$SPRC): $_O / $(cat "$_K/docker.log")"
fi

: > "$_K/docker.log"
_sp "$_TN" -- --profile "$_P/tftest.env" --rank 0 --master 192.168.100.10 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "serve /models/tftest --tp 2 --rank 0 --no-update-check --drafter none --master 192.168.100.10 --master-port 29551" \
   && _has "$_O" "--parallel auto --name local-model --host 100.64.1.2 --port 8000" \
   && _has "$_O" "-v $_K/models/tftest:/models/tftest:ro" && _has "$_O" "-e HF_HUB_OFFLINE=1" \
   && _has "$_O" "git+https://github.com/ashhart/TensorFold.git@6b2e4c40064b1e4a05965f61b19ce87b5e0265b3" \
   && _has "$_O" "start rank 1 FIRST" && _has "$_O" "NO API key" \
   && _has "$_O" "-e NCCL_SOCKET_IFNAME=enp1s0f1np1" && _has "$_O" "-e NCCL_IB_HCA=rocep1s0f1"; then
  pass "tensorfold rank 0 --print: the verified local copy (ro), --drafter none, commit-pinned, offline hub, notes"
else
  fail "tensorfold rank 0 --print (rc=$SPRC): $_O"
fi
[[ ! -s "$_K/docker.log" ]] && pass "tensorfold --print runs nothing" || fail "tensorfold --print called docker"
_sp "$_TN" -- --profile "$_P/tfd.env" --rank 0 --master 192.168.100.10 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--drafter /models/tfd.drafter" && _has "$_O" "-v $_K/models/tfd.drafter:/models/tfd.drafter:ro"; then
  pass "a profile's pinned DRAFTER is fetched-and-mounted like the model"
else
  fail "drafter (rc=$SPRC): $_O"
fi
_sp "$_TN" -- --profile "$_P/tfnofetch.env" --rank 0 --master 192.168.100.10 --env "$_K/spark.env" --print
if [[ $SPRC -eq 1 ]] && _has "$_O" "is not fetched" && _has "$_O" "cannot pin a revision"; then
  pass "tensorfold refuses an unfetched profile (it cannot pin a Hub revision itself)"
else
  fail "tensorfold unfetched (rc=$SPRC): $_O"
fi
_sp "$_TN" -- --profile "$_P/tftest.env" --rank 1 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && _has "$_O" "--rank 1 --no-update-check --drafter none --master 192.168.100.10" \
   && ! _has "$_O" "--host " && ! _has "$_O" "--name local-model"; then
  pass "tensorfold rank 1: master from SPARK_HEAD_IP, no HTTP flags"
else
  fail "tensorfold rank 1 (rc=$SPRC): $_O"
fi
_sp "$_TN" -- --profile "$_P/tftest.env" --rank 1 --env "$_K/absent.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "--master is required" && pass "tensorfold refuses to start without --master" \
  || fail "tensorfold without master (rc=$SPRC): $_O"
_sp "$_TN" -- --profile "$_P/vtest.env" --rank 0 --master 1.2.3.4 --env "$_K/spark.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "the tensorfold launcher cannot serve it" && pass "the TensorFold launcher refuses a vllm profile" \
  || fail "tensorfold took a vllm profile (rc=$SPRC): $_O"
for _ref in main v0.3.7 6b2e4c4 6B2E4C40064B1E4A05965F61B19CE87B5E0265B3; do
  _sp "$_TN" "TENSORFOLD_REF=$_ref" -- --profile "$_P/tftest.env" --rank 1 --env "$_K/spark.env" --print
  if [[ $SPRC -eq 1 ]] && _has "$_O" "is not a commit SHA"; then
    pass "tensorfold refuses TENSORFOLD_REF=$_ref (only a full 40-hex commit SHA)"
  else
    fail "tensorfold accepted TENSORFOLD_REF=$_ref (rc=$SPRC): $_O"
  fi
done
_sp "$_TN" TENSORFOLD_REF= -- --profile "$_P/tftest.env" --rank 1 --master 1.2.3.4 --env "$_K/absent.env" --print
[[ $SPRC -eq 1 ]] && _has "$_O" "TENSORFOLD_REF is not set" && pass "tensorfold refuses a missing TENSORFOLD_REF" \
  || fail "missing TENSORFOLD_REF accepted (rc=$SPRC): $_O"
_sp "$_TN" -- --profile "$_P/tftest.env" --rank 0 --ckpt Vontra/Other --env "$_K/spark.env" --print
[[ $SPRC -eq 2 ]] && _has "$_O" "--ckpt is gone" && pass "--ckpt is refused with a pointer to profiles" || fail "--ckpt (rc=$SPRC): $_O"
_sp "$_TN" TENSORFOLD_IMAGE_DIGEST="$_DIGEST" -- --profile "$_P/tftest.env" --rank 1 --env "$_K/spark.env"
if [[ $SPRC -eq 0 ]] && grep -qF "nvcr.io/nvidia/pytorch:26.07-py3@$_DIGEST -c" "$_K/docker.log"; then
  pass "a pinned tensorfold run starts the digest-pinned container"
else
  fail "tensorfold pinned run (rc=$SPRC): $_O / $(cat "$_K/docker.log")"
fi
# Injection: a profile value is data — it reaches argv as ONE element, never a shell.
cat > "$_P/vinject.env" <<EOF
BACKEND=vllm
SOURCE=acme/Brand-New-Model-FP8
REVISION=$_REV
SERVED_MODEL_NAME=\$(touch $_K/PWNED); \`touch $_K/PWNED2\`
TOOL_CALL_PARSER=hermes
EOF
cp "$_P/vtest.lock" "$_P/vinject.lock"
mkdir -p "$_K/models/vinject"; cp -r "$_K/models/vtest/." "$_K/models/vinject/"
_sp "$_VN" -- --profile "$_P/vinject.env" --rank 0 --env "$_K/spark.env" --print
if [[ $SPRC -eq 0 ]] && [[ ! -e "$_K/PWNED" && ! -e "$_K/PWNED2" ]] && _has "$_O" "--served-model-name \\\$\\(touch"; then
  pass "shell syntax in a profile value is carried as data (one argv element), never executed"
else
  fail "profile value injection (rc=$SPRC, pwned=$(ls "$_K"/PWNED* 2>/dev/null)): $_O"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
