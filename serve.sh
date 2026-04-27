#!/bin/bash
# Generic OpenAI-compatible API server for llama.cpp models
# Usage: ./serve.sh -m <model_path> [-c context] [-ctk quant] [-ctv quant] [-p port] [extra args...]
#
# Model-specific scripts (e.g. serve-qwen-27b.sh) call this with preset defaults.
# Endpoint: http://localhost:<port>/v1/chat/completions

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER="$SCRIPT_DIR/llama.cpp/build/bin/llama-server"

if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "Error: llama-server not found at $LLAMA_SERVER" >&2
  echo "Build llama.cpp first — see SETUP.md" >&2
  exit 1
fi

# Defaults (overridable by model scripts or CLI flags)
MODEL=""
CONTEXT=65536
KV_QUANT="q4_0"
GPU_LAYERS=99
HOST="0.0.0.0"
PORT=8080

# Parse known flags; collect the rest for passthrough
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m)          MODEL="$2";    shift 2 ;;
    -c)          CONTEXT="$2";  shift 2 ;;
    -ctk|-ctv)   KV_QUANT="$2"; shift 2 ;;
    -ngl)        GPU_LAYERS="$2"; shift 2 ;;
    --host)      HOST="$2";     shift 2 ;;
    -p|--port)   PORT="$2";     shift 2 ;;
    *)           EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "Error: no model specified. Use -m <path> or a model-specific script." >&2
  exit 1
fi

exec "$LLAMA_SERVER" \
  -m "$MODEL" \
  -ngl "$GPU_LAYERS" \
  -c "$CONTEXT" \
  -ctk "$KV_QUANT" \
  -ctv "$KV_QUANT" \
  --host "$HOST" \
  --port "$PORT" \
  "${EXTRA_ARGS[@]}"
