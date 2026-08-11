#!/usr/bin/env bash
# E27 / ABLATION-PLAN T1.3+T1.5: score one config vs the 100-chunk BF16
# truth logits (data/bf16ref27b-100.logits, generated + verified
# 2026-08-11, JOURNAL pre-registration 15:10). Same pattern as
# measure40.sh; logs go to kld100-<label>.log, rows to results-100ch.txt.
# usage: measure100.sh <label> <model.gguf> [--lora adapter.gguf]
set -euo pipefail
cd /home/max/Documents/openbeast
E27=research/lowrank/experiments/27-bf16-rederivation
label="$1"; model="$2"; shift 2
log="$E27/kld100-$label.log"
llama.cpp/build/bin/llama-perplexity \
  -m "$model" "$@" \
  -f research/lowrank/data/wikitext-2-raw/wiki.test.raw \
  --kl-divergence \
  --kl-divergence-base research/lowrank/data/bf16ref27b-100.logits \
  -ngl 99 -c 512 --no-warmup > "$log" 2>&1
bytes=$(stat -c%s "$model")
{ printf "%s bytes=%s " "$label" "$bytes"
  grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Same top p" "$log" | tail -1 | tr -s ' '
} >> "$E27/results-100ch.txt"
tail -1 "$E27/results-100ch.txt"
