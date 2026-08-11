# Prior art — probability/entropy-driven weight reconstruction at dequant time (E17 charter sweep)

Date: 2026-08-04. Charter: TODO.md "MAX DIRECTIVE E17". Question: can we recover
quality at DEQUANT time — E[w | code, context, prior] instead of the deterministic
grid point — inside the fused CUDA kernel (E14), at zero extra weight bytes?

**Verdict up front:** the lane is real, the math is classical and closed-form, the
kernel path is cheap (a 4-entry `prmt` byte-LUT), and the honest expected magnitude
is **0.1–1% KLD** (top ~3% if 2-bit bins are wide enough for nonlinear centroid
shifts to matter). Two sub-lanes measured DEAD tonight on our own model: neighbor-
context reconstruction (I(q_i;q_{i+1}) = 0.004 bits, measured) and entropy coding of
K-quant codes for storage (4.1% ceiling, measured). Numbers below.

---

## (a) Posterior-expected / Bayesian dequantization

### Classical foundation (where the math lives)

- **Lloyd** — "Least squares quantization in PCM," IEEE Trans. Inf. Theory 28(2),
  1982 (orig. 1957 memo). The MSE-optimal decoder for a FIXED encoder is the
  **conditional mean (centroid)**: ŵ(j) = E[w | w ∈ R_j]. This is the entire
  theoretical basis of the E17 idea — the decoder half of the Lloyd iteration,
  applied without touching the encoder (codes frozen).
- **Max** — "Quantizing for minimum distortion," IRE Trans. Inf. Theory 6(1), 1960.
- **Gersho & Gray**, *Vector Quantization and Signal Compression*, Kluwer 1992 —
  ch. 6: centroid condition under weighted distortion (our imatrix metric fits
  directly: the weighted centroid is E_u[w|R_j] with weights u).
- **Schuchman** — "Dither signals and their effect on quantization noise," IEEE
  Trans. Commun. Technol. 12(4), 1964. **Gray & Stockham** — "Dithered
  quantizers," IEEE Trans. Inf. Theory 39(3), 1993. Subtractive dither renders
  quantization error uniform and signal-independent.
- **Ziv** — "On universal quantization," IEEE Trans. Inf. Theory 31(3), 1985;
  **Zamir & Feder** — "On universal quantization by randomized uniform/lattice
  quantizers," IEEE Trans. Inf. Theory 38(2), 1992; and "On lattice quantization
  noise," 42(4), 1996; **Zamir**, *Lattice Coding for Signals and Networks*,
  Cambridge 2014. Universal/dithered quantization is within 0.254 bit (~1.53 dB)
  of optimal at any rate; the optimal post-filter after dithered quantization is a
  **Wiener-style shrinkage** ŵ = (σ²/(σ²+Δ²/12))·(dequant) — i.e. the posterior
  mean of a Gaussian observed in uniform noise. NOTE: shrinkage-toward-0 gain
  factors are the affine shadow of the conditional mean — mostly already absorbed
  by K-quant's least-squares scale fit (see honesty section).

### Applied to NN weights (2023–2026)

- **arXiv:2305.14314** — QLoRA / **NF4**: quantile codebook for N(0,1) weights,
  claimed "information-theoretically optimal." The claim is wrong in the useful
  direction:
