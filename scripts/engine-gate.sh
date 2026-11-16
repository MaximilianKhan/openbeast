#!/bin/bash
# The engine gate (docs/WEIGHT_STREAMING.md "The engine gate"): is a
# candidate engine measurably faster than llama.cpp on THIS rig, on the SAME
# weights? Max's rule (2026-11-15): a single-author engine (Project Maya) is
# admitted as an opt-in engine only at >= 1.5x llama.cpp's decode tok/s.
#
#   scripts/engine-gate.sh <engine> --profile NAME --serve-script serve-X.sh
#                          [--runs 3] [--prompt short|long] [--max-tokens 256]
#                          [--load-timeout 1800] [--dry-run]
#
# What it does, in order (each step printed first; --dry-run stops there):
#   1. refuses while someone else's gpu-lease.sh lease holds the GPU
#   2. stops the stack, makes llama the engine (engine.sh use llama
#      --no-restart), starts `start.sh -d <serve-script>`, waits for it, and
#      measures it with scripts/tokps.py: the baseline
#   3. stops it, switches to <engine> with --profile (the same weights),
#      starts it with OPENBEAST_ENGINE_GATE=measuring (the one launch allowed
#      before a verdict exists), waits, measures: the candidate
#   4. stops it and switches back to llama (always, pass or fail)
#   5. tokps.py gate: candidate / baseline >= 1.5 writes a PASS verdict to
#      .engines/gate/<engine>.json and a history line; anything else FAIL
#   6. if the stack was running when this began, starts the default again
# Exit: 0 PASS, 1 FAIL, 2 usage, 3 a launch or a measurement failed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$REPO_DIR/.run"
usage() { awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$0"; }

ENGINE="" PROFILE="" SERVE="" RUNS=3 PROMPT=short MAXTOK=256 LOAD_TIMEOUT=1800 DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)      PROFILE="${2:?--profile needs a name}"; shift 2 ;;
    --serve-script) SERVE="${2:?--serve-script needs a name}"; shift 2 ;;
    --runs)         RUNS="${2:?}"; shift 2 ;;
    --prompt)       PROMPT="${2:?}"; shift 2 ;;
    --max-tokens)   MAXTOK="${2:?}"; shift 2 ;;
    --load-timeout) LOAD_TIMEOUT="${2:?}"; shift 2 ;;
    --dry-run|-n)   DRY=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    *) [[ -z "$ENGINE" ]] || { echo "Unexpected argument: $1" >&2; exit 2; }; ENGINE="$1"; shift ;;
  esac
done
[[ -n "$ENGINE" && -n "$PROFILE" && -n "$SERVE" ]] || { usage >&2; exit 2; }
[[ "$ENGINE" != llama ]] || { echo "llama.cpp is the baseline, not a candidate" >&2; exit 2; }
[[ "$RUNS" =~ ^[1-9][0-9]*$ && "$MAXTOK" =~ ^[0-9]+$ && "$LOAD_TIMEOUT" =~ ^[0-9]+$ ]] \
  || { echo "--runs, --max-tokens and --load-timeout are whole numbers" >&2; exit 2; }

export OB_CONF_READONLY=1
# shellcheck source=lib/conf.sh
source "$SCRIPT_DIR/lib/conf.sh"
# shellcheck source=lib/engine.sh
source "$SCRIPT_DIR/lib/engine.sh"
# shellcheck source=lib/backend.sh
source "$SCRIPT_DIR/lib/backend.sh"
# shellcheck source=lib/proc.sh
source "$SCRIPT_DIR/lib/proc.sh"

die() { echo "engine-gate: $*" >&2; exit "${2:-3}"; }
ob_engine_valid "$ENGINE" || die "no engine adapter '$ENGINE' (scripts/engines/)" 2
[[ -x "$REPO_DIR/scripts/$SERVE" ]] || die "scripts/$SERVE is not an executable serve script" 2

ENGINE_SH="${OB_GATE_ENGINE_SH:-$SCRIPT_DIR/engine.sh}"
TOKPS="${OB_GATE_TOKPS:-$SCRIPT_DIR/tokps.py}"
STOP="${OB_ENGINE_STOP_CMD:-$REPO_DIR/stop.sh}"
START="${OB_ENGINE_START_CMD:-$REPO_DIR/start.sh -d}"
URL="${OB_GATE_URL:-http://127.0.0.1:8080}"
GDIR="$OB_ENGINE_STATE_DIR/gate"
BASE_REC="$GDIR/$ENGINE-baseline.json" CAND_REC="$GDIR/$ENGINE-candidate.json"
VERDICT="$(ob_engine_gate_file "$ENGINE")"

running=0
ob_recorded_pid_ours "$RUN_DIR/supervisor.pid" 'start\.sh' 2>/dev/null && running=1

