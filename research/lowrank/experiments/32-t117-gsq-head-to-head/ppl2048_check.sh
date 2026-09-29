#!/usr/bin/env bash
# Card-vs-us PPL SIGN discrepancy check (pre-registered 2026-09-11): their
# card reports wikitext PPL GSQ-RCO < UD at both rungs (7.69<8.02, 7.07<7.16);
# our c=512 KLD-run PPLs show UD < GSQ-RCO at both (6.56<6.68, 6.006<6.027).
# Candidate cause: context length (ours 512; lm-eval-style 2048+). Measure
# full wiki.test PPL at -c 2048 for the four artifacts.
set -uo pipefail
OB=/home/max/Documents/openbeast; S=$OB/weights/research-staging
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
OUT=$E32/results-ppl2048.txt; : > $OUT
for pr in "gsq-rco-iq2xs GSQ-RCO-IQ2_XS" "unsloth-ud-iq2s UD-IQ2_S" "gsq-rco-iq3s GSQ-RCO-IQ3_S" "unsloth-ud-iq3s UD-IQ3_S"; do set -- $pr
  $OB/llama.cpp/build/bin/llama-perplexity -m $S/Qwen3.8-27B-$2.gguf -f $OB/research/lowrank/data/wikitext-2-raw/wiki.test.raw -ngl 99 -c 2048 --no-warmup > $E32/ppl2048-$1.log 2>&1
  echo "$1 c=2048 $(grep -oE 'Final estimate: PPL = [0-9.]+ \+/- [0-9.]+' $E32/ppl2048-$1.log)" | tee -a $OUT
done
echo "[$(date +%F' '%T)] ppl2048 check complete"
