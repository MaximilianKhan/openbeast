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
#       LAN-open behavior. Any non-loopback value warns at every source;
#       see ALLOW_OPEN_TOOLS for the keyless tool server.
#   LLAMA_API_KEY    (env OPENBEAST_API_KEY)     default empty (off)
#       When set, llama-server requires "Authorization: Bearer <key>", and
#       the WHOLE stack now presents it: serve.sh (via llama-server's env,
#       never argv — lib/curl_auth.sh is the curl side), WebUI via
#       compose, healthcheck, the router's classify probe, the dashboard's
#       probes, agents/runner.py + agent.sh (OPENBEAST_API_KEY/OPENAI_API_KEY
#       env), and the eval harness. Safe to leave on during evals.
#       For PER-DEVICE keys with revocation and an audit trail, use
#       beast-gate instead (EDGE_GATE — see docs/BEAST_SLOT.md).
#   WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD     default empty
#       Lets configure-webui.sh authenticate after WEBUI_AUTH is enabled.
#   BEAST_CHAT       (env OPENBEAST_BEAST_CHAT)  default false
#   CHAT_PORT        (env OPENBEAST_CHAT_PORT)   default 3003
#   CHAT_OPERATORS   (env OPENBEAST_CHAT_OPERATORS) default empty
#       beast-chat: the tailnet operator console for the rig's agent and job
#       sessions (docs/BEAST_CHAT.md). Off by default.
#   OFFLINE          (env OPENBEAST_OFFLINE)     default false
#       "This box has no route to the internet and never will." An installed
#       rig SERVES fine offline already; what OFFLINE changes is that steps
#       which cannot possibly succeed are not attempted, so a closed network
#       reports a clear refusal instead of a multi-minute stall followed by a
#       misdiagnosis. Affects bootstrap.sh, scripts/update.sh, the compose
#       pull policy and doctor's reporting. Use `ob_offline` as the predicate.
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

# _ob_bool <raw value> <default> [KEY] — the ONE parser for every true/false
# key. Prints exactly "true" or "false", which is all any consumer compares
# against (start.sh `== "true"`, Open WebUI's `.lower() == "true"`, edge.py,
# router.py).
#
# Why one helper: `_ob_conf_value` returns the rest of the line verbatim, so
# `WEBUI_AUTH=true   # remote access on` arrived as "true   # remote access
# on" — which Open WebUI reads as FALSE, silently dropping the login wall on
# a tailnet-published rig. The same line broke EDGE_GATE (:8443 published
# raw), and `1`/`yes`/`on` failed the same way. That class was fixed for
# OFFLINE alone (2026-09-17); every boolean key now goes through here.
#   - FIRST TOKEN ONLY, then any attached `#comment` and quotes dropped —
#     `"true" # x`, `true# x` and `true  # x` all mean true.
#   - true|yes|1|on / false|no|0|off, case-insensitively.
#   - empty → <default>. Anything else → false, with a warning naming KEY,
#     so a typo is visible instead of silently flipping a mode.
# Not applied in _ob_conf_value itself: a blanket comment-strip would corrupt
# the keys whose values may legitimately contain " #" (WEBUI_ADMIN_PASSWORD,
# SEARXNG_SECRET). `read`, NOT `set --` — see the OFFLINE note below.
_ob_bool() {
  local raw="$1" def="$2" key="${3:-value}" tok rest
  read -r tok rest <<< "$raw" || true
  tok="${tok%%#*}"
  tok="${tok//[\"\']/}"
  if [[ -z "$tok" ]]; then printf '%s\n' "$def"; return 0; fi
  case "$(printf '%s' "$tok" | tr 'A-Z' 'a-z')" in
    true|yes|1|on)  printf 'true\n' ;;
    false|no|0|off) printf 'false\n' ;;
    *)
      echo "WARNING: $key='$raw' is not a boolean (true/false, yes/no, 1/0, on/off) — treating it as false." >&2
      printf 'false\n' ;;
  esac
}

# ob_bind_is_loopback [host] — true when the address only listens on this
# machine. Anything else (0.0.0.0, ::, a LAN or tailnet IP, a hostname) puts
# the services on a network.
ob_bind_is_loopback() {
  case "${1:-$BIND_HOST}" in
    127.*|::1|localhost|"[::1]") return 0 ;;
    *) return 1 ;;
  esac
}

