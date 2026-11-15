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

# The weight has to BE there. Nothing below checked: the context scaler
# computed "weights ~0 MiB", llama-server was launched with a path that does
# not exist, and start.sh was left to guess ("missing weight file or VRAM
# OOM") or to roll back to another model without a word about why.
if [[ ! -f "$MODEL" ]]; then
  echo "Error: weight not downloaded: $MODEL" >&2
  if [[ -f "$SCRIPT_DIR/weights.registry" ]] \
     && awk -F'\t' -v n="$(basename "$MODEL")" '$0 !~ /^#/ && $3 == n {f = 1} END {exit !f}' "$SCRIPT_DIR/weights.registry"; then
    echo "  Get it:  ./scripts/fetch-weight.sh $(basename "$MODEL")" >&2
  else
    echo "  It is not in scripts/weights.registry, so fetch-weight.sh cannot download it:" >&2
    echo "  put the file at that path yourself." >&2
  fi
  echo "  Already have it somewhere else? Set WEIGHTS_DIR in openbeast.conf to that directory." >&2
  # Exit 4, not 1: start.sh shows this instead of guessing at the cause, and
  # does not answer a request for one model by loading another.
  echo "  (exit 4 = weight file missing)" >&2
  exit 4
fi

# --- No keyless inference on a network bind --------------------------------
# A non-loopback HOST (BIND_HOST, or --host) with no LLAMA_API_KEY put a
# keyless llama-server on that network: every route (/v1/*, /slots, /props,
# /metrics) to anyone who can reach it, past beast-gate's per-device keys,
# caps and audit even with EDGE_GATE=true, and past the acknowledgement
# setup-tailscale.sh demands before it publishes the same thing on :8443.
# The keyless tool server is refused in this state (ALLOW_OPEN_TOOLS); so is
# this, with the same kind of explicit "yes": ALLOW_OPEN_INFERENCE=true.
# Exit 3, like a registry refusal: start.sh must not "recover" by launching
# another model on the same bind.
if ! ob_bind_is_loopback "$HOST" && [[ -z "$LLAMA_API_KEY" && "${ALLOW_OPEN_INFERENCE:-false}" != "true" ]]; then
  echo "Error: refusing to serve keyless inference on $HOST:$PORT — anyone on that network" >&2
  echo "       could use the GPU and every llama-server route, past beast-gate and its audit." >&2
  echo "       Fix one of: BIND_HOST=127.0.0.1 (remote access: scripts/setup-tailscale.sh)," >&2
  echo "       set LLAMA_API_KEY, or ALLOW_OPEN_INFERENCE=true in openbeast.conf to accept it." >&2
  echo "       (exit 3 = refusal; rollback must NOT substitute another model)" >&2
  exit 3
fi

# --- Model registry enforcement (supply chain for weights) -----------------
# Every weight file this launch loads — the -m file, all shards of a split
# GGUF, and any --mmproj / -md draft — is checked against
# scripts/weights.registry (lib/weightcheck.sh has the rules). Default `warn`;
# `strict` refuses with exit 3; `off` silences it. Size-only by design;
# `./scripts/verify-weights.sh --deep` is the hash-level check.
source "$SCRIPT_DIR/lib/weightcheck.sh"
WEIGHT_FILES=()
while IFS= read -r _wf; do WEIGHT_FILES+=("$_wf"); done \
  < <(ob_weight_files "$MODEL" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"})
_wr_rc=0
ob_weight_enforce "$SCRIPT_DIR/weights.registry" "${WEIGHT_ENFORCE:-warn}" "${WEIGHT_FILES[@]}" || _wr_rc=$?
[[ $_wr_rc -eq 0 ]] || exit "$_wr_rc"

# Weights kept in host RAM (MoE experts on the CPU, or no GPU layers at all):
# the GGUF size then says nothing about VRAM, and everything about RAM.
WEIGHTS_IN_HOST_RAM=0
[[ "$GPU_LAYERS" == "0" ]] && WEIGHTS_IN_HOST_RAM=1
for _a in ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; do
  case "$_a" in
    --n-cpu-moe|-ncmoe|--n-cpu-moe=*|--cpu-moe|-cmoe|--override-tensor|-ot|--override-tensor=*)
      WEIGHTS_IN_HOST_RAM=1 ;;
  esac
