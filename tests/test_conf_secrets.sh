#!/bin/bash
# Config parsing + secrets-off-argv fixes (2026-09-29 review) — behavior tests.
#
# Usage: bash tests/test_conf_secrets.sh
#
#   1  lib/conf.sh       every boolean key parses through ONE helper
#                        (network-exposure-2: WEBUI_AUTH/EDGE_GATE failed OPEN
#                        on an inline comment or on 1/yes)
#   2  lib/conf.sh       a non-loopback BIND_HOST warns, and names the keyless
#                        tool server's remote shell (identity-rbac-4)
#   3  serve.sh          LLAMA_API_KEY reaches llama-server via env, not argv
#                        (secrets-crypto-1 / network-exposure-4)
#   4  lib/curl_auth.sh  a credential header never lands on curl's argv, and
#                        a REAL curl really sends it (extensions-client-8)
#   5  client.sh         the device key stays off curl's argv
#   6  setup-client.sh   --api-key-stdin, and the probe keeps the key off argv
#
# Everything runs against THROWAWAY copies under $TMPDIR with stub binaries
# that RECORD their calls. No stack, no GPU, no real openbeast.conf. Every
# positive assertion has a negative control next to it.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-conf-secrets-XXXXXX")"
SRV_PID=""
cleanup() {
  [[ -n "$SRV_PID" ]] && kill "$SRV_PID" 2>/dev/null
  rm -rf "$T"
}
trap cleanup EXIT

echo "=== conf parsing + secrets off argv ==="

# A sandbox rig: the REAL conf.sh, a conf file this test writes.
SB="$T/rig"
mkdir -p "$SB/scripts/lib"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$SB/scripts/lib/"

# conf_eval <conf body> <extra env assignments…> -- <bash snippet>
# Sources conf.sh in a CLEAN environment (no ambient OPENBEAST_* from the rig
# running the test), stdout = snippet output, stderr to $T/conf.err.
conf_eval() {
  local body="$1"; shift
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  printf '%s\n' "$body" > "$SB/openbeast.conf"
  env -i HOME="$T" PATH="$PATH" REPO_DIR="$SB" ${envs[@]+"${envs[@]}"} \
    bash -c "source '$SB/scripts/lib/conf.sh'; $1" 2>"$T/conf.err"
}

# ---------------------------------------------------------------------------
echo ""
echo "1. boolean keys (conf.sh _ob_bool):"
OUT="$(conf_eval 'WEBUI_AUTH=true   # remote access on
EDGE_GATE=true # per-device keys
OFFLINE="true" # airgap
FAST_BOOT=YES
AGENT_ROUTER=on
BEAST_CHAT=1
BEAST_ARTIFACT=true# attached
ROUTER_REQUIRE_IDENTITY=yes
EDGE_ALLOW_ANON=no  # keep closed
FETCH_ALLOW_TAILNET=On' -- \
  'printf "%s|" "$OPENBEAST_WEBUI_AUTH" "$EDGE_GATE" "$OFFLINE" "$FAST_BOOT" "$AGENT_ROUTER" "$OPENBEAST_BEAST_CHAT" "$BEAST_ARTIFACT" "$OPENBEAST_ROUTER_REQUIRE_IDENTITY" "$OPENBEAST_EDGE_ALLOW_ANON" "$OPENBEAST_FETCH_ALLOW_TAILNET" "$MODEL_ROLLBACK"')"
if [[ "$OUT" == "true|true|true|true|true|true|true|true|false|true|true|" ]]; then
  pass "inline comments, quotes, 1/yes/on/YES all parse to canonical true (and no → false)"
else
  fail "boolean keys not canonical: got '$OUT'"
fi
# The exact finding: Open WebUI's own parse of what compose hands it.
if OPENBEAST_WEBUI_AUTH="$(conf_eval 'WEBUI_AUTH=true   # remote access on' -- 'printf %s "$OPENBEAST_WEBUI_AUTH"')" \
   python3 -c 'import os,sys; sys.exit(0 if os.environ["OPENBEAST_WEBUI_AUTH"].lower()=="true" else 1)'; then
  pass "WEBUI_AUTH with an inline comment reaches Open WebUI as login ON"
