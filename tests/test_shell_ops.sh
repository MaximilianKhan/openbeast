#!/bin/bash
# Shell-ops fixes, round 2 of the 2026-09-29 review — behavior tests.
#
# Usage: bash tests/test_shell_ops.sh
#
#   1  healthcheck.sh    LLAMA_API_KEY / tool-server keys never on curl argv
#   2  doctor.sh         tokens off argv; LAN bind is not "loopback-scoped";
#                        keyless tool server on a network bind FAILs; :443
#                        published with login off FAILs (WARNs with
#                        ALLOW_OPEN_WEBUI); default admin password FAILs,
#                        and no row when login is off; missing log-rotation
#                        timer WARNs
#   3  start.sh          edge token off argv; the log-rotation timer is
#                        installed on the default path (ensure_logrotate_timer)
#   4  setup-mcpo-keys   --rotate never puts the new key on any argv
#   5  lib/conf.sh       FILES_DIR ~ / relative; MODEL_URL and SEARXNG_URL
#                        follow BIND_HOST
#   6  lib/extensions    invalid EXTENSIONS words skipped, `*` not globbed
#   7  client.sh update  installs from the hash-pinned lock (pydeps.sh)
#   8  update.sh         --images mirrors the searxng pin into the client file
#   9  fetch-weight.sh   probes $HF_ENDPOINT; verify-weights.sh reports the
#                        weights dir lib/weights.sh resolves
#
# Everything runs against THROWAWAY copies under $TMPDIR with stub binaries
# that RECORD their calls. No stack, no GPU, no docker, no network, no real
# openbeast.conf or systemd user manager. Every positive assertion has a
# negative control next to it.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-shell-ops-XXXXXX")"
cleanup() { rm -rf "$T"; }
trap cleanup EXIT

echo "=== shell ops (round 2) ==="

# A sandbox rig: copies of the scripts under test, a conf this test writes,
# and stub binaries for everything that could reach the real machine.
SB="$T/rig"
mkdir -p "$SB/scripts/lib" "$SB/.run" "$SB/bin" "$SB/home"
cp "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$SB/"
for f in healthcheck.sh doctor.sh configure-webui.sh setup-mcpo-keys.sh; do
  cp "$REPO_DIR/scripts/$f" "$SB/scripts/"
