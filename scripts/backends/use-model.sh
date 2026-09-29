#!/bin/bash
# Point the rig's consumers at the model the Sparks serve (run ON THE RIG).
#
#   scripts/backends/use-model.sh --profile NAME     # the profile's SERVED_MODEL_NAME
#   scripts/backends/use-model.sh --model ID
#   … [--report .run/conformance/latest.json] [--opencode-out FILE] [--force] [--dry-run]
#
# Needs a PASSING conformance.sh report for that id at this rig's
# INFERENCE_URL. Then records INFERENCE_MODEL in openbeast.conf (the agent
# runner sends it for vllm/tensorfold), and prints the opencode provider
# entry — the tracked opencode.json is never edited. Open WebUI needs
# nothing: it lists /v1/models. Full help: --help.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
if [[ -f "$REPO_DIR/openbeast.conf" && -f "$REPO_DIR/scripts/lib/conf.sh" ]]; then
  # shellcheck source=../lib/conf.sh
  source "$REPO_DIR/scripts/lib/conf.sh"
fi
exec python3 "$HERE/pylib/use_model.py" "$@"