else
  fail "WEBUI_AUTH with an inline comment still turns the login wall OFF"
fi
# Negative controls: false stays false, absent keeps its default.
OUT="$(conf_eval 'WEBUI_AUTH=false  # local only
EDGE_GATE=0
MODEL_ROLLBACK=off' -- 'printf "%s|%s|%s|%s" "$OPENBEAST_WEBUI_AUTH" "$EDGE_GATE" "$MODEL_ROLLBACK" "$FAST_BOOT"')"
if [[ "$OUT" == "false|false|false|false" ]]; then
  pass "negative control: false/0/off stay false, absent FAST_BOOT defaults false"
else
  fail "negative control broke: got '$OUT'"
fi
OUT="$(conf_eval '# empty' -- 'printf "%s|%s|%s" "$MODEL_ROLLBACK" "$OPENBEAST_WEBUI_AUTH" "${OPENBEAST_EDGE_ALLOW_ANON-ABSENT}"')"
if [[ "$OUT" == "true|false|ABSENT" ]]; then
  pass "defaults: MODEL_ROLLBACK true, WEBUI_AUTH false, EDGE_ALLOW_ANON not exported"
else
  fail "defaults wrong: got '$OUT'"
fi
# Env overrides go through the same parser.
OUT="$(conf_eval 'WEBUI_AUTH=false' OPENBEAST_WEBUI_AUTH=1 OPENBEAST_EDGE_GATE='yes # x' -- 'printf "%s|%s" "$OPENBEAST_WEBUI_AUTH" "$EDGE_GATE"')"
if [[ "$OUT" == "true|true" ]]; then
  pass "env overrides (OPENBEAST_WEBUI_AUTH=1, OPENBEAST_EDGE_GATE='yes # x') normalise too"
else
  fail "env override not normalised: got '$OUT'"
fi
# A typo is visible, not silent.
OUT="$(conf_eval 'BEAST_CHAT=ture' -- 'printf %s "$BEAST_CHAT"')"
if [[ "$OUT" == "false" ]] && grep -q "BEAST_CHAT='ture' is not a boolean" "$T/conf.err"; then
  pass "an unrecognised value resolves false WITH a warning naming the key"
else
  fail "typo handling: got '$OUT', stderr: $(cat "$T/conf.err")"
fi
if conf_eval 'WEBUI_AUTH=true' -- 'true' && ! grep -q 'not a boolean' "$T/conf.err"; then
  pass "negative control: a valid value warns about nothing"
else
  fail "a valid value produced a spurious warning"
fi
# Sourcing must still leave the caller's positional parameters alone.
printf 'OFFLINE=true # x\n' > "$SB/openbeast.conf"
OUT="$(env -i HOME="$T" PATH="$PATH" REPO_DIR="$SB" bash -c \
  'source "$REPO_DIR/scripts/lib/conf.sh" 2>/dev/null; printf "%s|%s" "${1:-}" "$#"' _ sign extra)"
if [[ "$OUT" == "sign|2" ]]; then
  pass "sourcing conf.sh leaves \$@ intact"
else
  fail "conf.sh clobbered \$@ (got '$OUT')"
fi

# ---------------------------------------------------------------------------
echo ""
echo "2. non-loopback BIND_HOST (identity-rbac-4):"
conf_eval 'BIND_HOST=192.168.1.20' -- 'ob_tools_exposed_open && echo EXPOSED' > "$T/out" || true
if grep -q 'is not loopback' "$T/conf.err" && grep -q 'can run shell commands' "$T/conf.err" \
   && grep -q EXPOSED "$T/out"; then
  pass "a specific LAN IP with no MCPO keys warns and names the open tool-server shell"
else
  fail "LAN bind without keys not flagged (stderr: $(cat "$T/conf.err"))"
fi
conf_eval 'BIND_HOST=100.101.102.103
MCPO_ADMIN_KEY=abc' -- 'ob_tools_exposed_open && echo EXPOSED' > "$T/out" || true
if grep -q 'is not loopback' "$T/conf.err" && ! grep -q 'shell commands' "$T/conf.err" \
   && ! grep -q EXPOSED "$T/out"; then
  pass "keyed tool server: warns about the bind, not about an open shell"
