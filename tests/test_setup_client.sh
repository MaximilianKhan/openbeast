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
#      nobody named (supply S12); a miss lists the peers that exist (UX-29);
#      a 'beast' owned by another tailnet user is never wired unconfirmed
#   5  a stored key only ever goes back to the rig it was stored for
#   6  review 2026-11-16: a 3.10 venv left by an older install is rebuilt;
#      python3.14 is found; --wheelhouse installs with no index; the key-
#      bearing opencode.json is replaced atomically at 0600
#   7  the soul; --uninstall drops only our entries and the key-holding
#      backups our tools made (review 2026-11-18)

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
# The vetted interpreter must itself pass the installer's >=3.11 check, or every
# case dies in preflight ("no python >=3.11 found") and the suite reports 15
# regressions that are not there — stock macOS python3 is 3.9. Search the
# same way _find_py does, and skip clearly when nothing qualifies.
REAL_PY=""
for _c in python3.13 python3.12 python3.11 python3; do
  command -v "$_c" >/dev/null 2>&1 || continue
  if "$_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    REAL_PY="$("$_c" -c 'import sys; print(sys.executable)')"; break
  fi
done
if [[ -z "$REAL_PY" ]]; then
  echo "  SKIP: no python >= 3.11 on PATH — the installer under test refuses older ones"
  exit 0
fi

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
printf '#!/bin/bash\necho "pydeps $*" >> "%s/pydeps.calls"\nexit 0\n' "$S" > "$R/scripts/pydeps.sh"
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
  # a venv a case planted on purpose (an old 3.10 one) is left alone
  [[ -e "$H/.openbeast-client/venv/bin/python3" ]] \
    || printf '#!/bin/bash\nexit 0\n' > "$H/.openbeast-client/venv/bin/python3"
  printf '#!/bin/bash\nexit 0\n' > "$H/.openbeast-client/venv/bin/pip"
  chmod +x "$H/.openbeast-client/venv/bin/python3" "$H/.openbeast-client/venv/bin/pip"
  : > "$S/curl.argv"; : > "$S/py.calls"; : > "$S/client.calls"; : > "$S/pydeps.calls"
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
echo "2. env file mode (supply S11):"
mkdir -p "$T/hmode"
( umask 022; echo "# restored by a dotfile manager" > "$T/hmode/.openbeast-client.env" )
chmod 644 "$T/hmode/.openbeast-client.env"
run hmode 200 200 OPENBEAST_API_KEY=sekrit-key -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && grep -q "sekrit-key" "$H/.openbeast-client.env" \
   && [[ "$(_mode "$H/.openbeast-client.env")" == "600" ]]; then
  pass "a pre-existing 0644 env file is 0600 once it holds the key"
else
  fail "env file mode $(_mode "$H/.openbeast-client.env") (rc=$RC): $OUT"
fi
if [[ "$(_mode "$T/h200/.openbeast-client.env")" == "600" ]]; then
  pass "control: a freshly created env file is 0600 too"
else
  fail "fresh env file mode $(_mode "$T/h200/.openbeast-client.env")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "3. the opencode.json merge uses the vetted interpreter (UX-28):"
OC="$T/h200/.config/opencode/opencode.json"
run h200 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && ! grep -q bare "$S/py.calls" && grep -q vetted "$S/py.calls" \
   && "$REAL_PY" -c 'import json,sys; c=json.load(open(sys.argv[1])); sys.exit(0 if "openbeast-rig" in c["provider"] and "openbeast-tools" in c["mcp"] else 1)' "$OC" \
   && has "$(cat "$S/client.calls")" "refresh-config" && [[ -L "$T/h200/.local/bin/openbeast-client" ]]; then
  pass "with a broken python3 on PATH the install still merges the config and links the CLI"
else
  fail "merge with a bad PATH python3 (rc=$RC, calls=$(tr '\n' ' ' < "$S/py.calls")): $OUT"
fi

