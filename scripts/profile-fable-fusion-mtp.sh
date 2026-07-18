#!/bin/bash
# Quick MTP draft-depth profiler for the Fable-Fusion-711 MTP builds.
#
#   ./scripts/profile-fable-fusion-mtp.sh q5     # profile the Q5_K_M MTP GGUF
#   ./scripts/profile-fable-fusion-mtp.sh q6     # profile the Q6_K MTP GGUF
#   SWEEP_CTX=131072 ./scripts/profile-fable-fusion-mtp.sh q5   # smaller ctx
#
# Sweeps --spec-draft-n-max over {1,2,4,6,8,10} and reports, per config, the
# sustained DECODE tok/s, the draft acceptance rate + mean accepted length,
# and VRAM used. Controlled comparison: greedy (temp 0, fixed seed) so the
# generated tokens are IDENTICAL across every n — the only variable is draft
# depth, isolating pure speed. Pick the n with the highest decode tok/s that
# still fits VRAM, then set it (and a matching -c) in the serve script.
#
# Why this exists: the MTP optimum is model-specific — our unsloth Qwen 27B
# MTP peaked at n8, the NVFP4 27B at n4. DavidAU ships no recommended flags,
# so measure. Also confirms DavidAU's "switch to non-MTP if acceptance <50%"
# guidance for THIS card/build. Each config runs its own server; the sweep
# stops the stack's server if one is up (frees the GPU + port 8080).
#
# Results: .run/fable-fusion-mtp-<quant>-results.txt (+ per-n launch logs).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"
source "$SCRIPT_DIR/lib/weights.sh"

QUANT="${1:-}"
case "$QUANT" in
  q5) MODEL="$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M.gguf" ;;
  q6) MODEL="$WEIGHTS_DIR/Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf" ;;
  *)  echo "Usage: $0 {q5|q6}   (which MTP GGUF to profile)" >&2; exit 2 ;;
esac
[[ -f "$MODEL" ]] || { echo "Error: model not found: $MODEL" >&2
                       echo "  (download the MTP $QUANT variant into $WEIGHTS_DIR first)" >&2; exit 1; }

LS="$REPO_DIR/llama.cpp/build/bin/llama-server"
[[ -x "$LS" ]] || { echo "Error: llama-server not built ($LS)" >&2; exit 1; }
export LD_LIBRARY_PATH="$(dirname "$LS")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Q6 is heavier — default its sweep context lower so every n fits headroom.
if [[ "$QUANT" == q6 ]]; then CTX="${SWEEP_CTX:-131072}"; else CTX="${SWEEP_CTX:-200000}"; fi
PORT=8080
RESULTS="$REPO_DIR/.run/fable-fusion-mtp-${QUANT}-results.txt"
mkdir -p "$REPO_DIR/.run"
: > "$RESULTS"

# Free the GPU/port if a stack server is already listening on 8080.
if curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "A server is already on :$PORT — stopping the stack for the sweep (restart with ./start.sh -d after)."
  "$REPO_DIR/stop.sh" >/dev/null 2>&1 || true
  sleep 3
fi

# Deterministic ~700-token high-structure technical generation → realistic
# coding/reasoning draft-acceptance (the workload MTP is bought for).
PROMPT='Write a detailed, step-by-step technical explanation of how speculative decoding with multi-token prediction (MTP) accelerates transformer inference. Cover the draft step, verification, acceptance, and why it preserves output quality.'
REQ() { # $1 = max_tokens
  curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"sweep\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"max_tokens\":$1,\"temperature\":0,\"seed\":42,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
    -o /dev/null
}

wait_health() { # $1 = server PID
  for _ in $(seq 1 120); do
    curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"' && return 0
    kill -0 "$1" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}

run_one() { # $1 = n-max
  local N="$1" logf="$REPO_DIR/.run/fable-fusion-mtp-${QUANT}-n${1}.log"
  echo ">>> n-max=$N (ctx=$CTX) launching..."
  "$LS" -m "$MODEL" -a sweep -ngl 99 -c "$CTX" -np 1 --kv-unified \
    -ctk q4_0 -ctv q4_0 -ctkd q4_0 -ctvd q4_0 \
    --spec-type draft-mtp --spec-draft-n-max "$N" --spec-draft-p-min 0.0 \
    -fa on -ngld 99 --host 127.0.0.1 --port "$PORT" > "$logf" 2>&1 &
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
  acc=$(grep 'draft acceptance' "$logf" | tail -1 | sed -E 's/.*draft acceptance = ([0-9.]+).*mean acceptance length = *([0-9.]+).*/acc=\1 meanlen=\2/')
  printf 'n=%-2s  decode=%-7s tok/s  %-28s VRAM=%s MiB\n' "$N" "${toks:-?}" "${acc:-acc=?}" "${vram:-?}" | tee -a "$RESULTS"
  kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
  sleep 2
}

echo "=== Fable-Fusion MTP profile: $QUANT  (model: $(basename "$MODEL"), ctx=$CTX) ==="
for N in 1 2 4 6 8 10; do run_one "$N"; done
echo ""
echo "=== SWEEP COMPLETE — $RESULTS ==="
cat "$RESULTS"
echo ""
echo "Pick the n with the highest decode tok/s that fits VRAM (leave ~2 GB"
echo "headroom), set --spec-draft-n-max to it in scripts/serve-fable-fusion-27b-mtp-${QUANT}.sh,"
echo "and use scripts/measure-vram.sh to set the largest safe -c. Restart: ./start.sh -d"
echo "Per DavidAU: if the best acc= stays below ~0.50, prefer the non-MTP Q${QUANT#q} build."