BIND_HOST="${OPENBEAST_BIND:-$(_ob_conf_value BIND_HOST || echo 127.0.0.1)}"
# The warning for a non-loopback BIND_HOST sits further down, after the tool
# server keys are resolved — whether it is merely loud or names an open
# remote shell depends on them.
LLAMA_API_KEY="${OPENBEAST_API_KEY:-$(_ob_conf_value LLAMA_API_KEY || true)}"
WEBUI_ADMIN_EMAIL="${WEBUI_ADMIN_EMAIL:-$(_ob_conf_value WEBUI_ADMIN_EMAIL || true)}"
WEBUI_ADMIN_PASSWORD="${WEBUI_ADMIN_PASSWORD:-$(_ob_conf_value WEBUI_ADMIN_PASSWORD || true)}"
GPU_BACKEND="${OPENBEAST_GPU_BACKEND:-$(_ob_conf_value GPU_BACKEND || echo auto)}"
# OFFLINE: this box has no route to the internet and never will.
#
# The closed-network review's finding was not that the stack cannot run
# offline — an installed rig SERVES fine with no internet at all. It was that
# nothing could be TOLD there is no internet, so every install/update path
# stalled on a connect timeout and then misdiagnosed the stall as something
# local. This key is how you tell it. It never changes what the stack serves;
# it changes whether a step that CANNOT succeed is attempted.
#
# Presence, not truthiness, per the LANG_PACKS precedent: only the explicit
# strings _ob_bool accepts mean "on", so a typo does not silently enable an
# install-blocking mode.
#
# History, kept because it is the origin of _ob_bool: `OFFLINE=true  # air-
# gapped rig` arrived as "true  # air-gapped rig", matched nothing, and
# silently resolved to FALSE. The first fix used `set --` to split the token —
# which REPLACES THE POSITIONAL PARAMETERS OF THE SOURCING SHELL (start.sh,
# doctor.sh, update.sh, bundle.sh all parse "$@" after sourcing this), so
# `bundle.sh sign` became `bundle.sh false`. Never `set --` in this file.
OFFLINE="$(_ob_bool "${OPENBEAST_OFFLINE:-$(_ob_conf_value OFFLINE || true)}" false OFFLINE)"
# ob_offline is the predicate every script should use rather than re-deriving
# the string comparison — one answer to "are we offline", in one place.
ob_offline() { [[ "${OFFLINE:-false}" == "true" ]]; }
# Serve script launched when start.sh gets no positional arg — also what
# healthcheck.sh --restart falls back to when no supervisor (and no
# .run/serve-script record) exists. Conf key SERVE_SCRIPT.
DEFAULT_SERVE_SCRIPT="${OPENBEAST_SERVE_SCRIPT:-$(_ob_conf_value SERVE_SCRIPT || echo serve-qwen38-27b-uncensored-mtp-q5.sh)}"
# Fast boot (ODS-absorbed, docs/TODO.md): when true, start.sh serves the tiny
# Qwen3-0.6B bridge on :8080 for instant chat, brings up the full stack, then
# hot-swaps to DEFAULT_SERVE_SCRIPT once its weights are warmed. Off by default
# (a normal launch loads the configured model directly). Conf key FAST_BOOT.
FAST_BOOT="$(_ob_bool "${OPENBEAST_FAST_BOOT:-$(_ob_conf_value FAST_BOOT || true)}" false FAST_BOOT)"
# Model load-failure rollback (ODS-absorbed): if the configured model fails to
# load (OOM, missing/corrupt weight), start.sh reverts to the last model that
# loaded healthy (recorded in .run/last-good-serve-script) rather than leaving
# the stack down. On by default — a working stack beats a dead one; a loud
# warning names what failed. Conf key MODEL_ROLLBACK; set false to hard-fail.
MODEL_ROLLBACK="$(_ob_bool "${OPENBEAST_MODEL_ROLLBACK:-$(_ob_conf_value MODEL_ROLLBACK || true)}" true MODEL_ROLLBACK)"
# Enabled extensions (ODS-absorbed extension system, scripts/lib/extensions.sh)
# — space-separated names under extensions/. start.sh merges their compose
# fragments / launches their processes. Manage with scripts/ext.sh; empty by
# default (opinionated core only). Conf key EXTENSIONS.
EXTENSIONS="${OPENBEAST_EXTENSIONS:-$(_ob_conf_value EXTENSIONS || true)}"
# Reasoning/thinking control (serve.sh applies these to llama-server). OpenBeast
# keeps reasoning ON by default (per-request toggle preserved); these are the
# GLOBAL escape hatches, and they OVERRIDE any per-serve-script default:
#   REASONING          on | off | auto   — force thinking on/off for every model
#   REASONING_BUDGET   <int>              — cap thinking tokens (0 = none/immediate
#                                           answer, -1 = unlimited) before the
#                                           model is forced to answer. Tames the
#                                           over-reasoning "MAX" tunes.
# Both empty by default (each model's own default stands). Env overrides:
# $OPENBEAST_REASONING / $OPENBEAST_REASONING_BUDGET.
REASONING="${OPENBEAST_REASONING:-$(_ob_conf_value REASONING || true)}"
REASONING_BUDGET="${OPENBEAST_REASONING_BUDGET:-$(_ob_conf_value REASONING_BUDGET || true)}"
# Agent-spawn router (docs/RESEARCH_FINDINGS §8-11): opt-in proxy that reliably
# turns "spawn a background agent" requests into real agents. Off by default.
# When on, start.sh runs agents/router.py on ROUTER_PORT in front of
# llama-server (8080), and the human frontends (WebUI/OpenCode) point at it;
# evals and spawned agents keep hitting 8080 directly (never routed).
AGENT_ROUTER="$(_ob_bool "${OPENBEAST_AGENT_ROUTER:-$(_ob_conf_value AGENT_ROUTER || true)}" false AGENT_ROUTER)"
ROUTER_PORT="${OPENBEAST_ROUTER_PORT:-$(_ob_conf_value ROUTER_PORT || echo 8088)}"
# Router spawn-gate identity policy (docs/RBAC_PLAN.md): the router only runs
# its spawn path for admin turns — the role comes from the plain
# X-OpenWebUI-User-Role header, or, in signed-identity mode
# (IDENTITY_JWT_SECRET set), ONLY from the verified X-OpenWebUI-User-Jwt. A
# turn with NO identity fails CLOSED on its own whenever WEBUI_AUTH=true or
# IDENTITY_JWT_SECRET is set (agents/router.py REQUIRE_IDENTITY); `true`
# here forces fail-closed on every other rig too. The `false` default only
# matters on a single-user, auth-off rig, which sends no identity at all.
# Exported so start.sh's router process inherits it.
ROUTER_REQUIRE_IDENTITY="$(_ob_bool "${OPENBEAST_ROUTER_REQUIRE_IDENTITY:-$(_ob_conf_value ROUTER_REQUIRE_IDENTITY || true)}" false ROUTER_REQUIRE_IDENTITY)"
export OPENBEAST_ROUTER_REQUIRE_IDENTITY="$ROUTER_REQUIRE_IDENTITY"
# Kernel-level sandbox wrapper for the model's bash tool (docs/SANDBOXING.md).
# agents/tools.py reads OPENBEAST_BASH_WRAPPER per-call; forward the conf key
# only when non-empty (an exported empty string would still count as "set").
_BASH_WRAPPER="${OPENBEAST_BASH_WRAPPER:-$(_ob_conf_value BASH_WRAPPER || true)}"
if [[ -n "$_BASH_WRAPPER" ]]; then
  export OPENBEAST_BASH_WRAPPER="$_BASH_WRAPPER"
