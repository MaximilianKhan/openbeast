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
#
# THE CARD MUST BE EMPTY, and this script now makes sure of it instead of
# assuming it. It used to launch straight onto :8080 with no questions: with
# the stack (or a campaign's cell) already serving there, the new server
# failed to bind, the health probe was answered by the OTHER server, and the
# RESULT line reported that model's VRAM as this one's — or, when the bind
# failure won the race, "likely OOM" for a model that never tried to load.
# Then the EXIT trap ran `pkill -f "llama-server.*--port 8080"` and took the
# stack's (or the campaign's) server down mid-request. So:
#   * a GPU lease held by someone else  -> refuse (scripts/gpu-lease.sh check)
#   * anything already answering :8080  -> refuse (stop the stack first)
#   * otherwise the measurement runs UNDER the lease (gpu-lease.sh run)
#   * teardown signals only the process group this script launched
#   * a bind failure is reported as a port conflict, never as an OOM

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

# serve.sh's default port. OPENBEAST_MEASURE_PORT moves the measurement (it
# is passed to the serve script as --port) — the test suite uses it so it can
# never touch a real :8080.
PORT="${OPENBEAST_MEASURE_PORT:-8080}"
PORT_ARGS=()
[[ -n "${OPENBEAST_MEASURE_PORT:-}" ]] && PORT_ARGS=(--port "$PORT")
HEALTH="http://localhost:${PORT}/health"
LEASE_SH="$SCRIPT_DIR/gpu-lease.sh"

# 1. The lease. Somebody else's is a hard stop: loading a second model onto a
#    card a campaign is measuring on contaminates BOTH measurements.
LEASE_RC=0
LEASE_MSG="$("$LEASE_SH" check 2>&1)" || LEASE_RC=$?
case "$LEASE_RC" in
  0|3) ;;
  4) echo "Error: the GPU lease is $LEASE_MSG" >&2
     echo "  Measuring now would load a second model onto a card that job is using." >&2
     echo "  Wait for it (scripts/gpu-lease.sh status), then re-run." >&2
     exit 4 ;;
  *) echo "Error: could not read the GPU lease (gpu-lease.sh check: rc=$LEASE_RC) — unknown is not free." >&2
     exit 4 ;;
esac

# 2. The port. Anything that answers is somebody's server; its /health would
#    satisfy ours and its VRAM would be reported as this model's. curl rc 7
#    is "connection refused" — the only answer that means nobody is there.
PROBE_RC=0
curl -s -o /dev/null --max-time 2 "$HEALTH" 2>/dev/null || PROBE_RC=$?
if [[ "$PROBE_RC" -ne 7 ]]; then
  echo "Error: something is already listening on :$PORT (the stack's llama-server?)." >&2
  echo "  Its VRAM would be measured instead of this model's. Stop it first:" >&2
  echo "      ./stop.sh        # then re-run; restart with ./start.sh -d after" >&2
  exit 4
fi

# 3. Take the lease for the measurement (re-exec under `run`), unless a
#    `gpu-lease.sh run` already wraps us.
if [[ "$LEASE_RC" -eq 3 ]]; then
  exec "$LEASE_SH" run "measure-vram $SERVE" -- "$SCRIPT_DIR/measure-vram.sh" "$SERVE" "$@"
fi

TOTAL_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')

echo "=== Measuring: $SERVE ${*:+(extra args: $*)} ==="
echo "Card total: ${TOTAL_MIB} MiB"

# Launch the server, silencing its (noisy) logs to a temp file for post-mortem.
# `set -m` makes it the leader of its OWN process group (serve.sh execs
# llama-server, so the leader IS the server), and teardown signals that group
# and nothing else — never a pattern, which matched the stack's server, a
# campaign's, and the shell that invoked us.
LOG="$(mktemp)"
set -m
"$SCRIPT_DIR/$SERVE" "${PORT_ARGS[@]}" "$@" >"$LOG" 2>&1 &
SERVER_PID=$!
set +m

cleanup() {
  if kill -0 -- "-$SERVER_PID" 2>/dev/null; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null
    for _ in $(seq 1 50); do
      kill -0 -- "-$SERVER_PID" 2>/dev/null || break
      sleep 0.2
    done
    kill -KILL -- "-$SERVER_PID" 2>/dev/null
  fi
  wait "$SERVER_PID" 2>/dev/null
  rm -f "$LOG"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Waiting for health (up to 240s; big models + long context load slowly)..."
READY=0
for i in $(seq 1 240); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    # llama-server binds its HTTP socket BEFORE loading the model, so a busy
    # port kills it in a second, with no VRAM touched. That is not an OOM.
    if grep -qiE "couldn'?t bind|failed to bind|address already in use" "$LOG" 2>/dev/null; then
      echo "SERVER COULD NOT BIND :$PORT — a port conflict, NOT a VRAM result. Tail of log:" >&2
      tail -25 "$LOG" >&2
      echo "RESULT: $SERVE — PORT CONFLICT on :$PORT (nothing was measured)"
      exit 4
    fi
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
  # Still OUR server? If it died after /health said ok, the numbers below
  # would describe whatever is left on the card.
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "SERVER DIED while being measured. Tail of log:" >&2; tail -25 "$LOG" >&2; exit 2; }
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
