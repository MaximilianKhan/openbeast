#!/bin/bash
# Foreground launcher for a MANAGED non-llama engine (docs/ENGINES.md).
#
# start.sh launches this in place of a scripts/serve-*.sh when
# INFERENCE_BACKEND is vllm / tensorfold and INFERENCE_MANAGED=true (one
# host, TP 1 — `scripts/engine.sh use <engine> --profile NAME` sets that up).
# It runs the engine adapter's launch in the FOREGROUND, so start.sh's
# supervisor, rollback-free relaunch budget and trap work on it exactly as on
# a llama-server: a container engine is stopped by name when this process is
# TERMed; a native one is exec'd (this pid IS the server).
#
#   scripts/engine-serve.sh            # run (start.sh does this)
#   scripts/engine-serve.sh --print    # print the exact command; run nothing
#
# llama.cpp never comes here: its serve scripts are unchanged.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PRINT=0
for a in "$@"; do
  case "$a" in
    --print) PRINT=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $a (see --help)" >&2; exit 2 ;;
  esac
done
# Read-only: start.sh already resolved (and, if needed, created) the conf.
export OB_CONF_READONLY=1
# shellcheck source=lib/conf.sh
source "$SCRIPT_DIR/lib/conf.sh"
# shellcheck source=lib/engine.sh
source "$SCRIPT_DIR/lib/engine.sh"
ob_engine_host_class_pin          # one GPU detection for the whole launch
E="$INFERENCE_BACKEND"
if [[ "$E" == llama ]]; then
  echo "engine-serve.sh: INFERENCE_BACKEND=llama is launched by scripts/serve-*.sh, not here." >&2
  exit 2
fi
if ! ob_engine_can_manage "$E"; then
  echo "engine-serve.sh: the $E adapter has no single-host launcher (scripts/engines/$E.sh)." >&2
  exit 2
fi
# No keyless inference on a network bind (serve.sh has the same refusal for
# llama-server). The adapters bind INFERENCE_URL's host and pass a key only
# when LLAMA_API_KEY is set, and ob_url_is_local counts 0.0.0.0, :: and
# BIND_HOST as local, so a wildcard or a tailnet IP stays managed. Exit 3:
# a refusal, not a crash to relaunch.
_open_refusal() {
  echo "Error: refusing to serve keyless $E on ${INFERENCE_URL:-http://127.0.0.1:8080} — anyone on that network" >&2
  echo "       could use the GPU and every route, past beast-gate and its audit." >&2
  echo "       Fix one of: a 127.0.0.1 INFERENCE_URL (scripts/engine.sh use $E --url http://127.0.0.1:8080)," >&2
  echo "       set LLAMA_API_KEY, or ALLOW_OPEN_INFERENCE=true in openbeast.conf to accept it." >&2
}
if [[ $PRINT -eq 1 ]]; then
  ob_engine_keyless_open "${INFERENCE_URL:-http://127.0.0.1:8080}" && { _open_refusal; echo "       (--print: the command a launch would run)" >&2; }
  ENG_PRINT=1 ob_engine_call "$E" launch
  exit $?
fi
if [[ "$INFERENCE_MANAGED" != true ]]; then
  echo "engine-serve.sh: INFERENCE_MANAGED=false — this stack does not launch $E (scripts/engine.sh use $E --profile NAME)." >&2
  exit 2
fi
if ob_engine_keyless_open "${INFERENCE_URL:-http://127.0.0.1:8080}"; then
  _open_refusal
  exit 3
fi
ob_engine_call "$E" launch