fi
# beast-assist (compiler feedback in the agent loop, docs/FEATURES.md).
# agents/tools.py reads BEAST_ASSIST per-call; forward the conf key only
# when non-empty, same pattern as BASH_WRAPPER above. Eval arms override
# this explicitly per-cell (run_eval pins both spellings), so a rig-wide
# enable never leaks into diag-OFF baselines.
_BEAST_ASSIST="${BEAST_ASSIST:-$(_ob_conf_value BEAST_ASSIST || true)}"
if [[ -n "$_BEAST_ASSIST" ]]; then
  export BEAST_ASSIST="$_BEAST_ASSIST"
fi
# fetch() blocks Tailscale CGNAT (100.64.0.0/10) targets by default — pinned
# explicitly in agents/tools.py because CPython's is_private classification of
# that range changed across versions. Opt in (true) when the model should fetch
# from tailnet hosts. Forwarded only when non-empty, same as BASH_WRAPPER.
# Model supply chain: check the weight about to be served against
# scripts/weights.registry. warn (default) | strict (refuse) | off.
WEIGHT_ENFORCE="${OPENBEAST_WEIGHT_ENFORCE:-$(_ob_conf_value WEIGHT_ENFORCE || echo warn)}"
export WEIGHT_ENFORCE
_FETCH_ALLOW_TAILNET="${OPENBEAST_FETCH_ALLOW_TAILNET:-$(_ob_conf_value FETCH_ALLOW_TAILNET || true)}"
if [[ -n "$_FETCH_ALLOW_TAILNET" ]]; then
  export OPENBEAST_FETCH_ALLOW_TAILNET="$(_ob_bool "$_FETCH_ALLOW_TAILNET" false FETCH_ALLOW_TAILNET)"
