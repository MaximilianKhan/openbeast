#!/usr/bin/env bash
# T1.17 step (b): full activation-Gram capture on Qwen3.8-27B BF16.
# Research build only (b10866 = b10865 + beast-rank patch); the Gram
# accumulator is env-gated via LLAMA_IMATRIX_GRAM_DIR. E27 pattern:
# 48 chunks wikitext-train, PSD/symmetry/count gates checked after.
set -euo pipefail
OB=/home/max/Documents/openbeast
OUTDIR=$OB/research/lowrank/data/gram38-bf16
LOG=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head/capture-gram38.log
mkdir -p "$OUTDIR"
LLAMA_IMATRIX_GRAM_DIR="$OUTDIR" \
$OB/llama.cpp/build-research/bin/llama-imatrix \
  -m $OB/weights/research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf \
  -f $OB/research/lowrank/data/wikitext-2-raw/wiki.train.raw \
  --chunks 48 -ngl 20 -c 512 \
  -o $OB/research/lowrank/data/imatrix-38-bf16.gguf > "$LOG" 2>&1
echo "done: $(ls "$OUTDIR" | wc -l) gram files, imatrix $(ls -la $OB/research/lowrank/data/imatrix-38-bf16.gguf | awk '{print $5}') bytes"
