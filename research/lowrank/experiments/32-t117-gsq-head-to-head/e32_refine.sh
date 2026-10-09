#!/bin/bash
# E32 step (c): our arm — whitened r128-Q8 correction adapters on the
# SHIPPED Unsloth baselines, using the fresh 3.8 BF16 Grams (E27 recipe:
# rsvd + compute32 + q8 factors). Each ~5-15 min CPU.
set -uo pipefail
OB=/home/max/Documents/openbeast
S=$OB/weights/research-staging
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
X=$OB/research/lowrank/experiments/04-served-v0/extract_adapter.py
REF=$S/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf
IM=$OB/research/lowrank/data/imatrix-38-bf16.gguf
GD=$OB/research/lowrank/data/gram38-bf16
cd $OB
for base in UD-IQ2_S UD-Q2_K_XL UD-IQ3_S; do
  out=$E32/adapter-fc-r128q8-$base.gguf
  log=$E32/extract-$base.log
  echo "[$(date +%T)] extracting $base"
  python3 "$X" "$REF" "$S/Qwen3.8-27B-$base.gguf" "$IM" \
    --gram-dir "$GD" --rank 128 --rsvd --compute32 --q8-factors \
    -o "$out" > "$log" 2>&1 \
    && echo "[$(date +%T)] OK $base: $(ls -la "$out" | awk '{print $5}') bytes" \
    || echo "[$(date +%T)] FAIL $base rc=$? (see $log)"
done
echo "extractions done"
