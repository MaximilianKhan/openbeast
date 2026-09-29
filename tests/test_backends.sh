#!/bin/bash
# Inference-backend tests (docs/DGX_SPARK_PLAN.md): the INFERENCE_* conf keys
# and lib/backend.sh readiness per server.
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
    return 404, json.dumps({"error": "not found"})
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
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

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
