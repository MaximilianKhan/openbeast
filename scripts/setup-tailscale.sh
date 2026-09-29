#!/bin/bash
# OpenBeast remote access — one-shot Tailscale setup. Idempotent.
#
#   ./scripts/setup-tailscale.sh [--publish-searxng] [--publish-slot]
#                                [--publish-chat] [--publish-artifact]
#                                [--i-accept-open-webui]
#   ./scripts/setup-tailscale.sh  --unpublish-searxng | --unpublish-slot
#                               | --unpublish-chat | --unpublish-artifact
#   ./scripts/setup-tailscale.sh  --status    # read-only: print the mount table
#
# What it does:
#   1. Installs tailscale (pacman) and enables tailscaled
#   2. Joins your tailnet (prints a login URL on first run)
#   3. Publishes, tailnet-only, with automatic HTTPS:
#        https://<host>.<tailnet>.ts.net       → Open WebUI (:3000)
#        https://<host>.<tailnet>.ts.net:8443  → llama-server API (:8080)
#   4. Prints the URLs to use from your phone/laptop
#
# The identity tool server (:3001) and SearXNG (:8888) are NOT published by
# default — internal plumbing for the model, not human-facing services.
#
# --publish-searxng (client mode, docs/BEAST_SLOT.md) additionally
# publishes SearXNG at :8889 so a thin-client laptop's local web_search tool
# can use the rig's private metasearch. SECURITY: SearXNG has no auth and
# its rate limiter is off, so ANY tailnet device can search through it —
# same trust boundary as the llama endpoint, acceptable on a personal
# tailnet, never public (this script never funnels). Undo with
# --unpublish-searxng (standalone; doesn't rerun setup).
#
# --publish-slot publishes the beast-slot status/discovery API at :8444
# (→ the dashboard extension on :3002): read-only JSON — loaded model,
# slots busy/total, context, service health. Lets clients answer "what am
# I talking to" (llama-server ignores the requested model name). Requires
# the dashboard extension (./scripts/ext.sh enable dashboard). Undo with
# --unpublish-slot.
#
# --publish-chat publishes beast-chat at :8445 (→ agents/chat_server.py on
# CHAT_PORT): the operator console for the rig's own agent and job sessions —
# watch a campaign from a phone, steer an agent, stop one. Requires
# BEAST_CHAT=true in openbeast.conf. Two-tier auth (docs/BEAST_CHAT.md):
# READING is your tailnet login against CHAT_OPERATORS; WRITING (send, stop,
# start an agent) also needs a chat-scoped device key, because starting an
# agent is remote code execution on the rig. Undo with --unpublish-chat.
# --publish-artifact publishes beast-artifact at :8446 (→ the artifact
# server on ARTIFACT_PORT, default :3004): the gallery and the pages the
# model publishes, so an artifact URL opens on a phone. Requires
# BEAST_ARTIFACT=true in openbeast.conf. READ access is gated on the tailnet
# login ONLY when ARTIFACT_OPERATORS lists someone — an empty list means every
# signed-in tailnet device can read, and the flag says so out loud when it is.
# WRITES stay loopback-only either way, so nothing on the phone path can
# publish or delete. Undo with --unpublish-artifact.
#
# The WebUI (:443) is published ONLY behind its login wall. Before mounting
# it the script persists WEBUI_AUTH=true, checks that the RUNNING WebUI
# actually enforces it (a container started before this run still has auth
# off — every visitor would be admin, with bash), and retires Open WebUI's
# built-in admin@localhost/"admin" account password. If the running WebUI has
# auth off, :443 is left unpublished until the stack is restarted and this
# script re-run. An explicit WEBUI_AUTH=false in openbeast.conf also blocks
# :443 unless --i-accept-open-webui says the open WebUI is intended. That
# acknowledgement is persisted as ALLOW_OPEN_WEBUI=true in openbeast.conf, so
# re-runs keep honouring it and doctor.sh reports the open :443 as a WARN
# (acknowledged) instead of a FAIL. Delete that line to take it back.
#
# --status prints which OpenBeast surface sits on which tailnet port and
# changes nothing: no sudo, no openbeast.conf write, no serve reconfiguring.
#
# Every mount targets the address its service actually BINDS (lib/net.sh
# ob_probe_host): BIND_HOST for the WebUI, inference, SearXNG, slot and
# artifact servers, OPENBEAST_CHAT_BIND for beast-chat. A hard-coded
# 127.0.0.1 served 502s on a rig bound to a specific LAN/tailnet address.
#
# Public internet exposure (tailscale funnel) is deliberately not offered.
# The tailnet is the security perimeter. See docs/REMOTE_ACCESS_PLAN.md.
set -euo pipefail

