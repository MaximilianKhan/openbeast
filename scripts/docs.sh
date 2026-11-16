#!/bin/bash
# beast-docs — current platform developer docs, mirrored for offline use.
#
#   ./scripts/docs.sh sources                          what can be mirrored
#   ./scripts/docs.sh sync <source> [--max-pages N] [--root URL] [--accept-terms]
#   ./scripts/docs.sh status [--json]                  pages, age, STALE flags
#   ./scripts/docs.sh search <query> [--source S]      ranked hits + doc ids
#   ./scripts/docs.sh read <doc-id|url>                one page as markdown
#   ./scripts/docs.sh live <url|Symbol> [--source S]   the official page NOW
#                                                      (offline: the mirror copy)
#   ./scripts/docs.sh pack <out.tar.gz> [--source S]   carry a mirror to an
#   ./scripts/docs.sh unpack <tarball> [--sha256 H]    offline box, verified
#   ./scripts/docs.sh reindex
#
# Design: docs/BEAST_DOCS.md. The store is $OPENBEAST_DOCS_DIR (default
# ~/.openbeast/docs) and lives OUTSIDE git — the pages belong to their
# publishers; we ship the acquirer, as beast-lang does.
#
# Runs on a rig and in client mode (the slim checkout ships agents/ and
# scripts/). Stdlib python only; any python >= 3.9 works, and a newer one is
# preferred because tomllib is then used for the sources file.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pick_python() {
  local c
  for c in "${OPENBEAST_PYTHON:-}" "$HOME/.openbeast-client/venv/bin/python3" \
           python3.13 python3.12 python3.11 python3.10 python3; do
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
exec "$PY" -I "$SCRIPT_DIR/agents/docs/cli.py" "$@"
