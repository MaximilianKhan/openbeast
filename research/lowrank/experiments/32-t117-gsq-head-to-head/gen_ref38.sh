#!/usr/bin/env bash
# T1.17 step (a): Qwen3.8-27B BF16 reference logits, 40 chunks (PROTOCOL #3).
# Pattern = E27's gen (measured ~15 s/pass with partial offload on the 5090).
# Absolute paths — runs from anywhere; artifacts land in main-tree data/
# (gitignored) beside the E27 logits.
set -euo pipefail
OB=/home/max/Documents/openbeast
OUT=$OB/research/lowrank/data/bf16ref38-40.logits
LOG=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head/gen-bf16ref38-40.log
[ -f "$OUT" ] && { echo "logits exist: $OUT — delete to regenerate"; exit 0; }
$OB/llama.cpp/build/bin/llama-perplexity \
  -m $OB/weights/research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf \
  -f $OB/research/lowrank/data/wikitext-2-raw/wiki.test.raw \
  --save-all-logits "$OUT" \
  -ngl 20 -c 512 --chunks 40 --no-warmup > "$LOG" 2>&1
echo "done: $(ls -la $OUT)"
