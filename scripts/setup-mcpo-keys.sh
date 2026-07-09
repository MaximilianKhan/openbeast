#!/bin/bash
# Enable RBAC Phase 2: per-profile MCPO API keys (docs/RBAC_PLAN.md).
#
# Generates two random keys and persists them in openbeast.conf:
#   MCPO_ADMIN_KEY — the admin MCPO instance (:3001, all tools)
#   MCPO_GUEST_KEY — the guest MCPO instance (:MCPO_GUEST_PORT, default 3002,
#                    serving ONLY web_search + fetch via the
#                    OPENBEAST_MCP_TOOLS registration allowlist)
#
# With both keys set, start.sh launches the two keyed instances and
# configure-webui.sh binds each WebUI connection to its instance with a
# Bearer key — so a guest can't reach admin tools even if they bypass the
# WebUI's grant filter (below-app enforcement, defense in depth).
#
# Idempotent: existing keys are left untouched (use --rotate to replace).
#
# Usage:
#   ./scripts/setup-mcpo-keys.sh            # generate if absent
#   ./scripts/setup-mcpo-keys.sh --rotate   # replace existing keys

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$REPO_DIR/openbeast.conf"

ROTATE=false
[[ "${1:-}" == "--rotate" ]] && ROTATE=true

genkey() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

touch "$CONF"

set_key() { # set_key <NAME>
  local name="$1"
  if grep -qE "^[[:space:]]*${name}[[:space:]]*=" "$CONF"; then
    if $ROTATE; then
      local newval
      newval="$(genkey)"
      sed -i -E "s|^[[:space:]]*${name}[[:space:]]*=.*|${name}=${newval}|" "$CONF"
      echo "  ${name}: rotated."
    else
      echo "  ${name}: already set — leaving as-is (use --rotate to replace)."
    fi
  else
    printf '%s=%s\n' "$name" "$(genkey)" >> "$CONF"
    echo "  ${name}: generated."
  fi
}

echo "RBAC Phase 2 — per-profile MCPO keys"
if ! grep -qE '^[[:space:]]*MCPO_(ADMIN|GUEST)_KEY' "$CONF" && ! $ROTATE; then
  printf '\n# RBAC Phase 2 — per-profile MCPO keys (scripts/setup-mcpo-keys.sh).\n# Both set => keyed admin MCPO on :3001 + guest instance (web tools only)\n# on :MCPO_GUEST_PORT (default 3002). Delete both lines to fall back to\n# Phase 1 (single keyless instance).\n' >> "$CONF"
fi
set_key MCPO_ADMIN_KEY
set_key MCPO_GUEST_KEY

# Keys never printed — they live in openbeast.conf (gitignored).
chmod 600 "$CONF" 2>/dev/null || true

echo ""
echo "Done. Restart the stack to activate:"
echo "  ./stop.sh && ./start.sh -d"
echo ""
echo "Verify after restart:"
echo "  curl -s http://localhost:3001/openapi.json                # expect 401/403 (keyed)"
echo "  ./scripts/healthcheck.sh                                  # both instances OK"
