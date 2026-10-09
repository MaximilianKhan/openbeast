#!/usr/bin/env bash
# E16 rung 1 (0.8B): whitened low-rank correction on a SELF-QUANTIZED
# NVFP4 base — the first whitened-residual-on-frozen-NVFP4 LLM datapoint
# under PROTOCOL ground-up provenance (all bases one quantize step from
# BF16 with the gram08b diagonal imatrix; BF16 truth logits, 40 chunks).
# usage: e16_08b.sh prep     (CPU: quantize NVFP4 base + ladder, extract adapter)
#        e16_08b.sh measure  (GPU: KLD table + paired stats)
set -uo pipefail
OB=/home/max/Documents/openbeast; D=$OB/research/lowrank/data; cd $OB
ED=$OB/research/lowrank/experiments/34-e16-nvfp4-08b          # artifacts (gitignored tree)
EDR=$OB/../openbeast-research/research/lowrank/experiments/34-e16-nvfp4-08b
Q=$OB/llama.cpp/build/bin/llama-quantize; PPL=$OB/llama.cpp/build/bin/llama-perplexity
X=$OB/../openbeast-research/research/lowrank/experiments/04-served-v0/extract_adapter.py
PS=$OB/../openbeast-research/research/lowrank/experiments/24-yaqa-lite/paired_stats.py
BF=$OB/weights/research-staging/Qwen3.5-0.8B-BF16.gguf; IM=$D/gram08b/diag.imatrix.gguf
W=$OB/weights/research-staging/e16-08b; mkdir -p $W
export PYTHONPATH=$OB/llama.cpp/gguf-py
# blk.24 = the MTP/nextn layer: no imatrix rows → pin q5_k (E22 convention). FIRST match wins in llama-quantize, so the pin precedes the kind overrides.
PIN=(--tensor-type "blk\.24\.=q5_k")
NV=(attn_qkv attn_gate ssm_out ffn_up ffn_gate ffn_down)   # all weight-matrix kinds → nvfp4 (norms/ssm_alpha/beta/embd/output at the carrier default)
prep() {
  echo "[$(date +%T)] quantize NVFP4-all base"
  args=("${PIN[@]}"); for k in "${NV[@]}"; do args+=(--tensor-type "$k=nvfp4"); done
  $Q --imatrix $IM "${args[@]}" $BF $W/Qwen3.5-0.8B-NVFP4all.gguf q4_k_m > $ED/quant-nvfp4all.log 2>&1 || echo "NVFP4 quantize rc=$?"
  grep -c "nvfp4" $ED/quant-nvfp4all.log | xargs echo "  nvfp4 overrides applied:"
  for t in Q3_K_M IQ3_XXS IQ3_XS IQ3_S Q4_K_S Q4_K_M IQ4_XS Q4_0 Q5_K_S Q5_K_M Q6_K; do
    [ -f $W/Qwen3.5-0.8B-$t.gguf ] || $Q --imatrix $IM "${PIN[@]}" $BF $W/Qwen3.5-0.8B-$t.gguf $t > $ED/quant-$t.log 2>&1 || echo "$t rc=$?"
  done
  ls -la $W | awk '{print $5, $9}' | tee $ED/bytes.txt
  echo "[$(date +%T)] extract fc-r128q8 adapter on NVFP4 base (gram08b whitening)"
  python3 $X $BF $W/Qwen3.5-0.8B-NVFP4all.gguf $IM --gram-dir $D/gram08b --rank 128 --rsvd --compute32 --q8-factors \
    -o $ED/adapter-nvfp4all-fc-r128q8.gguf > $ED/extract-nvfp4all.log 2>&1 && echo "  adapter: $(stat -c%s $ED/adapter-nvfp4all-fc-r128q8.gguf) bytes" || echo "EXTRACT FAILED rc=$? (see $ED/extract-nvfp4all.log)"
  grep "captured" $ED/extract-nvfp4all.log | awk '{s+=$NF; n++} END {printf "  mean whitened-energy captured r=128: %.3f over %d tensors\n", s/n, n}'
  # reference K-quant capture at the same rank for the capture-vs-base comparison (E16-27B: NVFP4 0.52 vs Q2_K 0.36)
  python3 $X $BF $W/Qwen3.5-0.8B-Q3_K_M.gguf $IM --gram-dir $D/gram08b --rank 128 --rsvd --compute32 --q8-factors \
    -o $ED/adapter-q3km-fc-r128q8.gguf > $ED/extract-q3km.log 2>&1 && grep "captured" $ED/extract-q3km.log | awk '{s+=$NF; n++} END {printf "  Q3_K_M-base capture r=128: %.3f over %d tensors\n", s/n, n}'
  echo "[$(date +%T)] prep done"
}
measure() {
  OUT=$ED/results-e16-08b.txt; : > $OUT
  m() { local label="$1" model="$2"; shift 2; local log="$ED/kld40-$label.log"
    $PPL -m "$model" "$@" -f $D/wikitext-2-raw/wiki.test.raw --kl-divergence --kl-divergence-base $D/bf16ref08b-40.logits -ngl 99 -c 512 --chunks 40 --no-warmup > "$log" 2>&1
    { printf "%s bytes=%s " "$label" "$(stat -c%s "$model")"; grep -E "Mean PPL\(Q\) " "$log" | head -1 | tr -s ' ' | tr -d '\n'; printf " "
      grep -E "Mean    KLD" "$log" | tr -s ' ' | tr -d '\n'; printf " "; grep -E "Same top p" "$log" | tail -1 | tr -s ' '; } >> $OUT; echo "[$(date +%T)] $(tail -1 $OUT)"; }
  m nvfp4all-bare $W/Qwen3.5-0.8B-NVFP4all.gguf
  m nvfp4all-fc128 $W/Qwen3.5-0.8B-NVFP4all.gguf --lora $ED/adapter-nvfp4all-fc-r128q8.gguf
  m q3km-fc128 $W/Qwen3.5-0.8B-Q3_K_M.gguf --lora $ED/adapter-q3km-fc-r128q8.gguf
  for t in Q3_K_M IQ3_XXS IQ3_XS IQ3_S Q4_K_S Q4_K_M IQ4_XS Q4_0 Q5_K_S Q5_K_M Q6_K; do m $(echo $t | tr 'A-Z' 'a-z') $W/Qwen3.5-0.8B-$t.gguf; done
  m vendor-ud-q4kxl $OB/weights/research-staging/Qwen3.5-0.8B-UD-Q4_K_XL.gguf
  P=$ED/paired-e16-08b.txt; : > $P
  pair() { echo "=== $1 vs $2 (A-B) ===" >> $P; python3 $PS kld $ED/kld40-$1.log $ED/kld40-$2.log >> $P 2>&1; }
  pair nvfp4all-fc128 nvfp4all-bare; pair q3km-fc128 q3_k_m
  for t in q4_k_s q4_k_m iq4_xs q4_0 q5_k_s q5_k_m q6_k iq3_s; do pair nvfp4all-fc128 $t; pair nvfp4all-bare $t; done
  echo "[$(date +%T)] measure done → $OUT, $P"
}
case "${1:-}" in prep) prep;; measure) measure;; *) echo "usage: $0 prep|measure"; exit 2;; esac