- **arXiv:2306.06965** — Yoshida, "NF4 Isn't Information Theoretically Optimal
  (and that's Good)" — **AF4**: replace quantiles with levels minimizing expected
  L1 reconstruction error under the blockwise-absmax-normalized Gaussian. First
  paper to make the codebook-as-posterior-mean move for LLM weights.
- **arXiv:2505.06653** — **BOF4 / BOF4-S** (Block-wise Optimal Float): THE
  closest prior art to E17. Derives the exactly-optimal reconstruction levels for
  block-max-normalized weights as an **m²-weighted conditional expectation**:
  x(ℓ) ∝ ∫ m²·E[X | M=m, X∈R_ℓ]·p_M(m)·P(R_ℓ|m) dm, which empirically reduces to
  the weighted mean Σ w_k²... over samples in the bin — i.e. Lloyd's centroid
  corrected for normalization-induced error scaling. BOF4-S (signed absmax) fixes
  the ±1 level redundancy. **Measured gains: 0.04–0.06 PPL points vs NF4/AF4 at
  4-bit on Llama-3.1/Qwen-2.5/Mistral 7–8B; MSE-optimized beats MAE-optimized.**
  This calibrates our expected magnitude: sub-1% at 4-bit. At 2-bit the bins are
  ~4× wider and centroid shifts scale up — but so does everything else.
- **arXiv:2511.08821** — **BayesQ**: first framework to optimize quantization
  under **posterior expected loss** — Laplace/K-FAC Gaussian posterior over
  weights, posterior-covariance whitening, codebooks minimizing posterior-expected
  distortion. Quant-time, not dequant-time, but it is the modern Bayesian-decision-
  theory statement of the objective. Our full-covariance Grams are exactly the
  posterior-metric instrumentation it requires.
- **arXiv:2306.07629** — SqueezeLLM: sensitivity-weighted k-means codebooks =
  weighted Lloyd centroids as the dequant LUT. Proves per-code LUT dequant serves
  fast. **arXiv:2407.10960** — FLUTE: general LUT-quantized matmul kernels —
  kernel-feasibility precedent for table-driven dequant on GPU.
- **arXiv:2402.15319** — GPTVQ; **arXiv:2402.04396** — QuIP# (E8 lattice
  codebooks); **arXiv:2406.11235** — QTIP (trellis); **arXiv:2502.09720** —
  NestQuant (nested Gosset lattice + dither, information-theoretically optimal
  for matmul); **arXiv:2603.04956** — WaterSIC (waterfilling rate allocation,
  within 0.255 bit of the IT limit; shows GPTQ can be arbitrarily far from it);
  **arXiv:2606.23406** — HyperQuant (RD-optimal pipeline). These are quant-time
  codebook designs — the re-quant horizon, not this week.
- **arXiv:2606.10890** — PiSO: optimal channel scales under RTN have a
  **piecewise closed-form minimizer** — the scale-side version of frozen-code
  reconstruction optimization. **arXiv:2508.04853** — provable analysis of
  OPTQ/Qronos. **arXiv:2605.08692** — AAAC: activation-aware adaptive codebooks;
  notes the field "treats the scalar reconstruction grid as fixed" — the gap E17
  attacks. **arXiv:2604.08118** — codebook optimisation basins.
- Bias-correction lineage (dequant-side affine fixes, proven genre):
  **arXiv:1810.05723** (ACIQ — analytical clipping under Gaussian/Laplace, closed
  forms), **arXiv:1906.04721** (DFQ — per-channel quantization bias correction),
  **arXiv:1906.03193** ("Fighting quantization bias with bias").

### The exact math for our case (Q2_K)

Q2_K reconstruction: ŵ = d·sc·q − dmin·m, q ∈ {0..3}, per-16 sub-block (sc, m)
nibbles, per-256 super-block (d, dmin). For a prior p(w) within a sub-block and
frozen thresholds, the optimal decoder is the (imatrix-weighted) truncated-prior
centroid. Closed forms:

- Gaussian N(0,σ²), bin [a,b]: E[w|bin] = σ·(φ(a/σ)−φ(b/σ))/(Φ(b/σ)−Φ(a/σ)).
- Laplace(0,b_L): piecewise-exponential truncated means, elementary.
- Empirical Bayes (preferred — no model risk): fit per-tensor code-conditional
  residuals directly against F16 at quant time:
  **δ_j = Σ_blocks Σ_{i: q_i=j} u_i·(w_i − ŵ_i) / Σ u_i**, u = imatrix weights
  (or full-Gram-whitened residuals for the campaign metric). Ship 4 floats per
  tensor (or 4×16 conditioned on sc). Zero per-weight bytes.

