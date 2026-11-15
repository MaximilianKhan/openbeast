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
  echo "Build llama.cpp first — see docs/REFERENCE.md" >&2
  exit 1
fi

# Same as serve.sh: the binary's baked-in RUNPATH points at wherever
# llama.cpp was built, which breaks if the repo moves.
export LD_LIBRARY_PATH="$(dirname "$LLAMA_CLI")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

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

# WEIGHT_ENFORCE applies here too (lib/weightcheck.sh, the check serve.sh
# runs): strict mode is a supply-chain control, and an interactive run loads
# the same weights. The mode comes from openbeast.conf via conf.sh (read-only:
# a chat launcher mints no secrets). Context scaling stays serve.sh's — the
# run-*.sh presets are fixed RTX 5090 settings (override with -c).
OB_CONF_READONLY=1 source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/weightcheck.sh"
WEIGHT_FILES=()
while IFS= read -r _wf; do WEIGHT_FILES+=("$_wf"); done \
  < <(ob_weight_files "$MODEL" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"})
_wr_rc=0
ob_weight_enforce "$SCRIPT_DIR/weights.registry" "${WEIGHT_ENFORCE:-warn}" "${WEIGHT_FILES[@]}" || _wr_rc=$?
[[ $_wr_rc -eq 0 ]] || exit "$_wr_rc"

exec "$LLAMA_CLI" \
  -m "$MODEL" \
  -ngl "$GPU_LAYERS" \
  -c "$CONTEXT" \
  -ctk "$KV_QUANT" \
  -ctv "$KV_QUANT" \
  --conversation \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
