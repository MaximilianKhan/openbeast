#!/bin/bash
# Black-box conformance probe of a served model through the OpenAI API:
# model listing, strict model ids, chat, streaming, where reasoning arrives,
# OpenBeast's real tool schemas round-tripping, parallel calls, max_tokens,
# (--heavy) the context-overflow error text, (--concurrency N) N at once.
#
#   scripts/backends/conformance.sh                              # the rig's INFERENCE_URL
#   scripts/backends/conformance.sh --url http://10.0.0.5:8000 --model my-model
#   … [--backend vllm|tensorfold] [--key-file F] [--heavy] [--concurrency 4] [--json] [--out DIR]
#
# On an installed rig (openbeast.conf present) it reads INFERENCE_URL,
# INFERENCE_BACKEND and LLAMA_API_KEY from lib/conf.sh; flags override.
# The key goes by environment or --key-file, never argv, and never to
# TensorFold. Writes JSON + text reports to .run/conformance/ (latest.json
# is what use-model.sh reads). Exit 0 only when chat, stream and tools pass.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
if [[ -f "$REPO_DIR/openbeast.conf" && -f "$REPO_DIR/scripts/lib/conf.sh" ]]; then
  # shellcheck source=../lib/conf.sh
  source "$REPO_DIR/scripts/lib/conf.sh"
fi
exec python3 "$HERE/pylib/conformance.py" "$@"
