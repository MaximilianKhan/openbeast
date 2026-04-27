#!/bin/bash
# Start the full local AI stack:
#   1. llama.cpp server (Qwen3.6-27B Q5_K_XL by default)
#   2. Open WebUI (http://localhost:3000)
#
# Usage:
#   ./start.sh                          # default: 27B Q5
#   ./start.sh serve-qwen-27b-q4.sh     # specify a different serve script
#
# OpenCode connects independently — just run `opencode` in any project directory
# while the server is running.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SERVE_SCRIPT="${1:-serve-qwen-27b-uncensored-q5.sh}"

if [[ ! -x "$SCRIPT_DIR/$SERVE_SCRIPT" ]]; then
  echo "Error: $SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

echo "Starting llama.cpp server ($SERVE_SCRIPT)..."
"$SCRIPT_DIR/$SERVE_SCRIPT" &
LLAMA_PID=$!

echo "Waiting for llama.cpp server to be ready..."
until curl -s http://localhost:8080/health > /dev/null 2>&1; do
  sleep 1
done
echo "llama.cpp server ready on http://localhost:8080"

echo "Starting Open WebUI..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

echo ""
echo "Stack is running:"
echo "  Model server:  http://localhost:8080"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
echo ""
echo "Press Ctrl+C to stop the llama.cpp server."
echo "Run './stop.sh' to stop everything."

wait $LLAMA_PID
