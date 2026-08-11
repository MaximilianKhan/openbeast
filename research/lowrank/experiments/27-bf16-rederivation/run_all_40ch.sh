#!/usr/bin/env bash
# T1.16 driver: six sequential 40-chunk evals vs bf16ref27b-40.logits.
# Skips labels whose kld40-<label>.log already carries a final KLD line,
# so it is resume-safe.
set -uo pipefail
E27=/home/max/Documents/openbeast/research/lowrank/experiments/27-bf16-rederivation
EXP=/home/max/Documents/openbeast/research/lowrank/experiments
done_label() { grep -q "Mean    KLD" "$E27/kld40-$1.log" 2>/dev/null; }
run() { # label model [extra args]
  local label="$1"; shift
  if done_label "$label"; then echo "SKIP $label (done)"; return 0; fi
  echo "RUN $label $(date +%H:%M:%S)"
  "$E27/measure40.sh" "$label" "$@" || { echo "FAIL $label"; exit 1; }
}
run MIXEDfc        "$E27/h27bf16-MIXED.gguf" --lora "$E27/mixed-fc-r128q8-bf16.gguf"
run CONTROL        "$E27/h27bf16-CONTROL.gguf"
run Q3_K_S         "$E27/h27bf16-Q3_K_S.gguf"
run Q3_K_M         "$E27/h27bf16-Q3_K_M.gguf"
run Q2Krr-legacy   "$EXP/13-rerounder/heretic27b-Q2K-rr.gguf"
run Q2Kbare-legacy "$EXP/04b-27b/heretic27b-Q2_K.gguf"
echo "ALL-SIX-DONE $(date +%H:%M:%S)"
