#!/bin/bash
# Stop the full OpenBeast stack — gracefully.
#
# Prefers the supervisor pidfile (written by start.sh): SIGTERM lets the
# supervisor's trap shut MCPO and llama-server down in order, then we verify
# and only escalate to pkill for anything orphaned (e.g. a stack started
# before pidfiles existed, or a supervisor that was SIGKILLed).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$SCRIPT_DIR/.run"
# Resolve user config before `docker compose down` below — compose
# interpolates docker-compose.yml (including the required
# OPENBEAST_SEARXNG_SECRET) for EVERY subcommand, down included.
REPO_DIR="$SCRIPT_DIR"
source "$SCRIPT_DIR/scripts/lib/conf.sh"
source "$SCRIPT_DIR/scripts/lib/extensions.sh"
source "$SCRIPT_DIR/scripts/lib/proc.sh"   # _ob_ere, ob_pid_matches

# Identity-checked: never TERM — and 20s later KILL — an unrelated process
# that recycled a stale pidfile's PID. The supervisor records its start time
# (ob_pid_record), which is exact; 'start\.sh' in a command line is not (any
# project's ./start.sh matches it) and is only the fallback for a pidfile
# written before the start time was recorded.
if ob_recorded_pid_ours "$RUN_DIR/supervisor.pid" 'start\.sh'; then
  SUP_PID=$(cat "$RUN_DIR/supervisor.pid")
  echo "Stopping supervisor (pid $SUP_PID) gracefully..."
  kill -TERM "$SUP_PID" 2>/dev/null || true
  for _i in $(seq 1 20); do
    kill -0 "$SUP_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$SUP_PID" 2>/dev/null; then
    echo "Supervisor did not exit in 20s — escalating to SIGKILL."
    kill -KILL "$SUP_PID" 2>/dev/null || true
  else
    echo "Supervisor stopped cleanly (its trap shut down MCPO + llama-server)."
  fi
fi
# Clear the daemon scope if one exists (memory-capped systemd-run unit).
systemctl --user stop openbeast-stack 2>/dev/null || true

# Keep going even if docker is stopped/absent (--minimal installs) — the
# whole point of stop.sh is to reach the fallback kills below.
# Reap any process-kind extensions the supervisor launched (belt-and-braces —
# the supervisor's own trap also reaps them; this covers a SIGKILLed supervisor).
# Identity-checked (lib/proc.sh ob_ext_reap), like every other pid this
# script signals: .run/ survives a reboot, and a bare `kill $(cat ext-*.pid)`
# SIGTERMed whatever process had inherited the number.
for _pf in "$RUN_DIR"/ext-*.pid; do
  [[ -e "$_pf" ]] || continue
  _n="$(basename "$_pf" .pid)"
  ob_ext_reap "$_pf" "$SCRIPT_DIR/extensions/${_n#ext-}"
done

