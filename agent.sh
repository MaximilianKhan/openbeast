#!/bin/bash
# Run a local AI agent against a task.
#
# Usage:
#   ./agent.sh "add error handling to the API routes"
#   ./agent.sh -w ~/projects/myapp "write unit tests for auth.py"
#   ./agent.sh -f tasks/refactor-logging.md
#   ./agent.sh --max-iter 50 "fix the failing CI tests"
#
# The llama.cpp server must be running (./start.sh or any serve-*.sh script).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install deps if needed
if ! python3 -c "import openai" 2>/dev/null; then
  echo "Installing agent dependencies..."
  pip install --user --break-system-packages -q -r "$SCRIPT_DIR/agents/requirements.txt"
fi

exec python3 "$SCRIPT_DIR/agents/runner.py" "$@"