done

# --- Adaptive context (Hardware Profiles Phase 2) --------------------------
# The shipped -c values are MEASURED on the 32 GB reference card (RTX 5090).
# On a smaller card that context would OOM, so scale it to the card's KV
# budget: weights are a fixed cost (≈ the GGUF file size), and with
# --kv-unified the KV cache scales with context and is shared across slots.
# --metrics turns on llama-server's Prometheus endpoint. It's what makes
# requests_deferred — the real queue depth — visible to the beast-slot
# contract (slots.busy only counts in-flight work). Bound to HOST like every
# route: loopback by default, and never keyless on a network bind (refused
# above unless ALLOW_OPEN_INFERENCE=true). NOTE the honest caveat: beast-gate's allowlist keeps
# /metrics away from remote clients, but on the DEFAULT topology (EDGE_GATE
# =false, :8443 mapped straight at llama-server) it joins /slots and /props
# as tailnet-readable aggregate metadata. Documented in docs/BEAST_SLOT.md.
# We only ever scale DOWN — the measured value stands on reference-class
# cards, so behavior is byte-identical there. Overrides:
#   OPENBEAST_CONTEXT=<n>     force an exact context (skip scaling)
#   OPENBEAST_VRAM_MIB=<n>    tell us the VRAM to budget against (when
#                             detection is wrong, e.g. Intel Arc / headless
#                             AMD, or a multi-GPU launch pinned to one card)
#   OPENBEAST_AUTO_CONTEXT=0  disable scaling entirely
if [[ -n "${OPENBEAST_CONTEXT:-}" ]]; then
  CONTEXT="$OPENBEAST_CONTEXT"
  echo "Context: $CONTEXT (forced via OPENBEAST_CONTEXT)"