fi
# beast-gate: the identity-aware inference edge (agents/edge.py, docs/BEAST_SLOT.md).
# Opt-in. When true, start.sh runs it on EDGE_PORT and setup-tailscale.sh
# publishes :8443 at IT instead of raw llama-server — remote clients then get
# per-device keys, a path allowlist, rate limits, and an inference audit. The
# local command center is untouched either way.
EDGE_GATE="$(_ob_bool "${OPENBEAST_EDGE_GATE:-$(_ob_conf_value EDGE_GATE || true)}" false EDGE_GATE)"
EDGE_PORT="${OPENBEAST_EDGE_PORT:-$(_ob_conf_value EDGE_PORT || echo 8090)}"
export EDGE_GATE EDGE_PORT
export OPENBEAST_EDGE_PORT="$EDGE_PORT"
# beast-artifact: a durable URL for anything the model renders (docs/BEAST_ARTIFACT_PLAN.md).
# Opt-in. When true, start.sh runs agents/artifact_server.py on ARTIFACT_PORT
# (loopback) and setup-tailscale.sh --publish-artifact mounts it at :8446 so
# pages open on a phone. The server SERVES the pages; it is not the publish
# path for the MCP/WebUI tools — publish_artifact/list_artifacts call the
# store (agents/artifact.py) in process. scripts/artifact.sh is what talks to
# it over loopback, with a proof-of-locality token on write verbs (the same
# idiom beast-gate uses).
BEAST_ARTIFACT="$(_ob_bool "${OPENBEAST_BEAST_ARTIFACT:-$(_ob_conf_value BEAST_ARTIFACT || true)}" false BEAST_ARTIFACT)"
ARTIFACT_PORT="${OPENBEAST_ARTIFACT_PORT:-$(_ob_conf_value ARTIFACT_PORT || echo 3004)}"
export BEAST_ARTIFACT ARTIFACT_PORT
export OPENBEAST_ARTIFACT_PORT="$ARTIFACT_PORT"
# The public base of every artifact URL. Normally UNSET: agents/artifact.py
# then asks `tailscale serve` for the name it publishes :8446 under, and falls
# back to http://localhost:<ARTIFACT_PORT>. Set it only behind a proxy of your
# own. Exported only when non-empty, so "unset" still means "detect".
_ARTIFACT_BASE_URL="${OPENBEAST_ARTIFACT_BASE_URL:-$(_ob_conf_value ARTIFACT_BASE_URL || true)}"
if [[ -n "$_ARTIFACT_BASE_URL" ]]; then
  export OPENBEAST_ARTIFACT_BASE_URL="$_ARTIFACT_BASE_URL"
fi
unset _ARTIFACT_BASE_URL
# Tailnet logins allowed to READ the gallery. Empty = fall back to
# CHAT_OPERATORS (beast-chat's list). Only exported when non-empty, same
# discipline as the keys above: an exported empty string reads as "configured
# but nobody allowed" to the server instead of "not configured".
_ARTIFACT_OPERATORS="${OPENBEAST_ARTIFACT_OPERATORS:-$(_ob_conf_value ARTIFACT_OPERATORS || true)}"
if [[ -n "$_ARTIFACT_OPERATORS" ]]; then
  export OPENBEAST_ARTIFACT_OPERATORS="$_ARTIFACT_OPERATORS"
