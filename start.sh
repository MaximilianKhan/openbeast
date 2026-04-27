#!/bin/bash
# Start the full local AI stack:
#   1. llama.cpp server (Qwen3.6-27B Uncensored Q5 by default)
#   2. MCP tool server (http://localhost:3001 for Open WebUI)
#   3. Open WebUI (http://localhost:3000)
#
# Usage:
#   ./start.sh                          # default model
#   ./start.sh serve-qwen-27b-q4.sh     # specify a different serve script
#
# OpenCode connects to the MCP server via stdio (configured in opencode.json),
# so it doesn't need the HTTP MCP server — just run `opencode` in any project.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SERVE_SCRIPT="${1:-serve-qwen-27b-uncensored-q5.sh}"

if [[ ! -x "$SCRIPT_DIR/$SERVE_SCRIPT" ]]; then
  echo "Error: $SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

# Cleanup on exit: stop MCP server and llama.cpp
cleanup() {
  echo ""
  echo "Shutting down..."
  if [[ -n "${MCP_PID:-}" ]]; then
    kill "$MCP_PID" 2>/dev/null && echo "MCP server stopped."
  fi
  if [[ -n "${LLAMA_PID:-}" ]]; then
    kill "$LLAMA_PID" 2>/dev/null && echo "llama.cpp server stopped."
  fi
}
trap cleanup EXIT INT TERM

echo "Starting llama.cpp server ($SERVE_SCRIPT)..."
"$SCRIPT_DIR/$SERVE_SCRIPT" &
LLAMA_PID=$!

echo "Waiting for llama.cpp server to be ready..."
until curl -s http://localhost:8080/health > /dev/null 2>&1; do
  sleep 1
done
echo "llama.cpp server ready on http://localhost:8080"

echo "Starting MCP tool server on http://localhost:3001..."
python3 "$SCRIPT_DIR/agents/mcp_server.py" --transport http --port 3001 &
MCP_PID=$!
sleep 1
echo "MCP tool server ready on http://localhost:3001"

echo "Starting Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

echo ""
echo "Stack is running:"
echo "  Model server:  http://localhost:8080"
echo "  MCP tools:     http://localhost:3001/mcp"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
echo ""
echo "Press Ctrl+C to stop. Or run './stop.sh' from another terminal."

wait $LLAMA_PID