else
  fail "keyed + non-loopback handled wrong (stderr: $(cat "$T/conf.err"))"
fi
for _h in 127.0.0.1 127.0.0.53 ::1 localhost; do
  conf_eval "BIND_HOST=$_h" -- 'ob_tools_exposed_open && echo EXPOSED' > "$T/out" || true
  if [[ ! -s "$T/conf.err" && ! -s "$T/out" ]]; then
    pass "negative control: BIND_HOST=$_h is loopback — silent"
  else
    fail "BIND_HOST=$_h wrongly flagged (stderr: $(cat "$T/conf.err"))"
  fi
done
OUT="$(conf_eval 'BIND_HOST=0.0.0.0
ALLOW_OPEN_TOOLS=yes' -- 'printf %s "$OPENBEAST_ALLOW_OPEN_TOOLS"')"
if [[ "$OUT" == "true" ]] && grep -q 'ALLOW_OPEN_TOOLS=true' "$T/conf.err"; then
  pass "ALLOW_OPEN_TOOLS is the explicit override, exported canonical"
else
  fail "ALLOW_OPEN_TOOLS override: got '$OUT'"
fi
OUT="$(conf_eval 'BIND_HOST=0.0.0.0' -- 'printf %s "$OPENBEAST_ALLOW_OPEN_TOOLS"')"
if [[ "$OUT" == "false" ]] && grep -q 'setup-mcpo-keys.sh' "$T/conf.err"; then
  pass "negative control: without the override it is false and the fix is named"
else
  fail "ALLOW_OPEN_TOOLS default: got '$OUT'"
fi

# ---------------------------------------------------------------------------
echo ""
echo "3. serve.sh — the API key is not on llama-server's argv:"
SS="$T/serve"
mkdir -p "$SS/scripts/lib" "$SS/llama.cpp/build/bin"
install -m 755 "$REPO_DIR/scripts/serve.sh" "$SS/scripts/"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$SS/scripts/lib/"
cat > "$SS/llama.cpp/build/bin/llama-server" <<EOF
#!/bin/bash
printf '%s\n' "\$@" > "$T/llama.argv"
printf '%s' "\${LLAMA_API_KEY-UNSET}" > "$T/llama.envkey"
EOF
chmod +x "$SS/llama.cpp/build/bin/llama-server"
touch "$SS/model.gguf"
KEY="sk-serve-$RANDOM$RANDOM"
printf 'LLAMA_API_KEY=%s\n' "$KEY" > "$SS/openbeast.conf"
env -i HOME="$T" PATH="$PATH" OPENBEAST_CONTEXT=4096 WEIGHT_ENFORCE=off \
  bash "$SS/scripts/serve.sh" -m "$SS/model.gguf" >/dev/null 2>&1 || true
if [[ -f "$T/llama.argv" ]] && ! grep -qF -- "$KEY" "$T/llama.argv" \
   && ! grep -qx -- '--api-key' "$T/llama.argv" \
   && [[ "$(cat "$T/llama.envkey")" == "$KEY" ]]; then
  pass "keyed: llama-server gets LLAMA_API_KEY in its env, nothing on argv"
else
  fail "keyed serve: argv=[$(tr '\n' ' ' < "$T/llama.argv" 2>/dev/null)] env=[$(cat "$T/llama.envkey" 2>/dev/null)]"
fi
# The launch itself must still be the real one (negative control: the stub ran).
if grep -qx -- '--kv-unified' "$T/llama.argv" 2>/dev/null; then
  pass "negative control: the stub recorded a real launch line"
else
  fail "the llama-server stub never ran"
fi
rm -f "$T/llama.argv" "$T/llama.envkey"
printf '# unkeyed\n' > "$SS/openbeast.conf"
env -i HOME="$T" PATH="$PATH" OPENBEAST_CONTEXT=4096 WEIGHT_ENFORCE=off LLAMA_API_KEY= \
  bash "$SS/scripts/serve.sh" -m "$SS/model.gguf" >/dev/null 2>&1 || true
if [[ "$(cat "$T/llama.envkey" 2>/dev/null)" == "UNSET" ]]; then
  pass "unkeyed: LLAMA_API_KEY is UNSET for llama-server (an empty export counts as set)"
