#!/bin/bash
# MTP draft-depth profiler for Qwen3.8-27B-Uncensored (abliterated) Q5_K_M.
#
#   ./scripts/profile-qwen38-uncensored-mtp.sh          # sweep at the native 262144 ctx
#   SWEEP_CTX=131072 ./scripts/profile-qwen38-uncensored-mtp.sh
#   SWEEP_N="1 2 4" ./scripts/profile-qwen38-uncensored-mtp.sh
#
# Sweeps --spec-draft-n-max over {1,2,4,6,8,10} and reports, per config, the
# sustained DECODE tok/s, the draft acceptance rate + mean accepted length, and
# VRAM used. Greedy (temp 0, fixed seed) so the generated tokens are IDENTICAL
# across every n — pure speed comparison. Pick the n with the highest decode
# tok/s that fits VRAM; set it (and a matching -c) in the serve script.
#
# WHY THIS MODEL NEEDS ITS OWN SWEEP: the MTP head ships INSIDE the standard
# GGUF (qwen35.nextn_predict_layers = 1, tensors at blk.64.nextn.*, block_count
# 65 = 64 trunk + 1 nextn) — same layout as stock Qwen3.8-27B. But the trunk has
# been abliterated, and the nextn head drafts from the trunk it was trained
# beside. Abliteration edits the refusal direction out of the residual stream,
# which is exactly what the draft head predicts into, so draft acceptance can
# move even though the tensor layout is untouched. Stock Qwen3.8 peaks at n4
# (123.7 tok/s, 48% acceptance) while Qwen3.6-27B wanted n8 — the optimum is
# not a property of the architecture. MEASURE, don't inherit.
#
# Flags below MIRROR scripts/serve.sh exactly (-ngl 99, --kv-unified, -ctk/-ctv
# q4_0, -np 1) plus the MTP triple, so the numbers this prints are the numbers
# the shipped serve script will deliver. Do not add flags here without adding
# them to the serve script too.
#
# Results: .run/qwen38-uncensored-mtp-results.txt (+ per-n launch logs).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"
source "$SCRIPT_DIR/lib/weights.sh"

MODEL="$WEIGHTS_DIR/Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
[[ -f "$MODEL" ]] || { echo "Error: model not found: $MODEL" >&2; exit 1; }

LS="$REPO_DIR/llama.cpp/build/bin/llama-server"
[[ -x "$LS" ]] || { echo "Error: llama-server not built ($LS)" >&2; exit 1; }
export LD_LIBRARY_PATH="$(dirname "$LS")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

CTX="${SWEEP_CTX:-262144}"
PORT=8080
RESULTS="$REPO_DIR/.run/qwen38-uncensored-mtp-results.txt"
mkdir -p "$REPO_DIR/.run"
: > "$RESULTS"

# Free the GPU/port if a stack server is already listening on 8080.
if curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "A server is already on :$PORT — stopping the stack for the sweep (restart with ./start.sh -d after)."
  "$REPO_DIR/stop.sh" >/dev/null 2>&1 || true
  sleep 3
fi

PROMPT='Write a detailed, step-by-step technical explanation of how speculative decoding with multi-token prediction (MTP) accelerates transformer inference. Cover the draft step, verification, acceptance, and why it preserves output quality.'
REQ() { # $1 = max_tokens
  curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"sweep\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"max_tokens\":$1,\"temperature\":0,\"seed\":42,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
    -o /dev/null
}

wait_health() { # $1 = server PID
  for _ in $(seq 1 180); do
    curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"' && return 0
    kill -0 "$1" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}

run_one() { # $1 = n-max ("none" = MTP off, the baseline)
  local N="$1" logf="$REPO_DIR/.run/qwen38-uncensored-mtp-n${1}.log"
  local SPEC=(--spec-type draft-mtp --spec-draft-n-max "$N" --spec-draft-p-min 0.0)
  [[ "$N" == "none" ]] && SPEC=()
  echo ">>> n-max=$N (ctx=$CTX) launching..."
  "$LS" -m "$MODEL" -a sweep -ngl 99 -c "$CTX" -np 1 --kv-unified \
    -ctk q4_0 -ctv q4_0 "${SPEC[@]}" \
    --host 127.0.0.1 --port "$PORT" > "$logf" 2>&1 &
  local PID=$!
  if ! wait_health "$PID"; then
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
  printf 'n=%-4s  decode=%-7s tok/s  %-28s VRAM=%s MiB\n' "$N" "${toks:-?}" "${acc:-acc=n/a}" "${vram:-?}" | tee -a "$RESULTS"
  kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
  sleep 2
}

echo "=== Qwen3.8-27B-Uncensored MTP profile (model: $(basename "$MODEL"), ctx=$CTX) ==="
for N in ${SWEEP_N:-none 1 2 4 6 8 10}; do run_one "$N"; done
echo ""
echo "=== SWEEP COMPLETE — $RESULTS ==="
cat "$RESULTS"
echo ""
echo "n=none is the no-MTP baseline; the speedup is peak-n / none."
echo "Pick the n with the highest decode tok/s that fits VRAM (leave ~2 GB"
echo "headroom); set --spec-draft-n-max in scripts/serve-qwen38-27b-uncensored-mtp-q5.sh."
echo "Restart the stack: ./start.sh -d"
