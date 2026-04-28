#!/bin/bash
# Generic interactive chat launcher for llama.cpp models
# Usage: ./run.sh -m <model_path> [-c context] [-ctk quant] [-ctv quant] [extra args...]
#
# Model-specific scripts (e.g. run-qwen-27b.sh) call this with preset defaults.
# Any extra args are forwarded directly to llama-cli.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LLAMA_CLI="$REPO_DIR/llama.cpp/build/bin/llama-cli"

if [[ ! -x "$LLAMA_CLI" ]]; then
  echo "Error: llama-cli not found at $LLAMA_CLI" >&2
  echo "Build llama.cpp first — see SETUP.md" >&2
  exit 1
fi

# Defaults (overridable by model scripts or CLI flags)
MODEL=""
CONTEXT=65536
KV_QUANT="q4_0"
GPU_LAYERS=99

# Parse known flags; collect the rest for passthrough
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m)          MODEL="$2";    shift 2 ;;
    -c)          CONTEXT="$2";  shift 2 ;;
    -ctk|-ctv)   KV_QUANT="$2"; shift 2 ;;
    -ngl)        GPU_LAYERS="$2"; shift 2 ;;
    *)           EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "Error: no model specified. Use -m <path> or a model-specific script." >&2
  exit 1
fi

exec "$LLAMA_CLI" \
  -m "$MODEL" \
  -ngl "$GPU_LAYERS" \
  -c "$CONTEXT" \
  -ctk "$KV_QUANT" \
  -ctv "$KV_QUANT" \
  --conversation \
  "${EXTRA_ARGS[@]}"