# opencode v2 keeps MCP servers under mcp.servers and does not merge a v1
# top-level entry with them: write the v2 shape (Code Mode off) and move the
# user's own v1 servers in with it, so the file never mixes the two layouts.
echo ""
echo "3b. opencode v2: the MCP entry lands in mcp.servers, never mixed with v1:"
printf '#!/bin/bash\n[[ "$1" == --version ]] && echo "opencode v2.0.25"\nexit 0\n' > "$BIN/opencode"
OC="$T/hv2/.config/opencode/opencode.json"; mkdir -p "$(dirname "$OC")"
echo '{"mcp":{"mine":{"type":"local","command":["/bin/true"],"enabled":false}}}' > "$OC"
run hv2 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && "$REAL_PY" - "$OC" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1]))["mcp"]
s = m.get("servers", {})
ok = (set(m) == {"servers"}                                   # nothing left at the v1 level
      and s.get("openbeast-tools", {}).get("codemode") is False
      and "enabled" not in s["openbeast-tools"]
      and s.get("mine", {}).get("disabled") is True)          # the user's v1 entry, moved, still off
sys.exit(0 if ok else 1)
PYEOF
then
  pass "v2: openbeast-tools under mcp.servers with Code Mode off; the user's v1 server moved in, still disabled"
else
  fail "v2 MCP layout (rc=$RC): $(cat "$OC" 2>/dev/null)"
fi
run hv2 200 200 -- --uninstall
if "$REAL_PY" -c 'import json,sys; m=json.load(open(sys.argv[1]))["mcp"]; sys.exit(0 if "openbeast-tools" not in m.get("servers",{}) and "mine" in m["servers"] else 1)' "$OC"; then
  pass "v2: --uninstall removes mcp.servers.openbeast-tools and keeps the user's own server"
else
  fail "v2 uninstall: $(cat "$OC" 2>/dev/null)"
fi
printf '#!/bin/bash\nexit 0\n' > "$BIN/opencode"

# ---------------------------------------------------------------------------
echo ""
echo "4. rig auto-detection (supply S12, UX-29):"
# A `beast` shared in from ANOTHER tailnet, and one unrelated peer of our own.
cat > "$S/ts.json" <<'EOF'
{"Self":{"DNSName":"lap.tail1.ts.net."},
 "Peer":{"a":{"DNSName":"beast.othernet.ts.net."},
         "b":{"DNSName":"desk.tail1.ts.net."}}}
EOF
run hauto1 200 200 -- --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "no peer named 'beast' on your tailnet" \
   && has "$OUT" "Preflight failed" && ! grep -q "othernet" "$S/curl.argv"; then
  pass "a 'beast' under a different MagicDNS suffix is NOT picked, and is never probed"
else
  fail "foreign beast (rc=$RC, curl=$(tr '\n' ' ' < "$S/curl.argv")): $OUT"
fi
if has "$OUT" "desk.tail1.ts.net" && has "$OUT" "ANOTHER tailnet" && has "$OUT" "beast.othernet.ts.net"; then
  pass "the miss lists the peers that do exist, and says which one it ignored"
else
  fail "miss did not list peers: $OUT"
fi
# Same suffix, but the 'beast' belongs to ANOTHER member of the tailnet.
cat > "$S/ts.json" <<'EOF'
{"Self":{"DNSName":"lap.tail1.ts.net.","UserID":1},
 "Peer":{"b":{"DNSName":"desk.tail1.ts.net.","UserID":1},
         "c":{"DNSName":"beast.tail1.ts.net.","UserID":2}}}
EOF
run hautox 200 200 -- --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "owned by another tailnet user" \
   && has "$OUT" "--host beast.tail1.ts.net" && [[ ! -s "$S/curl.argv" ]] \
   && [[ ! -e "$H/.openbeast-client.env" ]]; then
  pass "keyless: another user's 'beast' is refused with no terminal, nothing probed or written"
else
  fail "other-owner beast, keyless (rc=$RC, curl=$(tr '\n' ' ' < "$S/curl.argv")): $OUT"
fi
run hautox2 200 200 -- --host beast.tail1.ts.net --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "Client mode ready"; then
  pass "control: naming that host with --host wires it"
else
  fail "other-owner beast, explicit host (rc=$RC): $OUT"
fi
cat > "$S/ts.json" <<'EOF'
{"Self":{"DNSName":"lap.tail1.ts.net.","UserID":1},
 "Peer":{"a":{"DNSName":"beast.othernet.ts.net.","UserID":1},
         "b":{"DNSName":"desk.tail1.ts.net.","UserID":1},
         "c":{"DNSName":"beast.tail1.ts.net.","UserID":1}}}
