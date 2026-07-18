#!/bin/bash
# Launch the OpenBeast status dashboard (process-kind extension). Binds the
# stack-wide address (loopback by default; remote goes through Tailscale like
# every other service). Port via DASHBOARD_PORT (default 3002).
set -euo pipefail
EXT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$EXT_DIR/../.." && pwd)"
export OPENBEAST_REPO_DIR="$REPO_DIR"
export OPENBEAST_BIND="${OPENBEAST_BIND:-127.0.0.1}"
export DASHBOARD_PORT="${DASHBOARD_PORT:-3002}"
exec python3 "$EXT_DIR/dashboard.py"
