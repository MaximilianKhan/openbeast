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

_pid_alive() { # _pid_alive <pidfile> [cmdline-pattern]
  # Identity-checked liveness: never TERM an unrelated process that recycled
  # a stale pidfile's PID. Unreadable /proc cmdline → plain kill -0 result.
  local pat="${2:-start\.sh|llama|mcpo|openapi_tools|router}" pid cmd
  [[ -f "$1" ]] || return 1
  pid="$(cat "$1" 2>/dev/null)" && [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" && [[ -n "$cmd" ]]; then
    [[ "$cmd" =~ $pat ]] || return 1
  fi
  return 0
}

if _pid_alive "$RUN_DIR/supervisor.pid" 'start\.sh'; then
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
for _pf in "$RUN_DIR"/ext-*.pid; do
  [[ -e "$_pf" ]] || continue
  kill "$(cat "$_pf" 2>/dev/null)" 2>/dev/null && echo "extension stopped ($(basename "$_pf" .pid | sed 's/^ext-//'))."
  rm -f "$_pf"
done

echo "Stopping Open WebUI..."
if command -v docker >/dev/null 2>&1; then
  # Include enabled compose-extension fragments so their services come down too.
  COMPOSE_FILES=(-f "$SCRIPT_DIR/docker-compose.yml")
  while IFS= read -r _cf; do [[ -n "$_cf" ]] && COMPOSE_FILES+=("$_cf"); done < <(ob_ext_compose_args)
  docker compose "${COMPOSE_FILES[@]}" down \
    || echo "Warning: docker compose down failed (daemon not running?)"
else
  echo "Docker not installed — skipping containers."
fi

# Fallback sweep for anything the supervisor didn't own. Patterns are
# anchored to THIS repo's path so we never kill an unrelated llama-server
# or router the user runs for another project on the same box.
echo "Stopping agent router..."
pkill -f "$SCRIPT_DIR/agents/router.py" 2>/dev/null && echo "agent router stopped." || echo "agent router was not running."

echo "Stopping tool server..."
pkill -f "$SCRIPT_DIR/agents/openapi_tools.py" 2>/dev/null && echo "Tool server stopped." || echo "Tool server was not running."
# Legacy mcpo instances (pre-identity-server stacks)
pkill -f "mcpo --port" 2>/dev/null || true

echo "Stopping llama.cpp server..."
pkill -f "$SCRIPT_DIR/llama.cpp/build/bin/llama-server" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."

rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid" \
      "$RUN_DIR/mcpo-guest.pid" "$RUN_DIR/router.pid" 2>/dev/null || true
