#!/bin/bash
# Start the full local AI stack:
#   1. llama.cpp server (Qwen3.6-35B-A3B Uncensored Q4_K_M by default — top of the leaderboard)
#   2. MCPO proxy (wraps MCP tools as OpenAPI on http://localhost:3001)
#   3. Open WebUI (http://localhost:3000)
#
# Usage:
#   ./start.sh                                # default model
#   ./start.sh serve-qwen-27b-uncensored-q5.sh   # specify a different serve script
#
# OpenCode connects to the MCP server via stdio (configured in opencode.json),
# so it doesn't need MCPO — just run `opencode` in any project.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SERVE_SCRIPT="${1:-serve-qwen-35b-a3b-uncensored-q4.sh}"

if [[ ! -x "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" ]]; then
  echo "Error: scripts/$SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

# Cleanup on exit: stop MCPO and llama.cpp
cleanup() {
  echo ""
  echo "Shutting down..."
  if [[ -n "${MCPO_PID:-}" ]]; then
    kill "$MCPO_PID" 2>/dev/null && echo "MCPO proxy stopped."
  fi
  if [[ -n "${LLAMA_PID:-}" ]]; then
    kill "$LLAMA_PID" 2>/dev/null && echo "llama.cpp server stopped."
  fi
}
trap cleanup EXIT INT TERM

echo "Starting llama.cpp server ($SERVE_SCRIPT)..."
"$SCRIPT_DIR/scripts/$SERVE_SCRIPT" &
LLAMA_PID=$!

echo "Waiting for llama.cpp server to be ready..."
until curl -s http://localhost:8080/health > /dev/null 2>&1; do
  sleep 1
done
echo "llama.cpp server ready on http://localhost:8080"

echo "Starting MCPO proxy (MCP tools → OpenAPI) on http://localhost:3001..."
mcpo --port 3001 --host 0.0.0.0 -- python3 "$SCRIPT_DIR/agents/mcp_server.py" &
MCPO_PID=$!
sleep 2
echo "MCPO proxy ready on http://localhost:3001"

echo "Starting Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

# Configure Open WebUI (tool server + native function calling) in background
"$SCRIPT_DIR/scripts/configure-webui.sh" &

echo ""
echo "Stack is running:"
echo "  Model server:  http://localhost:8080"
echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
echo ""
echo "Press Ctrl+C to stop. Or run './stop.sh' from another terminal."

wait $LLAMA_PID