fi
# Per-device admission control (llama-server's own queue is unbounded).
_EDGE_RATE="${OPENBEAST_EDGE_RATE_LIMIT:-$(_ob_conf_value EDGE_RATE_LIMIT || true)}"
[[ -n "$_EDGE_RATE" ]] && export OPENBEAST_EDGE_RATE_LIMIT="$_EDGE_RATE"
_EDGE_INFLIGHT="${OPENBEAST_EDGE_MAX_INFLIGHT:-$(_ob_conf_value EDGE_MAX_INFLIGHT || true)}"
[[ -n "$_EDGE_INFLIGHT" ]] && export OPENBEAST_EDGE_MAX_INFLIGHT="$_EDGE_INFLIGHT"
# Fail-closed by default: an empty device registry refuses remote callers
# rather than serving them anonymously (the 2026-07-17 RBAC lesson).
_EDGE_ANON="${OPENBEAST_EDGE_ALLOW_ANON:-$(_ob_conf_value EDGE_ALLOW_ANON || true)}"
[[ -n "$_EDGE_ANON" ]] && export OPENBEAST_EDGE_ALLOW_ANON="$(_ob_bool "$_EDGE_ANON" false EDGE_ALLOW_ANON)"
# beast-chat — the operator console for this rig's own sessions
# (agents/chat_server.py, docs/BEAST_CHAT.md). Opt-in. When true, start.sh
# runs it on CHAT_PORT bound to loopback, and setup-tailscale.sh --publish-chat
# maps :8445 at it so a phone on the tailnet can watch and steer agents and
# jobs. CHAT_OPERATORS is the READ allowlist: a comma-separated list of
# tailnet logins (the Tailscale-User-Login that `tailscale serve` injects);
# anything not listed gets 404, never 403 — the beast-gate convention. Left
# EMPTY the allowlist is not enforced at all: every login on your tailnet can
# READ every session. That is the single-operator default and it is fine on a
# tailnet you own outright — set it the moment a device you don't own joins.
# WRITING (send a message, stop a session, start an agent) additionally needs
# a chat-scoped device key: ./scripts/clients.sh enroll phone --scope chat.
BEAST_CHAT="$(_ob_bool "${OPENBEAST_BEAST_CHAT:-$(_ob_conf_value BEAST_CHAT || true)}" false BEAST_CHAT)"
CHAT_PORT="${OPENBEAST_CHAT_PORT:-$(_ob_conf_value CHAT_PORT || echo 3003)}"
CHAT_OPERATORS="${OPENBEAST_CHAT_OPERATORS:-$(_ob_conf_value CHAT_OPERATORS || true)}"
export OPENBEAST_BEAST_CHAT="$BEAST_CHAT"
export OPENBEAST_CHAT_PORT="$CHAT_PORT"
# Same export discipline as the keys above: only when non-empty, so the chat
# server can tell "no allowlist configured" from "an allowlist of nobody".
if [[ -n "$CHAT_OPERATORS" ]]; then
  export OPENBEAST_CHAT_OPERATORS="$CHAT_OPERATORS"
fi
# Where a process ON THIS BOX dials the stack's BIND_HOST services
# (lib/net.sh: wildcard/empty -> 127.0.0.1, :: -> [::1], a specific LAN or
# tailnet address -> itself, since a socket bound there refuses loopback).
# Exported for configure-webui.sh and anyone else building local URLs.
# (net.sh always ships next to this file; a stripped copy without it — a
# test sandbox, a hand-copied conf.sh — keeps the old loopback behaviour.)
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/net.sh" ]]; then
  # shellcheck source=net.sh
  source "$(dirname "${BASH_SOURCE[0]}")/net.sh"
  OPENBEAST_PROBE_HOST="$(ob_probe_host "$BIND_HOST")"
else
  OPENBEAST_PROBE_HOST=127.0.0.1