EOF
run hauto2 200 200 -- --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "rig auto-detected: beast.tail1.ts.net" \
   && grep -q "https://beast.tail1.ts.net:8443/health" "$S/curl.argv" && ! grep -q "othernet" "$S/curl.argv"; then
  pass "control: our own 'beast' (same owner) is auto-detected (no key involved → no prompt)"
else
  fail "own beast, keyless (rc=$RC): $OUT"
fi
run hauto3 200 200 OPENBEAST_API_KEY=sekrit-key -- --no-search
if [[ $RC -eq 1 ]] && has "$OUT" "refusing to send the rig API key to an AUTO-DETECTED host" \
   && has "$OUT" "--host beast.tail1.ts.net" && [[ ! -s "$S/curl.argv" ]] \
   && [[ ! -e "$H/.openbeast-client.env" ]]; then
  pass "with a key and no terminal, an auto-detected host is refused before anything is sent"
else
  fail "keyed auto-detect (rc=$RC, curl=$(tr '\n' ' ' < "$S/curl.argv")): $OUT"
fi
run hauto4 200 200 OPENBEAST_API_KEY=sekrit-key -- --host beast.tail1.ts.net --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "Client mode ready" && ! has "$OUT" "AUTO-DETECTED"; then
  pass "control: the same key with an explicit --host installs without asking"
else
  fail "keyed explicit host (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "5. a stored key goes back only to the rig it was stored for:"
_stored_env() {
  mkdir -p "$T/$1"
  printf "OPENBEAST_AGENT_INFERENCE_URL='https://%s:8443/v1'\nOPENBEAST_API_KEY='%s'\n" "$2" "$3" \
    > "$T/$1/.openbeast-client.env"
  chmod 600 "$T/$1/.openbeast-client.env"
}
_stored_env hswitch rigA.tail1.ts.net rigA-device-key
run hswitch 200 200 -- --host rigB.tail1.ts.net --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "belongs to rigA.tail1.ts.net" \
   && ! grep -q "rigA-device-key" "$S/curl.argv" "$H/.openbeast-client.env" "$H/.config/opencode/opencode.json"; then
  pass "re-run against a DIFFERENT rig: the old rig's key is not sent, nor written as the new one's"
else
  fail "rig switch leaked the stored key (rc=$RC): $OUT"
fi
_stored_env hsame rigA.tail1.ts.net rigA-device-key
run hsame 200 200 -- --host RIGA.tail1.ts.net. --no-search
if [[ $RC -eq 0 ]] && grep -q "rigA-device-key" "$H/.openbeast-client.env" && ! has "$OUT" "NOT sending"; then
  pass "control: a re-run against the SAME rig keeps its key (host compared case/dot-insensitively)"
else
  fail "same-rig re-run de-keyed (rc=$RC): $OUT"
fi
_stored_env hnew rigA.tail1.ts.net rigA-device-key
run hnew 200 200 OPENBEAST_API_KEY=rigB-key -- --host rigB.tail1.ts.net --no-search
if [[ $RC -eq 0 ]] && grep -q "rigB-key" "$H/.openbeast-client.env" && ! grep -q "rigA-device-key" "$H/.openbeast-client.env"; then
  pass "control: an explicitly given key for the new rig is used"
else
  fail "explicit key on switch (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "6. venv floor, python3.14, offline wheelhouse, atomic config (review 2026-11-16):"
# An interpreter whose `-m venv` builds a stub venv (a real one has no mcp).
cat > "$T/venv-python" <<EOF
#!/bin/bash
if [[ "\$1" == "-m" && "\$2" == "venv" ]]; then
  echo "venv \$*" >> "$S/py.calls"; d="\${@: -1}"; mkdir -p "\$d/bin"
  printf '#!/bin/bash\\nexit 0\\n' > "\$d/bin/python3"; cp "\$d/bin/python3" "\$d/bin/pip"
  chmod +x "\$d/bin/python3" "\$d/bin/pip"; exit 0
