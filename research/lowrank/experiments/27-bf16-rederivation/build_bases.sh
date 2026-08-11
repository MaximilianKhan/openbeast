#!/usr/bin/env bash
# E27: single-step bases from the BF16 reference (PROTOCOL v2 §1).
# Every base: ONE llama-quantize step from BF16, new 48-chunk BF16
# imatrix, MTP pin blk.64=q5_k. Resume-safe: skips existing outputs.
set -euo pipefail
cd /home/max/Documents/openbeast
Q=llama.cpp/build/bin/llama-quantize
BF16=weights/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf
IM=research/lowrank/data/gram27b-bf16/diag.imatrix.gguf
OUT=research/lowrank/experiments/27-bf16-rederivation
PIN=(--tensor-type 'blk\.64\.=q5_k')
NT=28

build() { # $1 out-name  $2 type  $3.. extra tensor-type args
  local out="$OUT/$1"; shift; local ty="$1"; shift
  if [ -s "$out" ]; then echo "SKIP $out (exists)"; return; fi
  echo "=== building $out ($ty) $(date +%H:%M:%S)"
  $Q --imatrix "$IM" "${PIN[@]}" "$@" "$BF16" "$out.part" "$ty" $NT \
    > "$out.quantize.log" 2>&1
  mv "$out.part" "$out"
  echo "=== done $out $(stat -c%s "$out") bytes"
}

build h27bf16-Q2_K.gguf    Q2_K
build h27bf16-Q3_K_S.gguf  Q3_K_S
build h27bf16-Q3_K_M.gguf  Q3_K_M
build h27bf16-IQ3_XXS.gguf IQ3_XXS
build h27bf16-IQ3_XS.gguf  IQ3_XS
build h27bf16-Q4_K_M.gguf  Q4_K_M
# MIXED (E10 geometry-aware): FFN spends base bits (q3_k), correction
# handles the rest. Q2_K default already promotes ffn_down->Q3_K,
# attn_v/attn_qkv->Q4_K, attn_output->Q3_K (verified vs the E10
# artifact); the explicit overrides make gate/up match.
build h27bf16-MIXED.gguf   Q2_K \
  --tensor-type 'ffn_gate=q3_k' --tensor-type 'ffn_up=q3_k' \
  --tensor-type 'ffn_down=q3_k'
# CONTROL (the ladder's own interpolation at the flagship byte point,
# PROTOCOL §0/§6): Q3_K_S + promotions in Q3_K_M's OWN direction
# (attn_v/attn_output -> q4_k, partial attn_qkv promotion mirroring
# M's per-layer subsets) + attn_k (the campaign's most sensitive kind).
# Predicted 12,496,933,280 tensor-bytes vs flagship 12,498,903,520
# (-0.016%). No correction, pure quantization.
build h27bf16-CONTROL.gguf Q3_K_S \
  --tensor-type 'attn_k=q4_k' --tensor-type 'attn_v=q4_k' \
  --tensor-type 'attn_output=q4_k' \
  --tensor-type 'blk\.0\.attn_qkv=q4_k' \
  --tensor-type 'blk\.12\.attn_qkv=q4_k' \
  --tensor-type 'blk\.25\.attn_qkv=q4_k' \
  --tensor-type 'blk\.37\.attn_qkv=q4_k' \
  --tensor-type 'blk\.50\.attn_qkv=q4_k' \
  --tensor-type 'blk\.62\.attn_qkv=q4_k'
echo "ALL BASES DONE"