### Honesty: what the LS scale fit already ate

`make_qkx2/3_quants` grid-searches (d, dmin) per sub-block to minimize
(imatrix-weighted) MSE — that IS the optimal per-block **affine** decoder for the
frozen codes. Everything affine-in-q is gone. What survives for a per-tensor
table: (1) **nonlinear per-code deviations** — under bell-shaped within-block
distributions the outer bins' centroids sit inward of the grid point (tail
asymmetry), the inner bins' sit outward; (2) **metric mismatch** — the fit
minimized diag-imatrix MSE, our success bar is KLD; (3) **aggregation across
sub-blocks** — the per-block fit is noisy at 16 samples; a tensor-level table is
the empirical-Bayes shrinkage of that noise. All three are exactly what the
residual fit measures in one afternoon. This is why 0.1–1% is the honest band.

## (b) Context-modeled reconstruction — MEASURED DEAD on our stack

Prior art is thin and coding-side, not reconstruction-side:
- **arXiv:1905.08318** — DeepCABAC (MPEG-7 pt.17 / ISO-IEC 15938-17 NNR):
  CABAC context models over weight code streams — for LOSSLESS coding of codes,
  never for better reconstruction.
- **arXiv:1907.06835** — Inter-Layer Weight Prediction: DPCM-style prediction of
  conv layers across layers; residuals Laplace; CNN-era, storage-side.
- **arXiv:2207.01394** — BiTAT (cross-layer dependency-aware binarization).

**Tonight's measurement (0.6B Q2_K-imat, all 112 Q2_K tensors, 264M weights):**
mutual information between codes at true spatial lag 1 = **0.0042 bits**, lag 2
= 0.0037, lag 4 = 0.0028, lag 8 = 0.0014, lag 32 ≈ 4e-6 (script:
scratchpad q2k_entropy.py, reproducible via gguf-py). The correlation is real and
decays like a weak AR process — fully consistent with E08's tile rank-1 share
1.7–1.9× random (experiments/08-structure-probes/README.md): structure exists,
value ≈ nil. A neighbor-conditioned reconstructor could harvest at most ~0.2% of
the code information; kernel cost would be cross-lane shuffles in the hot loop.
**Lane closed for both quality and bytes. One paper line, as predicted.**

## (c) Weight-marginal priors — what the literature says

- **arXiv:2509.23202** (MXFP4 gap analysis): FFN matrices + attn V/O projections
  are **near-perfect Gaussian**; Q/K projections have heavier tails, Laplace
  preferred in ~half the layers. Best per-kind summary in print — matches our
  per-kind whitening experience.
- **arXiv:2509.00046** (Exploring and Reshaping the Weight Distribution in LLMs);
  **arXiv:2212.09720** (Dettmers & Zettlemoyer, k-bit scaling laws — quantile
  view); **arXiv:1810.01075** (Martin & Mahoney, heavy-tailed self-regularization
  — Student-t tails at the SPECTRAL level, not marginals); **arXiv:2102.06571**
  (BNN priors revisited — trained weights non-Gaussian, heavier tails).
- Consensus: **per-tensor Gaussian-to-Laplace, kurtosis grows toward Q/K and with
  depth**. Consequence: the closed-form Gaussian centroid table is a decent
  init, but the direct empirical fit (lane a) dominates and needs no distribution
  choice. Parametric fits are useful only as a 3-parameter compression of the
  table (σ, or (b_L), or (ν,σ) Student-t) — pointless when the table is 4 floats.

## (d) Stochastic reconstruction (posterior sampling / dither at dequant)

- Theory: subtractive dither (Schuchman 1964; Gray-Stockham 1993; Zamir-Feder
  1992) makes error uniform-independent — but requires the SAME dither realization
  at encode and decode; we don't have an encode-side dither (codes are frozen
  RTN-ish). Non-subtractive noise at dequant only ADDS variance.
