#!/usr/bin/env bash
# E36 diagnostic B6 (PLAN.md): single-step base re-rounded with pre-fix E27 BF16 Grams.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
O=$E36/h27bf16-Q2K-rr-oldgram.gguf
if [ ! -s "$O" ]; then
  log "re-round with pre-fix Grams (CPU)"
  nice -n 5 python3 "$L/experiments/13-rerounder/e13_reround.py" "$BF27" "$E27/h27bf16-Q2_K.gguf" \
    "$D/gram27b-bf16" -o "$O.part" --codes-dir "$E36/codes27b-oldgram" \
    > "$E36/logs-b/reround-oldgram.log" 2>&1 && mv "$O.part" "$O" && rm -rf "$E36/codes27b-oldgram"
fi
for c in wiki fw code; do
  score "$E36/results-b.txt" "$E36/logs-a" "$c" "$c" "$D/e36-bf16ref27b-$c-100.logits" 512 Q2Krr-oldgram "$O"
done
log "B6 DONE"