else
  fail "unkeyed: llama-server saw LLAMA_API_KEY='$(cat "$T/llama.envkey" 2>/dev/null)'"
fi

# ---------------------------------------------------------------------------
echo ""
echo "4. lib/curl_auth.sh:"
BIN="$T/bin"
mkdir -p "$BIN"
# The stub curl records its argv AND what it can read from a --config fd.
cat > "$BIN/curl" <<EOF
#!/bin/bash
printf '%s\n' "\$@" >> "$T/curl.argv"
prev=""
for a in "\$@"; do
  if [[ "\$prev" == "--config" || "\$prev" == "-K" ]]; then cat "\$a" >> "$T/curl.cfg" 2>/dev/null; fi
  prev="\$a"
done
echo ok
EOF
chmod +x "$BIN/curl"
reset_curl() { : > "$T/curl.argv"; : > "$T/curl.cfg"; }
reset_curl
SEC="sekrit-$RANDOM$RANDOM"
PATH="$BIN:$PATH" bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_bearer '$SEC' -s http://x/health" >/dev/null
if ! grep -qF "$SEC" "$T/curl.argv" && grep -qF "header = \"Authorization: Bearer $SEC\"" "$T/curl.cfg"; then
  pass "ob_curl_bearer: key reaches curl through --config, not argv"
else
  fail "ob_curl_bearer: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi
reset_curl
PATH="$BIN:$PATH" bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_bearer '' -s http://x/health" >/dev/null
if ! grep -qx -- '--config' "$T/curl.argv" && grep -qx 'http://x/health' "$T/curl.argv"; then
  pass "negative control: an empty key is a plain curl (no --config)"
else
  fail "empty key still used --config: $(tr '\n' ' ' < "$T/curl.argv")"
fi
reset_curl
RC=0
PATH="$BIN:$PATH" bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_hdr \$'X-A: b\nurl = http://evil/' http://x/" >/dev/null 2>&1 || RC=$?
if [[ $RC -eq 2 && ! -s "$T/curl.argv" ]]; then
  pass "a header with a line break is refused before curl runs (no config injection)"
else
  fail "line-break header: rc=$RC argv=[$(tr '\n' ' ' < "$T/curl.argv")]"
fi
reset_curl
PATH="$BIN:$PATH" bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_hdr 'X-T: a\"b\\c' http://x/" >/dev/null
if grep -qF 'header = "X-T: a\"b\\c"' "$T/curl.cfg"; then
  pass "quotes and backslashes are escaped for curl's config syntax"
else
  fail "escaping wrong: $(cat "$T/curl.cfg")"
fi
# End-to-end with the REAL curl: a one-shot local server echoes the header
# it received, so the fd-3 config idiom is proven, not assumed.
REAL_CURL="$(command -v curl || true)"
if [[ -n "$REAL_CURL" ]]; then
  python3 - "$T/port" > /dev/null 2>&1 <<'PY' &
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (self.headers.get("Authorization") or "NONE").encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(s.server_address[1]))
s.timeout = 10
for _ in range(2):
    s.handle_request()
PY
  SRV_PID=$!
  for _ in $(seq 1 50); do [[ -s "$T/port" ]] && break; sleep 0.1; done
  PORT="$(cat "$T/port" 2>/dev/null || true)"
  GOT="$(bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_bearer '$SEC' -s -m 5 http://127.0.0.1:$PORT/" 2>/dev/null || true)"
  GOT0="$(bash -c ". '$REPO_DIR/scripts/lib/curl_auth.sh'; ob_curl_bearer '' -s -m 5 http://127.0.0.1:$PORT/" 2>/dev/null || true)"
  if [[ "$GOT" == "Bearer $SEC" && "$GOT0" == "NONE" ]]; then
    pass "real curl: the server receives the bearer (and none when unkeyed)"
  else
    fail "real curl: keyed got '$GOT', unkeyed got '$GOT0'"
  fi
  wait "$SRV_PID" 2>/dev/null || true
  SRV_PID=""
fi