- ML face of posterior-sampling dequantization: variational dequantization in
  flows — **arXiv:1511.01844** (Theis et al.), **arXiv:1902.00275** (Flow++).
  Density-modeling machinery; no inference-quality claim transfers.
- **arXiv:2510.08999** (SQS) / **arXiv:2511.08821** (BayesQ): Bayesian model
  averaging over the weight posterior ≈ using the posterior MEAN — the mean is
  the fixed point, not sampling. **arXiv:2505.11170** (Gaussian weight sampling)
  is TRAINING-side (pseudo-quantization noise), not inference.
- Decision-theoretic bottom line: for any (locally) convex downstream loss —
  KLD included — E[loss(sample)] ≥ loss(mean) by Jensen. Sampling can only win
  via ensemble-diversity effects nobody has demonstrated for weight dequant.
  **Null hypothesis strongly favored. Keep only as a CONTROL ARM:** a ±Δ/2
  uniform dither run tells us whether Q2_K error is already noise-like (dither
  barely hurts → little structure left to correct) or structured (dither hurts a
  lot → deterministic correction has room). One measurement, useful either way.

## (e) Entropy coding of quant codes for STORAGE (strictly the bytes lane)

- **arXiv:2411.05239** — ZipNN: 33% on BF16 (exponent skew), 17% on FP32;
  explicitly notes already-quantized formats compress far less.
- **arXiv:2508.19263** — ZipNN extension to low-precision weights/checkpoints/KV.
- **arXiv:2504.11651** — DFloat11: ~30% off BF16 losslessly (11 effective bits),
  GPU-decodable Huffman. **arXiv:2502.00922** — Huff-LLM (hardware-side).
  **arXiv:2505.02380** — EntroLLM (edge, up to 30% over uint8 / 65% over uint4
  claims on already-quantized edge models — their codes are NOT scale-normalized
  like K-quants, hence the headroom). **arXiv:2606.15789** — approaching the
  Shannon bound for lossless weight compression; **arXiv:2510.02676** — exponent
  concentration theory; **arXiv:2603.17435** — ZipServ; **arXiv:2604.03298** —
  ENEC. Context-modeled: DeepCABAC (1905.08318). Sub-4-bit "statistical
  losslessness" framing: **arXiv:2605.02404** (SLQ, EAR metric).
- **Tonight's measurement on OUR bytes (0.6B Q2_K-imat):** H(q) = **1.947/2.000
  bits** (code plane 97.4% saturated — K-quant RTN over a bell curve is nearly
  max-entropy by construction); H(sc)=3.42/4, H(m)=3.71/4, H(sc|prev)=3.33.
  Order-1 context adds ~nothing on codes (see lane b). Achievable file-level:
  2.625 → **~2.52 bpw = 4.1%**, order-0 rANS on codes + scales, GPU-decodable at
  load. Honest verdict: real, small, storage-only, zero interaction with the
  quality lane. Do it only if we ever ship a downloader/loader lever; never in
  the serving kernel.

## (f) Fused-kernel feasibility (against the E14 mmvq architecture)

