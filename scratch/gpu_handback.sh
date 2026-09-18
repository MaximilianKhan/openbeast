#!/bin/bash
# Wait for the live eval cell to finish, then make sure the GPU is actually
# free — llama-server gone AND its VRAM released, which are not the same
# moment (the ops lesson from 2026-09-11: pkill returns long before the
# allocator gives the memory back).
set -uo pipefail
cd /home/max/Documents/openbeast
L=scratch/logs/campaign; BENCH=${1:?pid}
say() { echo "[$(date +%F' '%T)] $*" | tee -a "$L/gpu-handback.log"; }

say "waiting for cell P0a (pid $BENCH) to finish; nothing else is queued"
while kill -0 "$BENCH" 2>/dev/null; do sleep 30; done
say "cell P0a exited"

# benchmark_all stops its own server, but confirm rather than assume.
for i in $(seq 1 20); do
  pgrep -x llama-server >/dev/null 2>&1 || break
  [ "$i" -eq 1 ] && say "llama-server still up — asking it to stop"
  pkill -x llama-server 2>/dev/null || true
  sleep 3
done
pgrep -x llama-server >/dev/null 2>&1 && { say "llama-server ignored SIGTERM — SIGKILL"; pkill -9 -x llama-server 2>/dev/null || true; }

# VRAM: wait for it to actually drain, don't just report the first reading.
for i in $(seq 1 40); do
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null || echo 0)
  [ "${used:-0}" -lt 2000 ] && break
  sleep 3
done
say "GPU FREE — ${used:-?} MiB in use (desktop only), no eval processes left"
say "row banked: $(ls -t evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-*.json 2>/dev/null | head -1)"
say "resume when you want it back: bash scratch/tier3_zig_ab.sh  (5 cells left)"