STATUS_ONLY=0
PUBLISH_SEARXNG=0
PUBLISH_SLOT=0
PUBLISH_CHAT=0
PUBLISH_ARTIFACT=0
ACCEPT_OPEN_WEBUI=0
for _arg in "$@"; do
  case "$_arg" in
    --publish-searxng)   PUBLISH_SEARXNG=1 ;;
    --unpublish-searxng)
      sudo tailscale serve --https=8889 off
      echo "SearXNG unpublished from the tailnet (:8889 off)."
      exit 0 ;;
    --publish-slot)      PUBLISH_SLOT=1 ;;
    --unpublish-slot)
      sudo tailscale serve --https=8444 off
      echo "beast-slot status API unpublished from the tailnet (:8444 off)."
      exit 0 ;;
    --publish-chat)      PUBLISH_CHAT=1 ;;
    --unpublish-chat)
      # No conf needed to unpublish: the mount is keyed by the PUBLISHED port
      # (8445), not by CHAT_PORT. Keeping this branch conf-free means it still
      # works on a rig whose openbeast.conf is broken — exactly when you most
      # want to take a surface down.
      sudo tailscale serve --https=8445 off
      echo "beast-chat unpublished from the tailnet (:8445 off)."
      exit 0 ;;
    --publish-artifact)  PUBLISH_ARTIFACT=1 ;;
    --unpublish-artifact)
      sudo tailscale serve --https=8446 off
      echo "beast-artifact unpublished from the tailnet (:8446 off)."
      exit 0 ;;
    --i-accept-open-webui) ACCEPT_OPEN_WEBUI=1 ;;
    --status)            STATUS_ONLY=1 ;;
    -h|--help) sed -n '2,74p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $_arg (see --help)" >&2; exit 2 ;;
  esac
done

# The mount table: which OpenBeast surface sits on which tailnet port, and
# whether it is mounted right now. `tailscale serve status` names upstreams,
# not features. Purely informational — never fails the caller.
_print_mounts() { # _print_mounts <chat-port> <artifact-port>
  local serve_now row port what state
  serve_now="$(tailscale serve status 2>/dev/null || true)"
  echo "      Tailnet serve mounts:"
  printf '        %-6s  %-34s  %s\n' "PORT" "SURFACE" "STATE"
  for row in \
    "443|Open WebUI (:3000)" \
    "8443|inference (llama-server / beast-gate)" \
    "8444|beast-slot status API (:3002)" \
    "8445|beast-chat console (:$1)" \
    "8446|beast-artifact pages (:$2)" \
    "8889|SearXNG for thin clients (:8888)"; do
    port="${row%%|*}"; what="${row#*|}"
    # The default :443 entry prints WITHOUT a port token, so it needs its own
    # pattern; a port-keyed grep alone silently omits the WebUI.
    if [[ "$port" == "443" ]]; then
      printf '%s\n' "$serve_now" | grep -qE '^https://[^ :]+(:443)?( |$)' && state=published || state=-
    else
      printf '%s\n' "$serve_now" | grep -qE "^https://[^ ]+:$port( |$)" && state=published || state=-
    fi
    printf '        %-6s  %-34s  %s\n' "$port" "$what" "$state"
  done
}