| Form | Kernel cost | Verdict |
|---|---|---|
| Per-block affine (gain/offset on levels) | folds into d, dmin at LOAD — zero kernel change | already absorbed by LS scale fit; only re-fit under a DIFFERENT metric is new, and that is requant/E13 territory |
| **Per-tensor per-code level table (4 int8 entries)** | **~1 `prmt.b32` per 4 weights** in the Q2_K unpack: byte-select replaces `(b>>s)&3` with LUT[(b>>s)&3]; int8 levels ride the existing dp4a integer-dot path; scale slack folded into d | **CHEAP — the candidate.** Same trick as SqueezeLLM/FLUTE LUT dequant, but 4-entry and per-tensor, constant-memory resident |
| Per-(code, sc) table (64 entries) | constant memory + one extra index mix | cheap; fit will show whether sc-conditioning adds anything (tonight's H(q|sc)=H(q) suggests: no) |
| Additive per-code correction on OTHER K-quants (Q4_K/Q6_K) | per-code masked activation sums OR wider LUT in unpack | moderate; only after Q2_K proves the lane |
| Neighbor-context correction f(q_i, q_i±1) | codes are in-register (same block), so lane shuffles ~feasible | **dead by measurement** (I = 0.004 bits) — do not build |
| Stochastic dither in epilogue | Philox per-element RNG in hot loop | build only as the one-off control arm, host-side offline is fine |

## Ranked implementable-this-week candidates

1. **Empirical-Bayes per-tensor code-conditional dequant table** ("learned dequant
   table", lanes a+c). Math: δ_j = weighted mean of (w−ŵ) over all weights with
   code j, per tensor, weights = imatrix diag (variant: full-Gram-whitened).
   4 floats/tensor shipped, ~0 bytes. Offline fit + offline application (dequant
   to F16) needs NO kernel work to get the verdict. Expected: **0.1–1% KLD**
   (BOF4-calibrated; 2-bit bin width argues for the upper half; the LS-fit
   absorption argues for the lower). Kernel path if it wins: `prmt` LUT in E14.
2. **KLD/whitened-metric variant of the same table** — same infra, fit under the
   campaign metric instead of diag-MSE; this is the part NO prior work does
   (BOF4/AF4 are MSE/MAE-only) and where our Gram instrumentation is an edge.
3. **Dither control arm** (lane d) — one offline run, ±Δ/2 uniform on dequant;
   interprets candidate 1's result (structured error vs noise-like error).
4. **Storage rider** (lane e) — order-0 rANS/Huffman on code+scale planes, 4.1%
   measured ceiling, loader-side only. Park unless bytes lever is wanted.
5. **CLOSED tonight, do not build:** neighbor-context reconstruction (measured
   I=0.004 bits), posterior SAMPLING for quality (Jensen argument + zero
   literature support), context-coded storage beyond order-0 (measured ~0 gain).

## Null-hypothesis test design (campaign standard)

- **Step 1 — fit & magnitude check (CPU, ~1–2 h, reuses E13 numpy K-quant infra
  + F16 reference + imatrix):** compute δ_j per tensor on 0.6B Q2_K-imat. Report
  max_j |δ_j| in units of sub-block step Δ. **Kill rule: if |δ_j| < 1% of Δ
  everywhere, the lane is dead** — write the number in the rollup and stop.
- **Step 2 — quality verdict (offline, byte-fair):** apply tables in an offline
  dequant → corrected model; measure **KLD vs the uncorrected Q2_K dequant of the
  same file** on the standard harness. Bytes identical (+4 floats/tensor ≈ 1.8 KB
  total). Success bar: KLD improvement beyond run-to-run noise at 0.6B, then one
  27B spot-check (MIXED/fc configs compose — the table is orthogonal to adapters).
- **Step 3 — kernel (only on a Step-2 win):** `prmt` LUT in the E14 tree, verify
  digit-stability vs offline application, tok/s delta expected ~0.
- Control arm: dither run from candidate 3 alongside Step 2.

## Cross-references

- experiments/08-structure-probes/README.md — tile rank-1 share 1.7–1.9× random
  (the within-block structure this sweep's lane b now quantifies in bits: 0.004).
- experiments/13-rerounder/ — quant-time twin of this dequant-time lane (encoder
  vs decoder halves of the same Lloyd iteration; they compose).
- experiments/14-fused-kernel/REPORT.md — the kernel this lands in; mmvq unpack
  is where the `prmt` LUT goes.
- prior-art/arxiv-whitening-allocation.md, arxiv-kernels.md — metric + kernel
  context.
