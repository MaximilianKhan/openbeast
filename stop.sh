#!/bin/bash
# Stop the full local AI stack.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" down

echo "Stopping MCP tool server..."
pkill -f "mcp_server.py" 2>/dev/null && echo "MCP tool server stopped." || echo "MCP tool server was not running."

echo "Stopping llama.cpp server..."
pkill -f "llama-server" 2>/dev/null && echo "llama.cpp server stopped." || echo "llama.cpp server was not running."