if [[ $STATUS_ONLY -eq 1 ]]; then
  # Read-only by construction: lib/conf.sh is NOT sourced here (it may write
  # a generated secret into openbeast.conf); the two port labels are read
  # straight from the env / conf with a plain grep.
  command -v tailscale >/dev/null 2>&1 || {
    echo "tailscale is not installed — nothing is published on a tailnet." >&2; exit 1; }
  _conf_file="$(cd "$(dirname "$0")/.." && pwd)/openbeast.conf"
  _conf_get() { # _conf_get <KEY> <default>
    local v
    v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$_conf_file" 2>/dev/null | tail -n1 \
         | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]+(#.*)?$//; s/^["'\'']//; s/["'\'']$//' || true)"
    printf '%s\n' "${v:-$2}"
  }
  echo "Current serve config (tailscale serve status):"
  tailscale serve status 2>/dev/null | sed 's/^/      /' || true
  echo ""
  _print_mounts "${OPENBEAST_CHAT_PORT:-$(_conf_get CHAT_PORT 3003)}" \
                "${OPENBEAST_ARTIFACT_PORT:-$(_conf_get ARTIFACT_PORT 3004)}"
  exit 0
fi

# Tailnet machine name — becomes https://beast.<tailnet>.ts.net everywhere.
# (Chosen 2026-07-07; independent of the system hostname.)
TS_HOSTNAME="${TS_HOSTNAME:-beast}"

if [[ $EUID -eq 0 ]]; then
  echo "Run as your normal user — the script sudo's only where needed." >&2
  exit 1
fi

echo "=== OpenBeast remote access setup (Tailscale) ==="
echo ""

# --- 1. Install + enable -----------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  echo "[1/4] Installing tailscale..."
  if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm tailscale
  elif command -v apt-get >/dev/null 2>&1 || command -v dnf >/dev/null 2>&1; then
    # Tailscale's official installer handles Debian/Ubuntu/Fedora repos.
    curl -fsSL https://tailscale.com/install.sh | sh
  else
    echo "Error: no supported package manager found." >&2
    echo "       Install tailscale manually (https://tailscale.com/download)" >&2
    echo "       and re-run this script." >&2
    exit 1
  fi
else
  echo "[1/4] tailscale already installed."
fi

if ! systemctl is-active --quiet tailscaled; then
  echo "      Enabling tailscaled..."
  sudo systemctl enable --now tailscaled
  sleep 1
else
  echo "      tailscaled already running."
fi

# --- 2. Join the tailnet -----------------------------------------------------
if tailscale status >/dev/null 2>&1; then
  echo "[2/4] Already joined a tailnet."
else
  echo "[2/4] Joining your tailnet as '$TS_HOSTNAME' — a browser login URL will print below."
  echo "      (Sign in with any SSO account; the free plan is plenty.)"
  sudo tailscale up --hostname="$TS_HOSTNAME"
fi

# --- 3. Publish WebUI + API over HTTPS, tailnet-only -------------------------
# `tailscale serve --https` needs two one-time toggles on the tailnet
# (not on this machine): MagicDNS and HTTPS Certificates. Without them the
# serve command blocks silently-forever — learned the hard way on first
# run. Check proactively and walk the user through it instead.
_ts_ready() {
  tailscale status --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
ok = bool(d.get('Self', {}).get('DNSName')) and bool(d.get('CertDomains'))
print('yes' if ok else 'no')"
}

