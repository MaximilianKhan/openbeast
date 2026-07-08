#!/bin/bash
# Start the full OpenBeast stack:
#   1. llama.cpp server (Qwen3.6-27B Uncensored Q5_K_P by default — uncensored fine-tune, 96.16%)
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
REPO_DIR="$SCRIPT_DIR"

# BIND_HOST (default 127.0.0.1 — loopback-only; remote devices come in via
# Tailscale Serve, see scripts/setup-tailscale.sh). docker-compose reads it
# through the OPENBEAST_BIND env var exported by lib/conf.sh sourcing below.
source "$SCRIPT_DIR/scripts/lib/conf.sh"
export OPENBEAST_BIND="$BIND_HOST"
# Only propagate a real key. Exporting the WebUI's "not-needed" placeholder
# would leak into serve.sh's conf resolution (OPENBEAST_API_KEY has top
# priority there) and silently key-protect the llama API; docker-compose
# already has its own not-needed default for the WebUI side.
if [[ -n "$LLAMA_API_KEY" ]]; then
  export OPENBEAST_API_KEY="$LLAMA_API_KEY"
fi

SERVE_SCRIPT="${1:-serve-qwen-27b-uncensored-q5.sh}"

if [[ ! -x "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" ]]; then
  echo "Error: scripts/$SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

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
    exit 1
  fi
done

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
mcpo --port 3001 --host "$BIND_HOST" -- python3 "$SCRIPT_DIR/agents/mcp_server.py" &
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
