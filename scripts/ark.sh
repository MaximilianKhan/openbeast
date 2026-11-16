#!/bin/bash
# beast-ark — open-source software, pinned and verifiable, in cold storage.
#
#   ./scripts/ark.sh list [--category C] [--profile core]     the catalog
#   ./scripts/ark.sh plan [--platform P] [--profile both]     sizes per platform, full vs core
#   ./scripts/ark.sh lint                                     the catalog schema check
#   ./scripts/ark.sh lock [--only a,b] [--refresh]            fill pins from upstream (online)
#   ./scripts/ark.sh update [--only a,b] [--write]            bump pins to new releases; prints the diff
#   ./scripts/ark.sh fetch --to /Volumes/ARK [--profile core] [--platform linux-aarch64]
#   ./scripts/ark.sh verify /Volumes/ARK [--deep]             re-hash everything (offline)
#   ./scripts/ark.sh restore go --from /Volumes/ARK           unpack into ~/.local/opt (offline)
#   ./scripts/ark.sh serve /Volumes/ARK --tailnet             registry + files + pip/npm/go mirrors
#
# Design and runbook: docs/BEAST_ARK.md. The catalog is agents/ark/catalog.toml;
# the software itself lives on the drive, never in git.
#
# Stdlib python only; any python >= 3.9 (a Mac's /usr/bin/python3 works). The
# drive carries its own copy of the tool: DRIVE/ark runs it with no checkout.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pick_python() {
  local c
  for c in "${OPENBEAST_PYTHON:-}" "$HOME/.openbeast-client/venv/bin/python3" \
           python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    [[ -n "$c" ]] || continue
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || { echo "ERROR: no python >= 3.9 found (set OPENBEAST_PYTHON)" >&2; exit 2; }
exec "$PY" -I "$SCRIPT_DIR/agents/ark/cli.py" "$@"
