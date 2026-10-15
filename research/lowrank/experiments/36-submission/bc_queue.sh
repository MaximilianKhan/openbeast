#!/usr/bin/env bash
# E36-B (27B re-rounder, single-step) + E36-C (KLD@2048 for the E32 files).
# PLAN.md §B, §C. GPU steps run in this process; the two CPU re-round sweeps
# and the IQ2_M quantize run in a background CPU lane that overlaps C.
# Resume-safe at every step. Run under scripts/gpu-lease.sh.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
LD=$E36/logs-b; mkdir -p "$LD"
RB=$E36/results-b.txt; RC=$E36/results-c.txt
RES_BIN=$OB/llama.cpp/build-research/bin
STG=$OB/weights/research-staging
E32=$L/experiments/32-t117-gsq-head-to-head
RR=$L/experiments/13-rerounder/e13_reround.py

capture() { # name corpus-file
  local out=$D/$1 lg=$LD/capture-$1.log
  if [ -f "$out/grams.json" ] && grep -q "imatrix" "$lg" 2>/dev/null \
     && [ -s "$out/diag.imatrix.gguf" ]; then log "gram exists: $out"; return 0; fi
  rm -rf "$out"; mkdir -p "$out"
  log "capture $1 from $2"
  LLAMA_IMATRIX_GRAM_DIR="$out" "$RES_BIN/llama-imatrix" -m "$BF27" -f "$2" \
    --chunks 48 -ngl 20 -c 512 -o "$out/diag.imatrix.gguf" > "$lg" 2>&1 \
    || { log "CAPTURE FAILED $1"; return 1; }
  log "capture done: $(ls "$out" | wc -l) files"
}

# ---- B: Gram captures (GPU)
capture gram27b-e36-wiki  "$D/wikitext-2-raw/wiki.train.raw" || exit 1
capture gram27b-e36-mixed "$D/mixed-calib2.txt" || exit 1

# ---- B: CPU lane in the background (quantize IQ2_M, two re-round sweeps)
cpu_lane() {
  local q2=$E27/h27bf16-Q2_K.gguf
  local iq2m=$E36/h27bf16-IQ2_M.gguf
  if [ ! -s "$iq2m" ]; then
    log "quantize IQ2_M"
    "$BIN/llama-quantize" --imatrix "$D/gram27b-bf16/diag.imatrix.gguf" \
      --tensor-type 'blk\.64\.=q5_k' "$BF27" "$iq2m.part" IQ2_M 28 \
      > "$LD/quantize-IQ2_M.log" 2>&1 && mv "$iq2m.part" "$iq2m"
  fi
  for arm in wiki mixed; do
    local out=$E36/h27bf16-Q2K-rr-$arm.gguf
    [ -s "$out" ] && { log "exists $out"; continue; }
    log "re-round $arm"
    python3 "$RR" "$BF27" "$q2" "$D/gram27b-e36-$arm" -o "$out.part" \
      --codes-dir "$E36/codes27b-$arm" > "$LD/reround-$arm.log" 2>&1 \
      && mv "$out.part" "$out" || log "REROUND FAILED $arm"
  done
  log "CPU lane done"
}
cpu_lane > "$LD/cpu-lane.log" 2>&1 &
CPU_PID=$!

# ---- C: KLD at a 2048-token context (GPU, overlaps the CPU lane)
REF38=$D/e36-bf16ref38-wiki-2048x40.logits
gen_ref "$STG/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf" wiki "$REF38" 40 2048 20 \
  "$LD/gen-ref38-2048.log" || exit 1
c38() { score "$RC" "$LD" ctx2048 wiki "$REF38" 2048 "$@"; }
c38 unsloth-ud-iq2s  "$STG/Qwen3.8-27B-UD-IQ2_S.gguf"
c38 gsq-rco-iq2xs    "$STG/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf"
c38 unsloth-ud-iq3s  "$STG/Qwen3.8-27B-UD-IQ3_S.gguf"
c38 gsq-rco-iq3s     "$STG/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf"
c38 unsloth-ud-q2kxl "$STG/Qwen3.8-27B-UD-Q2_K_XL.gguf"
c38 ours-iq2s-fc128  "$STG/Qwen3.8-27B-UD-IQ2_S.gguf"    --lora "$E32/adapter-fc-r128q8-UD-IQ2_S.gguf"
c38 ours-iq3s-fc128  "$STG/Qwen3.8-27B-UD-IQ3_S.gguf"    --lora "$E32/adapter-fc-r128q8-UD-IQ3_S.gguf"
c38 ours-q2kxl-fc128 "$STG/Qwen3.8-27B-UD-Q2_K_XL.gguf"  --lora "$E32/adapter-fc-r128q8-UD-Q2_K_XL.gguf"
log "E36-C DONE"

# ---- B: score the new files once the CPU lane finishes
wait "$CPU_PID"
for c in wiki fw code; do
  ref="$D/e36-bf16ref27b-$c-100.logits"
  score "$RB" "$E36/logs-a" "$c" "$c" "$ref" 512 IQ2_M       "$E36/h27bf16-IQ2_M.gguf"
  score "$RB" "$E36/logs-a" "$c" "$c" "$ref" 512 Q2Krr-wiki  "$E36/h27bf16-Q2K-rr-wiki.gguf"
  score "$RB" "$E36/logs-a" "$c" "$c" "$ref" 512 Q2Krr-mixed "$E36/h27bf16-Q2K-rr-mixed.gguf"
done
log "E36-B DONE"