echo "[3/4] Configuring tailscale serve (tailnet-only HTTPS)..."
if [[ "$(_ts_ready)" != "yes" ]]; then
  echo ""
  echo "      One-time tailnet setup needed (takes ~20 seconds, whole tailnet):"
  echo "        1. Open   https://login.tailscale.com/admin/dns"
  echo "        2. Enable 'MagicDNS'            (if not already on)"
  echo "        3. Enable 'HTTPS Certificates'  (further down the same page;"
  echo "           the cert-transparency warning is expected — only machine"
  echo "           NAMES become public, your services stay tailnet-only)"
  echo ""
  echo -n "      Waiting for the toggles"
  # Bounded (30 min): an unattended/scripted run must not hang forever on a
  # browser toggle nobody is going to click.
  _ts_waited=0
  while [[ "$(_ts_ready)" != "yes" ]]; do
    if [[ $_ts_waited -ge 1800 ]]; then
      echo ""
      echo "      Timed out after 30 min waiting for the admin-console toggles." >&2
      echo "      Enable MagicDNS + HTTPS Certificates, then re-run this script" >&2
      echo "      (it is idempotent)." >&2
      exit 1
    fi
    sleep 5
    _ts_waited=$((_ts_waited + 5))
    echo -n "."
  done
  echo " done!"
fi
# Resolve settings via lib/conf.sh, not an ad-hoc grep: that keeps the
# documented env-var-over-conf precedence and one parser for the whole repo.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "$(dirname "$0")/lib/conf.sh"
# shellcheck source=/dev/null
source "$(dirname "$0")/lib/net.sh"   # ob_probe_host (conf.sh sources it too)

# Where each mount must point: the address its service BINDS, dialled the
# way a local client dials it (wildcard -> loopback, IPv6 bracketed, a
# specific LAN/tailnet address as itself — a socket bound there refuses
# 127.0.0.1, so a loopback mount was a 502).
UP_HOST="$(ob_probe_host "$BIND_HOST")"
CHAT_UP_HOST="$(ob_probe_host "${OPENBEAST_CHAT_BIND:-127.0.0.1}")"
_is_loop() { case "$1" in 127.*|"[::1]") return 0 ;; *) return 1 ;; esac; }

# --- 3a. The WebUI login boundary, BEFORE the WebUI goes tailnet-wide -------
# Local-only installs run WEBUI_AUTH=false (no login wall). Going remote is
# exactly when per-user auth + RBAC start to matter, so persist it in
# openbeast.conf (idempotent) — and do it before :443 exists, not after.
CONF="$REPO_DIR/openbeast.conf"
touch "$CONF"
if ! grep -qE '^[[:space:]]*WEBUI_AUTH[[:space:]]*=' "$CONF"; then
  printf '\n# Remote access enabled — require a WebUI login (RBAC tiers apply).\nWEBUI_AUTH=true\n' >> "$CONF"
  WEBUI_AUTH=true
  echo "      Enabled WebUI login (WEBUI_AUTH=true in openbeast.conf)."
else
  echo "      WEBUI_AUTH already set — resolves to: ${WEBUI_AUTH:-false}"
fi

# What the RUNNING WebUI enforces (public /api/config → features.auth):
# true / false / unknown (not running — it will start from the conf).
# One word, always: a failed curl under pipefail must not print a second line.
_webui_live_auth() {
  local cfg
  cfg=$(curl -s -m 5 "http://$UP_HOST:3000/api/config" 2>/dev/null) || cfg=""
  printf '%s' "$cfg" | python3 -c "
import sys, json
try:
    a = json.load(sys.stdin).get('features', {}).get('auth')
except Exception:
    a = None
print('unknown' if a is None else ('true' if a else 'false'))" 2>/dev/null \
    || echo unknown
}

# When /api/config does not answer, "not running" and "an auth-off container
# still booting" look the same — and start.sh does not wait for the WebUI.
# Ask docker which it is: absent | unqueryable | <status>:<env auth>.
_webui_container() {
  command -v docker >/dev/null 2>&1 || { echo absent; return 0; }
  local out
  if ! out=$(docker inspect -f '{{.State.Status}}{{range .Config.Env}} {{.}}{{end}}' \
               open-webui 2>&1); then
    if printf '%s' "$out" | grep -qi 'no such'; then echo absent; else echo unqueryable; fi
    return 0
  fi
  local status=${out%% *} auth=false
  [[ " $out " == *" WEBUI_AUTH=true "* ]] && auth=true
  echo "${status}:${auth}"
}