echo "Stopping Open WebUI..."
if command -v docker >/dev/null 2>&1; then
  # Include enabled compose-extension fragments so their services come down too.
  COMPOSE_FILES=(-f "$SCRIPT_DIR/docker-compose.yml")
  while IFS= read -r _cf; do [[ -n "$_cf" ]] && COMPOSE_FILES+=("$_cf"); done < <(ob_ext_compose_args)
  # ...and every OTHER compose fragment on disk. `ext.sh disable x` edits the
  # conf first and then says "./stop.sh && ./start.sh -d", so the fragment
  # of the extension just disabled is exactly the one an enabled-only list
  # leaves out — and compose leaves its containers running, ports bound.
  ALL_FILES=("${COMPOSE_FILES[@]}")
  while IFS= read -r _e; do
    [[ -n "$_e" ]] || continue
    _cf="$SCRIPT_DIR/extensions/$_e/compose.yaml"
    [[ -f "$_cf" && "$(ob_ext_meta "$_e" KIND 2>/dev/null || true)" == "compose" ]] || continue
    [[ " ${ALL_FILES[*]} " == *" $_cf "* ]] || ALL_FILES+=(-f "$_cf")
  done < <(ob_ext_available)
  # A broken fragment of an extension nobody enabled must not keep the CORE
  # up: if the full list fails, fall back to the enabled-only one.
  docker compose "${ALL_FILES[@]}" down \
    || { [[ ${#ALL_FILES[@]} -ne ${#COMPOSE_FILES[@]} ]] && docker compose "${COMPOSE_FILES[@]}" down; } \
    || echo "Warning: docker compose down failed (daemon not running?)"
else
  echo "Docker not installed — skipping containers."
fi

# Fallback sweep for anything the supervisor didn't own. Patterns are
# anchored to THIS repo's path so we never kill an unrelated llama-server
# or router the user runs for another project on the same box.
echo "Stopping agent router..."
pkill -f "$(_ob_ere "$SCRIPT_DIR/agents/router.py")" 2>/dev/null && echo "agent router stopped." || echo "agent router was not running."

echo "Stopping beast-gate..."
pkill -f "$(_ob_ere "$SCRIPT_DIR/agents/edge.py")" 2>/dev/null && echo "beast-gate stopped." || echo "beast-gate was not running."

# RECORDED PID FIRST for the two services that record one. The pattern below
# is anchored to this repo's path, so it was never going to reap a sibling
# worktree — but v1.3.0's health-check path already kills these by recorded
# pid, with the reasoning written into it (a pattern kill destroyed a live
# measurement run on this box on 2026-09-14), and shipping two different
# rules for the same hazard is one rule too many. The pattern stays as the
# fallback for an instance started outside start.sh, which records no pid.
# (router/edge still sweep by pattern only; they were not in this review.)
_stop_recorded() {                 # _stop_recorded <label> <pidfile> <script-path>
  local label="$1" pidfile="$2" pattern pid=""
  # ERE-quoted: pkill -f takes a regex, and a `+` or `(` in the repo path made
  # every pattern below match nothing — stop.sh then "stopped" a stack whose
  # llama-server kept the VRAM.
  pattern="$(_ob_ere "$3")"
  echo "Stopping $label..."
  [[ -f "$pidfile" ]] && pid="$(cat "$pidfile" 2>/dev/null || true)"
  # IDENTITY-checked, like start.sh's _pid_alive: .run/ survives a reboot and
  # a recycled pid is a stranger. `kill -0 && kill` on the bare number
  # SIGTERMed whatever had inherited it, reported "stopped", and RETURNED —
  # skipping the path fallback that would have found the real orphan.
  if ob_pid_matches "$pid" "$pattern"; then
    kill "$pid" 2>/dev/null && echo "$label stopped (pid $pid)." && return 0
  fi
  pkill -f "$pattern" 2>/dev/null \
    && echo "$label stopped (by path; no live recorded pid)." \
    || echo "$label was not running."
}

_stop_recorded "artifact server" "$RUN_DIR/artifact.pid" \
  "$SCRIPT_DIR/agents/artifact_server.py"
_stop_recorded "chat server" "$RUN_DIR/chat.pid" \
  "$SCRIPT_DIR/agents/chat_server.py"

echo "Stopping tool server..."
pkill -f "$(_ob_ere "$SCRIPT_DIR/agents/openapi_tools.py")" 2>/dev/null && echo "Tool server stopped." || echo "Tool server was not running."
# Legacy mcpo instances (pre-identity-server stacks)
pkill -f "mcpo --port" 2>/dev/null || true

echo "Stopping llama.cpp server..."
# The supervisor's own llama-server is already gone by now (its trap stopped
# it). What is left for this sweep is anything ELSE running the repo's binary
# — and while a GPU lease is held that is a campaign's server, mid-measurement,
# on a card it claimed on purpose. A pattern kill like this one destroyed a
# live run here on 2026-09-14. Skip it and say so.
# ...but the RECORDED llama-server is ours whatever the lease says (it is on
# record because this stack launched it: a supervisor that was SIGKILLed above,
# or healthcheck's no-supervisor relaunch). Skipping it too left it holding the
# VRAM with its pidfile deleted a few lines below.
_llama_pid="$(cat "$RUN_DIR/llama.pid" 2>/dev/null || true)"
if ob_pid_matches "$_llama_pid" '(^|/)llama-server( |$)'; then
  kill "$_llama_pid" 2>/dev/null && echo "llama.cpp server stopped (pid $_llama_pid)."
fi
if [[ -x "$SCRIPT_DIR/scripts/gpu-lease.sh" ]] \
   && _lease="$("$SCRIPT_DIR/scripts/gpu-lease.sh" status 2>/dev/null | head -n1 || true)" \
   && [[ "$_lease" == HELD* ]]; then
  echo "GPU lease is $_lease"
  echo "  leaving llama-server processes alone — stop that job first (scripts/gpu-lease.sh status)."
else
pkill -f "$(_ob_ere "$SCRIPT_DIR/llama.cpp/build/bin/llama-server")" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."
fi

rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/supervisor.start" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid" \
      "$RUN_DIR/mcpo-guest.pid" "$RUN_DIR/router.pid" \
      "$RUN_DIR/edge.pid" "$RUN_DIR/artifact.pid" "$RUN_DIR/chat.pid" 2>/dev/null || true
