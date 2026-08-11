#!/usr/bin/env bash
# E27: score one config vs the verified BF16 truth logits (20 chunks).
# usage: measure.sh <label> <model.gguf> [--lora adapter.gguf]
# One run yields PPL20 + KLD + top-1 with printed stderr AND the
# per-chunk cumulative rows that paired_stats.py consumes (R1 repair).
set -euo pipefail
cd /home/max/Documents/openbeast
E27=research/lowrank/experiments/27-bf16-rederivation
label="$1"; model="$2"; shift 2
log="$E27/kld-$label.log"
llama.cpp/build/bin/llama-perplexity \
  -m "$model" "$@" \
  -f research/lowrank/data/wikitext-2-raw/wiki.test.raw \
  --kl-divergence \
  --kl-divergence-base research/lowrank/data/bf16ref27b-20.logits \
  -ngl 99 -c 512 --no-warmup > "$log" 2>&1
bytes=$(stat -c%s "$model")
{ printf "%s bytes=%s " "$label" "$bytes"
  grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Same top p" "$log" | tail -1 | tr -s ' '
} >> "$E27/results.txt"
tail -1 "$E27/results.txt"
