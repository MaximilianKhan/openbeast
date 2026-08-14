#!/usr/bin/env bash
# T1.8 (pre-registered JOURNAL 2026-08-11): the campaign's FIRST task
# eval. Full HellaSwag val set (10042 tasks — full set => identical
# tasks per config) on the flagship trio. Sequential, resume-safe.
set -uo pipefail
cd "$(dirname "$0")"
E27=/home/max/Documents/openbeast/research/lowrank/experiments/27-bf16-rederivation
DATA=/home/max/Documents/openbeast/research/lowrank/data/hellaswag_val_full.txt
BIN=/home/max/Documents/openbeast/llama.cpp/build/bin/llama-perplexity
run() { # label model [extra]
  local label="$1"; shift
  local log="hswag-$label.log"
  if [ -f "$log" ] && grep -qE "10042|hellaswag: final" "$log"; then
    echo "SKIP $label"; return
  fi
  echo "=== hellaswag $label $(date +%H:%M:%S)"
  $BIN -m "$@" -f "$DATA" --hellaswag --hellaswag-tasks 10042 \
    -ngl 99 -c 2048 --no-warmup > "$log" 2>&1 || echo "FAILED $label"
  tail -2 "$log"
}
run MIXEDfc "$E27/h27bf16-MIXED.gguf" --lora "$E27/mixed-fc-r128q8-bf16.gguf"
run Q3_K_S  "$E27/h27bf16-Q3_K_S.gguf"
run Q3_K_M  "$E27/h27bf16-Q3_K_M.gguf"
echo "T1.8 DONE $(date +%H:%M:%S)"