WEBUI_PUBLISHED=0
_webui_block=""
# The flag given on an earlier run, persisted (see below), counts as given.
ACCEPT_FROM_FLAG=$ACCEPT_OPEN_WEBUI
[[ "${ALLOW_OPEN_WEBUI:-false}" == "true" ]] && ACCEPT_OPEN_WEBUI=1
if [[ "${WEBUI_AUTH:-false}" != "true" ]]; then
  if [[ $ACCEPT_OPEN_WEBUI -eq 1 ]]; then
    echo "      WARNING: WEBUI_AUTH=false and --i-accept-open-webui given — publishing"
    echo "               the WebUI with NO login. Every tailnet device is admin, with"
    echo "               bash through the privileged tool connection."
  else
    _webui_block="WEBUI_AUTH=false in openbeast.conf — publishing it would make every
               tailnet device an admin (with bash). Remove that line (or set it to
               true), restart the stack, and re-run; or pass --i-accept-open-webui
               if an open WebUI on your tailnet is really what you want."
  fi
else
  _live="$(_webui_live_auth)"
  _ct=""
  if [[ "$_live" == unknown ]]; then
    _ct="$(_webui_container)"
    if [[ "$_ct" == running:* ]]; then
      # Up but not answering: booting (migrations, embedding download).
      _wait_s="${WEBUI_WAIT_S:-90}"
      echo "      Open WebUI is starting — waiting up to ${_wait_s}s for it to answer..."
      _deadline=$((SECONDS + _wait_s))
      while [[ "$_live" == unknown ]] && (( SECONDS < _deadline )); do
        sleep 2
        _live="$(_webui_live_auth)"
      done
    fi
  fi
  case "$_live" in
    false)
      _webui_block="the RUNNING WebUI was started with auth OFF (the conf change only
               applies on restart). Publishing now would make every tailnet device
               an admin. Restart the stack (./stop.sh && ./start.sh), then re-run
               this script to publish the WebUI." ;;
    true)
      # Open WebUI's built-in admin@localhost was created with upstream's
      # fixed password "admin" while auth was off. Retire it before the
      # login page is reachable from the tailnet.
      if ! "$REPO_DIR/scripts/configure-webui.sh" --secure-default-admin; then
        _webui_block="admin@localhost still has upstream's default password and it
               could not be rotated (see the warning above). Change it, then re-run."
      fi ;;
    *)
      case "$_ct" in
        running:true)
          _webui_block="the WebUI container is up but never answered, so its default
               admin password could not be checked. Re-run this script once it is up." ;;
        absent|*:true)
          # Not running (or a stopped container that restarts auth on): it
          # starts from the conf, and start.sh's configure-webui.sh run
          # retires the default password then.
          : ;;
        running:*|*:false)
          # A container carrying WEBUI_AUTH=false keeps it (restart:
          # unless-stopped) until compose recreates it.
          _webui_block="the open-webui container was created with auth OFF and is not
               answering yet. Publishing now would make every tailnet device an
               admin once it boots. Restart the stack (./stop.sh && ./start.sh),
               wait for the WebUI, then re-run this script." ;;
        *)
          _webui_block="the WebUI is not answering and docker could not be asked
               whether an auth-off open-webui container exists. Start the stack
               (./start.sh), wait for the WebUI, then re-run this script." ;;
      esac ;;
  esac
fi
if [[ -n "$_webui_block" ]]; then
  echo "      NOT publishing the WebUI (:443): $_webui_block" >&2
  # An earlier run may have mounted it — take it down rather than leave an
  # open admin UI on the tailnet.
  sudo tailscale serve --https=443 off >/dev/null 2>&1 || true
