#!/bin/bash
# OpenBeast remote access — one-shot Tailscale setup. Idempotent.
#
#   ./scripts/setup-tailscale.sh
#
# What it does:
#   1. Installs tailscale (pacman) and enables tailscaled
#   2. Joins your tailnet (prints a login URL on first run)
#   3. Publishes, tailnet-only, with automatic HTTPS:
#        https://<host>.<tailnet>.ts.net       → Open WebUI (:3000)
#        https://<host>.<tailnet>.ts.net:8443  → llama-server API (:8080)
#   4. Prints the URLs to use from your phone/laptop
#
# MCPO (:3001) and SearXNG (:8888) are NOT published — they are internal
# plumbing for the model, not human-facing services.
#
# Public internet exposure (tailscale funnel) is deliberately not offered.
# The tailnet is the security perimeter. See docs/REMOTE_ACCESS_PLAN.md.
set -euo pipefail

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
  sudo pacman -S --needed --noconfirm tailscale
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
  while [[ "$(_ts_ready)" != "yes" ]]; do
    sleep 5
    echo -n "."
  done
  echo " done!"
fi
sudo tailscale serve --bg --https=443  http://127.0.0.1:3000
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8080
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
echo ""
echo "  Full walkthrough + verification checklist: docs/INSTALL.md §7"
echo ""
echo "  Note: services now bind 127.0.0.1 by default (see BIND_HOST in"
echo "  openbeast.conf.example). Devices reach them via the tailnet, not"
echo "  raw LAN IPs. Restart the stack (./stop.sh && ./start.sh) if it was"
echo "  running before this setup."
