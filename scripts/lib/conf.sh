#!/bin/bash
# OpenBeast — general config resolver (openbeast.conf).
#
# Sourced by serve.sh / start.sh / configure-webui.sh for stack-wide settings
# that aren't the weights path (that one has its own resolver, lib/weights.sh,
# kept separate because launch scripts need it even without a conf file).
#
# Each value resolves as: env var (highest priority) → openbeast.conf → default.
#
#   BIND_HOST        (env OPENBEAST_BIND)        default 127.0.0.1
#       Address the stack's services listen on. 127.0.0.1 keeps everything
#       loopback-only — remote devices come in through Tailscale Serve
#       (scripts/setup-tailscale.sh). Set 0.0.0.0 to restore the legacy
#       LAN-open behavior.
#   LLAMA_API_KEY    (env OPENBEAST_API_KEY)     default empty (off)
#       When set, llama-server requires "Authorization: Bearer <key>".
#       NOTE: the eval harness and agents/runner.py do not send a key —
#       leave unset on eval days, or export OPENAI_API_KEY to match.
#   WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD     default empty
#       Lets configure-webui.sh authenticate after WEBUI_AUTH is enabled.
#
# Requires REPO_DIR to be set before sourcing.

: "${REPO_DIR:?REPO_DIR must be set before sourcing lib/conf.sh}"

# Read one KEY= value from openbeast.conf. Ignores comments and surrounding
# whitespace/quotes; last assignment wins. Prints nothing when absent.
_ob_conf_value() {
  local key="$1" conf="$REPO_DIR/openbeast.conf" line
  [[ -f "$conf" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$conf" | tail -n1)" || return 1
  [[ -n "$line" ]] || return 1
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  [[ -n "$line" ]] || return 1
  printf '%s\n' "$line"
}

BIND_HOST="${OPENBEAST_BIND:-$(_ob_conf_value BIND_HOST || echo 127.0.0.1)}"
LLAMA_API_KEY="${OPENBEAST_API_KEY:-$(_ob_conf_value LLAMA_API_KEY || true)}"
WEBUI_ADMIN_EMAIL="${WEBUI_ADMIN_EMAIL:-$(_ob_conf_value WEBUI_ADMIN_EMAIL || true)}"
WEBUI_ADMIN_PASSWORD="${WEBUI_ADMIN_PASSWORD:-$(_ob_conf_value WEBUI_ADMIN_PASSWORD || true)}"
export BIND_HOST WEBUI_ADMIN_EMAIL WEBUI_ADMIN_PASSWORD
# Export the key only when real: llama-server reads the LLAMA_API_KEY env
# var natively, and an exported empty string still counts as "set" to it.
if [[ -n "$LLAMA_API_KEY" ]]; then
  export LLAMA_API_KEY
fi
