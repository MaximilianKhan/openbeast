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

# --- Adaptive context (Hardware Profiles Phase 2) --------------------------
# The shipped -c values are MEASURED on the 32 GB reference card (RTX 5090).
# On a smaller card that context would OOM, so scale it to the card's KV
# budget: weights are a fixed cost (≈ the GGUF file size), and with
# --kv-unified the KV cache scales with context and is shared across slots.
# We only ever scale DOWN — the measured value stands on reference-class
# cards, so behavior is byte-identical there. Overrides:
#   OPENBEAST_CONTEXT=<n>     force an exact context (skip scaling)
#   OPENBEAST_VRAM_MIB=<n>    tell us the card's VRAM (when detection is wrong,
#                             e.g. Intel Arc / headless AMD)
#   OPENBEAST_AUTO_CONTEXT=0  disable scaling entirely
if [[ -n "${OPENBEAST_CONTEXT:-}" ]]; then
  CONTEXT="$OPENBEAST_CONTEXT"
  echo "Context: $CONTEXT (forced via OPENBEAST_CONTEXT)"
elif [[ "${OPENBEAST_AUTO_CONTEXT:-1}" == "1" ]]; then
  source "$SCRIPT_DIR/lib/hardware.sh" 2>/dev/null || true
  command -v ob_detect_gpu >/dev/null 2>&1 && ob_detect_gpu 2>/dev/null || true
  vram="${OPENBEAST_VRAM_MIB:-${OB_VRAM_MB:-0}}"
  weights_mib=0
  [[ -f "$MODEL" ]] && weights_mib=$(( ($(stat -c '%s' "$MODEL") + 1048575) / 1048576 ))
  # rc=0 capture-then-|| : under `set -e`, a plain `scaled=$(...)` with a
  # nonzero exit would kill the script HERE — the rc=2 "weights don't fit"
  # branch below was unreachable and small-card users got a silent exit.
  rc=0
  scaled=$(ob_scale_context "$CONTEXT" "$vram" "$weights_mib") || rc=$?
  if [[ $rc -eq 2 ]]; then
    echo "Warning: a ${vram} MiB card can't hold this model's weights (~${weights_mib} MiB) + 2 GB headroom — try a smaller quant. Forcing -c ${scaled}." >&2
    CONTEXT="$scaled"
  elif [[ "$scaled" -lt "$CONTEXT" ]]; then
    echo "Context: $CONTEXT -> $scaled (auto-scaled for ${vram} MiB card; weights ~${weights_mib} MiB, 2 GB headroom). Override: OPENBEAST_CONTEXT=<n>."
    CONTEXT="$scaled"
  fi
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