fi
echo vetted >> "$S/py.calls"; exec "$REAL_PY" "\$@"
EOF
chmod +x "$T/venv-python"
mkdir -p "$T/hold/.openbeast-client/venv/bin"
cat > "$T/hold/.openbeast-client/venv/bin/python3" <<'EOF'
#!/bin/bash
[[ "$1" == "-V" ]] && { echo "Python 3.10.14"; exit 0; }
case "$*" in *version_info*) exit 1 ;; esac
exit 0
EOF
chmod +x "$T/hold/.openbeast-client/venv/bin/python3"
run hold 200 200 OPENBEAST_PYTHON="$T/venv-python" -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "Python 3.10.14, below 3.11" && grep -q "venv -m venv --clear" "$S/py.calls"; then
  pass "a 3.10 venv from an older install is rebuilt with the vetted python, not reused"
else
  fail "old venv (rc=$RC, calls=$(tr '\n' ' ' < "$S/py.calls")): $OUT"
fi
run h200 200 200 OPENBEAST_PYTHON="$T/venv-python" -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && ! grep -q "venv -m venv" "$S/py.calls"; then
  pass "control: a >=3.11 venv is kept"
else
  fail "good venv rebuilt (rc=$RC, calls=$(tr '\n' ' ' < "$S/py.calls"))"
fi
printf '#!/bin/bash\necho py314 >> "%s/py.calls"\nexec "%s" "$@"\n' "$S" "$REAL_PY" > "$BIN/python3.14"
chmod +x "$BIN/python3.14"
run h314 200 200 OPENBEAST_PYTHON= -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && grep -q py314 "$S/py.calls" && ! grep -q bare "$S/py.calls"; then
  pass "python3.14 is found (only a 3.9 python3 besides it)"
else
  fail "python3.14 (rc=$RC, calls=$(tr '\n' ' ' < "$S/py.calls")): $OUT"
fi
rm -f "$BIN/python3.14"
mkdir -p "$T/wheels"
run hwh 200 200 -- --host rig.example --no-search --wheelhouse "$T/wheels"
if [[ $RC -eq 0 ]] && grep -q "^pydeps install --from $T/wheels -q" "$S/pydeps.calls"; then
  pass "--wheelhouse installs the pinned deps from it (pydeps --from: no index)"
else
  fail "--wheelhouse (rc=$RC, pydeps=$(tr '\n' ' ' < "$S/pydeps.calls")): $OUT"
fi
run hwe 200 200 OPENBEAST_WHEELHOUSE="$T/wheels" -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && grep -q "^pydeps install --from $T/wheels -q" "$S/pydeps.calls"; then
  pass "…and so does OPENBEAST_WHEELHOUSE"
else
  fail "OPENBEAST_WHEELHOUSE (rc=$RC, pydeps=$(tr '\n' ' ' < "$S/pydeps.calls")): $OUT"
fi
_ino() { stat -c '%i' "$1" 2>/dev/null || stat -f '%i' "$1"; }
OC="$T/hatom/.config/opencode/opencode.json"; mkdir -p "$(dirname "$OC")"
echo '{"model":"mine"}' > "$OC"; chmod 644 "$OC"; _before="$(_ino "$OC")"
run hatom 200 200 OPENBEAST_API_KEY=atom-key -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && [[ "$(_mode "$OC")" == "600" ]] && [[ "$(_ino "$OC")" != "$_before" ]] \
   && grep -q atom-key "$OC" && grep -q '"model": "mine"' "$OC" && ! ls "$(dirname "$OC")"/.opencode.json.* >/dev/null 2>&1; then
  pass "a keyed opencode.json is a NEW 0600 file (rename), never the old 0644 one rewritten"
else
  fail "atomic config (rc=$RC, mode=$(_mode "$OC")): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "7. the soul: opencode 'instructions' get the WebUI system prompt:"
