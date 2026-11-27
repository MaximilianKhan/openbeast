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
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# llama-only: drives a local llama-server, so it has nothing to do on a
# stack whose INFERENCE_BACKEND is not llama (lib/backend.sh).
if [[ -f "$SCRIPT_DIR/lib/backend.sh" ]]; then
  source "$SCRIPT_DIR/lib/backend.sh"
  ob_llama_only "$(basename "$0")" || exit 0
fi

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
# The measured server goes on loopback, whatever BIND_HOST says: serve.sh
# binds BIND_HOST by default, and with a LAN/tailnet BIND_HOST the
# localhost health probe never reached it — a clean load waited 240 s and
# was reported "never became healthy". A --host among the extra args wins
# (serve.sh takes the last one), so the probe follows it.
SERVE_HOST=127.0.0.1
_prev=""
for _a in "$@"; do
  [[ "$_prev" == "--host" ]] && SERVE_HOST="$_a"
  _prev="$_a"
done
# BIND_HOST (openbeast.conf, read-only) is where the STACK's server listens.
OB_CONF_READONLY=1 source "$SCRIPT_DIR/lib/conf.sh" 2>/dev/null || true
command -v ob_probe_host >/dev/null 2>&1 || source "$SCRIPT_DIR/lib/net.sh"
HEALTH="http://$(ob_probe_host "$SERVE_HOST"):${PORT}/health"
STACK_HEALTH="http://$(ob_probe_host "${BIND_HOST:-127.0.0.1}"):${PORT}/health"
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
#    Both addresses: where ours will listen, and where the stack's does (a
#    server on a LAN BIND_HOST does not answer loopback, yet it is on the
#    same card).
#    On the stack's address only an answer counts (rc 0, or 52/56: connected,
#    then cut off): an address this box does not hold times out, and nothing
#    of ours can be listening there.
PROBE_RC=0
curl -s -o /dev/null --max-time 2 "$HEALTH" 2>/dev/null || PROBE_RC=$?
if [[ "$PROBE_RC" -eq 7 && "$STACK_HEALTH" != "$HEALTH" ]]; then
  _rc=0; curl -s -o /dev/null --max-time 2 "$STACK_HEALTH" 2>/dev/null || _rc=$?
  case "$_rc" in 0|52|56) PROBE_RC="$_rc" ;; esac
fi
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
"$SCRIPT_DIR/$SERVE" --host 127.0.0.1 ${PORT_ARGS[@]+"${PORT_ARGS[@]}"} "$@" >"$LOG" 2>&1 &
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
# The context the server actually allocated: serve.sh auto-scales -c on a
# card smaller than the reference, so the script's -c is not what was
# measured, and a per-card profile (docs/HARDWARE_PROFILES.md) needs the real
# one. llama.cpp logs it at load ("llama_context: n_ctx = N").
CTX="$(grep -m1 -oE 'llama_context: n_ctx +=  *[0-9]+' "$LOG" 2>/dev/null | grep -oE '[0-9]+$' || true)"

echo ""
echo "-------------------------------------------------------------"
printf "RESULT  %-34s ctx=%-7s used=%6s MiB  headroom=%6s MiB  [%s]\n" \
       "$SERVE" "${CTX:-?}" "$MAX_USED" "$HEADROOM" "$VERDICT"
echo "-------------------------------------------------------------"
