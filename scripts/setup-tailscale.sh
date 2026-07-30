#!/bin/bash
# OpenBeast remote access — one-shot Tailscale setup. Idempotent.
#
#   ./scripts/setup-tailscale.sh [--publish-searxng] [--publish-slot]
#   ./scripts/setup-tailscale.sh  --unpublish-searxng | --unpublish-slot
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
# Public internet exposure (tailscale funnel) is deliberately not offered.
# The tailnet is the security perimeter. See docs/REMOTE_ACCESS_PLAN.md.
set -euo pipefail

PUBLISH_SEARXNG=0
PUBLISH_SLOT=0
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
    -h|--help) sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $_arg (see --help)" >&2; exit 2 ;;
  esac
done

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
sudo tailscale serve --bg --https=443  http://127.0.0.1:3000
# :8443 = the inference endpoint remote clients use. When beast-gate is
# enabled (EDGE_GATE=true) publish IT instead of raw llama-server: the gate
# adds per-device keys, a path allowlist (no /lora-adapters, /slots,
# /v1/stream for remote callers), rate limits, and an inference audit. Raw
# llama-server stays on loopback for the local command center either way.
# Resolve via lib/conf.sh, not an ad-hoc grep: that keeps the documented
# env-var-over-conf precedence and one parser for the whole repo.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "$(dirname "$0")/lib/conf.sh"
_EDGE_GATE="${EDGE_GATE:-false}"
_EDGE_PORT="${EDGE_PORT:-8090}"
if [[ "$_EDGE_GATE" == "true" ]]; then
  sudo tailscale serve --bg --https=8443 "http://127.0.0.1:${_EDGE_PORT:-8090}"
  echo "      Inference published via beast-gate (:8443 → :${_EDGE_PORT:-8090} → llama-server)."
  echo "      Remote devices need an enrolled key: ./scripts/clients.sh enroll <id>"
else
  sudo tailscale serve --bg --https=8443 http://127.0.0.1:8080
  echo "      Inference published RAW (:8443 → :8080) — the whole llama-server"
  echo "      route table is tailnet-visible. For per-device keys + audit, set"
  echo "      EDGE_GATE=true in openbeast.conf and re-run (docs/BEAST_SLOT.md)."
fi
if [[ $PUBLISH_SEARXNG -eq 1 ]]; then
  # Client mode (docs/BEAST_SLOT.md): the laptop's local web_search
  # tool calls the rig's SearXNG. Tailnet-only like everything else; see
  # the security note in the header.
  sudo tailscale serve --bg --https=8889 http://127.0.0.1:8888
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
       http://127.0.0.1:3002/api/slot 2>/dev/null; then
    echo "      beast-slot status API published (tailnet-only, :8444/api/slot)."
  else
    sudo tailscale serve --bg --https=8444 http://127.0.0.1:3002
    echo "      beast-slot published (tailnet-only, :8444 → :3002)."
    echo "      NOTE: this tailscale build lacks --set-path, so the whole"
    echo "            dashboard (page + /api/status) is tailnet-visible."
  fi
fi
echo "      Done. Current serve config:"
tailscale serve status | sed 's/^/      /'

# --- 3b. Turn on the WebUI login boundary now that it's tailnet-wide --------
# Local-only installs run WEBUI_AUTH=false (no login wall). Going remote is
# exactly when per-user auth + RBAC start to matter, so persist it in
# openbeast.conf (idempotent). The stack restart below picks it up.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$REPO_DIR/openbeast.conf"
touch "$CONF"
if ! grep -qE '^[[:space:]]*WEBUI_AUTH[[:space:]]*=' "$CONF"; then
  printf '\n# Remote access enabled — require a WebUI login (RBAC tiers apply).\nWEBUI_AUTH=true\n' >> "$CONF"
  echo "      Enabled WebUI login (WEBUI_AUTH=true in openbeast.conf)."
else
  echo "      WEBUI_AUTH already set in openbeast.conf — leaving as-is."
fi

# --- 4. Report ---------------------------------------------------------------
FQDN=$(tailscale status --json | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
echo ""
echo "[4/4] OpenBeast is reachable from every device on your tailnet:"
echo ""
echo "  Chat (Open WebUI):   https://$FQDN"
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
echo ""
echo "  Full walkthrough + verification checklist: docs/INSTALL.md §7"
echo "  Installing a client device (laptop):       docs/INSTALL.md §8"
echo ""
echo "  Note: services now bind 127.0.0.1 by default (see BIND_HOST in"
echo "  openbeast.conf.example). Devices reach them via the tailnet, not"
echo "  raw LAN IPs. Restart the stack (./stop.sh && ./start.sh) if it was"
echo "  running before this setup."
