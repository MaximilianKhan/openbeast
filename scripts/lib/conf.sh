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
#   GPU_BACKEND      (env OPENBEAST_GPU_BACKEND) default auto
#       llama.cpp build backend: auto | cuda | hip | sycl | cpu. "auto" maps
#       the detected GPU vendor (lib/hardware.sh): nvidia→cuda, amd→hip,
#       intel→sycl, none→cpu. bootstrap.sh persists the resolved value into
#       openbeast.conf so scripts/update.sh rebuilds with the same backend.
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
GPU_BACKEND="${OPENBEAST_GPU_BACKEND:-$(_ob_conf_value GPU_BACKEND || echo auto)}"
# Agent-spawn router (docs/RESEARCH_FINDINGS §8-11): opt-in proxy that reliably
# turns "spawn a background agent" requests into real agents. Off by default.
# When on, start.sh runs agents/router.py on ROUTER_PORT in front of
# llama-server (8080), and the human frontends (WebUI/OpenCode) point at it;
# evals and spawned agents keep hitting 8080 directly (never routed).
AGENT_ROUTER="${OPENBEAST_AGENT_ROUTER:-$(_ob_conf_value AGENT_ROUTER || echo false)}"
ROUTER_PORT="${OPENBEAST_ROUTER_PORT:-$(_ob_conf_value ROUTER_PORT || echo 8088)}"
if [[ "$AGENT_ROUTER" == "true" ]]; then
  MODEL_URL="http://localhost:${ROUTER_PORT}/v1"
else
  MODEL_URL="http://localhost:8080/v1"
fi
export AGENT_ROUTER ROUTER_PORT
# Frontends read this for the model endpoint (docker-compose interpolates it).
export OPENBEAST_MODEL_URL="$MODEL_URL"
# Daemon-mode memory cap as a PERCENT of this machine's physical RAM
# (start.sh computes the byte value from /proc/meminfo at every launch, so
# the cap scales with whatever box OpenBeast lands on — 128 GB or 32 GB).
MEM_LIMIT_PCT="${OPENBEAST_MEM_LIMIT_PCT:-$(_ob_conf_value MEM_LIMIT_PCT || echo 75)}"
# WEBUI_AUTH default is FALSE (local-only single user — no login wall, and
# configure-webui.sh can auto-configure via the default admin account). It
# is flipped to true by scripts/setup-tailscale.sh when the WebUI becomes
# reachable from the whole tailnet (that's when a login boundary matters).
# docker-compose reads this via OPENBEAST_WEBUI_AUTH.
WEBUI_AUTH="${OPENBEAST_WEBUI_AUTH:-$(_ob_conf_value WEBUI_AUTH || echo false)}"
export BIND_HOST WEBUI_ADMIN_EMAIL WEBUI_ADMIN_PASSWORD
export OPENBEAST_WEBUI_AUTH="$WEBUI_AUTH"
# docker-compose interpolates OPENBEAST_BIND / OPENBEAST_API_KEY directly.
# Export them HERE so every caller that later runs `docker compose up`
# (start.sh, healthcheck.sh --restart, update.sh --images) recreates
# containers with the user's real settings — an update must never silently
# revert WEBUI auth or the bind address to defaults.
export OPENBEAST_BIND="$BIND_HOST"
# Export the key only when real: llama-server reads the LLAMA_API_KEY env
# var natively, and an exported empty string still counts as "set" to it.
# Same logic for OPENBEAST_API_KEY: exporting the WebUI's "not-needed"
# placeholder would leak into serve.sh's conf resolution and silently
# key-protect the llama API.
if [[ -n "$LLAMA_API_KEY" ]]; then
  export LLAMA_API_KEY
  export OPENBEAST_API_KEY="$LLAMA_API_KEY"
fi
