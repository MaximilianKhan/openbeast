#!/bin/bash
# Compile Project Maya for this GPU and build its pack for one profile
# (docs/WEIGHT_STREAMING.md "Project Maya"). The one step `engine.sh install
# maya` leaves out, because it needs the GPU and the model's weights: Maya's
# own setup, run non-interactively, with every download it would make
# already in place (llama.cpp at its pinned commit, the hash-locked packages)
# and the model given as files we fetched and verified (--gguf-dir).
#
#   scripts/engines/maya/prepare.sh <profile> [--print]
#
# Takes 10-30 minutes once (the engine build), then about a minute per model
# for the pack, which Maya writes beside the GGUF in WEIGHTS_DIR (the folder
# must be writable). Run it with the stack stopped: it needs the whole GPU.
# Records the config Maya wrote in .engines/maya/<profile>.config, which the
# adapter's launch reads.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT_DIR="$REPO_DIR/scripts"
PROFILE="" PRINT=0
for a in "$@"; do
  case "$a" in
    --print) PRINT=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $a" >&2; exit 2 ;;
    *) PROFILE="$a" ;;
  esac
done
[[ -n "$PROFILE" ]] || { echo "usage: $0 <profile> [--print]  (ls scripts/engines/maya/profiles)" >&2; exit 2; }
export OB_CONF_READONLY=1
# shellcheck source=../../lib/conf.sh
source "$SCRIPT_DIR/lib/conf.sh"
# shellcheck source=../../lib/engine.sh
source "$SCRIPT_DIR/lib/engine.sh"
# shellcheck source=../../lib/proc.sh
source "$SCRIPT_DIR/lib/proc.sh"
ob_engine_load maya
_eng_maya_profile_load "$PROFILE" || exit 1
HOME_DIR="$(_eng_maya_active)"
W="$(_eng_maya_weights_dir)"
CMD=(env MAYA_UPDATE_CHECK=0 HF_HUB_OFFLINE=1
     "$HOME_DIR/.venv/bin/python" "$HOME_DIR/maya.py" --setup --yes --plain --no-start
     --gguf-dir "$W/$PF_GGUF" --llama-dir "$HOME_DIR/third_party/llama.cpp"
     --no-vision --drafting off --context "$PF_CONTEXT")
[[ -n "$PF_VRAM_RESERVE_MIB" ]] && CMD+=(--vram-reserve-mib "$PF_VRAM_RESERVE_MIB")
if [[ $PRINT -eq 1 ]]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi
[[ -f "$HOME_DIR/maya.py" ]] || { echo "Maya is not installed — scripts/engine.sh install maya" >&2; exit 1; }
eng_maya_profile_locate "$PROFILE"
if [[ $SP_LOCATE_RC -ne 0 ]]; then
  echo "the weights of $PF_PROFILE_NAME are not all in $W — fetch every shard:" >&2
  awk -F'\t' -v p="${PF_GGUF%-00001-of-*}" 'index($3, p) == 1 {print "  ./scripts/fetch-weight.sh " $3}' "$SCRIPT_DIR/weights.registry" >&2
  exit 1
fi
if ob_recorded_pid_ours "$REPO_DIR/.run/supervisor.pid" 'start\.sh' 2>/dev/null; then
  echo "the stack is running and holds the GPU — ./stop.sh first (the build and the pack need the whole card)" >&2
  exit 1
fi
if [[ -x "$SCRIPT_DIR/gpu-lease.sh" ]]; then
  _rc=0; _l="$("$SCRIPT_DIR/gpu-lease.sh" check 2>/dev/null)" || _rc=$?
  [[ $_rc -ne 4 ]] || { echo "the GPU is leased — $_l" >&2; exit 1; }
fi
mark="$(mktemp)"
echo "Preparing Project Maya for $PF_PROFILE_NAME (build + pack; 10-30 min the first time)..."
( cd "$HOME_DIR" && "${CMD[@]}" )
cfg="$(find "$HOME_DIR" -maxdepth 1 -name 'maya-*.json' -newer "$mark" -print 2>/dev/null | head -n1)"
rm -f "$mark"
[[ -n "$cfg" ]] || { echo "Maya's setup finished but wrote no maya-*.json in $HOME_DIR — read maya-setup.log there" >&2; exit 1; }
( umask 077; mkdir -p "$(_eng_maya_state)"; printf '%s\n' "$cfg" > "$(_eng_maya_state)/$PF_PROFILE_NAME.config" )
echo "Prepared: $cfg (recorded for profile $PF_PROFILE_NAME)."
echo "Next: scripts/engine-gate.sh maya --profile $PF_PROFILE_NAME --serve-script serve-glm53-flash-abl-q2kxl-big.sh"
