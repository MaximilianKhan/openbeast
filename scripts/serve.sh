#!/bin/bash
# Generic OpenAI-compatible API server for llama.cpp models
# Usage: ./serve.sh -m <model_path> [-c context] [-np parallel] [-ctk quant] [-p port] [extra args...]
#
# Model-specific scripts (e.g. serve-qwen-27b.sh) call this with preset defaults.
# Endpoint: http://localhost:<port>/v1/chat/completions

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LLAMA_SERVER="$REPO_DIR/llama.cpp/build/bin/llama-server"

if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "Error: llama-server not found at $LLAMA_SERVER" >&2
  echo "Build llama.cpp first — see docs/REFERENCE.md" >&2
  exit 1
fi

# The binary's baked-in RUNPATH points at wherever llama.cpp was built, which
# breaks if the repo moves. Resolve its shared libs relative to the binary.
export LD_LIBRARY_PATH="$(dirname "$LLAMA_SERVER")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# BIND_HOST / LLAMA_API_KEY from openbeast.conf (remote access goes through
# Tailscale Serve — see scripts/setup-tailscale.sh).
source "$SCRIPT_DIR/lib/conf.sh"

# Defaults (overridable by model scripts or CLI flags)
MODEL=""
ALIAS=""
CONTEXT=65536
KV_QUANT="q4_0"
GPU_LAYERS=99
PARALLEL=6
HOST="$BIND_HOST"
PORT=8080

# Parse known flags; collect the rest for passthrough
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m)          MODEL="$2";    shift 2 ;;
    -a|--alias)  ALIAS="$2";    shift 2 ;;
    -c)          CONTEXT="$2";  shift 2 ;;
    -np|--parallel) PARALLEL="$2"; shift 2 ;;
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

ALIAS_ARGS=()
if [[ -n "$ALIAS" ]]; then
  ALIAS_ARGS=(-a "$ALIAS")
fi

API_KEY_ARGS=()
if [[ -n "$LLAMA_API_KEY" ]]; then
  API_KEY_ARGS=(--api-key "$LLAMA_API_KEY")
fi

echo "Parallel slots: $PARALLEL (unified KV cache, continuous batching)"

exec "$LLAMA_SERVER" \
  -m "$MODEL" \
  "${ALIAS_ARGS[@]}" \
  "${API_KEY_ARGS[@]}" \
  -ngl "$GPU_LAYERS" \
  -c "$CONTEXT" \
  -np "$PARALLEL" \
  --kv-unified \
  -ctk "$KV_QUANT" \
  -ctv "$KV_QUANT" \
  --host "$HOST" \
  --port "$PORT" \
  "${EXTRA_ARGS[@]}"
