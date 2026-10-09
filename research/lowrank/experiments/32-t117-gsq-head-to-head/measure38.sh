#!/usr/bin/env bash
# T1.17 step (d): score one artifact vs the 3.8 BF16 truth logits (40ch).
# usage: measure38.sh <label> <model.gguf> [--lora adapter.gguf ...]
set -euo pipefail
OB=/home/max/Documents/openbeast
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
label="$1"; model="$2"; shift 2
log="$E32/kld40-$label.log"
$OB/llama.cpp/build/bin/llama-perplexity \
  -m "$model" "$@" \
  -f $OB/research/lowrank/data/wikitext-2-raw/wiki.test.raw \
  --kl-divergence \
  --kl-divergence-base $OB/research/lowrank/data/bf16ref38-40.logits \
  -ngl 99 -c 512 --no-warmup > "$log" 2>&1
bytes=$(stat -c%s "$model")
{ printf "%s bytes=%s " "$label" "$bytes"
  grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'
  printf " "
  grep -E "Same top p" "$log" | tail -1 | tr -s ' '
} >> "$E32/results-40ch.txt"
tail -1 "$E32/results-40ch.txt"
