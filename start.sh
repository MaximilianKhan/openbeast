#!/bin/bash
# Start the full OpenBeast stack:
#   1. llama.cpp server (Qwen3.6-27B Uncensored Q5_K_P by default — uncensored fine-tune, 96.16%)
#   2. MCPO proxy (wraps MCP tools as OpenAPI on http://localhost:3001)
#   3. Open WebUI (http://localhost:3000)
#
# Usage:
#   ./start.sh                     # foreground (Ctrl+C stops the stack)
#   ./start.sh -d                  # background daemon: returns when ready,
#                                  #   stack keeps running; stop with ./stop.sh
#   ./start.sh --status            # what's running (pids); health details via
#                                  #   ./scripts/healthcheck.sh
#   ./start.sh serve-qwen-27b-q5.sh    # specific model (combines with -d)
#
# Daemon mode runs inside a memory-capped systemd scope when available
# (MemoryMax=96G, swap 8G) so a runaway process can only take down the
# stack — never the box. On OOM the supervisor shuts down what remains
# gracefully. Logs: .run/stack.log; pidfiles: .run/*.pid.
#
# OpenCode connects to the MCP server via stdio (configured in opencode.json),
# so it doesn't need MCPO — just run `opencode` in any project.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
RUN_DIR="$REPO_DIR/.run"
SUP_PID_FILE="$RUN_DIR/supervisor.pid"

DAEMON=0; STATUS=0; DAEMONIZED=0; SERVE_SCRIPT=""
for arg in "$@"; do
  case "$arg" in
    -d|--daemon)   DAEMON=1 ;;
    --status)      STATUS=1 ;;
    --_daemonized) DAEMONIZED=1 ;;   # internal: this process IS the detached supervisor
    -h|--help)     sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    *)             SERVE_SCRIPT="$arg" ;;
  esac
done
SERVE_SCRIPT="${SERVE_SCRIPT:-serve-qwen-27b-uncensored-q5.sh}"

_pid_alive() { # _pid_alive <pidfile>
  [[ -f "$1" ]] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null
}

if [[ $STATUS -eq 1 ]]; then
  echo "OpenBeast stack status:"
  for name in supervisor llama mcpo; do
    f="$RUN_DIR/$name.pid"
    if _pid_alive "$f"; then
      echo "  $name: running (pid $(cat "$f"))"
    else
      echo "  $name: not running"
    fi
  done
  echo ""
  echo "Service health: ./scripts/healthcheck.sh   Logs: .run/stack.log"
  exit 0
fi

# BIND_HOST (default 127.0.0.1 — loopback-only; remote devices come in via
# Tailscale Serve, see scripts/setup-tailscale.sh). lib/conf.sh also exports
# OPENBEAST_BIND / OPENBEAST_API_KEY for docker-compose interpolation.
source "$SCRIPT_DIR/scripts/lib/conf.sh"

if [[ ! -x "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" ]]; then
  echo "Error: scripts/$SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

# ---- daemon launcher: spawn the detached supervisor, wait for readiness ----
if [[ $DAEMON -eq 1 ]]; then
  mkdir -p "$RUN_DIR"
  if _pid_alive "$SUP_PID_FILE"; then
    echo "Stack already running (supervisor pid $(cat "$SUP_PID_FILE"))." >&2
    echo "Check ./start.sh --status, or ./stop.sh first." >&2
    exit 1
  fi
  echo "Starting OpenBeast in the background ($SERVE_SCRIPT)..."
  if command -v systemd-run >/dev/null 2>&1 \
     && systemd-run --user --scope --quiet true 2>/dev/null; then
    # Transient service in a memory-capped cgroup: if anything in the stack
    # runs away, the kernel OOM-kills inside the scope; the box survives and
    # the supervisor's trap shuts the remainder down cleanly.
    systemctl --user reset-failed openbeast-stack 2>/dev/null || true
    systemd-run --user --quiet --collect --unit=openbeast-stack \
      -p MemoryMax=96G -p MemorySwapMax=8G \
      "$SCRIPT_DIR/start.sh" --_daemonized "$SERVE_SCRIPT"
    echo "  (running in memory-capped scope 'openbeast-stack': MemoryMax=96G)"
  else
    setsid nohup "$0" --_daemonized "$SERVE_SCRIPT" \
      >>"$RUN_DIR/stack.log" 2>&1 < /dev/null &
    echo "  (systemd-run unavailable — plain background process, no memory cap)"
  fi

  echo "Waiting for the model to load (log: .run/stack.log)..."
  for i in $(seq 1 300); do
    if curl -s "http://127.0.0.1:8080/health" >/dev/null 2>&1 \
       && curl -s -m 2 "http://127.0.0.1:3001/openapi.json" >/dev/null 2>&1; then
      echo ""
      echo "Stack is up:"
      echo "  Model server:  http://localhost:8080"
      echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
      echo "  Open WebUI:    http://localhost:3000"
      echo "  Status:        ./start.sh --status    Stop: ./stop.sh"
      exit 0
    fi
    if [[ $i -gt 10 ]] && ! _pid_alive "$SUP_PID_FILE"; then
      echo "Error: supervisor exited during startup. Last log lines:" >&2
      tail -20 "$RUN_DIR/stack.log" 2>/dev/null >&2 || true
      exit 1
    fi
    sleep 2
  done
  echo "Timed out after 10 min — inspect ./start.sh --status and .run/stack.log" >&2
  exit 1
