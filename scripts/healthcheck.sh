#!/bin/bash
# Health monitor for the local AI stack.
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

LLAMA_URL="${LLAMA_URL:-http://localhost:8080}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"
WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"

RESTART=false
[[ "${1:-}" == "--restart" ]] && RESTART=true

HEALTHY=0
UNHEALTHY=0

check() {
  local name="$1" url="$2" match="$3"
  if curl -s --max-time 5 "$url" 2>/dev/null | grep -qi "$match"; then
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
    echo "       → restarting llama.cpp..."
    # Find the most recent serve script used (from process list)
    SERVE_PID=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [[ -n "$SERVE_PID" ]]; then
      kill "$SERVE_PID" 2>/dev/null
      sleep 2
    fi
    "$SCRIPT_DIR/serve-qwen-35b-a3b-uncensored-q4.sh" &
    echo "       → started (waiting for health...)"
    for i in $(seq 1 30); do
      if curl -s --max-time 2 "$LLAMA_URL/health" | grep -q "ok"; then
        echo "       → healthy after ${i}s"
        break
      fi
      sleep 1
    done
  fi
fi

# MCPO proxy
if ! check "MCPO proxy" "$MCPO_URL/openapi.json" "openapi"; then
  if $RESTART; then
    echo "       → restarting MCPO..."
    pkill -f "mcpo" 2>/dev/null || true
    sleep 1
    mcpo --port 3001 --host 0.0.0.0 -- python3 "$REPO_DIR/agents/mcp_server.py" &
    sleep 3
    echo "       → restarted"
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

# GPU VRAM usage
echo ""
if command -v nvidia-smi &>/dev/null; then
  VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
  VRAM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
  VRAM_PCT=$((VRAM_USED * 100 / VRAM_TOTAL))
  echo "  GPU VRAM: ${VRAM_USED}/${VRAM_TOTAL} MiB (${VRAM_PCT}%)"
  if [[ $VRAM_PCT -gt 95 ]]; then
    echo "  WARNING: VRAM usage above 95% — risk of OOM"
  fi
fi

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