done
cp "$REPO_DIR"/scripts/lib/*.sh "$SB/scripts/lib/"
for c in docker nvidia-smi sudo systemd-run smartctl pkill pgrep; do
  printf '#!/bin/bash\nexit 1\n' > "$SB/bin/$c"; chmod +x "$SB/bin/$c"
done

# curl stub: records argv (one arg per line) and every --config it is handed,
# and plays a healthy stack whose answers the test steers through env.
cat > "$SB/bin/curl" <<'STUB'
#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["STUB_DIR"]
with open(f"{log}/curl.argv", "a") as f:
    f.write("\n".join(args) + "\n")
url, w, i = "", False, 0
while i < len(args):
    a = args[i]
    if a in ("--config", "-K"):
        with open(f"{log}/curl.cfg", "a") as f:
            f.write(open(args[i + 1]).read())
        i += 2; continue
    if a == "-d" and args[i + 1] == "@-":
        sys.stdin.read()
    if a == "%{http_code}": w = True
    if a.startswith("http"): url = a
    i += 1
def out(o):   # compact JSON, like the real servers (the scripts grep it)
    sys.stdout.write(o if isinstance(o, str) else json.dumps(o, separators=(",", ":")))
    sys.exit(0)
if w: out("200")
if url.endswith(":8080/health"): out({"status": "ok"})
if url.endswith("/slots"): out([])
if ":3001/health" in url: out({"status": "ok", "auth": "open", "identity": "headers"})
if url.endswith("/api/version"): out({"version": "stub"})
if url.endswith("/api/config"):
    la = os.environ.get("LIVE_AUTH", "true")
    if la == "down": sys.exit(7)          # WebUI not answering: curl fails
    out({} if la == "unknown" else {"features": {"auth": la == "true"}})
if url.endswith("/api/v1/auths/signin"):
    out({"token": "tok"} if os.environ.get("DEFAULT_PW_WORKS") == "1" else {"detail": "no"})
if "/gate/health" in url: out({"service": "beast-gate", "auth": "devices", "devices": 1})
if "/api/chat/health" in url: out({"status": "ok", "reads": "allowlist", "running": 0})
if "/api/artifacts/health" in url: out({"status": "ok", "artifacts": 2})
if url.endswith(":8888") or "/search" in url: out("<html></html>")
sys.exit(7)
STUB
chmod +x "$SB/bin/curl"
# tailscale: `serve status` prints $TS_SERVE; everything else is silent.
cat > "$SB/bin/tailscale" <<'STUB'
#!/bin/bash
[[ "$1 $2" == "serve status" ]] && { printf '%b' "${TS_SERVE:-}"; exit 0; }
[[ "$1" == "status" ]] && { echo '{}'; exit 0; }
exit 0
STUB
# systemctl: only the --user manager questions the scripts ask are answered,
# from env: SD_USER (manager reachable?) and TIMER_ON (timer enabled?).
cat > "$SB/bin/systemctl" <<'STUB'
#!/bin/bash
echo "systemctl $*" >> "$STUB_DIR/systemctl.log"
case "$*" in
  "--user show-environment") [[ "${SD_USER:-0}" == 1 ]] && { echo HOME=/x; exit 0; }; exit 1 ;;
  "--user is-enabled --quiet openbeast-logrotate.timer") [[ "${TIMER_ON:-0}" == 1 ]]; exit ;;
esac
exit 1
STUB
chmod +x "$SB/bin/tailscale" "$SB/bin/systemctl"

reset_logs() { : > "$T/curl.argv"; : > "$T/curl.cfg"; : > "$T/systemctl.log"; }
# run_sb <script> [args…] — clean env, stubs first on PATH; extra env via RUN_ENV.
RUN_ENV=()
run_sb() {
  env -i HOME="$SB/home" PATH="$SB/bin:/usr/bin:/bin" STUB_DIR="$T" \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} bash "$@" 2>&1
}

# ---------------------------------------------------------------------------
echo ""
echo "1. healthcheck.sh — keys off curl's argv:"
LK="llama-$RANDOM$RANDOM"; AK="adm-$RANDOM$RANDOM"; GK="gst-$RANDOM$RANDOM"
printf 'LLAMA_API_KEY=%s\nMCPO_ADMIN_KEY=%s\nMCPO_GUEST_KEY=%s\nSEARXNG_SECRET=x\n' "$LK" "$AK" "$GK" > "$SB/openbeast.conf"
reset_logs
_O="$(run_sb "$SB/scripts/healthcheck.sh")"
if ! grep -qF -e "$LK" -e "$AK" -e "$GK" "$T/curl.argv" \
   && [[ "$(grep -cF "Bearer $LK" "$T/curl.cfg")" -ge 3 ]]; then
  # three keyed calls: check() on /health, the loading probe, and /slots
  pass "healthcheck sends LLAMA_API_KEY via --config on every keyed call, never argv"
else
  fail "healthcheck argv leak: $(grep -cF -e "$LK" -e "$AK" -e "$GK" "$T/curl.argv") hit(s); cfg=[$(tr '\n' ' ' < "$T/curl.cfg")] :: $_O"
fi
if has "$_O" "OK   llama.cpp" || has "$_O" "OK   llama"; then
  pass "…and the keyed probes still report the service up (negative control)"
else
  fail "healthcheck no longer sees a healthy llama: $_O"
fi

# ---------------------------------------------------------------------------
echo ""
echo "2. doctor.sh:"
GT="gate-$RANDOM$RANDOM"; CT="chat-$RANDOM$RANDOM"; ART="art-$RANDOM$RANDOM"
printf '%s' "$GT" > "$SB/.run/edge-local.token"
printf '%s' "$CT" > "$SB/.run/chat-local.token"
printf '%s' "$ART" > "$SB/.run/artifact-local.token"
doctor() { # doctor <conf lines…> — output in $_O
  printf '%s\n' "SEARXNG_SECRET=x" "$@" > "$SB/openbeast.conf"
  reset_logs
  _O="$(run_sb "$SB/scripts/doctor.sh")"
}
doctor "LLAMA_API_KEY=$LK" EDGE_GATE=true BEAST_CHAT=true BEAST_ARTIFACT=true
if ! grep -qF -e "$LK" -e "$GT" -e "$CT" -e "$ART" "$T/curl.argv" \
   && grep -qF "X-OpenBeast-Local: $GT" "$T/curl.cfg" \
   && grep -qF "X-OpenBeast-Local: $CT" "$T/curl.cfg" \
   && grep -qF "X-OpenBeast-Local: $ART" "$T/curl.cfg"; then
  pass "doctor's gate/chat/artifact tokens and the llama key reach curl via --config only"
else
  fail "doctor argv leak or token not sent: argv hits=$(grep -cF -e "$LK" -e "$GT" -e "$CT" -e "$ART" "$T/curl.argv") cfg=[$(tr '\n' ' ' < "$T/curl.cfg")]"
fi
if has "$_O" "beast-gate (:8090) — 1 enrolled" && has "$_O" "2 published page(s)"; then
  pass "…and the token-gated detail still comes back (negative control)"
else
  fail "doctor lost the token-gated detail: $(grep -E 'gate|artifact' <<< "$_O" | tr '\n' ' ')"
fi

doctor BIND_HOST=192.168.1.20
if ! has "$_O" "loopback-scoped (192.168.1.20)" && has "$_O" "BIND_HOST=192.168.1.20 is not loopback" \
   && has "$_O" "tool server keyless on a network bind"; then
  pass "a LAN BIND_HOST warns (was 'loopback-scoped'), and a keyless tool server there FAILs"
else
  fail "LAN bind rows: $(grep -iE 'bind|keyless' <<< "$_O" | tr '\n' ' ')"
fi
doctor BIND_HOST=192.168.1.20 MCPO_ADMIN_KEY=a MCPO_GUEST_KEY=b
if has "$_O" "is not loopback" && ! has "$_O" "tool server keyless"; then
  pass "…with tool-server keys set, the LAN bind warns but does not FAIL (control)"
else
  fail "keyed LAN bind: $(grep -iE 'bind|keyless' <<< "$_O" | tr '\n' ' ')"
fi
doctor BIND_HOST=127.0.0.1
if has "$_O" "bind host is loopback-scoped (127.0.0.1)" && ! has "$_O" "keyless"; then
  pass "…and loopback still passes (control)"
else
  fail "loopback bind row: $(grep -iE 'bind' <<< "$_O" | tr '\n' ' ')"
fi

SERVE_443='https://beast.example.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:3000\n'
RUN_ENV=(TS_SERVE="$SERVE_443" LIVE_AUTH=false)
doctor WEBUI_AUTH=false
if has "$_O" "published on :443 but WEBUI_AUTH is off"; then
  pass ":443 mounted with WEBUI_AUTH off FAILs (network-exposure-3)"
else
  fail ":443 + auth off: $(grep -iE '443|auth' <<< "$_O" | tr '\n' ' ')"
fi
doctor WEBUI_AUTH=true
if has "$_O" "RUNNING WebUI still has login off"; then
  pass ":443 mounted, conf auth on but the live WebUI still auth-off FAILs"
else
  fail ":443 + live auth off: $(grep -iE '443|auth' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=(TS_SERVE="$SERVE_443" LIVE_AUTH=true)
doctor WEBUI_AUTH=true
if has "$_O" "published on :443 with login enforced" && ! has "$_O" "every tailnet device is admin"; then
  pass "…:443 with login enforced passes (control)"
else
  fail ":443 + auth on: $(grep -iE '443|auth' <<< "$_O" | tr '\n' ' ')"
fi
# 2026-09-29 smoke test: with the WebUI down (docker unreachable) the row read
# "✓ … login enforced (live auth=unknown\nunknown)" — a false green, and the
# pipefail'd fallback printed "unknown" twice.
RUN_ENV=(TS_SERVE="$SERVE_443" LIVE_AUTH=down)
doctor WEBUI_AUTH=true
if has "$_O" "live login enforcement cannot be confirmed" && ! has "$_O" "login enforced (" \
   && ! grep -q '^unknown' <<< "$_O"; then
  pass "…:443 with the WebUI down WARNs instead of a false ✓"
else
  fail ":443 + WebUI down: $(grep -iE -A1 '443|auth' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=(TS_SERVE="$SERVE_443" LIVE_AUTH=true)
doctor WEBUI_AUTH=false ALLOW_OPEN_WEBUI=true
if has "$_O" "ALLOW_OPEN_WEBUI=true acknowledges it" && ! has "$_O" "but WEBUI_AUTH is off"; then
  pass "…published open on purpose (ALLOW_OPEN_WEBUI=true, from --i-accept-open-webui) WARNs, not FAILs"
else
  fail ":443 + acknowledged open WebUI: $(grep -iE '443|auth' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=(TS_SERVE='https://beast.example.ts.net:8443 (tailnet only)\n|-- / proxy http://127.0.0.1:8080\n' LIVE_AUTH=false)
doctor WEBUI_AUTH=false
if ! has "$_O" ":443"; then
  pass "…no :443 mount, no :443 row (control)"
else
  fail "a :443 row without a :443 mount: $(grep ':443' <<< "$_O" | tr '\n' ' ')"
fi

RUN_ENV=(LIVE_AUTH=true DEFAULT_PW_WORKS=1)
doctor WEBUI_AUTH=true
if has "$_O" "admin@localhost still signs in with upstream's default password"; then
  pass "a WebUI that still accepts admin@localhost/admin with login on FAILs"
else
  fail "default admin row: $(grep -iE 'admin' <<< "$_O" | tr '\n' ' ')"
fi
if grep -qx 'admin' "$T/curl.argv"; then
  fail "the default password was passed to curl on argv"
else
  pass "…and the probe never put the password on curl's argv"
fi
RUN_ENV=(LIVE_AUTH=true DEFAULT_PW_WORKS=0)
doctor WEBUI_AUTH=true
if has "$_O" "does not accept the upstream default password" && ! has "$_O" "still signs in"; then
  pass "…a rotated default passes (control)"
else
  fail "rotated default row: $(grep -iE 'admin' <<< "$_O" | tr '\n' ' ')"
fi
# The default fresh install: login off. The probe never signs in, so doctor
# must not print a green "does not accept the default password" it never
# checked (it did: configure-webui.sh answered 0 for "login off").
for _la in false unknown; do
  RUN_ENV=(LIVE_AUTH=$_la DEFAULT_PW_WORKS=1)
  doctor WEBUI_AUTH=false
  if has "$_O" "Open WebUI (:3000)" && ! has "$_O" "does not accept the upstream default password" \
     && ! has "$_O" "still signs in"; then
    pass "…live auth=$_la: no default-admin row at all (was a false green)"
  else
    fail "auth=$_la default-admin row: $(grep -iE 'admin|WebUI' <<< "$_O" | tr '\n' ' ')"
  fi
done

RUN_ENV=(SD_USER=1 TIMER_ON=0)
doctor
if has "$_O" "log rotation timer is missing or disabled"; then
  pass "a missing openbeast-logrotate timer WARNs (storage-04)"
else
  fail "logrotate row: $(grep -iE 'rotat' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=(SD_USER=1 TIMER_ON=1)
doctor
if has "$_O" "log rotation timer enabled" && ! has "$_O" "missing or disabled"; then
  pass "…an enabled timer passes (control)"
else
  fail "enabled timer row: $(grep -iE 'rotat' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=(SD_USER=0)
doctor
if ! has "$_O" "log rotation"; then
  pass "…no systemd user manager (macOS/CI): no row (control)"
else
  fail "logrotate row without a user manager: $(grep -iE 'rotat' <<< "$_O" | tr '\n' ' ')"
fi
RUN_ENV=()

# ---------------------------------------------------------------------------
echo ""
echo "3. start.sh:"
if grep -qE -- '-H "X-OpenBeast-Local' "$REPO_DIR/start.sh" "$REPO_DIR/scripts/doctor.sh" \
   || grep -qE -- '-H "Authorization: Bearer' "$REPO_DIR/start.sh" "$REPO_DIR/scripts/doctor.sh" \
        "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/configure-webui.sh"; then
  fail "a credential header is still built on curl's argv: $(grep -nE -- '-H "(X-OpenBeast-Local|Authorization: Bearer)' "$REPO_DIR/start.sh" "$REPO_DIR"/scripts/{doctor,healthcheck,configure-webui}.sh | tr '\n' ' ')"
else
  pass "no rig script builds a credential header on curl's argv"
fi
# ensure_logrotate_timer, lifted verbatim, with a stub logrotate.sh.
python3 - "$REPO_DIR/start.sh" "$T/ensure.sh" <<'PY'
import sys
src = open(sys.argv[1]).read()
a = src.index("ensure_logrotate_timer() {")
b = src.index("\n}\n", a) + 3
open(sys.argv[2], "w").write('set -euo pipefail\nSCRIPT_DIR="$SANDBOX"\n' + src[a:b]
                             + 'ensure_logrotate_timer\necho ENSURE-DONE\n')
PY
mkdir -p "$T/lr/scripts"
cat > "$T/lr/scripts/logrotate.sh" <<'STUB'
#!/bin/bash
echo "logrotate.sh $*" >> "$STUB_DIR/lr.log"
exit "${LR_RC:-0}"
STUB
chmod +x "$T/lr/scripts/logrotate.sh"
ensure() { # ensure [ENV=VAL…] — output in $_O, calls in $T/lr.log
  : > "$T/lr.log"
  _O="$(env -i PATH="$SB/bin:/usr/bin:/bin" STUB_DIR="$T" SANDBOX="$T/lr" "$@" bash "$T/ensure.sh" 2>&1)"
}
ensure SD_USER=1 TIMER_ON=0
if grep -qx 'logrotate.sh --install' "$T/lr.log" && has "$_O" "ENSURE-DONE"; then
  pass "start.sh installs the log-rotation timer when it is missing (storage-04)"
else
  fail "ensure_logrotate_timer (missing): $(cat "$T/lr.log") :: $_O"
fi
ensure SD_USER=1 TIMER_ON=1
if [[ ! -s "$T/lr.log" ]] && has "$_O" "ENSURE-DONE"; then
  pass "…already enabled: no reinstall (idempotent)"
else
  fail "ensure_logrotate_timer reinstalled an enabled timer: $(cat "$T/lr.log")"
fi
ensure SD_USER=0 TIMER_ON=0
if [[ ! -s "$T/lr.log" ]] && has "$_O" "ENSURE-DONE"; then
  pass "…no systemd user manager: no-op"
else
  fail "ensure_logrotate_timer without a user manager: $(cat "$T/lr.log")"
fi
ensure SD_USER=1 TIMER_ON=0 LOGROTATE_AUTOINSTALL=false
if [[ ! -s "$T/lr.log" ]]; then
  pass "…LOGROTATE_AUTOINSTALL=false opts out"
else
  fail "opt-out ignored: $(cat "$T/lr.log")"
fi
ensure SD_USER=1 TIMER_ON=0 LR_RC=1
if has "$_O" "ENSURE-DONE" && has "$_O" "log rotation not installed"; then
  pass "…a failed install warns and never stops the start (set -e safe)"
else
  fail "a failed install aborted start.sh: $_O"
fi

# ---------------------------------------------------------------------------
echo ""
echo "4. setup-mcpo-keys.sh --rotate — the new key never on argv:"
# Wrappers record the argv of every sed/awk the script runs, then exec the
# real tool, so the rotation still happens for real.
mkdir -p "$T/wrap"
for c in sed awk; do
  _real="$(command -v "$c")"
  printf '#!/bin/bash\nprintf "%%s\\n" "$@" >> "%s/tool.argv"\nexec %s "$@"\n' "$T" "$_real" > "$T/wrap/$c"
  chmod +x "$T/wrap/$c"
done
printf '# my conf\nSEARXNG_SECRET=keep\nMCPO_ADMIN_KEY=old-admin\n  MCPO_GUEST_KEY = old-guest\nMCPO_ADMIN_KEY_NOTE=untouched\n' > "$SB/openbeast.conf"
chmod 644 "$SB/openbeast.conf"
: > "$T/tool.argv"
_O="$(env -i HOME="$SB/home" PATH="$T/wrap:/usr/bin:/bin" bash "$SB/scripts/setup-mcpo-keys.sh" --rotate 2>&1)"; _rc=$?
_NA="$(sed -n 's/^MCPO_ADMIN_KEY=//p' "$SB/openbeast.conf")"
_NG="$(sed -n 's/^MCPO_GUEST_KEY=//p' "$SB/openbeast.conf")"
if [[ $_rc -eq 0 && ${#_NA} -eq 64 && ${#_NG} -eq 64 && "$_NA" != "$_NG" ]] \
   && ! grep -qF -e "$_NA" -e "$_NG" "$T/tool.argv"; then
  pass "both keys rotated, and neither ever appeared on a sed/awk command line"
else
  fail "rotation (rc=$_rc, admin=${#_NA} guest=${#_NG} chars, argv hits=$(grep -cF -e "${_NA:-x}" -e "${_NG:-x}" "$T/tool.argv")): $_O"
fi
if grep -qx '# my conf' "$SB/openbeast.conf" && grep -qx 'SEARXNG_SECRET=keep' "$SB/openbeast.conf" \
   && grep -qx 'MCPO_ADMIN_KEY_NOTE=untouched' "$SB/openbeast.conf" \
   && [[ "$(grep -c '^MCPO_ADMIN_KEY=' "$SB/openbeast.conf")" == 1 ]] \
   && [[ "$(stat -c %a "$SB/openbeast.conf")" == 600 ]]; then
  pass "…every other line is kept, a look-alike key is untouched, and the conf ends 0600"
else
  fail "conf after rotation: mode $(stat -c %a "$SB/openbeast.conf"): $(tr '\n' '|' < "$SB/openbeast.conf")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "5. lib/conf.sh:"
conf_eval() { # conf_eval <conf body> <env…> -- <snippet>
  local body="$1"; shift
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  printf '%s\n' "$body" > "$SB/openbeast.conf"
  env -i HOME="$T/h" PATH="/usr/bin:/bin" REPO_DIR="$SB" ${envs[@]+"${envs[@]}"} \
    bash -c "source '$SB/scripts/lib/conf.sh'; $1" 2>/dev/null
}
_F="$(conf_eval 'FILES_DIR=~/openbeast-files' -- 'echo "$OPENBEAST_FILES_DIR"')"
if [[ "$_F" == "$T/h/openbeast-files" ]]; then
  pass "FILES_DIR=~/… expands to \$HOME (was a literal '~' dir under the cwd)"
else
  fail "FILES_DIR=~/openbeast-files -> '$_F'"
fi
_F="$(conf_eval 'FILES_DIR=ws' -- 'echo "$OPENBEAST_FILES_DIR"')"
_F2="$(conf_eval 'FILES_DIR=/abs/ws' -- 'echo "$OPENBEAST_FILES_DIR"')"
if [[ "$_F" == "$SB/ws" && "$_F2" == "/abs/ws" ]]; then
  pass "…a relative FILES_DIR anchors to the checkout; an absolute one is kept (control)"
else
  fail "FILES_DIR relative -> '$_F', absolute -> '$_F2'"
fi
_M="$(conf_eval 'BIND_HOST=192.168.1.50' -- 'echo "$OPENBEAST_MODEL_URL"')"
_M0="$(conf_eval '' -- 'echo "$OPENBEAST_MODEL_URL"')"
_MW="$(conf_eval 'BIND_HOST=0.0.0.0' -- 'echo "$OPENBEAST_MODEL_URL"')"
_MR="$(conf_eval $'BIND_HOST=192.168.1.50\nAGENT_ROUTER=true' -- 'echo "$OPENBEAST_MODEL_URL"')"
if [[ "$_M" == "http://192.168.1.50:8080/v1" ]]; then
  pass "a LAN BIND_HOST gives Open WebUI http://192.168.1.50:8080/v1 (lifecycle-6)"
else
  fail "MODEL_URL for BIND_HOST=192.168.1.50 -> '$_M'"
fi
if [[ "$_M0" == "http://localhost:8080/v1" && "$_MW" == "http://localhost:8080/v1" \
      && "$_MR" == "http://localhost:8088/v1" ]]; then
  pass "…loopback/wildcard keep localhost, and the router (loopback-only) stays localhost (controls)"
else
  fail "MODEL_URL default='$_M0' wildcard='$_MW' router='$_MR'"
fi

_S="$(conf_eval 'BIND_HOST=192.168.1.50' -- 'echo "${SEARXNG_URL-unset}"')"
_S0="$(conf_eval '' -- 'echo "${SEARXNG_URL-unset}"')"
_SW="$(conf_eval 'BIND_HOST=0.0.0.0' -- 'echo "${SEARXNG_URL-unset}"')"
_SO="$(conf_eval 'BIND_HOST=192.168.1.50' SEARXNG_URL=http://search.lan:9 -- 'echo "$SEARXNG_URL"')"
if [[ "$_S" == "http://192.168.1.50:8888" ]]; then
  pass "a LAN BIND_HOST exports SEARXNG_URL on the bind host for the model's web_search (lifecycle-6)"
else
  fail "SEARXNG_URL for BIND_HOST=192.168.1.50 -> '$_S'"
fi
if [[ "$_S0" == unset && "$_SW" == unset && "$_SO" == "http://search.lan:9" ]]; then
  pass "…loopback/wildcard leave it unset, and an operator's SEARXNG_URL wins (controls)"
else
  fail "SEARXNG_URL default='$_S0' wildcard='$_SW' operator='$_SO'"
fi

# ---------------------------------------------------------------------------
echo ""
echo "6. lib/extensions.sh — only plain names reach the consumers:"
mkdir -p "$T/ext/extensions/dashboard" "$T/ext/extensions/other" "$T/ext/cwd"
touch "$T/ext/cwd/dashboard"   # what an unquoted `*` would have globbed to
_E="$(cd "$T/ext/cwd" && REPO_DIR="$T/ext" EXTENSIONS='dashboard/ ../x * other dashboard' \
        bash -c "source '$REPO_DIR/scripts/lib/extensions.sh'; ob_ext_enabled" 2>"$T/ext.err")"
if [[ "$_E" == $'other\ndashboard' ]] && grep -qF "'dashboard/'" "$T/ext.err" \
   && grep -qF "'../x'" "$T/ext.err" && grep -qF "'*'" "$T/ext.err"; then
  pass "dashboard/, ../x and * are skipped with a warning; valid names still listed"
else
  fail "ob_ext_enabled -> [$(tr '\n' ' ' <<< "$_E")] warnings: $(tr '\n' ' ' < "$T/ext.err")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "7. client.sh update — the hash-pinned lock, like setup-client.sh:"
CR="$T/client_repo"; CH="$T/client_home"
mkdir -p "$CR/scripts/lib" "$CR/agents" "$CH/.openbeast-client/venv/bin"
cp "$REPO_DIR/scripts/client.sh" "$CR/scripts/"
cp "$REPO_DIR/scripts/lib/curl_auth.sh" "$CR/scripts/lib/"
echo 'openai==1.0' > "$CR/agents/requirements.txt"
cat > "$CR/scripts/pydeps.sh" <<'STUB'
#!/bin/bash
echo "pydeps $* :: py=${OPENBEAST_PYTHON:-unset}" >> "$STUB_DIR/client.log"
exit "${PYDEPS_RC:-0}"
STUB
printf '#!/bin/bash\necho "venv-pip $*" >> "$STUB_DIR/client.log"\n' > "$CH/.openbeast-client/venv/bin/pip"
printf '#!/bin/bash\nexit 0\n' > "$CH/.openbeast-client/venv/bin/python3"
chmod +x "$CR/scripts/pydeps.sh" "$CH/.openbeast-client/venv/bin/pip" "$CH/.openbeast-client/venv/bin/python3"
cupdate() { # cupdate [ENV=VAL…]
  : > "$T/client.log"
  _O="$(env -i HOME="$CH" PATH="$SB/bin:/usr/bin:/bin" STUB_DIR="$T" "$@" bash "$CR/scripts/client.sh" update 2>&1)"; _rc=$?
}
cupdate
if [[ $_rc -eq 0 ]] && grep -q "^pydeps install -q :: py=$CH/.openbeast-client/venv/bin/python3" "$T/client.log" \
   && ! grep -q requirements.txt "$T/client.log"; then
  pass "update installs via pydeps.sh (hash-pinned) into the venv's python; requirements.txt unused"
else
  fail "client update (rc=$_rc): $(tr '\n' '|' < "$T/client.log") :: $_O"
fi
cupdate PYDEPS_RC=3
if [[ $_rc -ne 0 ]] && has "$_O" "HASH MISMATCH" && ! grep -q requirements.txt "$T/client.log"; then
  pass "…a HASH MISMATCH (exit 3) is fatal and never falls back"
else
  fail "client update hash mismatch (rc=$_rc): $(tr '\n' '|' < "$T/client.log") :: $_O"
fi
cupdate PYDEPS_RC=1
if [[ $_rc -eq 0 ]] && grep -q "venv-pip install -q -r $CR/agents/requirements.txt" "$T/client.log" \
   && has "$_O" "falling back"; then
  pass "…any other failure falls back to requirements.txt, loudly (control)"
else
  fail "client update fallback (rc=$_rc): $(tr '\n' '|' < "$T/client.log") :: $_O"
fi
cupdate PYDEPS_RC=1 OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 ]] && ! grep -q requirements.txt "$T/client.log"; then
  pass "…and OPENBEAST_PIP_STRICT=1 forbids that fallback"
else
  fail "client update strict (rc=$_rc): $(tr '\n' '|' < "$T/client.log")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "8. update.sh --images — the client searxng pin moves with the rig's:"
SBU="$T/upd"
mkdir -p "$SBU/scripts/lib" "$SBU/.run" "$T/binu"
cp "$REPO_DIR/scripts/update.sh" "$SBU/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$SBU/scripts/lib/"
D_SX="sha256:$(printf 'b%.0s' $(seq 1 64))"
D_OW="sha256:$(printf 'a%.0s' $(seq 1 64))"
cat > "$T/binu/docker" <<STUB
#!/bin/bash
case "\$1" in
  inspect)
    case "\${@: -1}" in
      ghcr.io/open-webui/open-webui:main) echo "ghcr.io/open-webui/open-webui@$D_OW" ;;
      searxng/searxng:latest)             echo "searxng/searxng@$D_SX" ;;
    esac ;;
esac
exit 0
STUB
chmod +x "$T/binu/docker"
OLD_SX="searxng/searxng:latest@sha256:$(printf '2%.0s' $(seq 1 64))"
OLD_OW="ghcr.io/open-webui/open-webui:main@sha256:$(printf '1%.0s' $(seq 1 64))"
printf 'services:\n  open-webui:\n    image: %s\n  searxng:\n    image: %s\n' "$OLD_OW" "$OLD_SX" > "$SBU/docker-compose.yml"
printf 'services:\n  searxng:\n    # client copy\n    image: %s\n    container_name: c\n' "$OLD_SX" > "$SBU/scripts/client-searxng.compose.yml"
_O="$(env -i HOME="$T/h" PATH="$T/binu:/usr/bin:/bin" bash "$SBU/scripts/update.sh" --images 2>&1)"; _rc=$?
if [[ $_rc -eq 0 ]] && grep -qx "    image: searxng/searxng:latest@$D_SX" "$SBU/scripts/client-searxng.compose.yml" \
   && grep -qx "    image: searxng/searxng:latest@$D_SX" "$SBU/docker-compose.yml" \
   && grep -qx '    # client copy' "$SBU/scripts/client-searxng.compose.yml"; then
  pass "a searxng bump is mirrored into scripts/client-searxng.compose.yml (network-exposure-5)"
else
  fail "client pin after --images (rc=$_rc): $(tr '\n' '|' < "$SBU/scripts/client-searxng.compose.yml") :: $_O"
fi
if ! grep -q 'open-webui' "$SBU/scripts/client-searxng.compose.yml"; then
  pass "…and only the searxng pin: the open-webui bump never lands in the client file (control)"
else
  fail "the client compose picked up a non-searxng image"
fi
# Already-drifted client pin while the rig is current: re-synced too.
printf 'services:\n  searxng:\n    image: %s\n' "$OLD_SX" > "$SBU/scripts/client-searxng.compose.yml"
_O="$(env -i HOME="$T/h" PATH="$T/binu:/usr/bin:/bin" bash "$SBU/scripts/update.sh" --images 2>&1)"
if grep -qx "    image: searxng/searxng:latest@$D_SX" "$SBU/scripts/client-searxng.compose.yml"; then
  pass "…a client pin that had already drifted is re-synced even when the rig's is current"
else
  fail "drifted client pin left alone: $(tr '\n' '|' < "$SBU/scripts/client-searxng.compose.yml")"
fi
# EXTENSION FRAGMENTS (the ntfy compose.yaml): pinned by digest like the core,
# and nothing moved them. --images now re-resolves each fragment's pinned TAG
# and rewrites its digest, and recreates with the ENABLED fragments merged.
D_NT="sha256:$(printf 'd%.0s' $(seq 1 64))"
OLD_NT="reg.example/ntfy:v2.28.0@sha256:$(printf '3%.0s' $(seq 1 64))"
mkdir -p "$SBU/extensions/ntfy" "$SBU/extensions/idle"
printf 'KIND=compose\n' > "$SBU/extensions/ntfy/manifest"
printf 'KIND=compose\n' > "$SBU/extensions/idle/manifest"
printf 'services:\n  ntfy:\n    # was %s\n    image: %s\n' "$OLD_NT" "$OLD_NT" > "$SBU/extensions/ntfy/compose.yaml"
printf 'services:\n  idle:\n    image: reg.example/idle:v1\n' > "$SBU/extensions/idle/compose.yaml"
cat > "$T/binu/docker" <<STUB
#!/bin/bash
echo "docker \$*" >> "$T/binu.log"
case "\$1" in
  inspect)
    case "\${@: -1}" in
      ghcr.io/open-webui/open-webui:main) echo "ghcr.io/open-webui/open-webui@$D_OW" ;;
      searxng/searxng:latest)             echo "searxng/searxng@$D_SX" ;;
      reg.example/ntfy:v2.28.0)           echo "reg.example/ntfy@$D_NT" ;;
    esac ;;
  compose)
    [[ " \$* " == *" ps "* ]] && echo running-container-id ;;
esac
exit 0
STUB
: > "$T/binu.log"
_O="$(env -i HOME="$T/h" PATH="$T/binu:/usr/bin:/bin" OPENBEAST_EXTENSIONS=ntfy bash "$SBU/scripts/update.sh" --images 2>&1)"; _rc=$?
if [[ $_rc -eq 0 ]] && grep -qx "    image: reg.example/ntfy:v2.28.0@$D_NT" "$SBU/extensions/ntfy/compose.yaml" \
   && grep -qx "    # was $OLD_NT" "$SBU/extensions/ntfy/compose.yaml" \
   && grep -q "docker pull -q reg.example/ntfy:v2.28.0" "$T/binu.log"; then
  pass "--images re-resolves an extension fragment's pinned tag and rewrites its digest (image line only)"
else
  fail "extension pin after --images (rc=$_rc): $(tr '\n' '|' < "$SBU/extensions/ntfy/compose.yaml") :: $_O"
fi
if has "$_O" "reg.example/idle:v1 is not digest-pinned" \
   && grep -qx '    image: reg.example/idle:v1' "$SBU/extensions/idle/compose.yaml"; then
  pass "…an unpinned fragment image is named, never silently pulled or rewritten"
else
  fail "unpinned fragment: $_O"
fi
if grep -q "docker compose -f $SBU/docker-compose.yml -f $SBU/extensions/ntfy/compose.yaml up -d" "$T/binu.log" \
   && ! grep -q "extensions/idle/compose.yaml up" "$T/binu.log"; then
  pass "…and running containers are recreated with the ENABLED fragments merged (not the disabled one)"
else
  fail "recreate args: $(grep 'compose' "$T/binu.log" | tr '\n' '|')"
fi
# A bundle-installed fragment (image: sha256:<id>) is reported, not skipped.
printf 'services:\n  ntfy:\n    image: sha256:%s\n' "$(printf 'e%.0s' $(seq 1 64))" > "$SBU/extensions/ntfy/compose.yaml"
_O="$(env -i HOME="$T/h" PATH="$T/binu:/usr/bin:/bin" bash "$SBU/scripts/update.sh" --images 2>&1)"
if has "$_O" "extensions/ntfy/compose.yaml runs a bundle content ID"; then
  pass "…a fragment a bundle install rewrote to a content ID says how to resume pinning"
else
  fail "content-ID fragment: $_O"
fi
rm -rf "$SBU/extensions"
if has "$(sed -n '/OFFLINE=true → skipping the python upgrade/,/wheels/p' "$REPO_DIR/scripts/update.sh")" "COMMIT it"; then
  pass "OFFLINE guidance says to commit the regenerated lock (pydeps refuses a stick-borne one)"
else
  fail "update.sh OFFLINE guidance still leads into pydeps's 'cannot vouch for' refusal"
fi

# ---------------------------------------------------------------------------
echo ""
echo "9. fetch-weight.sh / verify-weights.sh:"
FW="$T/fw"
mkdir -p "$FW/scripts/lib" "$T/binf"
cp "$REPO_DIR/scripts/fetch-weight.sh" "$REPO_DIR/scripts/verify-weights.sh" "$FW/scripts/"
cp "$REPO_DIR/scripts/lib/weights.sh" "$FW/scripts/lib/"
printf 'PENDING\t0\ttest.gguf\torg/repo\t-\n' > "$FW/scripts/weights.registry"
printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == http* ]] && echo "$a" >> "%s/fw.log"; done\nexit 7\n' "$T" > "$T/binf/curl"
chmod +x "$T/binf/curl"
: > "$T/fw.log"
_O="$(env -i HOME="$T/h" PATH="$T/binf:/usr/bin:/bin" OPENBEAST_WEIGHTS_DIR="$T/wd" \
        HF_ENDPOINT=https://hf-mirror.example/ bash "$FW/scripts/fetch-weight.sh" test.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 4 ]] && grep -q '^https://hf-mirror.example/api/whoami-v2$' "$T/fw.log" \
   && ! grep -q 'huggingface.co' "$T/fw.log" && has "$_O" "No route to https://hf-mirror.example"; then
  pass "the online probe asks HF_ENDPOINT (a mirror-only box is not told 'no route')"
else
  fail "HF_ENDPOINT probe (rc=$_rc): $(tr '\n' ' ' < "$T/fw.log") :: $_O"
fi
: > "$T/fw.log"
env -i HOME="$T/h" PATH="$T/binf:/usr/bin:/bin" OPENBEAST_WEIGHTS_DIR="$T/wd2" \
  bash "$FW/scripts/fetch-weight.sh" test.gguf >/dev/null 2>&1
if grep -q '^https://huggingface.co/api/whoami-v2$' "$T/fw.log"; then
  pass "…no HF_ENDPOINT: huggingface.co as before (control)"
else
  fail "default probe: $(tr '\n' ' ' < "$T/fw.log")"
fi
_O="$(env -i HOME="$T/h" PATH="/usr/bin:/bin" OPENBEAST_WEIGHTS_DIR="$T/not-there" \
        bash "$FW/scripts/verify-weights.sh" 2>&1)"; _rc=$?
if [[ $_rc -eq 0 ]] && has "$_O" "($T/not-there)"; then
  pass "verify-weights reports the dir lib/weights.sh resolved, even when it is missing"
else
  fail "verify-weights with a missing OPENBEAST_WEIGHTS_DIR (rc=$_rc): $_O"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
