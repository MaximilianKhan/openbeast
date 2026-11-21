#!/bin/bash
# weights-download: fetch a Hugging Face model's weights from a URL into one
# known folder, every file verified against the Hub's own hash.
#
#   scripts/weights-download.sh https://huggingface.co/ORG/REPO            # -> ~/Documents/weights/ORG/REPO
#   scripts/weights-download.sh https://huggingface.co/ORG/REPO ~/models/x # -> exactly ~/models/x
#   scripts/weights-download.sh URL --dry-run                              # what, how big, where; writes nothing
#   scripts/weights-download.sh --verify ~/Documents/weights/ORG/REPO      # re-hash, offline
#   scripts/weights-download.sh --help                                     # every URL form and option
#
# The one entry point for "download this model" on any OpenBeast machine (the
# Mac, the rig, either Spark). Re-running the same command resumes a broken
# download, or re-verifies a finished one. docs/WEIGHTS_DOWNLOAD.md
#
# Settings: WEIGHTS_DOWNLOAD_ROOT (default ~/Documents/weights), HF_TOKEN or
# HF_TOKEN_FILE for gated repos (else ~/.cache/huggingface/token if present),
# HF_ENDPOINT, OFFLINE. On a Spark, spark.env supplies them too.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backends/lib.sh
source "$HERE/backends/lib.sh"
sp_backends_hub_env "$HERE/backends"
if [[ -z "${HF_TOKEN:-}" && -z "${HF_TOKEN_FILE:-}" && -f "$HOME/.cache/huggingface/token" ]]; then
  export HF_TOKEN_FILE="$HOME/.cache/huggingface/token"
fi
PY="${OPENBEAST_PY:-python3}"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
  { echo "Error: $PY is missing or older than 3.9 (set OPENBEAST_PY)" >&2; exit 1; }
exec "$PY" "$HERE/backends/pylib/weights_download.py" "$@"
