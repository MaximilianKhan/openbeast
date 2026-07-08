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
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install deps if needed. --break-system-packages only where PEP-668
# requires it (Arch, newer Debian) — older pip errors on the unknown flag.
if ! python3 -c "import openai" 2>/dev/null; then
  echo "Installing agent dependencies..."
  PIP_FLAGS=""
  if python3 -c 'import sysconfig,os;p=sysconfig.get_path("stdlib");exit(0 if os.path.exists(os.path.join(p,"EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
    PIP_FLAGS="--break-system-packages"
  fi
  pip install --user $PIP_FLAGS -q -r "$REPO_DIR/agents/requirements.txt"
fi

exec python3 "$REPO_DIR/agents/runner.py" "$@"
