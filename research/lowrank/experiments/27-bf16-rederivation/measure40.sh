#!/usr/bin/env bash
# E27 / ABLATION-PLAN T1.16: score one config vs the 40-chunk BF16 truth
# logits (data/bf16ref27b-40.logits). Same pattern as measure.sh; logs go
# to kld40-<label>.log, summary rows append to results-40ch.txt.
# usage: measure40.sh <label> <model.gguf> [--lora adapter.gguf]
set -euo pipefail
cd /home/max/Documents/openbeast
E27=research/lowrank/experiments/27-bf16-rederivation
label="$1"; model="$2"; shift 2
log="$E27/kld40-$label.log"
llama.cpp/build/bin/llama-perplexity \
  -m "$model" "$@" \
  -f research/lowrank/data/wikitext-2-raw/wiki.test.raw \
  --kl-divergence \
  --kl-divergence-base research/lowrank/data/bf16ref27b-40.logits \
  -ngl 99 -c 512 --no-warmup > "$log" 2>&1
bytes=$(stat -c%s "$model")
{ printf "%s bytes=%s " "$label" "$bytes"
  grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Same top p" "$log" | tail -1 | tr -s ' '
} >> "$E27/results-40ch.txt"
tail -1 "$E27/results-40ch.txt"