else
  sudo tailscale serve --bg --https=443  "http://$UP_HOST:3000"
  WEBUI_PUBLISHED=1
  # Persist --i-accept-open-webui: without a recorded acknowledgement doctor
  # FAILed this deliberate configuration on every run, forever. Replace any
  # earlier assignment; keep the conf 0600 (it holds the stack's secrets);
  # write a temp file in the same dir and mv, so a crash never truncates it.
  if [[ "${WEBUI_AUTH:-false}" != "true" && $ACCEPT_FROM_FLAG -eq 1 \
        && "${ALLOW_OPEN_WEBUI:-false}" != "true" ]]; then
    _conf_tmp="$(umask 077; mktemp "$CONF.XXXXXX")"
    { grep -vE '^[[:space:]]*ALLOW_OPEN_WEBUI[[:space:]]*=' "$CONF" || true
      printf '\n# --i-accept-open-webui: the WebUI is published on :443 with NO login, on purpose.\nALLOW_OPEN_WEBUI=true\n'
    } > "$_conf_tmp"
    chmod 600 "$_conf_tmp" && mv -f "$_conf_tmp" "$CONF"
    ALLOW_OPEN_WEBUI=true
    echo "      Recorded ALLOW_OPEN_WEBUI=true in openbeast.conf (delete it to take the"
    echo "      acknowledgement back; doctor.sh warns about the open :443 while it is set)."
  fi
fi

# :8443 = the inference endpoint remote clients use. When beast-gate is
# enabled (EDGE_GATE=true) publish IT instead of raw llama-server: the gate
# adds per-device keys, a path allowlist (no /lora-adapters, /slots,
# /v1/stream for remote callers), rate limits, and an inference audit. Raw
# llama-server stays on loopback for the local command center either way.
_EDGE_GATE="${EDGE_GATE:-false}"
_EDGE_PORT="${EDGE_PORT:-8090}"
if [[ "$_EDGE_GATE" == "true" ]]; then
  sudo tailscale serve --bg --https=8443 "http://$UP_HOST:${_EDGE_PORT:-8090}"
  echo "      Inference published via beast-gate (:8443 → :${_EDGE_PORT:-8090} → llama-server)."
  echo "      Remote devices need an enrolled key: ./scripts/clients.sh enroll <id>"
else
  sudo tailscale serve --bg --https=8443 "http://$UP_HOST:8080"
  echo "      Inference published RAW (:8443 → :8080) — the whole llama-server"
  echo "      route table is tailnet-visible. For per-device keys + audit, set"
  echo "      EDGE_GATE=true in openbeast.conf and re-run (docs/BEAST_SLOT.md)."
fi
if [[ $PUBLISH_SEARXNG -eq 1 ]]; then
  # Client mode (docs/BEAST_SLOT.md): the laptop's local web_search
  # tool calls the rig's SearXNG. Tailnet-only like everything else; see
  # the security note in the header.
  sudo tailscale serve --bg --https=8889 "http://$UP_HOST:8888"
  echo "      SearXNG published for thin clients (tailnet-only, :8889 → :8888)."
fi
if [[ $PUBLISH_SLOT -eq 1 ]]; then
  # beast-slot discovery (docs/BEAST_SLOT.md): read-only status JSON from
  # the dashboard extension. Best-effort preflight — publishing without the
  # extension enabled just serves 502s until it is.
  _conf_ext="$(grep -E '^[[:space:]]*EXTENSIONS[[:space:]]*=' "$(cd "$(dirname "$0")/.." && pwd)/openbeast.conf" 2>/dev/null | tail -1 || true)"
  if [[ "$_conf_ext" != *dashboard* ]]; then
    echo "      WARNING: dashboard extension not in EXTENSIONS — the slot API"
    echo "               will 502 until: ./scripts/ext.sh enable dashboard && ./stop.sh && ./start.sh"
  fi
  # Mount ONLY /api/slot — a bare `--https=8444 → :3002` would publish the
  # whole dashboard (HTML page + /api/status) to every tailnet device. Fall
  # back to the full mount on tailscale builds without --set-path.
  if sudo tailscale serve --bg --https=8444 --set-path=/api/slot \
       "http://$UP_HOST:3002/api/slot" 2>/dev/null; then
    echo "      beast-slot status API published (tailnet-only, :8444/api/slot)."
  else
    sudo tailscale serve --bg --https=8444 "http://$UP_HOST:3002"
    echo "      beast-slot published (tailnet-only, :8444 → :3002)."
    echo "      NOTE: this tailscale build lacks --set-path, so the whole"
    echo "            dashboard (page + /api/status) is tailnet-visible."
  fi
