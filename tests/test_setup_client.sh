#!/bin/bash
# scripts/setup-client.sh — the installer's own decisions, run for real.
#
# Usage: ./tests/test_setup_client.sh
#
# The installer is executed end to end inside a THROWAWAY home and a throwaway
# "full clone", with every outside dependency stubbed: tailscale (a canned
# `status --json`), curl (an HTTP status read from a file — nothing is ever
# dialed), the pinned-deps installer, the venv and the client CLI. No network,
# no real $HOME, no real ports.
#
#   1  the rig probe names WHICH failure it saw (2026-10-09 review, UX-06)
#   2  the env file is 0600 even when it already existed wider (supply S11)
#   3  the opencode.json merge runs the vetted interpreter (UX-28)
#   4  auto-detection: own-tailnet peers only, and never a key to a host
#      nobody named (supply S12); a miss lists the peers that exist (UX-29)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo "=== setup-client.sh tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-setup-client-test.XXXXXX")"
trap 'rm -rf "$T"' EXIT
R="$T/repo"; BIN="$T/bin"; S="$T/stub"
mkdir -p "$R/scripts/lib" "$R/agents" "$BIN" "$S"
install -m 755 "$REPO_DIR/scripts/setup-client.sh" "$R/scripts/"
install -m 644 "$REPO_DIR/scripts/lib/curl_auth.sh" "$R/scripts/lib/"
: > "$R/agents/tools.py"                       # marks "running inside a full clone"
echo 'openai==1.0' > "$R/agents/requirements.txt"
echo '{"provider":{"llama-cpp":{"models":{"stub-model":{"name":"Stub"}}}}}' > "$R/opencode.json"
REAL_PY="$(python3 -c 'import sys; print(sys.executable)')"

# --- stubs -------------------------------------------------------------------
# The vetted interpreter the installer is told to use (OPENBEAST_PYTHON)…
printf '#!/bin/bash\necho vetted >> "%s/py.calls"\nexec "%s" "$@"\n' "$S" "$REAL_PY" > "$T/vetted-python"
# …and a `python3` on PATH that is the wrong one (the Xcode-shim case): any
# use of bare python3 by the installer fails loudly.
printf '#!/bin/bash\necho bare >> "%s/py.calls"\nexit 97\n' "$S" > "$BIN/python3"
cat > "$BIN/tailscale" <<EOF
#!/bin/bash
if [[ "\$*" == "status --json" ]]; then cat "$S/ts.json"; fi
exit 0
EOF
# curl: never dials. Records argv; for a status probe (-w) prints the code
# configured for that endpoint, and exits 7 for 000 as real curl does.
cat > "$BIN/curl" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >> "$S/curl.argv"
url="\${@: -1}"; code=""
case "\$url" in
  */health)    code="\$(cat "$S/health_code")" ;;
  */v1/models) code="\$(cat "$S/models_code")" ;;
esac
case " \$* " in *" -w "*) printf '%s' "\${code:-000}" ;; esac
[[ "\${code:-000}" == "000" ]] && exit 7
exit 0
EOF
printf '#!/bin/bash\nexit 0\n' > "$BIN/opencode"
printf '#!/bin/bash\nexit 0\n' > "$R/scripts/pydeps.sh"
# The client CLI is not under test here, and its refresh would dial the rig.
printf '#!/bin/bash\necho "client.sh $*" >> "%s/client.calls"\nexit 0\n' "$S" > "$R/scripts/client.sh"
chmod +x "$T/vetted-python" "$BIN"/* "$R/scripts/pydeps.sh" "$R/scripts/client.sh"

_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
# run <home-name> <health> <models> [ENV=VAL…] -- [installer args…]
# Sets OUT, RC, H. stdin is /dev/null: there is no terminal to confirm on.
run() {
  H="$T/$1"; echo "$2" > "$S/health_code"; echo "$3" > "$S/models_code"; shift 3
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  mkdir -p "$H/.openbeast-client/venv/bin"
  printf '#!/bin/bash\nexit 0\n' > "$H/.openbeast-client/venv/bin/python3"
  printf '#!/bin/bash\nexit 0\n' > "$H/.openbeast-client/venv/bin/pip"
  chmod +x "$H/.openbeast-client/venv/bin/python3" "$H/.openbeast-client/venv/bin/pip"
  : > "$S/curl.argv"; : > "$S/py.calls"; : > "$S/client.calls"
  OUT="$(env -i HOME="$H" PATH="$BIN:/usr/bin:/bin" OPENBEAST_PYTHON="$T/vetted-python" \
         ${envs[@]+"${envs[@]}"} bash "$R/scripts/setup-client.sh" "$@" </dev/null 2>&1)"
  RC=$?
}
echo '{"Self":{"DNSName":"lap.tail1.ts.net."},"Peer":{}}' > "$S/ts.json"

# ---------------------------------------------------------------------------
echo ""
echo "1. the rig probe says which failure it is (UX-06):"
run h200 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "rig model API reachable" && has "$OUT" "Client mode ready" \
   && ! has "$OUT" "INSTALLED, but"; then
  pass "control: 200 → reachable, finishes 'Client mode ready'"
else
  fail "healthy rig (rc=$RC): $OUT"
fi
run h401 401 401 -- --host rig.example --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "requires a device key" && has "$OUT" "clients.sh enroll" \
   && has "$OUT" "--api-key-stdin" && ! has "$OUT" "Client mode" \
   && [[ ! -e "$H/.openbeast-client.env" ]]; then
  pass "401 without a key: exits 1 naming the device key + enroll, writes nothing"
else
  fail "keyless 401 (rc=$RC): $OUT"
fi
run h403 403 403 -- --host rig.example --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "requires a device key"; then
  pass "403 without a key is the same refusal"
else
  fail "keyless 403 (rc=$RC): $OUT"
fi
run h401k 401 401 OPENBEAST_API_KEY=wrongkey -- --host rig.example --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "rejected this device key" && ! has "$OUT" "requires a device key" \
   && [[ ! -e "$H/.openbeast-client.env" ]]; then
  pass "401 WITH a key: exits 1 saying the key was rejected (a different fix)"
else
  fail "keyed 401 (rc=$RC): $OUT"
fi
# A raw llama-server with LLAMA_API_KEY answers /health 200 to anyone.
run hpub 200 401 -- --host rig.example --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "requires a device key"; then
  pass "public /health but a 401 on /v1/models still reports the missing key"
else
  fail "200-health/401-models (rc=$RC): $OUT"
fi
run h000 000 000 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "not published on :8443" && has "$OUT" "./scripts/setup-tailscale.sh" \
   && has "$OUT" "INSTALLED, but the rig is not usable yet" && ! has "$OUT" "Client mode ready" \
   && ! has "$OUT" "device key"; then
  pass "nothing answering: 'not published on :8443' + the rig-side command; not 'ready'"
else
  fail "connection refused (rc=$RC): $OUT"
fi
run h502 502 502 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "Published, but the stack is down" && has "$OUT" "./start.sh -d" \
   && ! has "$OUT" "Client mode ready" && ! has "$OUT" "not published on :8443"; then
  pass "502: 'published, but the stack is down' + ./start.sh -d; not 'ready'"
else
  fail "502 (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
