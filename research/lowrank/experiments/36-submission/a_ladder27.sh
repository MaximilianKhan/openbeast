#!/usr/bin/env bash
# E36-A: 27B ladder on the current build, three corpora (see PLAN.md §A).
#   wiki: replication of the paper's n=100 table on the post-fix graph
#   fw, code: held-out corpora (T1.7 / referee 5); calibration stays wiki.train
# Resume-safe. Run under scripts/gpu-lease.sh.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
LD=$E36/logs-a; mkdir -p "$LD"
RES=$E36/results-a.txt
N=100

for c in wiki fw code; do
  gen_ref "$BF27" "$c" "$D/e36-bf16ref27b-$c-$N.logits" $N 512 20 "$LD/gen-ref27b-$c.log" || exit 1
done

configs() { # label model [extra]
  echo "MIXEDfc|$E27/h27bf16-MIXED.gguf|--lora $E27/mixed-fc-r128q8-bf16.gguf"
  echo "MIXEDbare|$E27/h27bf16-MIXED.gguf|"
  echo "Q3_K_S|$E27/h27bf16-Q3_K_S.gguf|"
  echo "CONTROL|$E27/h27bf16-CONTROL.gguf|"
  echo "IQ3_XS|$E27/h27bf16-IQ3_XS.gguf|"
  echo "IQ3_XXS|$E27/h27bf16-IQ3_XXS.gguf|"
  echo "Q3_K_M|$E27/h27bf16-Q3_K_M.gguf|"
  echo "Q2_K|$E27/h27bf16-Q2_K.gguf|"
  echo "Q2Krr-legacy|$L/experiments/13-rerounder/heretic27b-Q2K-rr.gguf|"
}

for c in wiki fw code; do
  ref="$D/e36-bf16ref27b-$c-$N.logits"
  while IFS='|' read -r label model extra; do
    # shellcheck disable=SC2086
    score "$RES" "$LD" "$c" "$c" "$ref" 512 "$label" "$model" $extra
  done < <(configs)
done
log "E36-A DONE"