fi

# ---- supervisor (foreground, or detached when --_daemonized) ---------------
mkdir -p "$RUN_DIR"
if [[ $DAEMONIZED -eq 1 ]]; then
  exec >>"$RUN_DIR/stack.log" 2>&1
  echo "=== OpenBeast supervisor start: $(date '+%Y-%m-%d %H:%M:%S') ($SERVE_SCRIPT) ==="
fi
# Transient systemd units (daemon mode) start with a minimal PATH that lacks
# ~/.local/bin, where pip --user puts mcpo. Harmless everywhere else.
export PATH="$HOME/.local/bin:$PATH"
echo $$ > "$SUP_PID_FILE"

# Fail fast on docker container-name conflicts BEFORE the multi-minute model
# load. Containers named ours but owned by another compose project (e.g.
# created before the repo was renamed) make `docker compose up` fail, and
# set -e would then tear the whole stack down mid-start.
for cname in open-webui searxng; do
  owner=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$cname" 2>/dev/null || true)
  if [[ -n "$owner" && "$owner" != "openbeast" ]]; then
    echo "Error: container '$cname' belongs to compose project '$owner', not 'openbeast'." >&2
    echo "  Its data lives in a docker volume and survives removal. Fix with:" >&2
    echo "    docker rm -f $cname     # then rerun ./start.sh" >&2
    echo "  If the old project had WebUI data, see docs/INSTALL.md troubleshooting" >&2
    echo "  ('renamed repo directory') for the volume-migration steps." >&2
    rm -f "$SUP_PID_FILE"
    exit 1
  fi
done

# Cleanup on exit: stop MCPO and llama.cpp, drop pidfiles. Runs on Ctrl+C,
# ./stop.sh (SIGTERM), and after an OOM kill takes out llama-server.
cleanup() {
  echo ""
  echo "Shutting down..."
  if [[ -n "${CONFIG_PID:-}" ]]; then
    kill "$CONFIG_PID" 2>/dev/null || true
  fi
  if [[ -n "${MCPO_PID:-}" ]]; then
    kill "$MCPO_PID" 2>/dev/null && echo "MCPO proxy stopped."
  fi
  if [[ -n "${LLAMA_PID:-}" ]]; then
    kill "$LLAMA_PID" 2>/dev/null && echo "llama.cpp server stopped."
  fi
  rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid"
}
trap cleanup EXIT INT TERM

echo "Starting llama.cpp server ($SERVE_SCRIPT)..."
"$SCRIPT_DIR/scripts/$SERVE_SCRIPT" &
LLAMA_PID=$!
echo "$LLAMA_PID" > "$RUN_DIR/llama.pid"

echo "Waiting for llama.cpp server to be ready..."
# Probe where llama-server actually listens: loopback answers for loopback
# and wildcard binds; a specific LAN/tailnet address must be probed directly.
case "$BIND_HOST" in
  127.*|localhost|0.*) HEALTH_HOST="127.0.0.1" ;;
  *)                   HEALTH_HOST="$BIND_HOST" ;;
esac
until curl -s "http://$HEALTH_HOST:8080/health" > /dev/null 2>&1; do
  if ! kill -0 "$LLAMA_PID" 2>/dev/null; then
    echo "Error: llama-server exited during startup — see its output above" >&2
    echo "       (common causes: missing weight file, VRAM OOM)" >&2
    exit 1
  fi
  sleep 1
done
echo "llama.cpp server ready on http://localhost:8080"

echo "Starting MCPO proxy (MCP tools → OpenAPI) on http://localhost:3001..."
command -v mcpo >/dev/null 2>&1 \
  || { echo "Error: mcpo not found on PATH (pip install --user mcpo puts it in ~/.local/bin)" >&2; exit 1; }
mcpo --port 3001 --host "$BIND_HOST" -- python3 "$SCRIPT_DIR/agents/mcp_server.py" &
MCPO_PID=$!
echo "$MCPO_PID" > "$RUN_DIR/mcpo.pid"
# Verify it actually serves — a blind sleep once masked a dead MCPO.
MCPO_UP=0
for _i in $(seq 1 30); do
  if ! kill -0 "$MCPO_PID" 2>/dev/null; then
    echo "Error: MCPO exited during startup — see output above" >&2
    exit 1
  fi
  curl -s -m 2 "http://$HEALTH_HOST:3001/openapi.json" >/dev/null 2>&1 && { MCPO_UP=1; break; }
  sleep 1
done
[[ $MCPO_UP -eq 1 ]] || { echo "Error: MCPO not serving after 30s" >&2; exit 1; }
echo "MCPO proxy ready on http://localhost:3001"

echo "Starting Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

# Configure Open WebUI (tool server + native function calling) in background
"$SCRIPT_DIR/scripts/configure-webui.sh" &
CONFIG_PID=$!

echo ""
echo "Stack is running:"
echo "  Model server:  http://localhost:8080"
echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
echo ""
if [[ $DAEMONIZED -eq 1 ]]; then
  echo "Running detached. Stop with ./stop.sh; status with ./start.sh --status."
else
  echo "Press Ctrl+C to stop. Or run './stop.sh' from another terminal."
fi

# Supervise: if llama-server exits (crash, OOM kill inside the scope, or a
# healthcheck restart), log it and shut the rest down gracefully via the trap.
rc=0
wait $LLAMA_PID || rc=$?
echo "llama-server exited (status $rc) — stopping the rest of the stack."
