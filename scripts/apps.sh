#!/usr/bin/env bash
# OpenBeast — beast-apps: let the model drive external apps (Xcode, Gradle/adb,
# Blender, Figma, 3D printers, ROS) through MCP servers. docs/BEAST_APPS.md.
#
#   ./scripts/apps.sh list [--json]                capabilities + status HERE
#   ./scripts/apps.sh find <words...> [--json]     which capability fits a request
#   ./scripts/apps.sh check <capability> [--json]  READY / MISSING + exact fix
#   ./scripts/apps.sh enable <capability> [--server S] [--key K] [--dry-run] [--accept-risk]
#   ./scripts/apps.sh disable <capability|server|key> [--dry-run]
#   ./scripts/apps.sh refresh [server...] [--json] [--no-introspect]
#   ./scripts/apps.sh upgrade <server> [--to VERSION]
#   ./scripts/apps.sh tools <server> [--json]      the recorded tool manifest
#   ./scripts/apps.sh validate                     check the catalog
#
# Tools execute where the PROCESS runs (docs/BEAST_SLOT.md): on a client-mode
# Mac this wires servers into THAT Mac's opencode, next to openbeast-tools; the
# model on the rig only ever sees the tool calls.
#
# Exit codes: 0 ok/READY · 1 error · 2 usage/unknown · 3 MISSING prerequisite
#             4 unsupported platform · 5 offline · 6 needs --accept-risk
#             7 the opencode key is yours (not written by beast-apps)
#
# Environment: OPENBEAST_OPENCODE_CONFIG (config to edit), OPENBEAST_APPS_STATE
# (default ~/.openbeast-client/apps), OPENBEAST_APPS_CATALOG_D (overlay dir),
# OPENBEAST_OPENCODE_SCHEMA=v1|v2 (skip detection), OPENBEAST_APPS_OFFLINE=1.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Python 3.11+ (tomllib). Never bare python3 first: on a Mac that is often the
# Xcode shim (3.9), which would die on the import with a useless traceback.
_pick_python() {
  local c
  for c in "${OPENBEAST_PYTHON:-}" "$HOME/.openbeast-client/venv/bin/python3" \
           python3.14 python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

PY="$(_pick_python)" || {
  echo "beast-apps needs Python 3.11+ (for tomllib); none found." >&2
  echo "  macOS: brew install python@3.13   ·   or set OPENBEAST_PYTHON=/path/to/python3.11+" >&2
  exit 1
}
exec "$PY" "$REPO_DIR/agents/apps/cli.py" "$@"
