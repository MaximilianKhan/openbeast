#!/bin/bash
# Health monitor for the OpenBeast stack.
#
# Checks all services and optionally restarts failed ones.
#
# Usage:
#   ./scripts/healthcheck.sh              # check and report
#   ./scripts/healthcheck.sh --restart    # check and restart failed services
#
# Exit codes:
#   0 — all healthy
#   1 — one or more services down

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/conf.sh"

LLAMA_URL="${LLAMA_URL:-http://localhost:8080}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"
WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"

# Where restarted services actually answer (same mapping start.sh uses):
# loopback for loopback/wildcard binds, the address itself otherwise.
case "$BIND_HOST" in
  127.*|localhost|0.*) HEALTH_HOST="127.0.0.1" ;;
  *)                   HEALTH_HOST="$BIND_HOST" ;;
esac

RESTART=false
[[ "${1:-}" == "--restart" ]] && RESTART=true

HEALTHY=0
UNHEALTHY=0

check() {
  # check <name> <url> <match> [bearer-key] — key adds an Authorization
  # header (keyed MCPO instances answer 401 without it, RBAC Phase 2).
  local name="$1" url="$2" match="$3" key="${4:-}"
  local auth=()
  [[ -n "$key" ]] && auth=(-H "Authorization: Bearer $key")
  if curl -s --max-time 5 "${auth[@]}" "$url" 2>/dev/null | grep -qi "$match"; then
    echo "  OK   $name"
    HEALTHY=$((HEALTHY + 1))
    return 0
  else
    echo "  DOWN $name ($url)"
    UNHEALTHY=$((UNHEALTHY + 1))
    return 1
  fi
}

echo "Stack health check — $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# llama.cpp
if ! check "llama.cpp server" "$LLAMA_URL/health" "ok"; then
  if $RESTART; then
    # If a start.sh supervisor is alive, it owns llama-server: kill the
    # server and let the supervisor's self-healing loop relaunch it —
    # starting our own copy here would race it for the port and the VRAM.
    SUP_PID_FILE="$REPO_DIR/.run/supervisor.pid"
    if [[ -f "$SUP_PID_FILE" ]] && kill -0 "$(cat "$SUP_PID_FILE" 2>/dev/null)" 2>/dev/null; then
      echo "       → supervisor alive: killing llama-server, letting it relaunch..."
      pkill -f "llama-server" 2>/dev/null || true
      for i in $(seq 1 180); do
        if curl -s --max-time 2 "$LLAMA_URL/health" | grep -q "ok"; then
          echo "       → healthy after ${i}s (supervisor relaunched it)"
          break
        fi
        sleep 1
      done
    else
      echo "       → no supervisor: restarting llama.cpp directly..."
      # pkill handles multiple stale PIDs; || true because "nothing to kill"
      # (or a race with a process exiting) must not abort the healthcheck.
      if pgrep -f "llama-server" >/dev/null 2>&1; then
        pkill -f "llama-server" 2>/dev/null || true
        sleep 2
      fi
      # Relaunch the serve script the stack was STARTED with (.run/serve-script,
      # recorded by start.sh); fall back to the configured default.
      SERVE_SCRIPT_NAME="$DEFAULT_SERVE_SCRIPT"
      if [[ -f "$REPO_DIR/.run/serve-script" ]]; then
        _recorded="$(head -n1 "$REPO_DIR/.run/serve-script" 2>/dev/null || true)"
        if [[ -n "$_recorded" && -x "$SCRIPT_DIR/$_recorded" ]]; then
          SERVE_SCRIPT_NAME="$_recorded"
        fi
      fi
      echo "       → launching $SERVE_SCRIPT_NAME"
      "$SCRIPT_DIR/$SERVE_SCRIPT_NAME" &
      echo "       → started (waiting for health...)"
      for i in $(seq 1 180); do
        if curl -s --max-time 2 "$LLAMA_URL/health" | grep -q "ok"; then
          echo "       → healthy after ${i}s"
          break
        fi
        sleep 1
      done
    fi
  fi
fi