fi
export OPENBEAST_PROBE_HOST
# The tool server's web_search (agents/tools.py) defaults SEARXNG_URL to
# localhost:8888, but SearXNG binds BIND_HOST — and a socket bound to a
# specific LAN/tailnet address refuses loopback, so the MODEL's search tool
# was refused on exactly the rigs whose WebUI search now worked. Point it at
# the probe host there. Loopback/wildcard binds leave it unset (the
# historical default already works), and an operator's own SEARXNG_URL wins.
if [[ -z "${SEARXNG_URL:-}" && "$OPENBEAST_PROBE_HOST" != "127.0.0.1" ]]; then
  export SEARXNG_URL="http://${OPENBEAST_PROBE_HOST}:8888"
fi
# The router hard-binds 127.0.0.1 (agents/router.py) whatever BIND_HOST is,
# so it is always dialled there; llama-server binds BIND_HOST, so it is
# dialled on the probe host. It was `localhost` for both, and on a rig with a
# specific BIND_HOST (the documented LAN/tailnet case) Open WebUI had no model
# while every health probe, which follows BIND_HOST, read green. Open WebUI
# runs with network_mode: host (docker-compose.yml), so the address that
# works from the host shell is the one that works from inside the container.
# The loopback/wildcard case keeps the historical `localhost` spelling on
# purpose: configure-webui.sh rewrites WebUI's stored connection (and
# restarts the container) whenever this string changes.
_ob_model_host="$OPENBEAST_PROBE_HOST"
[[ "$_ob_model_host" == "127.0.0.1" ]] && _ob_model_host=localhost
if [[ "$AGENT_ROUTER" == "true" ]]; then
  MODEL_URL="http://localhost:${ROUTER_PORT}/v1"
else
  MODEL_URL="http://${_ob_model_host}:8080/v1"
fi
unset _ob_model_host
export AGENT_ROUTER ROUTER_PORT
# Frontends read this for the model endpoint (docker-compose interpolates it).
export OPENBEAST_MODEL_URL="$MODEL_URL"
# Daemon-mode memory cap as a PERCENT of this machine's physical RAM
# (start.sh computes the byte value from /proc/meminfo at every launch, so
# the cap scales with whatever box OpenBeast lands on — 128 GB or 32 GB).
MEM_LIMIT_PCT="${OPENBEAST_MEM_LIMIT_PCT:-$(_ob_conf_value MEM_LIMIT_PCT || echo 75)}"
# start.sh installs + enables the daily openbeast-logrotate.timer (systemd
# --user) when it is missing. false = leave rotation to the operator.
LOGROTATE_AUTOINSTALL="$(_ob_bool "${OPENBEAST_LOGROTATE_AUTOINSTALL:-$(_ob_conf_value LOGROTATE_AUTOINSTALL || true)}" true LOGROTATE_AUTOINSTALL)"
# Where files the CHAT model writes/reads via the direct tools land. A direct
# tool call carries no conversation or user id (the OpenAPI tool server is
# stateless), so without this the model picks its own path and defaults to a
# world-readable, reboot-wiped /tmp. Anchor those ops to a persistent, private
# (0700) workspace instead. Spawned agents keep using their own AGENT_WORKDIR.
# start.sh creates the dir with the right mode; mcp_server inherits this env.
OPENBEAST_FILES_DIR="${OPENBEAST_FILES_DIR:-$(_ob_conf_value FILES_DIR || echo "$HOME/openbeast-files")}"
# Expand a leading ~ and anchor a relative path to the checkout — the same
# resolution uninstall.sh applies. The example conf's own form is
# `FILES_DIR=~/openbeast-files`, and _ob_conf_value returns it verbatim, so
# start.sh ran `mkdir -p "~/openbeast-files"` — a literal `~` directory under
# its cwd — while the Python side expanduser()'d the same string to $HOME.
# shellcheck disable=SC2088  # matching a literal ~, not expanding one
case "$OPENBEAST_FILES_DIR" in
  "~")   OPENBEAST_FILES_DIR="$HOME" ;;
  "~/"*) OPENBEAST_FILES_DIR="$HOME/${OPENBEAST_FILES_DIR#\~/}" ;;
