#!/usr/bin/env bash
# E36-E (CPU lane): free-grid GPTQ on the 27B Q2_K with the §B mixed Grams.
# Waits for the capture bc_queue.sh makes; CPU only (no GPU, no lease).
# imatrix = the base file's (E27) so the grid fit matches e13's frozen grid.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
G=$D/gram27b-e36-mixed
until [ -f "$G/grams.json" ] && [ -s "$G/diag.imatrix.gguf" ]; do sleep 60; done
sleep 30
export CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=12 OPENBLAS_NUM_THREADS=12 MKL_NUM_THREADS=12
cd "$E36/freegrid"
out=h27bf16-Q2K-freegrid-mixed.gguf
[ -s "$out" ] && { log "exists $out"; exit 0; }
log "free-grid 27B mixed start"
python3 e36_freegrid.py "$BF27" "$E27/h27bf16-Q2_K.gguf" "$G" \
  --imatrix "$D/gram27b-bf16/diag.imatrix.gguf" --codes-dir codes-27b-mixed \
  -o "$out.part" && mv "$out.part" "$out" && log "free-grid 27B mixed done"
