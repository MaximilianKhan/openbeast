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
# Fast boot (ODS-absorbed, docs/TODO.md): when true, start.sh serves the tiny
# Qwen3-0.6B bridge on :8080 for instant chat, brings up the full stack, then
# hot-swaps to DEFAULT_SERVE_SCRIPT once its weights are warmed. Off by default
# (a normal launch loads the configured model directly). Conf key FAST_BOOT.
FAST_BOOT="${OPENBEAST_FAST_BOOT:-$(_ob_conf_value FAST_BOOT || echo false)}"
# Model load-failure rollback (ODS-absorbed): if the configured model fails to
# load (OOM, missing/corrupt weight), start.sh reverts to the last model that
# loaded healthy (recorded in .run/last-good-serve-script) rather than leaving
# the stack down. On by default — a working stack beats a dead one; a loud
# warning names what failed. Conf key MODEL_ROLLBACK; set false to hard-fail.
MODEL_ROLLBACK="${OPENBEAST_MODEL_ROLLBACK:-$(_ob_conf_value MODEL_ROLLBACK || echo true)}"
# Enabled extensions (ODS-absorbed extension system, scripts/lib/extensions.sh)
# — space-separated names under extensions/. start.sh merges their compose
# fragments / launches their processes. Manage with scripts/ext.sh; empty by
# default (opinionated core only). Conf key EXTENSIONS.
EXTENSIONS="${OPENBEAST_EXTENSIONS:-$(_ob_conf_value EXTENSIONS || true)}"
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
# WEBUI_ADMIN_PASSWORD is deliberately NOT exported: the only consumer,
# configure-webui.sh, sources this file itself and reads the variable in
# its own shell. Exporting it would put the admin password in the
# environment of every child start.sh launches — including the tool
# server that runs model-authored shell commands.
export BIND_HOST WEBUI_ADMIN_EMAIL
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
# RBAC Phase 2 — per-profile tool-server API keys (docs/RBAC_PLAN.md).
# EITHER key set = keyed enforcement on the identity tool server (:3001):
# admin key = all tools, guest key = web_search+fetch only; a missing key
# disables that profile (fail closed). BOTH keys empty = Phase 1 behavior
# (open server on loopback; WebUI grants are the only enforcement).
# Generate keys with scripts/setup-mcpo-keys.sh.
# Same export discipline as LLAMA_API_KEY: only export when non-empty.
MCPO_ADMIN_KEY="${OPENBEAST_MCPO_ADMIN_KEY:-$(_ob_conf_value MCPO_ADMIN_KEY || true)}"
MCPO_GUEST_KEY="${OPENBEAST_MCPO_GUEST_KEY:-$(_ob_conf_value MCPO_GUEST_KEY || true)}"
# Workspace sharding mode for the identity tool server (off|user|chat).
FILES_SHARDING="${OPENBEAST_FILES_SHARDING:-$(_ob_conf_value FILES_SHARDING || echo user)}"
export OPENBEAST_FILES_SHARDING="$FILES_SHARDING"
# Signed identity (enterprise): one shared secret. Open WebUI mints an HS256
# JWT per tool call (FORWARD_USER_INFO_HEADER_JWT_SECRET, wired in
# docker-compose.yml) and the identity tool server verifies it — header
# forgery dies. Generate with scripts/setup-mcpo-keys.sh --with-jwt.
# Same export discipline: only when non-empty (empty = plain-header mode).
IDENTITY_JWT_SECRET="${OPENBEAST_IDENTITY_JWT_SECRET:-$(_ob_conf_value IDENTITY_JWT_SECRET || true)}"
if [[ -n "$IDENTITY_JWT_SECRET" ]]; then
  export OPENBEAST_IDENTITY_JWT_SECRET="$IDENTITY_JWT_SECRET"
fi
if [[ -n "$MCPO_ADMIN_KEY" ]]; then
  export OPENBEAST_MCPO_ADMIN_KEY="$MCPO_ADMIN_KEY"
fi
if [[ -n "$MCPO_GUEST_KEY" ]]; then
  export OPENBEAST_MCPO_GUEST_KEY="$MCPO_GUEST_KEY"
fi
# SearXNG session-signing secret — per-install, never shipped in the repo
# (a committed key would be shared by every install on GitHub, and remote
# access exposes SearXNG beyond loopback). Generated ONCE here and persisted
# in openbeast.conf (mode 600) so daemon mode — which re-sources this file
# from a clean systemd environment — and every later restart reuse the same
# key. docker-compose.yml hard-requires the export (`:?`), so any compose
# caller must source this file first, which they all already do.
SEARXNG_SECRET="${OPENBEAST_SEARXNG_SECRET:-$(_ob_conf_value SEARXNG_SECRET || true)}"
if [[ -z "$SEARXNG_SECRET" ]]; then
  SEARXNG_SECRET="$(openssl rand -hex 32 2>/dev/null)" \
    || SEARXNG_SECRET="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
  _ob_conf="$REPO_DIR/openbeast.conf"
  if [[ ! -f "$_ob_conf" ]]; then
    ( umask 077; echo "# OpenBeast local config — all keys: openbeast.conf.example" > "$_ob_conf" )
  fi
  printf 'SEARXNG_SECRET=%s\n' "$SEARXNG_SECRET" >> "$_ob_conf"
  chmod 600 "$_ob_conf" 2>/dev/null || true
fi
export OPENBEAST_SEARXNG_SECRET="$SEARXNG_SECRET"
