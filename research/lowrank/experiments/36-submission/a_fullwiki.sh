#!/usr/bin/env bash
# E36 amendment A-full: the A ladder's nine configs on the WHOLE wikitext-2
# test set (every 512-token chunk). Resume-safe. Run under gpu-lease.sh.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
LD=$E36/logs-afull; mkdir -p "$LD"
RES=$E36/results-afull.txt
REF=$D/e36-bf16ref27b-wiki-full.logits

gen_ref "$BF27" wiki "$REF" -1 512 20 "$LD/gen-ref27b-wiki-full.log" || exit 1

while IFS='|' read -r label model extra; do
  # shellcheck disable=SC2086
  score "$RES" "$LD" wiki wiki "$REF" 512 "$label" "$model" $extra
done <<CFG
MIXEDfc|$E27/h27bf16-MIXED.gguf|--lora $E27/mixed-fc-r128q8-bf16.gguf
MIXEDbare|$E27/h27bf16-MIXED.gguf|
Q3_K_S|$E27/h27bf16-Q3_K_S.gguf|
CONTROL|$E27/h27bf16-CONTROL.gguf|
IQ3_XS|$E27/h27bf16-IQ3_XS.gguf|
IQ3_XXS|$E27/h27bf16-IQ3_XXS.gguf|
Q3_K_M|$E27/h27bf16-Q3_K_M.gguf|
Q2_K|$E27/h27bf16-Q2_K.gguf|
Q2Krr-legacy|$L/experiments/13-rerounder/heretic27b-Q2K-rr.gguf|
CFG
log "E36 A-full DONE"