fi
if [[ $PUBLISH_CHAT -eq 1 ]]; then
  # beast-chat (docs/BEAST_CHAT.md). Unlike the slot API this mounts "/" on
  # purpose: the console is a page plus its own API, and every route under it
  # enforces the same two-tier auth in-process. There is nothing here to
  # narrow with --set-path.
  if [[ "${BEAST_CHAT:-false}" != "true" ]]; then
    echo "      WARNING: BEAST_CHAT is not true in openbeast.conf — :8445 will"
    echo "               502 until: set BEAST_CHAT=true && ./stop.sh && ./start.sh"
  fi
  sudo tailscale serve --bg --https=8445 "http://$CHAT_UP_HOST:${CHAT_PORT:-3003}"
  echo "      beast-chat published (tailnet-only, :8445 → :${CHAT_PORT:-3003})."
  if ! _is_loop "$CHAT_UP_HOST"; then
    # The console trusts Tailscale-User-Login only from a LOOPBACK peer (the
    # header is otherwise forgeable), and tailscaled dials this address from
    # itself — so logins are not honoured through this mount.
    echo "      NOTE: beast-chat binds $CHAT_UP_HOST, not loopback — tailnet logins are NOT"
    echo "            honoured through :8445 (identity headers count only from 127.0.0.1)."
    echo "            Unset OPENBEAST_CHAT_BIND to restore login-gated reads."
  fi
  if [[ -z "${CHAT_OPERATORS:-}" ]]; then
    echo "      NOTE: CHAT_OPERATORS is empty — EVERY login on your tailnet can"
    echo "            read every session. Set it in openbeast.conf to pin it to you."
  fi
  echo "      Writing (send/stop/start) needs a chat-scoped device key:"
  echo "        ./scripts/clients.sh enroll phone --label \"My phone\" --scope chat"
fi
if [[ $PUBLISH_ARTIFACT -eq 1 ]]; then
  # beast-artifact (docs/BEAST_ARTIFACT_PLAN.md): the gallery + the pages the
  # model publishes. Best-effort preflight — publishing while BEAST_ARTIFACT
  # is off just serves 502s until the server is running. conf.sh is already
  # sourced above, so ARTIFACT_PORT/BEAST_ARTIFACT are resolved here.
  if [[ "${BEAST_ARTIFACT:-false}" != "true" ]]; then
    echo "      WARNING: BEAST_ARTIFACT is not true — :8446 will 502 until:"
    echo "               set BEAST_ARTIFACT=true in openbeast.conf, then ./stop.sh && ./start.sh"
  fi
  sudo tailscale serve --bg --https=8446 "http://$UP_HOST:${ARTIFACT_PORT:-3004}"
  echo "      beast-artifact published (tailnet-only, :8446 → :${ARTIFACT_PORT:-3004})."
  if ! _is_loop "$UP_HOST"; then
    echo "      NOTE: beast-artifact binds $UP_HOST (BIND_HOST), not loopback — tailnet"
    echo "            logins are NOT honoured through :8446 (identity headers count only"
    echo "            from 127.0.0.1), so only pages marked public open. Keep BIND_HOST"
    echo "            loopback (the default) for login-gated reads."
  fi
  # Honesty about the READ gate: "gated on ARTIFACT_OPERATORS" is only true
  # when that list has somebody in it. Empty means every signed-in device on
  # the tailnet reads the gallery — the operator must hear that now, at the
  # moment they open the port, not discover it later.
  _ART_OPS="${OPENBEAST_ARTIFACT_OPERATORS:-$(_ob_conf_value ARTIFACT_OPERATORS || true)}"
  if [[ -z "$_ART_OPS" ]]; then
    _ART_OPS="$(_ob_conf_value CHAT_OPERATORS || true)"
  fi
  if [[ -z "${_ART_OPS// /}" ]]; then
    echo "      NOTE: ARTIFACT_OPERATORS is EMPTY — reads are NOT gated to a"
    echo "            list. Every device signed in to your tailnet can open"
    echo "            the gallery and every artifact marked 'tailnet'."
    echo "            Gate it:  echo 'ARTIFACT_OPERATORS=you@example.com' >> openbeast.conf"
    echo "                      ./stop.sh && ./start.sh"
  else
    echo "      Reads are gated on the tailnet login (ARTIFACT_OPERATORS=$_ART_OPS)."
  fi
  echo "      Publishing stays loopback-only — a phone can view, never write."
