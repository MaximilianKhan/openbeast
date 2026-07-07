#!/bin/bash
# Stop the full OpenBeast stack.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" down

echo "Stopping MCPO proxy..."
pkill -f "mcpo" 2>/dev/null && echo "MCPO proxy stopped." || echo "MCPO proxy was not running."

echo "Stopping llama.cpp server..."
pkill -f "llama-server" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."
