# E03 — Is quantization error low-rank?

**The load-bearing question of beast-rank.** E01 killed plain truncated SVD
(W is energy-full-rank). The surviving route stores an aggressively
quantized base Q plus a low-rank correction of the residual R = W −
dequant(Q). That only works if R concentrates its energy in few directions.

**Hypothesis (H2):** the quantization residual of a low-bit K-quant is
closer to isotropic noise than to a low-rank signal — BUT (H2b, the EoRA/
LQER claim) a small-rank correction still recovers a *disproportionate*
share of the FUNCTIONAL damage, because quant error is correlated with
weight magnitude structure. E03 measures the unweighted half (energy);
functional recovery is E04's perplexity job.

## Method

1. `llama-quantize` the Q8_0 lab rat → Q2_K (and Q4_K_M for a milder
   comparison point). Q8_0 stands in for F16 reference (its own error is
   ~0.1% RMS, far below Q2 error).
2. Per 2D tensor: R = dequant(Q8_0) − dequant(Q2_K), economy SVD of R.
3. Report: residual RMS relative to weight RMS; r90/r95 of R's energy;
   and the **bytes ledger** — effective bits-per-weight of
   `Q2_K + rank-r F16 factors` at r ∈ {16, 32, 64, 128} vs Q4_K_M/Q6_K,
   i.e. what VRAM a corrected-Q2 model would actually cost.

## Run

```bash
llama.cpp/build/bin/llama-quantize weights/Qwen3-0.6B-Q8_0.gguf \
    research/lowrank/experiments/03-residual-rank/qwen3-0.6b-Q2_K.gguf Q2_K
python3 research/lowrank/experiments/03-residual-rank/residual_census.py \
    weights/Qwen3-0.6B-Q8_0.gguf \
    research/lowrank/experiments/03-residual-rank/qwen3-0.6b-Q2_K.gguf \
    --outdir research/lowrank/experiments/03-residual-rank/
```

## Result — 2026-08-03, H2 CONFIRMED (residual is near-isotropic)

197 tensors, 39 s. Data: `residual_census.csv`, `residual_spectra.npz`.

- Residual r90 ≈ 62% of full rank on average — quantization error carries
  even LESS spectral structure than W itself (E01: ~49–78%). It behaves
  like near-isotropic round-off noise, as theory predicts.
- Energy recovered by an unweighted rank-r SVD correction of R: r=64 →
  15–26%; r=128 → 26–44%. Weak.
- relRMSE split is bimodal: ~0.15 (attn_v, attn_o, ffn_down — tensors
  llama-quantize's Q2_K heuristics give extra bits) vs ~0.30 (q/k/gate/up).
- **Bytes ledger:** Q2_K+r32 = 3.24 bpw (2.03× vs Q6_K); Q2_K+r128 =
  5.26 bpw — already COSTLIER than Q4_K (4.50). An unweighted correction
  can't win at equal bytes.

**Consequence:** the unweighted-energy framing is now dead at BOTH ends
(E01: W not low-rank; E03: R not low-rank). Everything rides on
activation-weighted error — published wins (EoRA/LQER/SVD-LLM) minimize
‖(W−Ŵ)X‖, not ‖W−Ŵ‖. Functional damage concentrates in the directions
inputs actually excite, which unweighted energy understates.

**The bridge llama.cpp hands us:** `llama-imatrix` computes exactly
diag(E[x²]) per weight column — the diagonal of the input covariance. That
enables ASVD-style DIAGONAL whitening with no torch and no new
instrumentation: SVD of R·D^{1/2} instead of R. → E05 (whitened residual
correction) is promoted to the critical path; E04 (served v0) should use
the whitened factors from day one. The honest baseline also sharpens:
compare against IMATRIX-weighted IQ quants at equal bytes, since llama.cpp
already spends activation information at quantization time.