esac
case "$OPENBEAST_FILES_DIR" in
  /*) ;;
  *)  OPENBEAST_FILES_DIR="$REPO_DIR/$OPENBEAST_FILES_DIR" ;;
esac
export OPENBEAST_FILES_DIR
# WEBUI_AUTH default is FALSE (local-only single user — no login wall, and
# configure-webui.sh can auto-configure via the default admin account). It
# is flipped to true by scripts/setup-tailscale.sh when the WebUI becomes
# reachable from the whole tailnet (that's when a login boundary matters).
# docker-compose reads this via OPENBEAST_WEBUI_AUTH.
WEBUI_AUTH="$(_ob_bool "${OPENBEAST_WEBUI_AUTH:-$(_ob_conf_value WEBUI_AUTH || true)}" false WEBUI_AUTH)"
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
# A non-loopback BIND_HOST puts every service on that network — and the
# identity tool server (:3001) binds it too (OPENBEAST_BIND). With neither
# MCPO key set it is OPEN: anyone who can reach the address can POST /bash
# and run commands as this user. The warning used to fire only for the
# literal 0.0.0.0 / ::, so a specific LAN or tailnet IP (192.168.1.20,
# 100.x) — exactly what an operator types to reach the WebUI from a laptop —
# got no word at all. It now fires for ANY non-loopback bind, and names the
# remote shell when the tool server is keyless.
#
# ALLOW_OPEN_TOOLS (env OPENBEAST_ALLOW_OPEN_TOOLS) default false: the
# explicit, auditable "yes, serve the keyless tool server on this network"
# acknowledgement. Exported canonical so the tool server / start.sh CAN refuse
# without it — but nothing reads it yet: today it only changes the warning.
ALLOW_OPEN_TOOLS="$(_ob_bool "${OPENBEAST_ALLOW_OPEN_TOOLS:-$(_ob_conf_value ALLOW_OPEN_TOOLS || true)}" false ALLOW_OPEN_TOOLS)"
export OPENBEAST_ALLOW_OPEN_TOOLS="$ALLOW_OPEN_TOOLS"
# ALLOW_OPEN_WEBUI (env OPENBEAST_ALLOW_OPEN_WEBUI) default false: the
# persisted form of setup-tailscale.sh --i-accept-open-webui — "yes, publish
# the WebUI on :443 with WEBUI_AUTH off". setup-tailscale writes it when that
# flag publishes; doctor.sh then WARNs about the open :443 instead of FAILing.
ALLOW_OPEN_WEBUI="$(_ob_bool "${OPENBEAST_ALLOW_OPEN_WEBUI:-$(_ob_conf_value ALLOW_OPEN_WEBUI || true)}" false ALLOW_OPEN_WEBUI)"
export OPENBEAST_ALLOW_OPEN_WEBUI="$ALLOW_OPEN_WEBUI"
# ob_tools_exposed_open — true when the tool server would listen off-loopback
# with no key: the state start.sh / doctor.sh / the tool server must refuse
# (or, with ALLOW_OPEN_TOOLS=true, shout about).
ob_tools_exposed_open() {
  ! ob_bind_is_loopback "$BIND_HOST" && [[ -z "$MCPO_ADMIN_KEY" && -z "$MCPO_GUEST_KEY" ]]
}
if ! ob_bind_is_loopback "$BIND_HOST"; then
  echo "WARNING: BIND_HOST=$BIND_HOST is not loopback — the stack's services are" >&2
  echo "         reachable from that network. Prefer Tailscale (scripts/setup-tailscale.sh)." >&2
  if ob_tools_exposed_open; then
    echo "WARNING: the tool server (:3001) has NO keys (MCPO_ADMIN_KEY/MCPO_GUEST_KEY):" >&2
    echo "         anyone who can reach $BIND_HOST:3001 can run shell commands as this user." >&2
    if [[ "$ALLOW_OPEN_TOOLS" == "true" ]]; then
      echo "         ALLOW_OPEN_TOOLS=true — acknowledged; the risk above still stands." >&2
    else
      echo "         It is served open regardless (no refusal is enforced yet). Run" >&2
      echo "         scripts/setup-mcpo-keys.sh, or set ALLOW_OPEN_TOOLS=true to acknowledge." >&2
    fi
  fi
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
