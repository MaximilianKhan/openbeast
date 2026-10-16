#!/usr/bin/env bash
# E36-E: free-grid arms at 0.8B (wiki and 2:1 mixed Grams from d_08b) and
# the 27B mixed arm (built by e_freegrid27_cpu.sh). Builds the 0.8B arms on
# CPU (69 s each), then scores everything. Run under the lease after d_08b.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
A=$E36/d08b/art; LD8=$E36/logs-d; R8=$E36/results-d.txt
BF08=$OB/weights/research-staging/Qwen3.5-0.8B-BF16.gguf
FG=$E36/freegrid/e36_freegrid.py
export OMP_NUM_THREADS=12 OPENBLAS_NUM_THREADS=12 MKL_NUM_THREADS=12
for g in wiki mix2; do
  out=$A/q2k-freegrid-$g.gguf; gd=$D/e36-gram08b-$g
  [ -s "$out" ] || CUDA_VISIBLE_DEVICES="" python3 "$FG" "$BF08" "$A/q2k.gguf" "$gd" \
     --imatrix "$D/e36-gram08b-wiki/diag.imatrix.gguf" --codes-dir "$A/codes-fg-$g" \
     -o "$out.part" > "$LD8/freegrid-$g.log" 2>&1 && mv "$out.part" "$out"
  for c in wiki fw code; do
    score "$R8" "$LD8" "$c" "$c" "$D/e36-bf16ref08b-$c-40.logits" 512 "Q2K-freegrid-$g" "$out"
  done
done
F27=$E36/freegrid/h27bf16-Q2K-freegrid-mixed.gguf
until [ -s "$F27" ]; do log "waiting for $F27"; sleep 300; done
for c in wiki fw code; do
  score "$E36/results-b.txt" "$E36/logs-a" "$c" "$c" "$D/e36-bf16ref27b-$c-100.logits" 512 Q2Kfreegrid-mixed "$F27"
done
log "E36-E DONE"
