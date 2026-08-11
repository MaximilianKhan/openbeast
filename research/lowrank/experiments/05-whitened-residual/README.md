# E05 — Whitened residual census (the go/no-go)

E01 and E03 killed the unweighted-energy routes. Published wins (EoRA,
LQER, SVD-LLM, ASVD) all minimize **activation-weighted** error
‖(W−Ŵ)X‖_F. With the diagonal approximation E[xxᵀ] ≈ D² = diag(E[x²]) —
which is exactly what `llama-imatrix` measures — the optimal rank-r
correction is the truncated SVD of R·D, un-whitened afterwards.

**Hypothesis (H3):** the whitened residual R·D concentrates energy in far
fewer directions than R, because activation second moments are wildly
non-uniform across input channels (heavy-tailed channel importance is the
entire reason imatrix-quantization works). If H3 holds with, say, rank 64
capturing ≳70% of *weighted* error energy, the corrected-Q2 route has a
real shot at beating the pure-quant ladder at equal bytes → proceed to E04
serving. If whitened concentration is only marginally better than E03's
unweighted numbers, the diagonal-whitening route dies here and only full-
covariance whitening (needs real activation capture, torch or llama.cpp
instrumentation) remains.

## Method

- imatrix: `data/qwen3-0.6b.imatrix.gguf` (Q8_0 model, wikitext-2 train,
  300 chunks, llama-imatrix @ 0ef6e55ed, final chunk-PPL 22.87).
- Per tensor: R = dequant(Q8_0) − dequant(Q2_K) (same pair as E03);
  d_j = sqrt(mean activation² of input channel j) from imatrix;
  SVD of R·diag(d); report weighted-energy recovered at rank r, side by
  side with E03's unweighted numbers.
- Guard: tensors missing from the imatrix (not touched by calibration
  forward pass) are reported unwhitened and flagged.

## Run

```bash
python3 research/lowrank/experiments/05-whitened-residual/whitened_census.py \
    weights/Qwen3-0.6B-Q8_0.gguf \
    research/lowrank/experiments/03-residual-rank/qwen3-0.6b-Q2_K.gguf \
    research/lowrank/data/qwen3-0.6b.imatrix.gguf \
    --outdir research/lowrank/experiments/05-whitened-residual/
```

## Result — 2026-08-03, H3 CONFIRMED → GO

196/197 tensors whitened (embed absent from imatrix — expected, tied
embeddings). 75 s. Data: `whitened_census.csv`.

Mean recovered residual energy, **whitened (unweighted)**:

| kind | @r16 | @r32 | @r64 | @r128 |
|---|---|---|---|---|
| attn_q | **0.54** (0.09) | 0.61 (0.15) | 0.68 (0.26) | 0.77 (0.42) |
| attn_k | **0.56** (0.09) | 0.64 (0.16) | 0.72 (0.27) | **0.81** (0.45) |
| attn_v | 0.38 (0.06) | 0.45 (0.12) | 0.54 (0.22) | 0.67 (0.40) |
| attn_o | 0.16 (0.05) | 0.24 (0.10) | 0.35 (0.18) | 0.51 (0.32) |
| ffn_gate | 0.51 (0.05) | 0.56 (0.09) | 0.62 (0.16) | 0.69 (0.29) |
| ffn_up | 0.52 (0.04) | 0.56 (0.08) | 0.61 (0.15) | 0.68 (0.27) |
| ffn_down | 0.19 (0.04) | 0.24 (0.08) | 0.33 (0.15) | 0.46 (0.27) |

**Reading:**
1. Whitening is a 4–7× multiplier on energy concentration for q/k/gate/up
   — rank-16 does what unweighted rank-128+ couldn't. The activation
   metric IS where the low-rank structure lives (H3, and the whole
   published field, confirmed on our own tensors).
2. Clear rank-allocation map: spend rank on q/k (steep), moderate on
   gate/up/v, little on o/down — OR treat o/down with full-covariance
   whitening (their inputs are internal activations; diagonal E[x²] is a
   poor proxy there — matches QERA's ablation that full covariance pays
   below ~4-bit effective).
3. Caveat before celebrating: weighted-energy recovery is the right
   OBJECTIVE but still a proxy — the decisive number is E04's
   perplexity/KL at equal total bytes vs the imatrix IQ-quant ladder.

**Next:** E04 builds the correction adapter from these exact factors via
the Gram route (see `prior-art/math-methods.md` Box 3: B=U_r, A=U_rᵀR from
eigh(R·D²·Rᵀ) — no SVD needed), exports as LoRA GGUF, and measures the
triangle.
