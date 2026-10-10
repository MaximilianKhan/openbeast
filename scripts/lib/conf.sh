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
#   CHAT_NOTIFY_URL / CHAT_NOTIFY_ON / CHAT_NOTIFY_TOKEN_FILE
#       (env OPENBEAST_CHAT_NOTIFY_*)  default empty / failed,lost,done / empty
#   CHAT_PUBLIC_URL  (env OPENBEAST_CHAT_PUBLIC_URL) default empty = detect
#       beast-chat push notifications; NTFY_PORT (default 3005) for the
#       opt-in ntfy extension.
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
#   OB_CONF_READONLY=1   (env only, set by the CALLER — never a conf key)
#       "I only want to read the config." Sourcing this file normally mints
#       SEARXNG_SECRET on first use and writes it to openbeast.conf; a command
#       that promises to change nothing (bootstrap.sh --preflight, doctor.sh,
#       a report-only healthcheck.sh) exports this first, and the file is
#       then never created or appended to. SEARXNG_SECRET stays empty there,
#       so a `docker compose up` from such a shell stops on compose's own
#       "must be set" message instead of running with a throwaway key.
#   OB_CONF_LINTED       (env, set by this file) the unknown-key / bad-value
#       warnings (ob_conf_lint, at the end) were already printed once by a
#       parent process; children that re-source this file stay quiet.
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