# From here on the clone carries the real registry helper and prompt files
# (the earlier cases run without them on purpose: step 4/5a are skipped there).
install -m 644 "$REPO_DIR/scripts/lib/client_rigs.py" "$R/scripts/lib/"
install -m 644 "$REPO_DIR/system-prompt.md" "$REPO_DIR/system-prompt-tools.md" "$R/"
OC="$T/hsoul/.config/opencode/opencode.json"; mkdir -p "$(dirname "$OC")"
echo '{"instructions":["CONTRIBUTING.md","~/rules/mine.md"],"model":"mine"}' > "$OC"
_ins() { "$REAL_PY" -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])).get("instructions")))' "$OC"; }
SD="$T/hsoul/.openbeast-client/prompts"
WANT="[\"$SD/system-prompt.md\", \"$SD/system-prompt-tools.md\", \"$SD/opencode-client.md\", \"CONTRIBUTING.md\", \"~/rules/mine.md\"]"
run hsoul 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && [[ "$(_ins)" == "$WANT" ]] \
   && cmp -s "$SD/system-prompt.md" "$REPO_DIR/system-prompt.md" \
   && cmp -s "$SD/system-prompt-tools.md" "$REPO_DIR/system-prompt-tools.md" \
   && has "$OUT" "opencode instructions: system-prompt.md, system-prompt-tools.md, opencode-client.md"; then
  pass "install puts the soul + tool guide (copied) in front of the user's own instructions"
else
  fail "soul install (rc=$RC): $(_ins) $OUT"
fi
_first="$(cat "$OC")"
run hsoul 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && [[ "$(_ins)" == "$WANT" ]] && [[ "$(cat "$OC")" == "$_first" ]]; then
  pass "a re-run is idempotent: no duplicate entries, same file"
else
  fail "soul re-run (rc=$RC): $(_ins)"
fi
printf '#!/bin/bash\n[[ "$1" == --version ]] && echo "opencode v2.0.25"\nexit 0\n' > "$BIN/opencode"
run hsoul 200 200 -- --host rig.example --no-search
if [[ $RC -eq 0 ]] && has "$OUT" "2.0.25 ignores the \`instructions\` setting" && has "$OUT" "brew upgrade opencode"; then
  pass "an opencode that ignores 'instructions' (2.0.x) is named, with the upgrade"
else
  fail "old-opencode warning (rc=$RC): $OUT"
fi
printf '#!/bin/bash\nexit 0\n' > "$BIN/opencode"
# client_rigs.py's rolling backup holds the device key: uninstall removes it.
echo '{"provider":{"openbeast-rig":{"options":{"apiKey":"k"}}}}' > "$OC.openbeast-prev"
# …and so do the timestamped backups beast-apps and client-e2e make (review
# 2026-11-18); a backup someone else made is listed, never deleted.
for _b in bak-beast-apps-20261118-010101 bak-e2e-20261118-020202 bak-mine; do echo '{}' > "$OC.$_b"; done
chmod 640 "$OC"
run hsoul 200 200 -- --uninstall
if [[ ! -e "$OC.bak-beast-apps-20261118-010101" ]] && [[ ! -e "$OC.bak-e2e-20261118-020202" ]] \
   && [[ -e "$OC.bak-mine" ]] && has "$OUT" "removed 2 opencode.json backup(s)" && has "$OUT" "kept $OC.bak-mine"; then
  pass "--uninstall removes the key-holding backups our tools made and lists any other"
else
  fail "uninstall backups: $(ls -a "$(dirname "$OC")") $OUT"
fi
rm -f "$OC.bak-mine"
if [[ "$(_ins)" == '["CONTRIBUTING.md", "~/rules/mine.md"]' ]] && has "$OUT" "instructions (3 soul files)" \
   && [[ ! -e "$SD" ]] && grep -q '"model": "mine"' "$OC"; then
  pass "--uninstall removes only our instruction entries (and the copies); the user's stay"
else
  fail "soul uninstall: $(_ins) $OUT"
fi
if [[ ! -e "$OC.openbeast-prev" ]] && [[ "$(_mode "$OC")" == "640" ]] \
   && ! compgen -G "$(dirname "$OC")/.opencode.json.*" >/dev/null; then
  pass "--uninstall rewrites opencode.json atomically (mode kept, no temp left) and drops the keyed backup"
else
  fail "uninstall backup/atomic (mode=$(_mode "$OC")): $(ls -a "$(dirname "$OC")") $OUT"
fi
OC="$T/hsoul2/.config/opencode/opencode.json"; mkdir -p "$(dirname "$OC")"; echo '{}' > "$OC"
run hsoul2 200 200 -- --host rig.example --no-search
run hsoul2 200 200 -- --uninstall
if [[ "$(_ins)" == "null" ]]; then
  pass "with no user entries, uninstall leaves no empty 'instructions' behind"
else
  fail "soul uninstall, no user entries: $(_ins)"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