# MCPO proxy — keyed when RBAC Phase 2 is active (conf.sh exports the keys)
MCPO_KEYED=0
[[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}" && -n "${OPENBEAST_MCPO_GUEST_KEY:-}" ]] && MCPO_KEYED=1
if ! check "MCPO proxy" "$MCPO_URL/openapi.json" "openapi" "${OPENBEAST_MCPO_ADMIN_KEY:-}"; then
  if $RESTART; then
    echo "       → restarting MCPO..."
    pkill -f "mcpo --port 3001" 2>/dev/null || true
    sleep 1
    # Mirror start.sh: the chat model's file workspace must exist and be
    # private before mcp_server (spawned by mcpo) starts using it.
    if [[ ! -d "$OPENBEAST_FILES_DIR" ]]; then
      mkdir -p "$OPENBEAST_FILES_DIR" && chmod 700 "$OPENBEAST_FILES_DIR"
    fi
    MCPO_RESTART_ARGS=()
    [[ $MCPO_KEYED -eq 1 ]] && MCPO_RESTART_ARGS=(--api-key "$OPENBEAST_MCPO_ADMIN_KEY")
    MCPO_AUTH_CURL=()
    [[ $MCPO_KEYED -eq 1 ]] && MCPO_AUTH_CURL=(-H "Authorization: Bearer $OPENBEAST_MCPO_ADMIN_KEY")
    mcpo --port 3001 --host "$BIND_HOST" "${MCPO_RESTART_ARGS[@]}" -- python3 "$REPO_DIR/agents/mcp_server.py" &
    MCPO_NEW_PID=$!
    MCPO_OK=0
    for _i in $(seq 1 15); do
      if curl -s --max-time 2 "${MCPO_AUTH_CURL[@]}" "http://$HEALTH_HOST:3001/openapi.json" 2>/dev/null | grep -qi openapi; then
        MCPO_OK=1
        break
      fi
      sleep 1
    done
    if [[ $MCPO_OK -eq 1 ]]; then
      # Keep the pidfile honest so start.sh --status / stop.sh see this pid.
      mkdir -p "$REPO_DIR/.run"
      echo "$MCPO_NEW_PID" > "$REPO_DIR/.run/mcpo.pid"
      echo "       → restarted (pid $MCPO_NEW_PID)"
    else
      echo "       → restart FAILED: MCPO not serving after 15s (check its output above)"
    fi
  fi
fi

# Guest MCPO instance (only exists when RBAC Phase 2 keys are active)
if [[ $MCPO_KEYED -eq 1 ]]; then
  GUEST_URL="http://$HEALTH_HOST:${MCPO_GUEST_PORT:-3002}"
  if ! check "MCPO guest" "$GUEST_URL/openapi.json" "openapi" "$OPENBEAST_MCPO_GUEST_KEY"; then
    if $RESTART; then
      echo "       → restarting guest MCPO..."
      pkill -f "mcpo --port ${MCPO_GUEST_PORT:-3002}" 2>/dev/null || true
      sleep 1
      OPENBEAST_MCP_TOOLS="web_search,fetch" \
        mcpo --port "${MCPO_GUEST_PORT:-3002}" --host "$BIND_HOST" \
             --api-key "$OPENBEAST_MCPO_GUEST_KEY" \
             -- python3 "$REPO_DIR/agents/mcp_server.py" &
      GUEST_NEW_PID=$!
      GUEST_OK=0
      for _i in $(seq 1 15); do
        if curl -s --max-time 2 -H "Authorization: Bearer $OPENBEAST_MCPO_GUEST_KEY" \
             "$GUEST_URL/openapi.json" 2>/dev/null | grep -qi openapi; then
          GUEST_OK=1
          break
        fi
        sleep 1
      done
      if [[ $GUEST_OK -eq 1 ]]; then
        mkdir -p "$REPO_DIR/.run"
        echo "$GUEST_NEW_PID" > "$REPO_DIR/.run/mcpo-guest.pid"
        echo "       → restarted (pid $GUEST_NEW_PID)"
      else
        echo "       → restart FAILED: guest MCPO not serving after 15s"
      fi
    fi
  fi
fi

# Open WebUI
if ! check "Open WebUI" "$WEBUI_URL/api/version" "version"; then
  if $RESTART; then
    echo "       → restarting Open WebUI..."
    docker compose -f "$REPO_DIR/docker-compose.yml" up -d open-webui
    sleep 5
    echo "       → restarted"
  fi
fi

# SearXNG
if ! check "SearXNG" "$SEARXNG_URL" "searx"; then
  if $RESTART; then
    echo "       → restarting SearXNG..."
    docker compose -f "$REPO_DIR/docker-compose.yml" up -d searxng
    sleep 3
    echo "       → restarted"
  fi
fi

# Tailscale (remote access) — only checked when installed; the stack is
# fully functional without it, just localhost-only.
if command -v tailscale &>/dev/null; then
  TS_ONLINE=$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try: print('yes' if json.load(sys.stdin)['Self']['Online'] else 'no')
except Exception: print('no')
" 2>/dev/null)
  if [[ "$TS_ONLINE" == "yes" ]]; then
    echo "  OK   Tailscale (remote access)"
    HEALTHY=$((HEALTHY + 1))
  else
    echo "  DOWN Tailscale (remote access)"
    UNHEALTHY=$((UNHEALTHY + 1))
    if $RESTART; then
      # -n: never hang an unattended healthcheck on a sudo password prompt.
      if sudo -n true 2>/dev/null; then
        echo "       → restarting tailscaled..."
        sudo -n systemctl restart tailscaled
        sleep 3
      else
        echo "       → skipping tailscaled restart (needs passwordless sudo);"
        echo "         run manually: sudo systemctl restart tailscaled"
      fi
    fi
  fi
fi

# GPU VRAM usage
echo ""
if command -v nvidia-smi &>/dev/null; then
  VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
  VRAM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
  if [[ -z "$VRAM_TOTAL" || ! "$VRAM_TOTAL" =~ ^[0-9]+$ || "$VRAM_TOTAL" -eq 0 \
        || -z "$VRAM_USED" || ! "$VRAM_USED" =~ ^[0-9]+$ ]]; then
    echo "  GPU VRAM: unavailable (nvidia-smi returned no data)"
    VRAM_PCT=0
  else
    VRAM_PCT=$((VRAM_USED * 100 / VRAM_TOTAL))
    echo "  GPU VRAM: ${VRAM_USED}/${VRAM_TOTAL} MiB (${VRAM_PCT}%)"
    if [[ $VRAM_PCT -gt 95 ]]; then
      echo "  WARNING: VRAM usage above 95% — risk of OOM"
    fi
  fi
fi

# Disk space — the two mounts that fill up in practice: the weights dir
# (model downloads) and the repo dir (logs, eval results, WebUI volume on
# the docker root). Warn below 10 GB free — one more quant download or a
# long sweep can eat that.
_weights_dir=$( (source "$SCRIPT_DIR/lib/weights.sh" >/dev/null 2>&1 && echo "$WEIGHTS_DIR") || echo "$REPO_DIR/weights" )
for _mount_label in "weights:$_weights_dir" "repo:$REPO_DIR"; do
  _label="${_mount_label%%:*}"; _dir="${_mount_label#*:}"
  [[ -d "$_dir" ]] || continue
  _free_gb=$(df -BG --output=avail "$_dir" 2>/dev/null | tail -1 | tr -dc '0-9' || echo "")
  if [[ -n "$_free_gb" && "$_free_gb" -lt 10 ]]; then
    echo "  WARNING: ${_free_gb}G free on the $_label mount ($_dir) — downloads/sweeps may fail"
  fi
done

# Slot utilization
SLOTS_JSON=$(curl -s --max-time 3 "$LLAMA_URL/slots" 2>/dev/null || echo "[]")
ACTIVE_SLOTS=$(echo "$SLOTS_JSON" | python3 -c "
import sys, json
try:
    slots = json.load(sys.stdin)
    active = sum(1 for s in slots if s.get('state', 0) != 0)
    print(f'{active}/{len(slots)}')
except: print('?/?')
" 2>/dev/null)
echo "  Slots: $ACTIVE_SLOTS active"

# Summary
echo ""
TOTAL=$((HEALTHY + UNHEALTHY))
if [[ $UNHEALTHY -eq 0 ]]; then
  echo "All $TOTAL services healthy."
else
  echo "$UNHEALTHY of $TOTAL services unhealthy."
fi

[[ $UNHEALTHY -eq 0 ]]
