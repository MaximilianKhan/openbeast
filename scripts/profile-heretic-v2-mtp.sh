#!/bin/bash
# Quick MTP draft-depth profiler for the Heretic v2 (llmfan46) MTP build.
#
#   ./scripts/profile-heretic-v2-mtp.sh q5     # profile the Q5_K_M GGUF
#   SWEEP_CTX=131072 ./scripts/profile-heretic-v2-mtp.sh q5
#
# q5 is the only quant: the Q6_K weight was pruned 2026-08-20 (dominated by
# Q5) and nothing pins, fetches or serves it any more.
#
# Sweeps --spec-draft-n-max over {1,2,4,6,8,10} and reports, per config, the
# sustained DECODE tok/s, the draft acceptance rate + mean accepted length,
# and VRAM used. Greedy (temp 0, fixed seed) so the generated tokens are
# IDENTICAL across every n — pure speed comparison. Pick the n with the
# highest decode tok/s that fits VRAM; set it (and a matching -c) in the serve
# script.
#
# NOTE: these preserve the NATIVE Qwen3.6 MTP heads (unlike DavidAU's modified
# NEO head that peaked at n2), so the optimum may sit deeper (base unsloth 27B
# MTP peaked at n8). MEASURE — don't assume.
#
# Results: .run/heretic-v2-mtp-<quant>-results.txt (+ per-n launch logs).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# --help is answered before anything else; every other argument is judged
# right after the backend check below and BEFORE anything is touched: a run
# stops the live stack and holds the GPU for a sweep, so a typo must never
# get that far.
case "${1:-}" in
  -h|--help)
    # shellcheck source=scripts/lib/usage.sh
    source "$SCRIPT_DIR/lib/usage.sh"
    ob_usage "$0"; exit 0 ;;
esac
# llama-only: drives a local llama-server, so it has nothing to do on a
# stack whose INFERENCE_BACKEND is not llama (lib/backend.sh).
if [[ -f "$SCRIPT_DIR/lib/backend.sh" ]]; then
  source "$SCRIPT_DIR/lib/backend.sh"
  ob_llama_only "$(basename "$0")" || exit 0
fi
case "${1:-}" in
  q5) ;;
  "") echo "Usage: $0 q5   (the MTP GGUF to profile; see --help)" >&2; exit 2 ;;
  q6) echo "q6 is gone: the Heretic v2 Q6_K weight was pruned 2026-08-20 and nothing pins or serves it." >&2
      echo "Usage: $0 q5   (the MTP GGUF to profile)" >&2; exit 2 ;;
  *)  echo "Unknown option: $1 (see --help)" >&2
      echo "Usage: $0 q5   (the MTP GGUF to profile)" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "Unknown option: $2 (see --help) — one argument only: q5." >&2; exit 2; }
cd "$REPO_DIR"
source "$SCRIPT_DIR/lib/weights.sh"

QUANT="${1:-}"
case "$QUANT" in
  q5) MODEL="$WEIGHTS_DIR/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf" ;;
  *)  echo "Usage: $0 q5   (the MTP GGUF to profile)" >&2; exit 2 ;;
esac
[[ -f "$MODEL" ]] || { echo "Error: model not found: $MODEL" >&2
                       echo "  (download the $QUANT variant into $WEIGHTS_DIR first)" >&2; exit 1; }

LS="$REPO_DIR/llama.cpp/build/bin/llama-server"
[[ -x "$LS" ]] || { echo "Error: llama-server not built ($LS)" >&2; exit 1; }
export LD_LIBRARY_PATH="$(dirname "$LS")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
CTX="${SWEEP_CTX:-200000}"
PORT=8080
RESULTS="$REPO_DIR/.run/heretic-v2-mtp-${QUANT}-results.txt"
mkdir -p "$REPO_DIR/.run"
: > "$RESULTS"

# THE CARD MUST BE OURS. stop.sh deliberately leaves llama-server alone while
# a GPU lease is HELD, so on a campaign's card the old "stop the stack, then
# launch" left the campaign's server on :$PORT, ours failed to bind, and
# wait_health took the campaign's /health as ours: the sweep's requests went
# into the campaign's timed cells, and its VRAM was recorded as this model's.
# So: someone else's lease is a hard stop; the stack is stopped only when no
# lease is held; anything still answering on the port is a refusal; and the
# sweep itself runs under its own lease (re-exec under gpu-lease.sh run).
LEASE_RC=0
LEASE_MSG="$("$SCRIPT_DIR/gpu-lease.sh" check 2>&1)" || LEASE_RC=$?
if [[ "$LEASE_RC" -ne 0 && "$LEASE_RC" -ne 3 ]]; then
  echo "Error: the GPU lease is ${LEASE_MSG:-unreadable (unknown is not free)}" >&2
  echo "  Refusing to profile on a card another job is using — wait for it (scripts/gpu-lease.sh status)." >&2
  exit 4
