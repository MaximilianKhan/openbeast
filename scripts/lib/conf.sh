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
# 0.0.0.0 exposes EVERY service (WebUI, model API, tools, search) to the
# whole network, unauthenticated by default. Legal but loud — remote access
# should go through Tailscale (scripts/setup-tailscale.sh) instead.
if [[ "$BIND_HOST" == "0.0.0.0" || "$BIND_HOST" == "::" ]]; then
  echo "WARNING: BIND_HOST=$BIND_HOST — the ENTIRE stack is network-reachable" >&2
  echo "         without auth. Prefer Tailscale (scripts/setup-tailscale.sh)." >&2
fi
LLAMA_API_KEY="${OPENBEAST_API_KEY:-$(_ob_conf_value LLAMA_API_KEY || true)}"
WEBUI_ADMIN_EMAIL="${WEBUI_ADMIN_EMAIL:-$(_ob_conf_value WEBUI_ADMIN_EMAIL || true)}"
WEBUI_ADMIN_PASSWORD="${WEBUI_ADMIN_PASSWORD:-$(_ob_conf_value WEBUI_ADMIN_PASSWORD || true)}"
GPU_BACKEND="${OPENBEAST_GPU_BACKEND:-$(_ob_conf_value GPU_BACKEND || echo auto)}"
# Serve script launched when start.sh gets no positional arg — also what
# healthcheck.sh --restart falls back to when no supervisor (and no
# .run/serve-script record) exists. Conf key SERVE_SCRIPT.
DEFAULT_SERVE_SCRIPT="${OPENBEAST_SERVE_SCRIPT:-$(_ob_conf_value SERVE_SCRIPT || echo serve-qwen-27b-uncensored-q5.sh)}"
# Agent-spawn router (docs/RESEARCH_FINDINGS §8-11): opt-in proxy that reliably
# turns "spawn a background agent" requests into real agents. Off by default.
# When on, start.sh runs agents/router.py on ROUTER_PORT in front of
# llama-server (8080), and the human frontends (WebUI/OpenCode) point at it;
# evals and spawned agents keep hitting 8080 directly (never routed).
AGENT_ROUTER="${OPENBEAST_AGENT_ROUTER:-$(_ob_conf_value AGENT_ROUTER || echo false)}"
ROUTER_PORT="${OPENBEAST_ROUTER_PORT:-$(_ob_conf_value ROUTER_PORT || echo 8088)}"
# Router spawn-gate identity policy (docs/RBAC_PLAN.md): the router only runs
# its spawn path for X-OpenWebUI-User-Role: admin turns. When this is true and
# the role header is ABSENT (e.g. header forwarding disabled), the router
# fails CLOSED (no spawn) instead of open — set true on hardened multi-user
# installs. Exported so start.sh's router process inherits it.
ROUTER_REQUIRE_IDENTITY="${OPENBEAST_ROUTER_REQUIRE_IDENTITY:-$(_ob_conf_value ROUTER_REQUIRE_IDENTITY || echo false)}"
export OPENBEAST_ROUTER_REQUIRE_IDENTITY="$ROUTER_REQUIRE_IDENTITY"
# Kernel-level sandbox wrapper for the model's bash tool (docs/SANDBOXING.md).
# agents/tools.py reads OPENBEAST_BASH_WRAPPER per-call; forward the conf key
# only when non-empty (an exported empty string would still count as "set").
_BASH_WRAPPER="${OPENBEAST_BASH_WRAPPER:-$(_ob_conf_value BASH_WRAPPER || true)}"
if [[ -n "$_BASH_WRAPPER" ]]; then
  export OPENBEAST_BASH_WRAPPER="$_BASH_WRAPPER"
fi
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
# Where files the CHAT model writes/reads via the direct tools land. A direct
# tool call carries no conversation or user id (the OpenAPI tool server is
# stateless), so without this the model picks its own path and defaults to a
# world-readable, reboot-wiped /tmp. Anchor those ops to a persistent, private
# (0700) workspace instead. Spawned agents keep using their own AGENT_WORKDIR.
# start.sh creates the dir with the right mode; mcp_server inherits this env.
OPENBEAST_FILES_DIR="${OPENBEAST_FILES_DIR:-$(_ob_conf_value FILES_DIR || echo "$HOME/openbeast-files")}"
export OPENBEAST_FILES_DIR
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
# Distributed agents Phase 1 (docs/DISTRIBUTED_AGENTS_PLAN.md): route spawned
# agents' INFERENCE to a worker box while they keep executing (files, shell)
# on THIS machine. Empty (the default) = local model server, single-box
# behavior unchanged. Same export discipline as LLAMA_API_KEY above: only
# export when non-empty — an exported empty string still counts as "set" to
# downstream resolvers (mcp_server.py, agent.sh) and would look like a
# configured-but-blank endpoint instead of "use the local default".
AGENT_INFERENCE_URL="${OPENBEAST_AGENT_INFERENCE_URL:-$(_ob_conf_value AGENT_INFERENCE_URL || true)}"
if [[ -n "$AGENT_INFERENCE_URL" ]]; then
  export OPENBEAST_AGENT_INFERENCE_URL="$AGENT_INFERENCE_URL"
fi
# RBAC Phase 2 — per-profile MCPO API keys (docs/RBAC_PLAN.md). BOTH keys set
# = hard enforcement: start.sh launches TWO MCPO instances — admin (:3001, all
# tools, admin key) and guest (:3002, web_search+fetch ONLY via the
# OPENBEAST_MCP_TOOLS allowlist, guest key) — and configure-webui.sh binds
# each WebUI connection to its instance with a Bearer key. Either key empty =
# Phase 1 behavior unchanged (one keyless instance on :3001; WebUI grants are
# the only enforcement). Generate keys with scripts/setup-mcpo-keys.sh.
# Same export discipline as LLAMA_API_KEY: only export when non-empty.
MCPO_ADMIN_KEY="${OPENBEAST_MCPO_ADMIN_KEY:-$(_ob_conf_value MCPO_ADMIN_KEY || true)}"
MCPO_GUEST_KEY="${OPENBEAST_MCPO_GUEST_KEY:-$(_ob_conf_value MCPO_GUEST_KEY || true)}"
MCPO_GUEST_PORT="${OPENBEAST_MCPO_GUEST_PORT:-$(_ob_conf_value MCPO_GUEST_PORT || echo 3002)}"
if [[ -n "$MCPO_ADMIN_KEY" ]]; then
  export OPENBEAST_MCPO_ADMIN_KEY="$MCPO_ADMIN_KEY"
fi
if [[ -n "$MCPO_GUEST_KEY" ]]; then
  export OPENBEAST_MCPO_GUEST_KEY="$MCPO_GUEST_KEY"
fi
export MCPO_GUEST_PORT