# A few OPENBEAST_<KEY> names are BOTH the env override for KEY and this
# file's own derived export (the dashboard, children and `start.sh -d` read
# them). Re-sourced in a shell that once had HYDRA=true, the old export read
# back as an operator override and pinned the value: flipping HYDRA=false in
# openbeast.conf did nothing. Such exports now go through _ob_derive, which
# records the value in OPENBEAST_DERIVED_<KEY> (forwarded by -d with the
# rest); _ob_override ignores an env value that is only that record, so the
# conf file decides again. An env value that differs from the record is a
# real override and wins as always.
_ob_override() { # _ob_override KEY — print the real env override, or fail
  local ev="OPENBEAST_$1" mv="OPENBEAST_DERIVED_$1"
  [[ -n "${!ev:-}" ]] || return 1
  [[ -n "${!mv+x}" && "${!ev}" == "${!mv}" ]] && return 1
  printf '%s\n' "${!ev}"
}
_ob_derive() { # _ob_derive KEY VALUE [FROM_ENV] — export OPENBEAST_KEY=VALUE as derived
  # ...unless the operator's own env set KEY: that stays an override on
  # every re-source, so no record is kept for it. FROM_ENV (1/0) says so
  # when the caller knows better than the env does now.
  local from_env="${3:-}"
  if [[ -z "$from_env" ]]; then
    from_env=0
    _ob_override "$1" >/dev/null && from_env=1
  fi
  if [[ "$from_env" == 1 ]]; then
    unset "OPENBEAST_DERIVED_$1"
    export "OPENBEAST_$1=$2"
  else
    export "OPENBEAST_$1=$2" "OPENBEAST_DERIVED_$1=$2"
  fi
}
_ob_underive() { # _ob_underive KEY — drop OPENBEAST_KEY if it is only our record
  local ev="OPENBEAST_$1" mv="OPENBEAST_DERIVED_$1"
  [[ -n "${!mv+x}" ]] || return 0
  [[ "${!ev:-}" == "${!mv}" ]] && unset "$ev"
  unset "$mv"
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
# Values this file had to drop while resolving, one message each. ob_conf_lint
# (end of file) reports them with its other findings, so they are said once
# per command and doctor.sh can show them as rows.
_OB_CONF_PROBLEMS=()
# An integer or nothing. `REASONING_BUDGET=lots` (or `4096  # cap`, which
# arrived whole) went to llama-server as --reasoning-budget and killed it at
# launch, reported only as the generic "llama-server exited".
REASONING_BUDGET="${REASONING_BUDGET%%[[:space:]#]*}"
if [[ -n "$REASONING_BUDGET" && ! "$REASONING_BUDGET" =~ ^-?[0-9]+$ ]]; then
  _OB_CONF_PROBLEMS+=("REASONING_BUDGET='$REASONING_BUDGET' is not an integer (thinking tokens; 0 = none, -1 = unlimited) — ignoring it, each model's own default stands")
  REASONING_BUDGET=""
fi
# Host-RAM prompt cache (serve.sh hands it to llama-server), integer MiB:
#   empty / auto = automatic (serve.sh sizes it to this machine's RAM),
#   0     = the server's own default, N = exactly N MiB, -1 = no limit.
# The same values serve.sh takes and openbeast.conf.example documents: `auto`
# and `-1` used to be reported here as "not a whole number — ignoring it"
# while serve.sh went on to apply them.
# Env override: $OPENBEAST_PROMPT_CACHE_RAM_MB — not a plain inherited
# PROMPT_CACHE_RAM_MB, which is dropped like any other stale export.
# Exported only when set, so "unset" still means "automatic" to whatever
# serve.sh runs under.
PROMPT_CACHE_RAM_MB="${OPENBEAST_PROMPT_CACHE_RAM_MB:-$(_ob_conf_value PROMPT_CACHE_RAM_MB || true)}"
PROMPT_CACHE_RAM_MB="${PROMPT_CACHE_RAM_MB%%[[:space:]#]*}"
if [[ -n "$PROMPT_CACHE_RAM_MB" && ! "$PROMPT_CACHE_RAM_MB" =~ ^(auto|-1|[0-9]+)$ ]]; then
  _OB_CONF_PROBLEMS+=("PROMPT_CACHE_RAM_MB='$PROMPT_CACHE_RAM_MB' is not a whole number of MiB (or auto, 0, -1) — ignoring it, the prompt cache is sized automatically")
  PROMPT_CACHE_RAM_MB=""
fi
if [[ -n "$PROMPT_CACHE_RAM_MB" ]]; then
  export PROMPT_CACHE_RAM_MB
else
  export -n PROMPT_CACHE_RAM_MB 2>/dev/null || true
fi
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
# beast-lang escalation (docs/BEAST_LANG_PLAN.md §7 P4): attach the
# toolchain-confirmed fix to a failing check. agents/tools.py reads
# BEAST_ESCALATE per-call and compares against exactly "1", so the conf value
# goes through _ob_bool and is exported canonically as 1/0 — `true`, `yes` or
# `1   # on` would otherwise be silently OFF. Forwarded only when set, like
# BEAST_ASSIST; eval arms pin both spellings per cell. It rides inside the
# beast-assist block, so on without BEAST_ASSIST=1 does nothing: say so.
_BEAST_ESCALATE="${BEAST_ESCALATE:-$(_ob_conf_value BEAST_ESCALATE || true)}"
if [[ -n "$_BEAST_ESCALATE" ]]; then
  if [[ "$(_ob_bool "$_BEAST_ESCALATE" false BEAST_ESCALATE)" == "true" ]]; then
    export BEAST_ESCALATE=1
    # Test what agents/tools.diagnostics_enabled() reads — the forwarded
    # BEAST_ASSIST (or OPENBEAST_DIAGNOSTICS), whitespace-stripped, equal to
    # exactly "1" — NOT _ob_bool: BEAST_ASSIST is forwarded verbatim, so
    # `BEAST_ASSIST=true` passed _ob_bool while the checker stayed off, and
    # this warning went quiet for exactly the case it exists to catch.
    _ob_assist="${BEAST_ASSIST:-}"; _ob_diag="${OPENBEAST_DIAGNOSTICS:-}"
    _ob_assist="${_ob_assist#"${_ob_assist%%[![:space:]]*}"}"; _ob_assist="${_ob_assist%"${_ob_assist##*[![:space:]]}"}"
    _ob_diag="${_ob_diag#"${_ob_diag%%[![:space:]]*}"}"; _ob_diag="${_ob_diag%"${_ob_diag##*[![:space:]]}"}"
    if [[ "$_ob_assist" != "1" && "$_ob_diag" != "1" ]]; then
      echo "WARNING: BEAST_ESCALATE=1 has no effect without BEAST_ASSIST=1 (the card rides inside the checker's verdict; BEAST_ASSIST is '${BEAST_ASSIST:-}', and only exactly 1 turns the checker on)." >&2
    fi
    unset _ob_assist _ob_diag
  else
    export BEAST_ESCALATE=0
  fi
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
# Fail-closed by default: with no device to match, remote callers are refused
# rather than served anonymously (the 2026-07-17 RBAC lesson). The opt-out
# covers ONLY a rig that has never had a registry: once .run/clients.json
# exists — emptied by `clients.sh remove`, or unreadable — the gate ignores
# it and answers 401.
_EDGE_ANON="${OPENBEAST_EDGE_ALLOW_ANON:-$(_ob_conf_value EDGE_ALLOW_ANON || true)}"
[[ -n "$_EDGE_ANON" ]] && export OPENBEAST_EDGE_ALLOW_ANON="$(_ob_bool "$_EDGE_ANON" false EDGE_ALLOW_ANON)"
# Largest request body the gate accepts, in bytes (the gate's own default is
# 8 MiB). Exported only when set, like the limits above.
_EDGE_BODY="${OPENBEAST_EDGE_MAX_BODY:-$(_ob_conf_value EDGE_MAX_BODY || true)}"
[[ -n "$_EDGE_BODY" ]] && export OPENBEAST_EDGE_MAX_BODY="$_EDGE_BODY"
# Inline media only by default: the gate answers 400 for an image/audio/video
# part that names a URL, because llama-server would fetch it from inside the
# rig (SSRF for a remote device). true forwards such parts again.
_EDGE_MEDIA="${OPENBEAST_EDGE_ALLOW_MEDIA_URLS:-$(_ob_conf_value EDGE_ALLOW_MEDIA_URLS || true)}"
[[ -n "$_EDGE_MEDIA" ]] && export OPENBEAST_EDGE_ALLOW_MEDIA_URLS="$(_ob_bool "$_EDGE_MEDIA" false EDGE_ALLOW_MEDIA_URLS)"
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
# beast-chat push notifications (openbeast.conf.example § beast-chat). The
# chat server reads OPENBEAST_CHAT_NOTIFY_URL / _ON / _TOKEN_FILE. ON always
# carries a value; the token file is a PATH (a leading ~/ expanded), never the
# token, and is exported only when set.
#
# The URL is NOT exported. With ntfy's default read-write access the topic in
# it IS the credential, and the name matches none of the secret filters
# (*KEY*/*SECRET*/*PASSWORD*/*TOKEN*): exported here it rode `./start.sh -d`'s
# `systemd-run --setenv=` onto argv and into the unit environment, and reached
# every model-authored bash command through tools._scrubbed_env (review
# 2026-09-30). It stays a plain shell variable; ob_exec_chat_server hands it to
# the chat server's process ALONE. An env override is honoured once, then
# removed from the environment so nothing this shell starts inherits it.
if [[ -n "${OPENBEAST_CHAT_NOTIFY_URL:-}" ]]; then
  _OB_NOTIFY_URL_ENV="$OPENBEAST_CHAT_NOTIFY_URL"
fi
unset OPENBEAST_CHAT_NOTIFY_URL
export -n CHAT_NOTIFY_URL 2>/dev/null || true
CHAT_NOTIFY_URL="${_OB_NOTIFY_URL_ENV:-$(_ob_conf_value CHAT_NOTIFY_URL || true)}"
CHAT_NOTIFY_ON="${OPENBEAST_CHAT_NOTIFY_ON:-$(_ob_conf_value CHAT_NOTIFY_ON || echo failed,lost,done)}"
CHAT_NOTIFY_TOKEN_FILE="${OPENBEAST_CHAT_NOTIFY_TOKEN_FILE:-$(_ob_conf_value CHAT_NOTIFY_TOKEN_FILE || true)}"
[[ "$CHAT_NOTIFY_TOKEN_FILE" == "~/"* ]] && CHAT_NOTIFY_TOKEN_FILE="$HOME/${CHAT_NOTIFY_TOKEN_FILE#\~/}"
export OPENBEAST_CHAT_NOTIFY_ON="$CHAT_NOTIFY_ON"
if [[ -n "$CHAT_NOTIFY_TOKEN_FILE" ]]; then
  export OPENBEAST_CHAT_NOTIFY_TOKEN_FILE="$CHAT_NOTIFY_TOKEN_FILE"
fi

# The console URL a notification's deep link opens (chat_server reads
# OPENBEAST_CHAT_PUBLIC_URL). Unset = detect the :8445 name from `tailscale
# serve status`. Not a secret; exported only when set, so unset stays "detect".
CHAT_PUBLIC_URL="${OPENBEAST_CHAT_PUBLIC_URL:-$(_ob_conf_value CHAT_PUBLIC_URL || true)}"
if [[ -n "$CHAT_PUBLIC_URL" ]]; then
  export OPENBEAST_CHAT_PUBLIC_URL="$CHAT_PUBLIC_URL"
fi

# ob_exec_chat_server <chat_server.py> — exec the chat server with the notify
# URL in ITS environment only. Run it as a background job (`… &`): the export
# lands in that subshell and exec replaces it, so the URL never appears on an
# argv and never enters the caller's environment. start.sh and healthcheck.sh
# both launch the console through here.
ob_exec_chat_server() {
  if [[ -n "${CHAT_NOTIFY_URL:-}" ]]; then
    export OPENBEAST_CHAT_NOTIFY_URL="$CHAT_NOTIFY_URL"
  fi
  exec python3 "$1"
}

# The opt-in ntfy extension (extensions/ntfy): its loopback port, and the two
# settings only iOS instant delivery needs. Exported for compose
# interpolation; the fragment defaults every one of them, so an unset value
# never fails `docker compose` (stop.sh passes every fragment on disk).
NTFY_PORT="${OPENBEAST_NTFY_PORT:-$(_ob_conf_value NTFY_PORT || echo 3005)}"
export OPENBEAST_NTFY_PORT="$NTFY_PORT"
_NTFY_BASE="${OPENBEAST_NTFY_BASE_URL:-$(_ob_conf_value NTFY_BASE_URL || true)}"
[[ -n "$_NTFY_BASE" ]] && export OPENBEAST_NTFY_BASE_URL="$_NTFY_BASE"
_NTFY_UP="${OPENBEAST_NTFY_UPSTREAM_BASE_URL:-$(_ob_conf_value NTFY_UPSTREAM_BASE_URL || true)}"
[[ -n "$_NTFY_UP" ]] && export OPENBEAST_NTFY_UPSTREAM_BASE_URL="$_NTFY_UP"
_NTFY_ACCESS="${OPENBEAST_NTFY_DEFAULT_ACCESS:-$(_ob_conf_value NTFY_DEFAULT_ACCESS || true)}"
[[ -n "$_NTFY_ACCESS" ]] && export OPENBEAST_NTFY_DEFAULT_ACCESS="$_NTFY_ACCESS"
unset _NTFY_BASE _NTFY_UP _NTFY_ACCESS
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
# ── Inference backend (docs/DGX_SPARK_PLAN.md) ─────────────────────────────
# Which OpenAI-compatible server the stack talks to, where it is, and whether
# this stack owns it. The default is today's stack, byte for byte: a
# llama-server that start.sh launches on :8080 of this box.
#   INFERENCE_BACKEND  llama | vllm | tensorfold          default llama
#       Readiness and capacity differ per server (lib/backend.sh). A value
#       that is none of the three warns and falls back to llama.
#   INFERENCE_URL      base URL, no /v1                   default http://<probe-host>:8080
#       MODEL_URL (WebUI), the router's and beast-gate's upstream, and the
#       spawned agents' AGENT_INFERENCE_URL all derive from it.
#   INFERENCE_MANAGED  true | false                       default true for llama, false otherwise
#       false: start.sh launches, rolls back, supervises and kills NOTHING —
#       it waits for the server to answer, brings up everything else, and
#       healthcheck --restart / stop.sh leave the server alone. vLLM and
#       TensorFold are never managed (they run on other boxes, in
#       containers this stack did not start), so `true` there warns and is
#       ignored.
#   INFERENCE_SLOTS    integer                            default empty
#       Concurrency /api/slot advertises when the server does not expose one
#       (vLLM --max-num-seqs, TensorFold --parallel).
#   INFERENCE_MODEL    served model id                    default empty
#       vllm/tensorfold only: exported as OPENBEAST_INFERENCE_MODEL, the id
#       agents/runner.py sends (scripts/backends/use-model.sh records it).
# The llama defaults are resolved even when nothing is set, so every consumer
# below reads the same values it always did.
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/backend.sh" ]]; then
  # shellcheck source=backend.sh
  source "$(dirname "${BASH_SOURCE[0]}")/backend.sh"
  INFERENCE_BACKEND="$(ob_backend_normalize "${OPENBEAST_INFERENCE_BACKEND:-$(_ob_conf_value INFERENCE_BACKEND || true)}")"
else
  INFERENCE_BACKEND=llama
fi
_ob_infer_url="${OPENBEAST_INFERENCE_URL:-$(_ob_conf_value INFERENCE_URL || true)}"
_ob_infer_url="${_ob_infer_url%%[[:space:]]*}"   # a trailing `# comment` is not part of a URL
_ob_infer_url="${_ob_infer_url%/}"
_ob_infer_url="${_ob_infer_url%/v1}"             # a pasted .../v1 means the same server
if [[ -n "$_ob_infer_url" && ! "$_ob_infer_url" =~ ^https?://[^/[:space:]]+ ]]; then
  echo "WARNING: INFERENCE_URL='$_ob_infer_url' is not an http(s):// URL — using the local default." >&2
  _ob_infer_url=""
fi
if [[ -z "$_ob_infer_url" && "$INFERENCE_BACKEND" != "llama" ]]; then
  echo "WARNING: INFERENCE_BACKEND=$INFERENCE_BACKEND but INFERENCE_URL is not set — pointing at the local default; set it to the server's base URL (docs/DGX_SPARK_PLAN.md)." >&2
fi
# Explicitly configured? Consumers that must stay byte-identical on a default
# rig (MODEL_URL's historical `localhost` spelling, the dashboard's probes)
# change only when an operator set it.
if [[ -n "$_ob_infer_url" ]]; then
  INFERENCE_URL_SET=true
  INFERENCE_URL="$_ob_infer_url"
else
  INFERENCE_URL_SET=false
  INFERENCE_URL="http://${OPENBEAST_PROBE_HOST}:8080"
fi
unset _ob_infer_url
_ob_managed_raw="${OPENBEAST_INFERENCE_MANAGED:-$(_ob_conf_value INFERENCE_MANAGED || true)}"
if [[ "$INFERENCE_BACKEND" == "llama" ]]; then
  # A llama INFERENCE_URL on ANOTHER box is not ours to launch: defaulting to
  # managed there started a LOCAL llama-server while start.sh waited on the
  # remote one, and healthcheck --restart then killed and relaunched the
  # local one whenever the remote was down.
  _ob_remote=false
  if [[ "$INFERENCE_URL_SET" == "true" ]] && declare -F ob_url_is_local >/dev/null 2>&1 \
     && ! ob_url_is_local "$INFERENCE_URL" "$OPENBEAST_PROBE_HOST" "$BIND_HOST"; then
    _ob_remote=true
  fi
  if [[ -z "$_ob_managed_raw" && "$_ob_remote" == "true" ]]; then
    INFERENCE_MANAGED=false
    echo "Note: INFERENCE_URL=$INFERENCE_URL is another machine — treating its llama-server as not managed here (set INFERENCE_MANAGED explicitly to silence this)." >&2
  else
    INFERENCE_MANAGED="$(_ob_bool "$_ob_managed_raw" true INFERENCE_MANAGED)"
    if [[ "$INFERENCE_MANAGED" == "true" && "$_ob_remote" == "true" ]]; then
      echo "WARNING: CONFLICTING CONFIG — INFERENCE_MANAGED=true, but INFERENCE_URL=$INFERENCE_URL is another machine." >&2
      echo "         start.sh would launch a LOCAL llama-server while waiting on the remote one, and the watchdog" >&2
      echo "         would kill/relaunch the local one whenever the remote is down. Set INFERENCE_MANAGED=false." >&2
    fi
  fi
  unset _ob_remote
else
  INFERENCE_MANAGED="$(_ob_bool "$_ob_managed_raw" false INFERENCE_MANAGED)"
  if [[ "$INFERENCE_MANAGED" == "true" ]]; then
    echo "WARNING: INFERENCE_MANAGED=true is not supported for INFERENCE_BACKEND=$INFERENCE_BACKEND — start.sh cannot launch it; treating it as false." >&2
    INFERENCE_MANAGED=false
  fi
fi
unset _ob_managed_raw
INFERENCE_SLOTS="${OPENBEAST_INFERENCE_SLOTS:-$(_ob_conf_value INFERENCE_SLOTS || true)}"
INFERENCE_SLOTS="${INFERENCE_SLOTS%%[[:space:]#]*}"
if [[ -n "$INFERENCE_SLOTS" && ! "$INFERENCE_SLOTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "WARNING: INFERENCE_SLOTS='$INFERENCE_SLOTS' is not a positive integer — ignoring it." >&2
  INFERENCE_SLOTS=""
fi
# INFERENCE_MODEL: the served model id (scripts/backends/use-model.sh writes
# it). llama-server ignores the request's model id; vLLM with strict names
# 404s any other id, so for vllm/tensorfold the agent runner sends this one
# (OPENBEAST_INFERENCE_MODEL). A served name may contain spaces but never
# " #", so a trailing comment is cut there; one layer of quotes is dropped.
# (_ob_im_env: whether the operator's env gave it — the export below would
# otherwise look like one to the hydra block.)
_ob_im_env=0
if INFERENCE_MODEL="$(_ob_override INFERENCE_MODEL)"; then
  _ob_im_env=1
else
  INFERENCE_MODEL="$(_ob_conf_value INFERENCE_MODEL || true)"
fi
INFERENCE_MODEL="${INFERENCE_MODEL%%[[:space:]]#*}"
INFERENCE_MODEL="${INFERENCE_MODEL%"${INFERENCE_MODEL##*[![:space:]]}"}"   # rtrim
INFERENCE_MODEL="${INFERENCE_MODEL%\"}"; INFERENCE_MODEL="${INFERENCE_MODEL#\"}"
INFERENCE_MODEL="${INFERENCE_MODEL%\'}"; INFERENCE_MODEL="${INFERENCE_MODEL#\'}"
if [[ -n "$INFERENCE_MODEL" && "$INFERENCE_BACKEND" != "llama" ]]; then
  export OPENBEAST_INFERENCE_MODEL="$INFERENCE_MODEL"
fi
export INFERENCE_BACKEND INFERENCE_URL INFERENCE_MANAGED
export OPENBEAST_INFERENCE_BACKEND="$INFERENCE_BACKEND"
export OPENBEAST_INFERENCE_MANAGED="$INFERENCE_MANAGED"
# Same export discipline as AGENT_INFERENCE_URL: only when configured, so a
# reader can tell "the operator pointed us somewhere" from "the default".
if [[ "$INFERENCE_URL_SET" == "true" ]]; then
  export OPENBEAST_INFERENCE_URL="$INFERENCE_URL"
fi
if [[ -n "$INFERENCE_SLOTS" ]]; then
  export OPENBEAST_INFERENCE_SLOTS="$INFERENCE_SLOTS"
fi
# ── beast-hydra + beast-instinct (docs/BEAST_HYDRA_PLAN.md §6.7,
#    docs/BEAST_INSTINCT_PLAN.md §5.2/§5.9) ─────────────────────────────────
# Both are OPT-IN. With HYDRA and INSTINCT off (the default) this block
# changes nothing below it and exports nothing: every derived value and the
# exported environment are byte-identical to a stack without it
# (tests/test_hydra_instinct_wiring.sh sources both versions and diffs them).
#   HYDRA               (env OPENBEAST_HYDRA)               default false
#       start.sh runs agents/hydra.py on 127.0.0.1:HYDRA_PORT in front of the
#       inference fleet, and every consumer (WebUI, router, beast-gate,
#       spawned agents) talks to it instead of INFERENCE_URL.
#   HYDRA_PORT          (env OPENBEAST_HYDRA_PORT)          default 8095
#   HYDRA_CONFIG        (env OPENBEAST_HYDRA_CONFIG)        default $REPO_DIR/hydra.toml
#       Relative paths resolve against the repo. Absent = the implicit
#       single-node config (this rig's engine behind route `beast`).
#   HYDRA_DEFAULT_MODEL (env OPENBEAST_HYDRA_DEFAULT_MODEL) default beast
#       The id agents send (exported as OPENBEAST_INFERENCE_MODEL under hydra).
#   HYDRA_READY_GRACE   (env OPENBEAST_HYDRA_READY_GRACE)   default 60
#   INSTINCT            (env OPENBEAST_INSTINCT)            default false
#   INSTINCT_PORT       (env OPENBEAST_INSTINCT_PORT)       default 8094
#   INSTINCT_SCORER     (env OPENBEAST_INSTINCT_SCORER)     default false
#       The CPU Qwen3-0.6B FALLBACK scorer (:8082). Decisions run on the
#       primary 27B (the rig-27b binding, which presents LLAMA_API_KEY via
#       the service's environment); the 0.6B answers only when it is busy.
#   INSTINCT_CONFIG     (env OPENBEAST_INSTINCT_CONFIG)     default agents/instinct/instinct.toml
#   ROUTER_INSTINCT     (env OPENBEAST_ROUTER_INSTINCT)     default off
#       off|shadow|enforce: the agent router's CEILING for router.spawn_intent
#       (the service still decides the effective mode). Never exported:
#       start.sh hands it to the router process alone.
# _ob_port <raw> <default> <KEY> — a 1..65535 integer, else the default (warned).
_ob_port() {
  local v="${1%%[[:space:]#]*}"
  if [[ -z "$v" ]]; then printf '%s\n' "$2"; return 0; fi
  if [[ "$v" =~ ^[0-9]{1,5}$ ]] && (( 10#$v >= 1 && 10#$v <= 65535 )); then
    printf '%s\n' "$((10#$v))"
  else
    echo "WARNING: $3='$1' is not a port (1-65535) — using $2." >&2
    printf '%s\n' "$2"
  fi
}
# _ob_repo_path <raw> <default> — a trailing ` # comment` dropped, `~`
# expanded, a relative path anchored to the checkout (FILES_DIR's rules).
_ob_repo_path() {
  local p="${1%%[[:space:]]#*}"
  p="${p%"${p##*[![:space:]]}"}"
  [[ -n "$p" ]] || p="$2"
  # shellcheck disable=SC2088  # matching a literal ~, not expanding one
  case "$p" in
    "~")   p="$HOME" ;;
    "~/"*) p="$HOME/${p#\~/}" ;;
  esac
  case "$p" in
    /*) ;;
    *)  p="$REPO_DIR/$p" ;;
  esac
  printf '%s\n' "$p"
}
HYDRA="$(_ob_bool "$(_ob_override HYDRA || _ob_conf_value HYDRA || true)" false HYDRA)"
HYDRA_PORT="$(_ob_port "$(_ob_override HYDRA_PORT || _ob_conf_value HYDRA_PORT || true)" 8095 HYDRA_PORT)"
HYDRA_CONFIG="$(_ob_repo_path "$(_ob_override HYDRA_CONFIG || _ob_conf_value HYDRA_CONFIG || true)" hydra.toml)"
HYDRA_DEFAULT_MODEL="$(_ob_override HYDRA_DEFAULT_MODEL || _ob_conf_value HYDRA_DEFAULT_MODEL || true)"
HYDRA_DEFAULT_MODEL="${HYDRA_DEFAULT_MODEL%%[[:space:]#]*}"
if [[ -z "$HYDRA_DEFAULT_MODEL" ]]; then
  HYDRA_DEFAULT_MODEL=beast
elif [[ ! "$HYDRA_DEFAULT_MODEL" =~ ^[a-z0-9][a-z0-9._:-]{0,63}$ ]]; then
  # The rule agents/hydra_core.py ROUTE_ID_RE applies to a route id.
  echo "WARNING: HYDRA_DEFAULT_MODEL='$HYDRA_DEFAULT_MODEL' is not a route id ([a-z0-9][a-z0-9._:-]*) — using beast." >&2
  HYDRA_DEFAULT_MODEL=beast
fi
HYDRA_READY_GRACE="${OPENBEAST_HYDRA_READY_GRACE:-$(_ob_conf_value HYDRA_READY_GRACE || true)}"
HYDRA_READY_GRACE="${HYDRA_READY_GRACE%%[[:space:]#]*}"
# Base 10 explicitly: `08` would otherwise be an invalid octal in every
# (( )) that reads it, and wait_hydra_routable would never time out.
if [[ "$HYDRA_READY_GRACE" =~ ^[0-9]{1,6}$ ]]; then
  HYDRA_READY_GRACE="$((10#$HYDRA_READY_GRACE))"
else
  HYDRA_READY_GRACE=60
fi
# CONSUMER_BASE: where the stack's own consumers (router, beast-gate) send
# inference. INFERENCE_URL keeps meaning "the local engine" either way.
CONSUMER_BASE="$INFERENCE_URL"
HYDRA_URL=""
HYDRA_CALLER_TOKEN_FILE=""
if [[ "$HYDRA" == "true" ]]; then
  HYDRA_URL="http://127.0.0.1:${HYDRA_PORT}"          # hydra binds loopback, always
  CONSUMER_BASE="$HYDRA_URL"
  HYDRA_CALLER_TOKEN_FILE="$REPO_DIR/.run/hydra-caller.token"
  # The id agents send becomes the ROUTE (`beast`), for every backend, so
  # agents/runner.py sends a routable name with no edit. The engine's own
  # served id travels separately as OPENBEAST_HYDRA_UPSTREAM_MODEL. A child
  # that re-sources this file inherits OPENBEAST_INFERENCE_MODEL=beast
  # (start.sh -d forwards every OPENBEAST_* into the daemon), so recover the
  # real id from there instead of taking the route for the served model. An
  # id that came from the env only is gone once that export is recognised as
  # derived (_ob_override): it is empty then, and recovered the same way.
  # An operator's own OPENBEAST_INFERENCE_MODEL is overwritten with the
  # route below and no record is kept for it, so stash his value: with hydra
  # flipped off in the same shell, the else branch hands it back.
  if [[ "$_ob_im_env" == 1 && -n "$INFERENCE_MODEL" && "$INFERENCE_MODEL" != "$HYDRA_DEFAULT_MODEL" ]]; then
    export OPENBEAST_HYDRA_OPERATOR_MODEL="$INFERENCE_MODEL"
  elif [[ "$_ob_im_env" != 1 ]]; then
    unset OPENBEAST_HYDRA_OPERATOR_MODEL
  fi
  if [[ -z "$INFERENCE_MODEL" || "$INFERENCE_MODEL" == "$HYDRA_DEFAULT_MODEL" ]]; then
    INFERENCE_MODEL="${OPENBEAST_HYDRA_UPSTREAM_MODEL:-}"
  fi
  if [[ -n "$INFERENCE_MODEL" ]]; then
    export OPENBEAST_HYDRA_UPSTREAM_MODEL="$INFERENCE_MODEL"
  else
    unset OPENBEAST_HYDRA_UPSTREAM_MODEL
  fi
  _ob_derive INFERENCE_MODEL "$HYDRA_DEFAULT_MODEL" "$_ob_im_env"
  # hydra itself reads this (hydra_core.implicit_raw's default_route, and
  # `hydra.py --check` holds an explicit hydra.toml to it) — without the
  # export a conf-file HYDRA_DEFAULT_MODEL reached the agents but not hydra.
  _ob_derive HYDRA_DEFAULT_MODEL "$HYDRA_DEFAULT_MODEL"
  _ob_derive HYDRA true
  _ob_derive HYDRA_PORT "$HYDRA_PORT"
  export OPENBEAST_HYDRA_URL="$HYDRA_URL"
  _ob_derive HYDRA_CONFIG "$HYDRA_CONFIG"
  export OPENBEAST_CONSUMER_BASE="$CONSUMER_BASE"
  # The PATH of the 0600 token start.sh mints (never the token): hydra trusts
  # X-OpenBeast-Device / X-OpenWebUI-User-* only next to X-Hydra-Caller, and
  # the router and beast-gate (including a healthcheck --restart relaunch)
  # read the token from here to vouch for what they forward.
  export OPENBEAST_HYDRA_CALLER_TOKEN_FILE="$HYDRA_CALLER_TOKEN_FILE"
else
  # Derived exports only (never an input knob): a shell that once sourced
  # this with HYDRA=true must not keep pointing consumers at a dead hydra.
  unset OPENBEAST_CONSUMER_BASE OPENBEAST_HYDRA_URL OPENBEAST_HYDRA_CALLER_TOKEN_FILE
  # ...nor keep its own earlier HYDRA=true (an input knob too) switching
  # hydra back on, or agents sending the route id to the bare engine. Only
  # values this file exported are dropped; an operator's env is kept.
  if [[ -n "${OPENBEAST_DERIVED_HYDRA+x}" ]]; then
    # The operator's own served id, overwritten with the route while hydra
    # was on: give it back, unless he has exported a new one since.
    if [[ -n "${OPENBEAST_HYDRA_OPERATOR_MODEL:-}" \
          && "${OPENBEAST_INFERENCE_MODEL:-}" == "${OPENBEAST_HYDRA_DEFAULT_MODEL:-}" ]]; then
      INFERENCE_MODEL="$OPENBEAST_HYDRA_OPERATOR_MODEL"
      export OPENBEAST_INFERENCE_MODEL="$INFERENCE_MODEL"
    fi
    _ob_underive HYDRA; _ob_underive HYDRA_PORT; _ob_underive HYDRA_CONFIG
    _ob_underive HYDRA_DEFAULT_MODEL; _ob_underive INFERENCE_MODEL
    unset OPENBEAST_HYDRA_UPSTREAM_MODEL OPENBEAST_HYDRA_OPERATOR_MODEL
  fi
fi
INSTINCT="$(_ob_bool "$(_ob_override INSTINCT || _ob_conf_value INSTINCT || true)" false INSTINCT)"
INSTINCT_PORT="$(_ob_port "$(_ob_override INSTINCT_PORT || _ob_conf_value INSTINCT_PORT || true)" 8094 INSTINCT_PORT)"
INSTINCT_SCORER="$(_ob_bool "${OPENBEAST_INSTINCT_SCORER:-$(_ob_conf_value INSTINCT_SCORER || true)}" false INSTINCT_SCORER)"
INSTINCT_CONFIG="$(_ob_repo_path "${OPENBEAST_INSTINCT_CONFIG:-$(_ob_conf_value INSTINCT_CONFIG || true)}" agents/instinct/instinct.toml)"
_ob_ri="${OPENBEAST_ROUTER_INSTINCT:-$(_ob_conf_value ROUTER_INSTINCT || true)}"
_ob_ri="${_ob_ri%%[[:space:]#]*}"
_ob_ri="${_ob_ri//[\"\']/}"
ROUTER_INSTINCT="$(printf '%s' "$_ob_ri" | tr 'A-Z' 'a-z')"
case "$ROUTER_INSTINCT" in
  "")                 ROUTER_INSTINCT=off ;;
  off|shadow|enforce) ;;
  *)
    echo "WARNING: ROUTER_INSTINCT='$_ob_ri' is not off|shadow|enforce — using off." >&2
    ROUTER_INSTINCT=off ;;
esac
unset _ob_ri _ob_im_env
if [[ "$INSTINCT" == "true" ]]; then
  # For the dashboard's services.instinct probe (extensions inherit this
  # environment). Only when on: a default rig exports nothing new.
  _ob_derive INSTINCT true
  _ob_derive INSTINCT_PORT "$INSTINCT_PORT"
else
  _ob_underive INSTINCT; _ob_underive INSTINCT_PORT
fi
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
elif [[ "$HYDRA" == "true" ]]; then
  # hydra binds 127.0.0.1 whatever BIND_HOST is (like the router), so it is
  # dialled there, in the router line's `localhost` spelling.
  MODEL_URL="http://localhost:${HYDRA_PORT}/v1"
elif [[ "$INFERENCE_URL_SET" == "true" ]]; then
  MODEL_URL="${INFERENCE_URL}/v1"
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
# Open WebUI's per-chat background generations. After every answer WebUI asks
# the model for a title, a set of tags and follow-up suggestions; on a
# single-slot rig the last two cost more GPU than the chats they decorate and
# hold the slot for seconds after each answer (perf review 2026-10-09).
#   false (default)  configure-webui.sh turns tag and follow-up generation OFF
#                    at every start; titles stay on.
#   true             it touches none of them — whatever Admin Settings →
#                    Interface says stands.
# Env override: $OPENBEAST_WEBUI_BACKGROUND_TASKS. Not exported: the only
# consumer, configure-webui.sh, sources this file itself.
WEBUI_BACKGROUND_TASKS="$(_ob_bool "${OPENBEAST_WEBUI_BACKGROUND_TASKS:-$(_ob_conf_value WEBUI_BACKGROUND_TASKS || true)}" false WEBUI_BACKGROUND_TASKS)"
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
# A configured INFERENCE_URL is where the model lives: spawned agents must go
# there too, or every start_agent dials a local :8080 nothing serves. An
# explicit AGENT_INFERENCE_URL (a separate worker box) still wins. Setting it
# is also what lets agents present LLAMA_API_KEY to that host
# (agents/runner.py _key_endpoint_trusted).
# Under HYDRA=true spawned agents go through hydra too (an explicit value
# still wins), so an agent's calls are routed and audited like every other.
if [[ -z "$AGENT_INFERENCE_URL" && "$HYDRA" == "true" ]]; then
  AGENT_INFERENCE_URL="${HYDRA_URL}/v1"
elif [[ -z "$AGENT_INFERENCE_URL" && "$INFERENCE_URL_SET" == "true" ]]; then
  AGENT_INFERENCE_URL="${INFERENCE_URL}/v1"
fi
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
# acknowledgement. Exported canonical: agents/openapi_tools.py main() refuses
# to start keyless on a non-loopback bind without it (so start.sh stops there).
ALLOW_OPEN_TOOLS="$(_ob_bool "${OPENBEAST_ALLOW_OPEN_TOOLS:-$(_ob_conf_value ALLOW_OPEN_TOOLS || true)}" false ALLOW_OPEN_TOOLS)"
export OPENBEAST_ALLOW_OPEN_TOOLS="$ALLOW_OPEN_TOOLS"
# ALLOW_OPEN_WEBUI (env OPENBEAST_ALLOW_OPEN_WEBUI) default false: the
# persisted form of setup-tailscale.sh --i-accept-open-webui — "yes, publish
# the WebUI on :443 with WEBUI_AUTH off". setup-tailscale writes it when that
# flag publishes; doctor.sh then WARNs about the open :443 instead of FAILing.
ALLOW_OPEN_WEBUI="$(_ob_bool "${OPENBEAST_ALLOW_OPEN_WEBUI:-$(_ob_conf_value ALLOW_OPEN_WEBUI || true)}" false ALLOW_OPEN_WEBUI)"
export OPENBEAST_ALLOW_OPEN_WEBUI="$ALLOW_OPEN_WEBUI"
# ALLOW_OPEN_INFERENCE (env OPENBEAST_ALLOW_OPEN_INFERENCE) default false: the
# persisted form of setup-tailscale.sh --i-accept-open-inference — "yes,
# publish llama-server on :8443 with no beast-gate and no LLAMA_API_KEY".
# setup-tailscale writes it when that flag publishes; doctor.sh then WARNs
# about the keyless :8443 instead of FAILing. Not exported: only those two
# scripts read it, and both source this file.
ALLOW_OPEN_INFERENCE="$(_ob_bool "${OPENBEAST_ALLOW_OPEN_INFERENCE:-$(_ob_conf_value ALLOW_OPEN_INFERENCE || true)}" false ALLOW_OPEN_INFERENCE)"
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
      echo "         The tool server will REFUSE to start like this. Run" >&2
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
#
# NOT under OB_CONF_READONLY=1 (see the header): `bootstrap.sh --preflight`
# promises to write NOTHING and `doctor.sh` only diagnoses, yet both created
# openbeast.conf here on a fresh checkout. The secret stays empty for them.
SEARXNG_SECRET="${OPENBEAST_SEARXNG_SECRET:-$(_ob_conf_value SEARXNG_SECRET || true)}"
if [[ -z "$SEARXNG_SECRET" && "${OB_CONF_READONLY:-}" != "1" ]]; then
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

# ── Config lint: unknown keys and values that cannot work ───────────────────
# Nothing above ever looked at a key it did not ask for, so a typo was
# accepted in silence: `EDGE_GTAE=true` left the gate off while its operator
# believed remote clients were keyed. ob_conf_lint prints one finding per
# line (nothing when the file is clean):
#   - keys that no part of OpenBeast reads, with the nearest real key when
#     the spelling is close ("did you mean");
#   - integer keys holding something else;
#   - a SERVE_SCRIPT that is not in scripts/;
#   - whatever this file dropped while resolving (_OB_CONF_PROBLEMS).
# Always a WARNING, never a failure: the stack starts exactly as it would
# have. "Known" = every KEY= that openbeast.conf.example mentions (commented
# or not) plus _OB_CONF_EXTRA_KEYS, the keys something reads or writes that
# the example does not list. tests/test_scripts.sh pins that every key read
# anywhere in the repo is in one of the two — add a new key there, or here.
# Without openbeast.conf.example next to the conf (a stripped copy) the
# unknown-key half is skipped: there is nothing to compare against.
_OB_CONF_EXTRA_KEYS="BEAST_ASSIST BEAST_ESCALATE CHAT_BASE_URL CHAT_PUBLIC_URL WEBUI_DEFAULT_ADMIN_PASSWORD"
# Whole numbers that are used exactly as written (the port keys and the gate's
# limits take no inline comment — openbeast.conf.example says so)...
_OB_CONF_INT_KEYS="ROUTER_PORT EDGE_PORT CHAT_PORT ARTIFACT_PORT NTFY_PORT MEM_LIMIT_PCT EDGE_RATE_LIMIT EDGE_MAX_INFLIGHT EDGE_MAX_BODY ARTIFACT_RETAIN_DAYS"
# ...and those whose reader strips a trailing `# comment` first.
_OB_CONF_INT_KEYS_COMMENT_OK="HYDRA_READY_GRACE AGENT_LOG_RETENTION_DAYS"
ob_conf_lint() {
  local conf="$REPO_DIR/openbeast.conf" example="$REPO_DIR/openbeast.conf.example" k v p
  for p in ${_OB_CONF_PROBLEMS[@]+"${_OB_CONF_PROBLEMS[@]}"}; do
    printf '%s\n' "$p"
  done
  [[ -f "$conf" ]] || return 0
  for k in $_OB_CONF_INT_KEYS; do
    v="$(_ob_conf_value "$k" || true)"
    [[ -z "$v" || "$v" =~ ^[0-9]+$ ]] \
      || printf "openbeast.conf: %s='%s' is not a whole number — it is used exactly as written (no inline comment, no units)\n" "$k" "$v"
  done
  for k in $_OB_CONF_INT_KEYS_COMMENT_OK; do
    v="$(_ob_conf_value "$k" || true)"; v="${v%%[[:space:]#]*}"
    [[ -z "$v" || "$v" =~ ^[0-9]+$ ]] || printf "openbeast.conf: %s='%s' is not a whole number\n" "$k" "$v"
  done
  # Only where this stack launches the model itself; start.sh says the same
  # thing, but only once the operator has asked for a start.
  v="$(_ob_conf_value SERVE_SCRIPT || true)"
  if [[ -n "$v" && "${INFERENCE_MANAGED:-true}" == "true" && ! -f "$REPO_DIR/scripts/$v" ]]; then
    printf "openbeast.conf: SERVE_SCRIPT='%s' names no file in scripts/ — start.sh will refuse to launch it (list them: ls scripts/serve-*.sh)\n" "$v"
  fi
  [[ -f "$example" ]] || return 0
  # One awk for the whole comparison: a bash loop over ~90 known keys per
  # unknown one is slow, and this runs once per command. Distance is
  # optimal-string-alignment (a swapped pair counts 1: GTAE -> GATE).
  { grep -oE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=' "$conf" 2>/dev/null || true; } \
    | tr -d ' \t=' | awk -v extra="$_OB_CONF_EXTRA_KEYS" '
      function min3(a, b, c) { return a < b ? (a < c ? a : c) : (b < c ? b : c) }
      function osa(s, t,    i, j, m, n, c, d) {
        m = length(s); n = length(t)
        for (i = 0; i <= m; i++) d[i, 0] = i
        for (j = 0; j <= n; j++) d[0, j] = j
        for (i = 1; i <= m; i++) for (j = 1; j <= n; j++) {
          c = (substr(s, i, 1) == substr(t, j, 1)) ? 0 : 1
          d[i, j] = min3(d[i-1, j] + 1, d[i, j-1] + 1, d[i-1, j-1] + c)
          if (i > 1 && j > 1 && substr(s, i, 1) == substr(t, j-1, 1) \
              && substr(s, i-1, 1) == substr(t, j, 1) && d[i-2, j-2] + 1 < d[i, j])
            d[i, j] = d[i-2, j-2] + 1
        }
        return d[m, n]
      }
      BEGIN { n = split(extra, e, " "); for (i = 1; i <= n; i++) known[e[i]] = 1 }
      NR == FNR {
        # openbeast.conf.example: `KEY=`, `#KEY=` and `#   KEY=<n>` all name a key.
        if (match($0, /^[#[:space:]]*[A-Z][A-Z0-9_]*=/)) {
          k = substr($0, RSTART, RLENGTH); gsub(/[#[:space:]=]/, "", k); known[k] = 1
        }
        next
      }
      ($0 in known) || seen[$0]++ { next }
      {
        u = toupper($0); hint = ""
        if (u in known) hint = u
        else if (u ~ /^OPENBEAST_/ && (substr(u, 11) in known)) hint = substr(u, 11)
        else {
          best = 99
          for (k in known) { d = osa(u, k); if (d < best || (d == best && k < hint)) { best = d; hint = k } }
          if (best > 2 || best * 3 > length(u)) hint = ""
        }
        if (hint != "") printf "openbeast.conf: unknown key '\''%s'\'' — did you mean %s? As written it is ignored\n", $0, hint
        else printf "openbeast.conf: unknown key '\''%s'\'' — nothing reads it (every key: openbeast.conf.example)\n", $0
      }' "$example" -
}
# Said once per command: the first process to source this file prints the
# findings and marks the environment, so serve.sh / configure-webui.sh /
# healthcheck.sh started underneath it do not repeat them. doctor.sh sets the
# mark itself and shows the same findings as rows instead.
if [[ -z "${OB_CONF_LINTED:-}" ]]; then
  # (`if`, not `[[ … ]] && echo`: callers source this under `set -e`.)
  while IFS= read -r _ob_lint; do
    if [[ -n "$_ob_lint" ]]; then echo "WARNING: $_ob_lint" >&2; fi
  done < <(ob_conf_lint 2>/dev/null || true)
  unset _ob_lint
  export OB_CONF_LINTED=1
fi
