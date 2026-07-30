#!/usr/bin/env bash
# OpenBeast client CLI — the laptop-side companion to a rig's beast-slot.
# (docs/BEAST_SLOT.md; installed by scripts/setup-client.sh, which symlinks
# this to ~/.local/bin/openbeast-client when that dir exists.)
#
#   client.sh [status]           client doctor: env, venv, tailnet, rig
#                                endpoints, beast-slot view, opencode wiring
#   client.sh agent [args…]      run a CLI agent HERE against the rig's model
#                                (files/shell act on this machine)
#   client.sh search up|down     start/stop the local SearXNG container
#   client.sh update             refresh the checkout + pinned deps
#   client.sh uninstall          remove client mode entirely
#
# Deliberately does NOT source scripts/lib/conf.sh — that file is rig-shaped
# (it generates secrets into openbeast.conf). Client config lives in
# ~/.openbeast-client.env, written by setup-client.sh.
#
# Bash 3.2-compatible on purpose (stock macOS); §13 enforces this.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT_DIR="$HOME/.openbeast-client"
ENV_FILE="$HOME/.openbeast-client.env"
VENV="$CLIENT_DIR/venv"
OC_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

CMD="${1:-status}"
[ $# -gt 0 ] && shift

_curl_auth() {
  # _curl_auth <url> [extra curl args…] — bearer added when keyed.
  _u="$1"; shift
  if [ -n "${OPENBEAST_API_KEY:-}" ]; then
    curl -s -m 5 -H "Authorization: Bearer $OPENBEAST_API_KEY" "$@" "$_u"
  else
    curl -s -m 5 "$@" "$_u"
  fi
}

case "$CMD" in
  status)
    echo "=== OpenBeast client status ==="
    ok=0; bad=0
    if [ -f "$ENV_FILE" ]; then
      echo "  ✓ env file ($ENV_FILE)"; ok=$((ok+1))
    else
      echo "  ✗ no $ENV_FILE — run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    if [ -x "$VENV/bin/python3" ] && "$VENV/bin/python3" -c "import mcp, openai" 2>/dev/null; then
      echo "  ✓ venv imports mcp + openai"; ok=$((ok+1))
    else
      echo "  ✗ venv broken — re-run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
      echo "  ✓ tailscale up"; ok=$((ok+1))
    else
      echo "  ✗ tailscale down — if it JUST dropped, a full-tunnel VPN"
      echo "    (NordVPN etc.) may have grabbed the default route: quit it,"
      echo "    then 'tailscale up' (docs/BEAST_SLOT.md § VPN collision)"
      bad=$((bad+1))
    fi
    BASE="${OPENBEAST_AGENT_INFERENCE_URL:-}"
    if [ -n "$BASE" ]; then
      HURL="$(echo "$BASE" | sed 's|/v1$||')/health"
      if _curl_auth "$HURL" | grep -q "ok"; then
        echo "  ✓ rig model API reachable ($BASE)"; ok=$((ok+1))
      else
        echo "  ✗ rig model API not answering ($BASE)"; bad=$((bad+1))
      fi
    else
      echo "  ✗ OPENBEAST_AGENT_INFERENCE_URL not set"; bad=$((bad+1))
    fi
    if [ -n "${OPENBEAST_SLOT_URL:-}" ]; then
      SLOT="$(curl -s -m 5 "$OPENBEAST_SLOT_URL" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    m, s = d.get("model") or {}, d.get("slots") or {}
    h = "healthy" if d.get("healthy") else "UNHEALTHY"
    print("%s | model %s | slots %s/%s busy | ctx %s | auth %s" % (
        h, m.get("id") or "?", s.get("busy", "?"), s.get("total", "?"),
        m.get("ctx") or "?", d.get("auth", "?")))
except Exception:
    pass' || true)"
      if [ -n "$SLOT" ]; then
        echo "  ✓ beast-slot: $SLOT"; ok=$((ok+1))
      else
        echo "  ! beast-slot status not published (optional — rig:"
        echo "      ./scripts/setup-tailscale.sh --publish-slot)"
      fi
    fi
    if [ -n "${SEARXNG_URL:-}" ]; then
      if curl -s -m 5 "$SEARXNG_URL/search?q=test&format=json" 2>/dev/null | grep -q '"results"'; then
        echo "  ✓ search reachable ($SEARXNG_URL)"; ok=$((ok+1))
      else
        echo "  ✗ search not answering ($SEARXNG_URL)"; bad=$((bad+1))
      fi
    else
      echo "  - search not wired (--no-search)"
    fi
    if [ -f "$OC_CONFIG" ] && grep -q '"local-tools"' "$OC_CONFIG" \
       && grep -q '"llama-cpp"' "$OC_CONFIG"; then
      echo "  ✓ opencode.json wired (local-tools + llama-cpp provider)"; ok=$((ok+1))
    else
      echo "  ✗ opencode.json not wired — re-run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    echo ""
    if [ $bad -eq 0 ]; then
      echo "client: $ok ok — ready. (cd <project> && opencode)"
    else
      echo "client: $ok ok, $bad failing."
      exit 1
    fi
    ;;

  agent)
    # CLI agent on THIS machine, thinking via the rig — without agent.sh or
    # conf.sh (rig-shaped). runner.py resolves OPENBEAST_API_KEY from env.
    [ -x "$VENV/bin/python3" ] || { echo "venv missing — run scripts/setup-client.sh" >&2; exit 1; }
    [ -n "${OPENBEAST_AGENT_INFERENCE_URL:-}" ] || { echo "OPENBEAST_AGENT_INFERENCE_URL not set — run scripts/setup-client.sh" >&2; exit 1; }
    exec "$VENV/bin/python3" "$REPO/agents/runner.py" \
      --base-url "$OPENBEAST_AGENT_INFERENCE_URL" "$@"
    ;;

  search)
    SUB="${1:-}"
    COMPOSE_FILE="$REPO/scripts/client-searxng.compose.yml"
    if [ -z "${OPENBEAST_SEARXNG_SECRET:-}" ]; then
      echo "local search not configured — install with: setup-client.sh --local-search" >&2
      exit 1
    fi
    case "$SUB" in
      up)   OPENBEAST_SEARXNG_SECRET="$OPENBEAST_SEARXNG_SECRET" \
              docker compose -p openbeast-client -f "$COMPOSE_FILE" up -d ;;
      down) OPENBEAST_SEARXNG_SECRET="$OPENBEAST_SEARXNG_SECRET" \
              docker compose -p openbeast-client -f "$COMPOSE_FILE" down ;;
      *) echo "usage: client.sh search up|down" >&2; exit 2 ;;
    esac
    ;;

  update)
    if [ -d "$CLIENT_DIR/repo/.git" ]; then
      git -C "$CLIENT_DIR/repo" pull --ff-only
    else
      echo "  (running from a full clone — pull it yourself: git -C $REPO pull)"
    fi
    [ -x "$VENV/bin/pip" ] && "$VENV/bin/pip" install -q -r "$REPO/agents/requirements.txt"
    echo "client updated."
    ;;

  uninstall)
    exec "$REPO/scripts/setup-client.sh" --uninstall
    ;;

  -h|--help|help)
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    echo "unknown command: $CMD (status|agent|search|update|uninstall)" >&2
    exit 2
    ;;
esac
