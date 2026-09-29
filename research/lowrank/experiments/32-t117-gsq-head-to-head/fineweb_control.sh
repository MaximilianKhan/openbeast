#!/usr/bin/env bash
# E32 FineWeb-KLD CONTROL (pre-registered 2026-09-10 14:35, hypothesis (a):
# calibration-corpus mismatch — their calibration corpus for the released
# GGUFs is undocumented; FineWeb-Edu is the one non-wikitext corpus their
# card reports on, so it is the control we can run; our truth/eval is wikitext). Re-measures the full KLD
# table on 40 chunks of FineWeb-Edu (sample-10BT, first 400 docs, pulled
# 2026-09-11 via the HF datasets-server) vs a BF16 reference on the SAME
# text. If GSQ-RCO's KLD deficit vs the bare UD baseline shrinks/flips on
# FineWeb, (a) stands; if it persists, (b) task-overfit stands.
set -uo pipefail
OB=/home/max/Documents/openbeast
E32=$OB/research/lowrank/experiments/32-t117-gsq-head-to-head
S=$OB/weights/research-staging
D=$OB/research/lowrank/data
FW=$E32/fineweb-edu-control.txt
REF=$D/bf16ref38-fw40.logits
PPL=$OB/llama.cpp/build/bin/llama-perplexity
PS=$OB/../openbeast-research/research/lowrank/experiments/24-yaqa-lite/paired_stats.py
OUT=$E32/results-fineweb-40ch.txt
cd $OB
echo "[$(date +%F' '%T)] fineweb control start (ref=$REF)"
if [ ! -f "$REF" ]; then
  $PPL -m $S/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf -f "$FW" \
    --save-all-logits "$REF" -ngl 20 -c 512 --chunks 40 --no-warmup \
    > $E32/gen-bf16ref38-fw40.log 2>&1
  echo "[$(date +%T)] ref logits: $(ls -la "$REF" 2>/dev/null | awk '{print $5}') bytes; PPL: $(grep -oE 'Final estimate: PPL = [0-9.]+ \+/- [0-9.]+' $E32/gen-bf16ref38-fw40.log)"
fi
[ -f "$REF" ] || { echo "REF FAILED — abort"; exit 1; }
measure() {
  local label="$1" model="$2"; shift 2
  local log="$E32/kldfw40-$label.log"
  $PPL -m "$model" "$@" -f "$FW" --kl-divergence --kl-divergence-base "$REF" \
    -ngl 99 -c 512 --chunks 40 --no-warmup > "$log" 2>&1
  { printf "%s bytes=%s " "$label" "$(stat -c%s "$model")"
    grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Same top p" "$log" | tail -1 | tr -s ' '; } >> "$OUT"
  echo "[$(date +%T)] $(tail -1 "$OUT")"
}
: > "$OUT"
measure gsq-rco-iq2xs   $S/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf
measure gsq-rco-iq3s    $S/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf
measure unsloth-ud-iq2s $S/Qwen3.8-27B-UD-IQ2_S.gguf
measure unsloth-ud-q2kxl $S/Qwen3.8-27B-UD-Q2_K_XL.gguf
measure unsloth-ud-iq3s $S/Qwen3.8-27B-UD-IQ3_S.gguf
measure ours-iq2s-fc128  $S/Qwen3.8-27B-UD-IQ2_S.gguf   --lora $E32/adapter-fc-r128q8-UD-IQ2_S.gguf
measure ours-q2kxl-fc128 $S/Qwen3.8-27B-UD-Q2_K_XL.gguf --lora $E32/adapter-fc-r128q8-UD-Q2_K_XL.gguf
measure ours-iq3s-fc128  $S/Qwen3.8-27B-UD-IQ3_S.gguf   --lora $E32/adapter-fc-r128q8-UD-IQ3_S.gguf
P=$E32/paired-fineweb-40ch.txt; : > $P
pair() { echo "=== $1 vs $2 (A-B) ===" >> $P; python3 $PS kld $E32/kldfw40-$1.log $E32/kldfw40-$2.log >> $P 2>&1; }
pair gsq-rco-iq2xs unsloth-ud-iq2s;  pair gsq-rco-iq3s unsloth-ud-iq3s
pair ours-iq2s-fc128 gsq-rco-iq2xs;  pair ours-iq3s-fc128 gsq-rco-iq3s
pair ours-iq2s-fc128 unsloth-ud-iq2s; pair ours-q2kxl-fc128 unsloth-ud-q2kxl; pair ours-iq3s-fc128 unsloth-ud-iq3s
pair ours-iq2s-fc128 unsloth-ud-q2kxl
echo "[$(date +%F' '%T)] fineweb control complete → $OUT + $P"