# ---------------------------------------------------------------------------
echo ""
echo "5. client.sh — device key off curl's argv:"
CH="$T/chome"
mkdir -p "$CH"
DKEY="dev-$RANDOM$RANDOM"
cat > "$CH/.openbeast-client.env" <<EOF
OPENBEAST_AGENT_INFERENCE_URL='https://rig.example:8443/v1'
OPENBEAST_API_KEY='$DKEY'
EOF
reset_curl
env -i HOME="$CH" PATH="$BIN:/usr/bin:/bin" bash "$REPO_DIR/scripts/client.sh" status >/dev/null 2>&1 || true
if grep -qF 'https://rig.example:8443/health' "$T/curl.argv" \
   && ! grep -qF "$DKEY" "$T/curl.argv" \
   && grep -qF "Bearer $DKEY" "$T/curl.cfg"; then
  pass "client.sh status probes the rig with the key in a --config, never argv"
else
  fail "client.sh: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi

# ---------------------------------------------------------------------------
echo ""
echo "6. setup-client.sh --api-key-stdin:"
SC="$T/sc"
SH="$T/shome"
mkdir -p "$SC/scripts/lib" "$SH"
install -m 755 "$REPO_DIR/scripts/setup-client.sh" "$SC/scripts/"
install -m 644 "$REPO_DIR/scripts/lib/curl_auth.sh" "$SC/scripts/lib/"
printf '#!/bin/sh\nexit 0\n' > "$BIN/tailscale"
printf '#!/bin/sh\nexit 1\n' > "$BIN/git"      # stops the run right after the probe
chmod +x "$BIN/tailscale" "$BIN/git"
SKEY="stdin-$RANDOM$RANDOM"
reset_curl
printf '%s\n' "$SKEY" | env -i HOME="$SH" PATH="$BIN:$PATH" \
  bash "$SC/scripts/setup-client.sh" --host rig.example --no-search --api-key-stdin >/dev/null 2>&1 || true
if grep -qF 'https://rig.example:8443/health' "$T/curl.argv" \
   && ! grep -qF "$SKEY" "$T/curl.argv" \
   && grep -qF "Bearer $SKEY" "$T/curl.cfg"; then
  pass "--api-key-stdin reads the key and the probe keeps it off curl's argv"
else
  fail "setup-client: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi
reset_curl
env -i HOME="$SH" PATH="$BIN:$PATH" \
  bash "$SC/scripts/setup-client.sh" --host rig.example --no-search </dev/null >/dev/null 2>&1 || true
if grep -qF 'https://rig.example:8443/health' "$T/curl.argv" && [[ ! -s "$T/curl.cfg" ]]; then
  pass "negative control: no key → an unkeyed probe, no --config"
else
  fail "unkeyed setup-client probe: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi

# The documented no-clone path: the script fetched ALONE, no lib/ next to it
# (the slim checkout comes after the probe). The inline fallback must still
# key the probe — dropping the key 401s a keyed rig into "not answering".
SA="$T/standalone"
mkdir -p "$SA"
install -m 755 "$REPO_DIR/scripts/setup-client.sh" "$SA/"
AKEY="alone-$RANDOM\\x\"y"
reset_curl
printf '%s\n' "$AKEY" | env -i HOME="$SH" PATH="$BIN:$PATH" \
  bash "$SA/setup-client.sh" --host rig.example --no-search --api-key-stdin >/dev/null 2>&1 || true
AKEY_ESC="$(printf '%s' "$AKEY" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
if grep -qF 'https://rig.example:8443/health' "$T/curl.argv" \
   && ! grep -qF "alone-" "$T/curl.argv" \
   && grep -qF "header = \"Authorization: Bearer $AKEY_ESC\"" "$T/curl.cfg"; then
  pass "standalone copy (no lib/): the probe is still keyed, off argv, config-escaped"
else
  fail "standalone setup-client: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi
reset_curl
env -i HOME="$SH" PATH="$BIN:$PATH" \
  bash "$SA/setup-client.sh" --host rig.example --no-search </dev/null >/dev/null 2>&1 || true
if grep -qF 'https://rig.example:8443/health' "$T/curl.argv" && [[ ! -s "$T/curl.cfg" ]]; then
  pass "negative control: standalone, no key → an unkeyed probe, no --config"
else
  fail "standalone unkeyed probe: argv=[$(tr '\n' ' ' < "$T/curl.argv")] cfg=[$(cat "$T/curl.cfg")]"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
