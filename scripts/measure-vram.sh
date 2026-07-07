#!/bin/bash
# Measure real VRAM usage + headroom for a model serve script.
#
# Launches a serve-*.sh, waits for the server to report healthy (KV cache is
# allocated up-front at load, so peak VRAM is reachable without sending load),
# samples nvidia-smi a few times, prints the max used / free / headroom against
# the card total, then tears the server down.
#
# Usage:
#   scripts/measure-vram.sh serve-qwen-27b-mtp-q5.sh            # at the script's configured context
#   scripts/measure-vram.sh serve-qwen-27b-mtp-q5.sh -c 393216  # override context (extra args pass through)
#
# The "2 GB OS-headroom rule": keep >= ~2048 MiB free under sustained load on a
# 32 GB card, or the server risks OOM/crash when the OS/compositor grows.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SERVE="${1:-}"
if [[ -z "$SERVE" || ! -x "$SCRIPT_DIR/$SERVE" ]]; then
  echo "Usage: scripts/measure-vram.sh <serve-*.sh> [extra serve args]" >&2
  echo "Available serve scripts:" >&2
  ls "$SCRIPT_DIR"/serve-*.sh | xargs -n1 basename | sed 's/^/  /' >&2
  exit 1
fi
shift

if ! nvidia-smi >/dev/null 2>&1; then
  echo "Error: nvidia-smi is not working. If you just upgraded the driver," >&2
  echo "reboot so the running kernel module matches the userspace library." >&2
  exit 1
fi

TOTAL_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
PORT=8080
HEALTH="http://localhost:${PORT}/health"

echo "=== Measuring: $SERVE ${*:+(extra args: $*)} ==="
echo "Card total: ${TOTAL_MIB} MiB"

# Launch the server, silencing its (noisy) logs to a temp file for post-mortem.
LOG="$(mktemp)"
"$SCRIPT_DIR/$SERVE" "$@" >"$LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null
  # llama-server may fork; also kill any llama-server bound to our port
  pkill -f "llama-server.*--port ${PORT}" 2>/dev/null
  wait "$SERVER_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "Waiting for health (up to 240s; big models + long context load slowly)..."
READY=0
for i in $(seq 1 240); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "SERVER DIED during load (likely OOM at this context). Tail of log:" >&2
    tail -25 "$LOG" >&2
    echo "RESULT: $SERVE — FAILED TO LOAD ${*:+with $*}"
    exit 2
  fi
  if curl -s --max-time 2 "$HEALTH" 2>/dev/null | grep -q '"status"[^}]*ok'; then
    READY=1; echo "Healthy after ~${i}s."; break
  fi
  sleep 1
done
if [[ "$READY" -ne 1 ]]; then
  echo "Server never became healthy within timeout. Tail of log:" >&2
  tail -25 "$LOG" >&2
  exit 3
fi

# Sample VRAM a handful of times; take the max used.
MAX_USED=0
for _ in $(seq 1 5); do
  U=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
  [[ "$U" -gt "$MAX_USED" ]] && MAX_USED="$U"
  sleep 1
done

HEADROOM=$(( TOTAL_MIB - MAX_USED ))
VERDICT="OK"
[[ "$HEADROOM" -lt 2048 ]] && VERDICT="TIGHT (<2 GB headroom — back off context)"

echo ""
echo "-------------------------------------------------------------"
printf "RESULT  %-34s used=%6s MiB  headroom=%6s MiB  [%s]\n" \
       "$SERVE" "$MAX_USED" "$HEADROOM" "$VERDICT"
echo "-------------------------------------------------------------"

rm -f "$LOG"
