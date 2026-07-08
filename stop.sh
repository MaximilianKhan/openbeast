#!/bin/bash
# Stop the full OpenBeast stack.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Keep going even if docker is stopped/absent (--minimal installs) — the
# whole point of stop.sh is to reach the pkill lines below.
echo "Stopping Open WebUI..."
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$SCRIPT_DIR/docker-compose.yml" down \
    || echo "Warning: docker compose down failed (daemon not running?)"
else
  echo "Docker not installed — skipping containers."
fi

echo "Stopping MCPO proxy..."
pkill -f "mcpo" 2>/dev/null && echo "MCPO proxy stopped." || echo "MCPO proxy was not running."

echo "Stopping llama.cpp server..."
pkill -f "llama-server" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."