fi
if [[ "$LEASE_RC" -eq 3 ]] && curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "A server is already on :$PORT — stopping the stack for the sweep (restart with ./start.sh -d after)."
  "$REPO_DIR/stop.sh" >/dev/null 2>&1 || true
  sleep 3
fi
PROBE_RC=0
curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null || PROBE_RC=$?
if [[ "$PROBE_RC" -ne 7 ]]; then     # 7 = connection refused: nobody is there
  echo "Error: something still answers on :$PORT — its numbers would be recorded as this model's." >&2
  echo "  Stop it first (./stop.sh; scripts/gpu-lease.sh status if a job holds the card)." >&2
  exit 4
fi
if [[ "$LEASE_RC" -eq 3 ]]; then
  exec "$SCRIPT_DIR/gpu-lease.sh" run "$(basename "$0") $*" -- "$SCRIPT_DIR/$(basename "$0")" "$@"
fi

PROMPT='Write a detailed, step-by-step technical explanation of how speculative decoding with multi-token prediction (MTP) accelerates transformer inference. Cover the draft step, verification, acceptance, and why it preserves output quality.'
REQ() { # $1 = max_tokens
  curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"sweep\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"max_tokens\":$1,\"temperature\":0,\"seed\":42,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
    -o /dev/null
}

wait_health() { # $1 = server PID
  # Liveness FIRST, and again after the probe: a server that died on bind
  # leaves /health to whoever holds the port, and that answer is not ours.
  # (Captured, not `curl | grep -q`: under pipefail the early-exiting grep
  # can SIGPIPE curl and turn a match into a failure.)
  local body
  for _ in $(seq 1 120); do
    kill -0 "$1" 2>/dev/null || return 1
    body="$(curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
    [[ "$body" == *'"ok"'* ]] && kill -0 "$1" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

run_one() { # $1 = n-max
  local N="$1" logf="$REPO_DIR/.run/heretic-v2-mtp-${QUANT}-n${1}.log"
  echo ">>> n-max=$N (ctx=$CTX) launching..."
  "$LS" -m "$MODEL" -a sweep -ngl 99 -c "$CTX" -np 1 --kv-unified \
    -ctk q4_0 -ctv q4_0 -ctkd q4_0 -ctvd q4_0 \
    --spec-type draft-mtp --spec-draft-n-max "$N" --spec-draft-p-min 0.0 \
    -fa on -ngld 99 --host 127.0.0.1 --port "$PORT" > "$logf" 2>&1 &
  local PID=$!
  if ! wait_health "$PID"; then
    if grep -qiE "couldn'?t bind|failed to bind|address already in use" "$logf" 2>/dev/null; then
      echo "n=$N  PORT_CONFLICT (see $logf — :$PORT was taken; nothing was measured)" | tee -a "$RESULTS"
      kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; return
    fi
    echo "n=$N  FAILED_TO_START (see $logf — likely VRAM OOM at ctx=$CTX; lower SWEEP_CTX)" | tee -a "$RESULTS"
    kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; return
  fi
  REQ 64  >/dev/null   # warmup: CUDA graph capture
  REQ 700 >/dev/null   # measured run 1
  REQ 700 >/dev/null   # measured run 2 (warmed — this is what the log reports)
  local vram toks acc
  vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
  toks=$(grep 'tokens per second' "$logf" | grep -v 'prompt eval' | tail -1 | sed -E 's/.*, *([0-9.]+) tokens per second.*/\1/')
  # llama-server renamed this field: it printed "mean acceptance length" when
  # these profilers were written and prints "mean len" as of build 10254. The
  # old pattern silently failed to substitute, so `acc` kept the WHOLE log line
  # and the results table came out garbled (caught 2026-08-19). Accept both.
  acc=$(grep 'draft acceptance' "$logf" | tail -1 | sed -E 's/.*draft acceptance = ([0-9.]+).*mean (acceptance length|len) = *([0-9.]+).*/acc=\1 meanlen=\3/')
  printf 'n=%-2s  decode=%-7s tok/s  %-28s VRAM=%s MiB\n' "$N" "${toks:-?}" "${acc:-acc=?}" "${vram:-?}" | tee -a "$RESULTS"
  kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
  sleep 2
}

echo "=== Heretic v2 MTP profile: $QUANT  (model: $(basename "$MODEL"), ctx=$CTX) ==="
for N in 1 2 4 6 8 10; do run_one "$N"; done
echo ""
echo "=== SWEEP COMPLETE — $RESULTS ==="
cat "$RESULTS"
echo ""
echo "Pick the n with the highest decode tok/s that fits VRAM (leave ~2 GB"
echo "headroom); set --spec-draft-n-max in scripts/serve-heretic-v2-27b-mtp-${QUANT}.sh,"
echo "and use scripts/measure-vram.sh to set the largest safe -c. Restart: ./start.sh -d"
echo "Per the model card: if best acc= stays below ~0.50, prefer a non-MTP quant."
