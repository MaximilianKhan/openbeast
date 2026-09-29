#!/bin/bash
# Download a profile's pinned checkpoint into MODELS_DIR/<profile>, verify
# every file against the Hub's own hashes for that commit, and lock it
# (models/<profile>.lock). A re-run verifies instead of downloading.
#
#   scripts/backends/model-fetch.sh --profile NAME            # fetch, or verify if present
#   scripts/backends/model-fetch.sh --profile NAME --verify   # re-hash, download nothing
#
# Runs on each Spark (TP 2 needs the files on both) or anywhere with room.
# MODELS_DIR, HF_ENDPOINT, HF_TOKEN_FILE and OFFLINE come from the
# environment or spark.env (--env FILE); the token is never on argv.
# Full help: --help.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
sp_backends_hub_env "$HERE" "$@"
exec python3 "$HERE/pylib/model_fetch.py" "$@"