elif [[ "${OPENBEAST_AUTO_CONTEXT:-1}" == "1" ]]; then
  source "$SCRIPT_DIR/lib/hardware.sh" 2>/dev/null || true
  command -v ob_detect_gpu >/dev/null 2>&1 && ob_detect_gpu 2>/dev/null || true
  vram="${OPENBEAST_VRAM_MIB:-${OB_VRAM_MB:-0}}"
  vram_what="a ${vram} MiB card"
  # Several GPUs: llama.cpp splits the model across them, so the KV budget is
  # their sum (ob_context_vram_mb), not the largest card — unless this launch
  # is pinned to a subset. nvidia-smi lists every card whatever
  # CUDA_VISIBLE_DEVICES says, and --device / --split-mode none restrict
  # llama-server itself; which cards remain cannot be told from here, so a
  # pinned launch keeps the conservative single-card budget and says so.
  if [[ -z "${OPENBEAST_VRAM_MIB:-}" && "${OB_GPU_COUNT:-0}" -gt 1 ]] \
     && command -v ob_context_vram_mb >/dev/null 2>&1; then
    _pinned=""
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] && _pinned="CUDA_VISIBLE_DEVICES"
    for _i in "${!EXTRA_ARGS[@]}"; do
      case "${EXTRA_ARGS[$_i]}" in
        -dev|--device) _pinned="${EXTRA_ARGS[$_i]}" ;;
        -sm|--split-mode)
          # (no :-default here: _i + 1 may be past the end of the array)
          _next=$((_i + 1))
          [[ $_next -lt ${#EXTRA_ARGS[@]} && "${EXTRA_ARGS[$_next]}" == "none" ]] && _pinned="--split-mode none" ;;
      esac
    done
    if [[ -n "$_pinned" ]]; then
      echo "Context: ${OB_GPU_COUNT} GPUs, but this launch is pinned ($_pinned) — budgeting for the largest single card (${vram} MiB). Set OPENBEAST_VRAM_MIB=<n> to the VRAM it really gets."
    else
      vram="$(ob_context_vram_mb "${OB_VRAM_MB:-0}" "${OB_VRAM_TOTAL_MB:-0}" "$OB_GPU_COUNT")"
      vram_what="${OB_GPU_COUNT} GPUs (${OB_VRAM_TOTAL_MB} MiB total, ${vram} MiB budgeted)"
    fi
  fi
  # Every file of the set: shard 1 alone of a 3-shard GGUF was 42.7 of its
  # 97 GB, and the --mmproj / draft weights sit in VRAM too.
  weights_mib="$(ob_weight_set_mib "${WEIGHT_FILES[@]}")"
  # rc=0 capture-then-|| : under `set -e`, a plain `scaled=$(...)` with a
  # nonzero exit would kill the script HERE — the rc=2 "weights don't fit"
  # branch below was unreachable and small-card users got a silent exit.
  rc=0
  scaled=$(ob_scale_context "$CONTEXT" "$vram" "$weights_mib") || rc=$?
  if [[ $WEIGHTS_IN_HOST_RAM -eq 1 ]]; then
    # How much of the GGUF lands on the GPU is up to --n-cpu-moe / -ot, not
    # this script: scaling on the whole file "forced -c 8192" on cards the
    # launch fits. The script's measured -c stands.
    [[ "$scaled" != "$CONTEXT" ]] && echo "Context: $CONTEXT kept — this launch keeps weights in host RAM, so the GGUF size (~${weights_mib} MiB) is not its VRAM cost. Override: OPENBEAST_CONTEXT=<n>."
  elif [[ $rc -eq 2 ]]; then
    echo "Warning: ${vram_what} can't hold this model's weights (~${weights_mib} MiB) + 2 GB headroom — try a smaller quant. Forcing -c ${scaled}." >&2
    CONTEXT="$scaled"
  elif [[ "$scaled" -lt "$CONTEXT" ]]; then
    echo "Context: $CONTEXT -> $scaled (auto-scaled for ${vram_what}; weights ~${weights_mib} MiB, 2 GB headroom). Override: OPENBEAST_CONTEXT=<n>."
    CONTEXT="$scaled"
  fi
fi

ALIAS_ARGS=()
if [[ -n "$ALIAS" ]]; then
  ALIAS_ARGS=(-a "$ALIAS")
fi

# The API key goes to llama-server through its ENVIRONMENT, never argv.
# `--api-key "$LLAMA_API_KEY"` sat in /proc/<pid>/cmdline for the server's
# whole lifetime, readable by every local uid (`ps -eo args`; /proc is not
# mounted hidepid) — and that key is the one that lets a caller bypass
# beast-gate's identity, caps and audit. llama-server reads LLAMA_API_KEY
# natively (common/arg.cpp: `--api-key … .set_env("LLAMA_API_KEY")`, same
# comma-separated parsing), and /proc/<pid>/environ is owner-only.
# Unset when empty: an exported empty string would still count as "set".
if [[ -n "$LLAMA_API_KEY" ]]; then
  export LLAMA_API_KEY
else
  unset LLAMA_API_KEY
fi

echo "Parallel slots: $PARALLEL (unified KV cache, continuous batching)"

# Global reasoning control (lib/conf.sh REASONING / REASONING_BUDGET). Placed
# AFTER EXTRA_ARGS so an explicit user setting overrides any --reasoning /
# --reasoning-budget a model-specific serve script baked in (last flag wins in
# llama-server). Unset = the per-script / model default stands.
REASONING_ARGS=()
if [[ -n "${REASONING:-}" ]]; then
  REASONING_ARGS+=(--reasoning "$REASONING")
  echo "Reasoning: forced '$REASONING' (global override)"
fi
if [[ -n "${REASONING_BUDGET:-}" ]]; then
  REASONING_ARGS+=(--reasoning-budget "$REASONING_BUDGET")
  echo "Reasoning budget: $REASONING_BUDGET thinking tokens (global override)"
fi

# Host prompt cache (--cache-ram N, MiB; llama-server's default is 8192).
# The single-slot default swaps conversations through this cache, and 8 GiB
# holds no real agent session (lib/hardware.sh ob_prompt_cache_mb has the
# numbers), so size it to this host's RAM.
#   PROMPT_CACHE_RAM_MB   (env OPENBEAST_PROMPT_CACHE_RAM_MB; openbeast.conf)
#       unset / auto   35% of host RAM, at most 48 GiB, never below 8192
#       0              pass nothing: llama-server's own default
#       <n>            exactly n MiB (-1 = no limit)
# Placed BEFORE EXTRA_ARGS, and skipped altogether when the model script
# passes --cache-ram itself. Only passed to a llama-server that knows the
# flag (it arrived in llama.cpp PR #16391; an older build would exit on it).
PROMPT_CACHE_ARGS=()
_pc="${OPENBEAST_PROMPT_CACHE_RAM_MB:-${PROMPT_CACHE_RAM_MB:-}}"
if [[ -z "$_pc" ]] && declare -F _ob_conf_value >/dev/null 2>&1; then
  _pc="$(_ob_conf_value PROMPT_CACHE_RAM_MB || true)"
fi
read -r _pc _ <<< "$_pc" || true          # first token: `32768  # comment`
_pc_own=0
for _a in "${EXTRA_ARGS[@]}"; do
  [[ "$_a" == "--cache-ram" || "$_a" == "-cram" ]] && _pc_own=1
done
if [[ -n "$_pc" && "$_pc" != "auto" && ! "$_pc" =~ ^(-1|[0-9]+)$ ]]; then
  echo "WARNING: PROMPT_CACHE_RAM_MB='$_pc' is not a number of MiB (or auto, 0, -1) — using auto" >&2
  _pc="auto"
fi
if [[ $_pc_own -eq 0 && "$_pc" != "0" ]]; then
  _pc_how="PROMPT_CACHE_RAM_MB"
  if [[ -z "$_pc" || "$_pc" == "auto" ]]; then
    declare -F ob_prompt_cache_mb >/dev/null 2>&1 || source "$SCRIPT_DIR/lib/hardware.sh" 2>/dev/null || true
    _pc=0
    # Weights kept in host RAM share the stack's memory scope with the cache.
    _pc_host=0
    [[ $WEIGHTS_IN_HOST_RAM -eq 1 ]] && _pc_host="$(ob_weight_set_mib "${WEIGHT_FILES[@]}")"
    if declare -F ob_prompt_cache_mb >/dev/null 2>&1; then
      _pc="$(ob_prompt_cache_mb "$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)" \
             "${MEM_LIMIT_PCT:-75}" "$_pc_host")"
    fi
    _pc_how="auto: 35% of host RAM, max 48 GiB"
    if [[ $_pc_host -gt 0 ]]; then
      _pc_how="auto: 35% of host RAM, max 48 GiB, less ~${_pc_host} MiB of host-resident weights in the ${MEM_LIMIT_PCT:-75}% memory scope"
      [[ "$_pc" == "0" ]] && echo "Prompt cache: llama-server's default — this launch keeps ~${_pc_host} MiB of weights in host RAM, which leaves the ${MEM_LIMIT_PCT:-75}% memory scope no room for more (PROMPT_CACHE_RAM_MB=<MiB> to choose)."
    fi
  fi
  if [[ "$_pc" != "0" ]]; then
    # Captured, not piped into grep -q: under pipefail an early-exiting grep
    # SIGPIPEs the writer and the test reads "unsupported" at random.
    # Bounded: --help prints and exits, but nothing here should be able to
    # hold up a model launch.
    if command -v timeout >/dev/null 2>&1; then
      _pc_help="$(timeout 10 "$LLAMA_SERVER" --help 2>&1 || true)"
    else
      _pc_help="$("$LLAMA_SERVER" --help 2>&1 || true)"
    fi
    if [[ "$_pc_help" == *"--cache-ram"* ]]; then
      PROMPT_CACHE_ARGS=(--cache-ram "$_pc")
      echo "Prompt cache: $_pc MiB of host RAM ($_pc_how; PROMPT_CACHE_RAM_MB=<MiB> to change, 0 = llama-server's default)"
    else
      echo "Prompt cache: this llama-server has no --cache-ram — its built-in default stands (rebuild: ./scripts/update.sh)."
    fi
    unset _pc_help
  fi
fi

exec "$LLAMA_SERVER" \
  -m "$MODEL" \
  "${ALIAS_ARGS[@]}" \
  -ngl "$GPU_LAYERS" \
  -c "$CONTEXT" \
  -np "$PARALLEL" \
  --kv-unified \
  -ctk "$KV_QUANT" \
  -ctv "$KV_QUANT" \
  --metrics \
  --host "$HOST" \
  --port "$PORT" \
  ${PROMPT_CACHE_ARGS[@]+"${PROMPT_CACHE_ARGS[@]}"} \
  "${EXTRA_ARGS[@]}" \
  ${REASONING_ARGS[@]+"${REASONING_ARGS[@]}"}
