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

_pid_alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

if _pid_alive "$RUN_DIR/supervisor.pid"; then
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
echo "Stopping Open WebUI..."
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$SCRIPT_DIR/docker-compose.yml" down \
    || echo "Warning: docker compose down failed (daemon not running?)"
else
  echo "Docker not installed — skipping containers."
fi

# Fallback sweep for anything the supervisor didn't own.
echo "Stopping agent router..."
pkill -f "agents/router.py" 2>/dev/null && echo "agent router stopped." || echo "agent router was not running."

echo "Stopping MCPO proxy..."
pkill -f "mcpo" 2>/dev/null && echo "MCPO proxy stopped." || echo "MCPO proxy was not running."

echo "Stopping llama.cpp server..."
pkill -f "llama-server" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."

rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid" "$RUN_DIR/router.pid" 2>/dev/null || true
