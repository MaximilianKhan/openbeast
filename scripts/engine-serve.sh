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
E="$INFERENCE_BACKEND"
if [[ "$E" == llama ]]; then
  echo "engine-serve.sh: INFERENCE_BACKEND=llama is launched by scripts/serve-*.sh, not here." >&2
  exit 2
fi
if ! ob_engine_can_manage "$E"; then
  echo "engine-serve.sh: the $E adapter has no single-host launcher (scripts/engines/$E.sh)." >&2
  exit 2
fi
if [[ $PRINT -eq 1 ]]; then
  ENG_PRINT=1 ob_engine_call "$E" launch
  exit $?
fi
if [[ "$INFERENCE_MANAGED" != true ]]; then
  echo "engine-serve.sh: INFERENCE_MANAGED=false — this stack does not launch $E (scripts/engine.sh use $E --profile NAME)." >&2
  exit 2
fi
ob_engine_call "$E" launch
