#!/usr/bin/env bash
# E29 part 2 driver: carve+deflate (python) then quantize (MIXED recipe,
# same imatrix + MTP pin as build_bases.sh).
set -euo pipefail
cd /home/max/Documents/openbeast
E29=research/lowrank/experiments/29-srr-split
OMP_NUM_THREADS=24 python3 "$E29/e29_build.py"
echo "=== quantize $(date +%H:%M:%S)"
llama.cpp/build/bin/llama-quantize \
  --imatrix research/lowrank/data/gram27b-bf16/diag.imatrix.gguf \
  --tensor-type 'blk\.64\.=q5_k' \
  --tensor-type 'ffn_gate=q3_k' --tensor-type 'ffn_up=q3_k' \
  --tensor-type 'ffn_down=q3_k' \
  "$E29/h27bf16-DEFLATED.gguf" "$E29/srr-MIXED.gguf.part" Q2_K 28 \
  > "$E29/quantize-srr.log" 2>&1
mv "$E29/srr-MIXED.gguf.part" "$E29/srr-MIXED.gguf"
echo "=== E29 BUILD DONE $(date +%H:%M:%S) $(stat -c%s "$E29/srr-MIXED.gguf") bytes"
