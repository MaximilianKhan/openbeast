#!/usr/bin/env bash
# E36 diagnostic B5 (PLAN.md): oracle headroom for re-rounding at 27B.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
O=$E36/h27bf16-Q2K-oracleQ8swept.gguf
if [ ! -s "$O" ]; then
  log "quantize oracle (CPU)"
  nice -n 5 "$BIN/llama-quantize" --imatrix "$D/gram27b-bf16/diag.imatrix.gguf" \
    --tensor-type 'blk\.64\.=q5_k' \
    --tensor-type 'attn_q\.weight=q8_0' --tensor-type 'attn_k\.weight=q8_0' \
    --tensor-type 'attn_gate\.weight=q8_0' --tensor-type 'ssm_out\.weight=q8_0' \
    --tensor-type 'ffn_gate\.weight=q8_0' --tensor-type 'ffn_up\.weight=q8_0' \
    "$BF27" "$O.part" Q2_K 16 > "$E36/logs-b/quantize-oracle.log" 2>&1 && mv "$O.part" "$O"
fi
[ "${1:-}" = "--build-only" ] && exit 0
for c in wiki fw code; do
  score "$E36/results-b.txt" "$E36/logs-a" "$c" "$c" "$D/e36-bf16ref27b-$c-100.logits" 512 Q2K-oracleQ8swept "$O"
done
log "B5 DONE"