fi
echo "      Done. Current serve config:"
tailscale serve status | sed 's/^/      /'

# The rig publishes several ports now: print the mapping the operator
# actually reasons about (the same table `--status` prints on its own).
echo ""
_print_mounts "${CHAT_PORT:-3003}" "${ARTIFACT_PORT:-3004}"

# --- 4. Report ---------------------------------------------------------------
FQDN=$(tailscale status --json | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
echo ""
echo "[4/4] OpenBeast is reachable from every device on your tailnet:"
echo ""
if [[ $WEBUI_PUBLISHED -eq 1 ]]; then
  echo "  Chat (Open WebUI):   https://$FQDN"
else
  echo "  Chat (Open WebUI):   NOT published yet — see the message above, then re-run."
fi
echo "  API (OpenAI-compat): https://$FQDN:8443/v1"
echo ""
echo "  Phone:  install the Tailscale app, sign in, open the chat URL,"
echo "          then 'Add to Home Screen' — Open WebUI installs as an app."
echo "  Laptop: install Tailscale (tailscale.com/download), sign in —"
echo "          both URLs just work in any browser."
echo "  Agents: point OpenCode/any OpenAI client at the API:"
echo "          \"baseURL\": \"https://$FQDN:8443/v1\""
if [[ $PUBLISH_SEARXNG -eq 1 ]]; then
  echo ""
  echo "  Thin clients (scripts/setup-client.sh on the laptop):"
  echo "          SEARXNG_URL=https://$FQDN:8889"
  echo "          (any tailnet device can search through this — undo with"
  echo "           ./scripts/setup-tailscale.sh --unpublish-searxng)"
fi
if [[ $PUBLISH_SLOT -eq 1 ]]; then
  echo ""
  echo "  beast-slot discovery:  https://$FQDN:8444/api/slot"
  echo "          (read-only model/slots/health JSON — undo with"
  echo "           ./scripts/setup-tailscale.sh --unpublish-slot)"
fi
if [[ $PUBLISH_CHAT -eq 1 ]]; then
  echo ""
  echo "  beast-chat console:    https://$FQDN:8445"
  echo "          Open it on the phone and 'Add to Home Screen'. Reading is"
  echo "          your tailnet login; sending/stopping needs a chat-scoped"
  echo "          device key (./scripts/clients.sh enroll phone --scope chat)."
  echo "          Undo with ./scripts/setup-tailscale.sh --unpublish-chat"
fi
if [[ $PUBLISH_ARTIFACT -eq 1 ]]; then
  echo ""
  echo "  Artifacts (beast-artifact):  https://$FQDN:8446/"
  echo "          (gallery + published pages, view-only from the tailnet —"
  echo "           undo with ./scripts/setup-tailscale.sh --unpublish-artifact)"
fi
echo ""
echo "  Full walkthrough + verification checklist: docs/INSTALL.md §7"
echo "  Installing a client device (laptop):       docs/INSTALL.md §8"
echo ""
echo "  Note: services now bind 127.0.0.1 by default (see BIND_HOST in"
echo "  openbeast.conf.example). Devices reach them via the tailnet, not"
echo "  raw LAN IPs. Restart the stack (./stop.sh && ./start.sh) if it was"
echo "  running before this setup."
