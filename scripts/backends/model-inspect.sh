#!/bin/bash
# Inspect a checkpoint you have never seen: architecture, quantization,
# context, experts, memory fit on 1 vs 2 Sparks, chat-template markers →
# suggested vLLM parsers, and whether vLLM / TensorFold (at the vendored
# commits in data/) can serve it. Reads metadata only, never weights.
#
#   scripts/backends/model-inspect.sh owner/name@<40-hex sha>   # the Hub, read-only
#   scripts/backends/model-inspect.sh /path/to/checkpoint       # offline
#   … [--json] [--write-profile NAME [--backend vllm|tensorfold] [--force]] [--gpu-mem-util 0.8]
#
# Hub settings come from the environment or spark.env (HF_ENDPOINT,
# HF_TOKEN_FILE — never argv); OFFLINE=true (env, spark.env or the rig's
# openbeast.conf) refuses the network. Full help: --help.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
sp_backends_hub_env "$HERE"
exec python3 "$HERE/pylib/model_inspect.py" "$@"