echo "engine gate: $ENGINE ($PROFILE) vs llama.cpp ($SERVE), same weights, on $(hostname)"
echo "  1. GPU lease check"
echo "  2. baseline:  $STOP; $ENGINE_SH use llama --no-restart; $START $SERVE; measure"
echo "  3. candidate: $STOP; $ENGINE_SH use $ENGINE --profile $PROFILE --no-restart;"
echo "                OPENBEAST_ENGINE_GATE=measuring $START; measure"
echo "  4. $STOP; $ENGINE_SH use llama --no-restart"
echo "  5. tokps.py gate --ratio 1.5 -> $VERDICT"
[[ $running -eq 1 ]] && echo "  6. $START  (the stack was running)"
echo "  measurement: $RUNS counted runs + 1 warm-up, prompt '$PROMPT', $MAXTOK tokens, at $URL"
[[ $DRY -eq 1 ]] && { echo "(dry run — nothing changed)"; exit 0; }

if [[ -x "$SCRIPT_DIR/gpu-lease.sh" ]]; then
  _rc=0; _l="$("$SCRIPT_DIR/gpu-lease.sh" check 2>/dev/null)" || _rc=$?
  [[ $_rc -ne 4 ]] || die "the GPU is leased — $_l. Nothing was changed."
fi
mkdir -p "$GDIR"

_run() { local c=(); read -r -a c <<< "$1"; shift; "${c[@]}" "$@"; }
_wait_ready() { # _wait_ready <backend>
  local deadline=$(( SECONDS + LOAD_TIMEOUT ))
  while (( SECONDS < deadline )); do
    ob_backend_ready "$URL" "$1" 2>/dev/null && return 0
    sleep "${OB_GATE_POLL:-5}"
  done
  return 1
}
_restore() {
  _run "$STOP" >/dev/null 2>&1 || true
  "$ENGINE_SH" use llama --no-restart >/dev/null 2>&1 || echo "engine-gate: could not switch back to llama — run: scripts/engine.sh use llama" >&2
}
_measure() { # _measure <label> <engine> <profile> <record>
  python3 "$TOKPS" measure --url "$URL" --runs "$RUNS" --prompt "$PROMPT" --max-tokens "$MAXTOK" \
    --label "$1" --engine "$2" --profile "$3" --record "$4"
}

echo "── baseline: llama.cpp, $SERVE"
_run "$STOP" >/dev/null 2>&1 || true
"$ENGINE_SH" use llama --no-restart >/dev/null || die "engine.sh use llama failed"
OPENBEAST_STOP_REASON="engine-gate baseline" _run "$START" "$SERVE" || { _restore; die "the baseline did not start ($START $SERVE)"; }
_wait_ready llama || { _restore; die "llama.cpp was not ready within ${LOAD_TIMEOUT}s"; }
_measure llama llama "$SERVE" "$BASE_REC" || { _restore; die "the baseline measurement failed"; }

echo "── candidate: $ENGINE, $PROFILE"
_run "$STOP" >/dev/null 2>&1 || true
OPENBEAST_ENGINE_GATE=measuring "$ENGINE_SH" use "$ENGINE" --profile "$PROFILE" --no-restart >/dev/null \
  || { _restore; die "engine.sh use $ENGINE --profile $PROFILE failed"; }
OPENBEAST_ENGINE_GATE=measuring _run "$START" || { _restore; die "$ENGINE did not start"; }
_wait_ready "$ENGINE" || { _restore; die "$ENGINE was not ready within ${LOAD_TIMEOUT}s"; }
_measure "$ENGINE" "$ENGINE" "$PROFILE" "$CAND_REC" || { _restore; die "the $ENGINE measurement failed"; }

echo "── back to llama.cpp"
_restore

rc=0
python3 "$TOKPS" gate --engine "$ENGINE" --candidate "$CAND_REC" --baseline "$BASE_REC" \
  --ratio 1.5 --out "$VERDICT" || rc=$?
_ratio="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["ratio"], "pass" if d["pass"] else "fail")' "$VERDICT" 2>/dev/null || echo "? fail")"
ob_engine_history_add "$ENGINE" gate "${_ratio%% *}x" "${_ratio##* }" "vs llama $SERVE, profile $PROFILE"
if [[ $running -eq 1 ]]; then
  echo "── the stack was running: starting the default again"
  _run "$START" || echo "engine-gate: restart failed — ./start.sh -d" >&2
fi
[[ $rc -eq 0 ]] && echo "PASS: $ENGINE may be used (scripts/engine.sh use $ENGINE --profile $PROFILE)." \
  || echo "FAIL: $ENGINE stays gated; llama.cpp remains the engine."
exit $rc
