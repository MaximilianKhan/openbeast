#!/bin/bash
# beast-recipe: OpenBeast's own serving recipes for DGX Sparks (one rank per Spark, 1 to 8).
# docs/BEAST_RECIPE.md
#
#   beast-recipe init <hf-url|org/repo[@sha]|local-dir> [--sparks N] [--name NAME] [--engine ID]
#   beast-recipe plan <recipe> [--sparks 1,2,3,4] [--write]     fit, engine, verdict, gaps, the commands
#   beast-recipe engine|stage|up|down|check|adopt <recipe> [--run]   print what would run; --run acts
#   beast-recipe rollback [--run] | list | show <recipe> | show --keys
#   every command: --help, --json
#
# A recipe is recipes/<name>/recipe.env (data, parsed, never sourced) + SHA256SUMS.
# The engine and family matrix is scripts/backends/data/recipe-engines.json.
# Offline: plan, show, list and every --dry-run read only local files; init
# reads a local directory, or the Hub when online (HF_ENDPOINT, HF_TOKEN_FILE,
# OFFLINE honoured as for model-inspect.sh). Nothing here prints a key.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
sp_backends_hub_env "$HERE"
PY="${OPENBEAST_PY:-python3}"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
  { echo "Error: $PY is missing or older than 3.9 (set OPENBEAST_PY)" >&2; exit 1; }
exec "$PY" "$HERE/pylib/recipe.py" "$@"
