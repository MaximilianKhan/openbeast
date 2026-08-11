# arXiv sweep: covariance whitening + rank allocation for low-rank quantization-error correction

**Sweep date:** 2026-08-03. **Scope:** 2023–2026 arXiv, four questions:
(a) how methods estimate/factor activation covariance at scale, (b) rank/bit
allocation algorithms, (c) sensitivity metrics, (d) random-matrix theory of
residual correction. Ends with the tonight-feasible 27B recipe.

**Context this answers:** E04c named null #3 — diagonal whitening
(imatrix diag E[x²]) collapsed at 27B/2-bit (capture 0.37→0.07 at r64).
QERA's published claim that full covariance wins below 4-bit is confirmed
by this sweep, and the sweep found the exact algorithms, costs, and the
allocation machinery we lacked.

---

## (a) How the field estimates and factors activation covariance

### The lineage in one paragraph

Everything descends from the layer-wise proxy objective
`min ‖(W − Ŵ)X‖_F²` whose Hessian is `H = 2·XXᵀ` (GPTQ, 2210.17323).
Three factorization camps exist: **Cholesky** (GPTQ solves with the
Cholesky of the damped inverse Hessian; SVD-LLM whitens with the Cholesky
factor of `XXᵀ`), **eigendecomposition** (EoRA projects into the
eigenspace `XXᵀ = QΛQᵀ` and scales by `√Λ`), and **matrix square root**
(QERA-exact computes `R_xx^{1/2}` by blocked Schur in FP64). They are the
same math — the whitened matrix `M = E·S^{1/2}` (or `E·L_chol`) is
SVD-truncated and the right factor un-whitened — and differ only in cost
and numerical stability. Cholesky is the cheapest (n³/3 flops) and, with
damping, the most robust; QERA's Schur route is the most expensive and the
paper explicitly names it their bottleneck.

### QERA (2410.06040, ICLR'25) — the theory anchor

- **QERA-exact:** optimal low-rank term for `min E‖(W−Q−L)x‖²` is
  `L = SVD_r(E·R_xx^{1/2})·R_xx^{-1/2}` with `R_xx = E[xxᵀ]` from
  calibration. (Notation: E = quantization residual.)
- **QERA-approx:** assume uncorrelated inputs → `R_xx` diagonal →
  `S = diag(√E[x_i²])`. **This is analytically identical to LQER's
  heuristic and to llama.cpp imatrix whitening — i.e., our E05 method is
  exactly QERA-approx.** The paper proves approx is what you lose when you
  drop cross-correlation.
- **Empirics:** exact beats approx and the gap WIDENS as bits drop
  (2–3-bit); e.g. TinyLlama 3-bit w2 PPL 19.51 (exact) vs 20.43 (approx);
  LLaMA-3.1 3-bit 6.68 vs LQER 7.05. Also: LQER/approx quality fluctuates
  non-monotonically with calibration size; exact converges monotonically.
- **Costs & stability:** they compute `R_xx^{1/2}` via blocked Schur **in
  FP64 on CPU** and call it the quantization-time bottleneck at scale;
  they flag accumulating numerical error in the square root for large
  models. Lesson: do NOT copy their factorization; use damped
  Cholesky/eigh instead (same optimum, fraction of the cost).
- License: Apache-2.0 reference code (github.com/ChengZhang-98/QERA) —
  safe to implement from.

### SVD-LLM (2403.07378, ICLR'25) + V2 (2503.12340)

- Whitening matrix = **Cholesky factor of `XXᵀ`** (damped). Theorem:
  truncating smallest singular values of `W·L_chol` minimizes activation
  reconstruction loss — the direct precedent for the Cholesky route at LLM
  scale (7B–70B), so numerical viability at our sizes is established.
- V2 adds heterogeneous per-matrix compression ratios (see §b).

### EoRA (2410.21271, NVIDIA) — same optimum, eigh route

- `X̃X̃ᵀ = QΛQᵀ`, project `ΔW' = ΔW·Q√Λ`, SVD_r, back-project with
  `(Q√Λ)^{-1}`. Algebraically ≡ QERA-exact with `S^{1/2} = Q√Λ`.
- Calibration: 256×2048-token sequences; **minutes of wall-clock per
  model**, rank 128 default. Gains over plain SVD grow with compression
  aggressiveness — consistent with our E03 null.
- Code is NVIDIA non-commercial — implement from QERA math, never from
  EoRA source (already the survey's guidance; re-confirmed).

### ARHQ (2605.00140, 2026) — residual-adapted covariance

- Weighted low-rank approx under the metric `G_x^{1/2}` where
  `G_x = (1/N)E_xᵀE_x` is the covariance **of the quantization residual
  activations**, not the raw activations — quantizer-adaptive whitening.
  Solution shape identical to QERA (`L = SVD_r(W·G^{1/2})·G^{-1/2}`).
- Stability: **eigenvalue flooring** ε for `G^{-1/2}` when calibration
  tokens < input dim. Fixed r=128; adaptive rank named as future work.
- Relevance: confirms the whitening metric is a free design choice — we
  can whiten by anything PSD we can estimate.

### IO-SVD (2605.15626, 2026) — double-sided whitening

- `B = C^{1/2} W R^{1/2}`: input Gram `R = E[xxᵀ]` **and** output-side
  KL-curvature `C = E[JᵀH_softmax J]`, approximated on the top-K
  vocabulary support via backward hooks (avoids materializing anything
  vocab-sized). Both damped (`R̄ = R + λI`) before the square root.
- 256×2048 WikiText2 calibration. Beats ASVD/SVD-LLM/Dobi-SVD.
- Lesson for us: output-side curvature is the next fidelity rung ABOVE
  input covariance, but needs backprop machinery — not tonight.

### Approximating S itself (the 25600-dim question)

- **Kronecker:** KronQ (2607.07964) factors the layer Hessian as
  (gradient covariance) ⊗ (activation covariance), O(m²+n²) storage.
  This is the standard layer-wise factorization, not a shrink of the
  n×n activation Gram itself.
- **Block-averaging:** GuidedQuant (2505.07004) needed per-output-channel
  Fisher blocks (would be 110 TB / 13k GPU-hours at 7B) and made it
  tractable by **averaging Fisher blocks within channel groups**.
  Precedent that structured averaging of second-order stats survives.
- **Nobody found doing Nyström / sketched / block-diagonal approximation
  of the activation Gram for whitening.** GPTQ implementations up to 25600
  dims simply materialize the full fp32 Gram (2.6 GB) per layer being
  processed and stream layers sequentially. → A block-diagonal or
  Nyström-plus-diagonal S is an open, publishable fallback if RAM binds
  (it degrades gracefully toward our existing diagonal method as block
  size → 1).

### Damping and calibration-size conventions (the numbers)

- **Damping:** GPTQ standard `λ = 0.01 × mean(diag(H))` (1% of average
  diagonal) — robust up to 175B. QuIP/QuIP# use the same order. ARHQ adds
  eigenvalue flooring. Use both: `S ← S + λI`, floor eigenvalues at
  `ε·λ_max` if using eigh.
- **Calibration tokens:** GPTQ 128×2048 = 262k (C4); EoRA/IO-SVD/SVD-LLM
  256×2048 ≈ 524k; LQER only 32×2048 = 65k. 2026 ablations (multi-scale
  calibration study, 2602.07465-adjacent): PPL improves steadily to ~4M
  tokens then saturates; 100k already competitive; 500k–1M is the
  knee. Rule of thumb for a full-rank n×n Gram: want tokens ≥ 4n
  (n=25600 → ≥100k), then damping covers the tail. **262k–524k tokens is
  the field's consensus sweet spot and is enough for n=25600.**
- Calibration source: QERA found pretraining-style text (WikiText2)
  beats downstream task data (padding artifacts). Matches our
  wikitext-2 setup.

---

## (b) Rank / bit allocation across layers and tensor types

Objectives allocated against, by family:

| Method | Granularity | Objective / score | Algorithm |
|---|---|---|---|
| OWQ (2306.02272) | columns within tensor | `H_jj·‖Δw_j‖²` (Hessian diag × error) | top-k columns kept fp16 |
| HAWQ-v2/v3 lineage | per layer, bits | avg Hessian trace (Hutchinson) × quant error | Pareto frontier → ILP (v3) |
| LQ-LoRA (2311.12023) | per matrix, quant config | Fisher-weighted reconstruction error | **integer linear program** under global bit budget |
| Dobi-SVD (2502.02723, ICLR'25) | per matrix, rank | end loss via backprop | **differentiable truncation** (Gumbel-ish renormalized rank ratios, IPCA reconstruction) |
| SVD-LLM V2 (2503.12340) | per matrix, ratio | theoretical truncation loss, grouped by tensor TYPE (q/k/v/o, gate/up/down) | closed-form ratio per group member |
| IO-SVD (2605.15626) | global, per component | `|g_i·σ_i|` (whitened gradient × singular value) | **global greedy pool: repeatedly drop the globally cheapest singular component until budget met** |
| BALF (2509.25136) | global, per component | calibration-loss impact of each whitened component | greedy under parameter budget |
| GAMMA (2605.18475) | per linear module, bits | layer-output MSE, teacher-forced | Gumbel-Softmax soft bits + augmented Lagrangian → 0-1 knapsack ILP; ~1 h A100 |
| SERQ (2603.08185) | per layer, rank | saliency-weighted error energy | adaptive ranks from error spectra; beats QERA/LQER/EoRA on their table |
| CALDERA (2405.18886) | fixed rank, bits on L/R too | `‖(Q+LR−W)Xᵀ‖²` | alternating LDLQ ↔ low-rank; rank 64–256 |
| ODLRI (2506.02077) | per layer bits+rank | joint `W=Q+L` alternating | outlier-aware init, alternating updates |
| GPTQ-intrinsic LoRA (2606.01412) | per matrix | joint quant+low-rank, near-optimality proof | alternating GPTQ-style quant ↔ factor refit |

**Convergent finding:** the 2025–26 winners (IO-SVD, BALF, SVD-LLM V2)
all abandoned per-layer heuristics for a **global greedy auction over
whitened singular components**: every candidate rank-1 component from
every tensor competes in one pool, scored by (whitened energy or loss
impact) per parameter, and the budget buys the best ones anywhere in the
network. This is exactly the water-filling shape math-methods proposed —
the sweep confirms it is the published state of the art, and that the
score should be **whitened σ² per byte**, i.e. `σ_i²/(m+n)` for a
component of an m×n tensor.

**Second convergent finding (SVD-LLM V2):** allocation must be
type-aware — q/k/v/o/gate/up/down have systematically different
truncation-loss curves, so comparing raw scores across types without
grouping misallocates. Our E05 map (q/k/gate/up steep, v moderate,
o/down flat) is the same phenomenon measured independently.

**Alternating refinement (CALDERA/ODLRI/GPTQ-intrinsic-LoRA):** after
computing L for a fixed Q, re-quantizing `W − L` and re-fitting L gains
another increment; near-optimality results exist (2606.01412). One
alternation pass is cheap (re-run llama-quantize + re-extract); worth one
pass, not worth iterating tonight.

---

## (c) Sensitivity metrics — which tensors deserve correction

- **Hessian/imatrix diagonal** (`E[x_i²]`): OWQ's `H_jj·‖Δw‖²` for column
  outliers; cheap, already ours via imatrix.
- **Hessian trace** (HAWQ-v2, Hestia 2601.20745): Hutchinson probes;
  2026 re-evaluations report trace computation for LLMs costs about the
  same as other allocation methods — no longer considered prohibitive.
- **Fisher / end-loss guidance** (LQ-LoRA, GuidedQuant 2505.07004):
  weight the layer objective by downstream loss sensitivity, not just
  layer-local energy. GuidedQuant's point: layer-local `‖ΔWX‖` ignores
  how errors propagate; their fix is Fisher-block-averaged end-loss
  weights. Costs a backward pass over calibration data.
- **Where sensitivity concentrates (measured, multiple papers,
  2506.01967 / 2309.15531 / 2404.03605):** massive activation outliers
  live almost exclusively at **ffn_down inputs**, and out-of-trend
  quantization error concentrates in **down_proj of the first (~1) and
  last two layers**; attention and gate/up carry systematic (repeatable,
  channel-aligned) outliers. Correlation between layer quant error and
  activation quantization difficulty is >0.97 once a handful of outliers
  are removed. **Implication: ffn_down — precisely the tensor where our
  diagonal proxy is weakest and the Gram is 25600-dim — is where the
  literature says the sensitivity actually lives. The expensive tensor is
  the load-bearing one; no dodging it.**
- **Task-circuit probes** (TaCQ 2504.07389): KL/circuit-based saliency
  for task-conditioned mixed precision — the fancy end; not needed for a
  general-purpose artifact.

---

## (d) Theory: optimal rank vs width under MP-shaped residuals

- **LQER (2402.02446)** made the founding observation: quantization-error
  singular spectra are **Marchenko-Pastur-like (flat bulk)** — matching
  our E03 measurement exactly — and left-multiplying by the activation
  scaling matrix reshapes the spectrum to fast decay, concentrating
  energy in few components. Whitening is spectrum surgery: it converts
  an incompressible MP bulk into a spiked spectrum with a correctable
  head.
- **RMT frames for transformer weights** (Research Square / 2602.22345
  lineage): FFN matrices track MP closely; attention/embedding deviate
  with Tracy-Widom edges. Consistent with our per-type E05 map.
- **Gavish-Donoho (1305.5870):** for a signal-plus-MP-noise matrix, the
  MSE-optimal hard threshold is `τ* = 2.858·σ_med` (unknown noise,
  square case; β-dependent constant otherwise). **Any singular component
  of the whitened residual below the MP bulk edge is noise — spending
  rank bytes on it is provably wasted.** This gives each tensor a
  principled per-tensor rank CAP, independent of the budget auction:
  allocation should water-fill only among components above the bulk
  edge. Optimal shrinkage refinements exist (1311.0851: shrink retained
  σ's toward the spike estimator) — a free quality nibble since we
  rescale factors anyway.
- **Interpretation for us:** at 0.6B the whitened spectra had spikes
  (54–81% capture) — signal existed. The 27B r64 collapse is r/d
  starvation, not absent signal; the MP cap tells us per tensor how much
  extractable signal exists at all, and therefore whether the campaign's
  byte budget should go to rank or to base bits per tensor.

---

## Practical costs at our scale (27B: d=5120, ffn=25600, 64 layers)

**Gram storage (fp32):**
- 5120-dim sites: 5120²·4B = **105 MB** each. Three distinct input sites
  per layer share Grams (attn-in feeds q/k/v; ffn-in feeds gate/up;
  o-in and down-in are their own): 5120-dim Grams ≈ 3/layer →
  **~20 GB for all 64 layers** — holdable in system RAM in ONE pass.
- 25600-dim site (ffn_down input): 25600²·4B = **2.62 GB per layer**,
  ×64 = **168 GB total** — NOT holdable. Options: (i) grouped passes,
  8 layers/pass ≈ 21 GB × 8 passes; (ii) fp16 accumulator sidecars
  (84 GB — only if RAM ≥ 96 GB); (iii) block-diagonal S (4×6400 blocks
  = 656 MB/layer, 42 GB total, single pass — the unpublished fallback).
- Accumulation flops: 262k tokens × 25600² ≈ 3.4e14 MAC per ffn_down —
  seconds per layer on the 5090 if accumulated via cuBLAS sgemm,
  minutes-per-layer if done naively on CPU. Accumulate on GPU.

**Factorization:**
- Damped Cholesky (potrf), n=25600, fp64 CPU: ~n³/3 ≈ 5.6e12 flops →
  **~30–90 s/tensor**, 64 tensors ≈ 0.5–1.5 h (parallelize 2–4 workers,
  watch 2.6 GB/worker). n=5120: <1 s each.
- eigh instead of Cholesky costs ~10× more (25600 fp64 CPU ≈ minutes
  each) — only needed if we want eigenvalue floors; damped Cholesky
  does not need them.
- QERA's blocked-Schur-FP64-on-CPU is strictly dominated; skip.
- Whitened SVD: our existing rsvd (r+32 oversampling, 4 power iters) on
  `E·L_chol` — same cost as E04c's 269 s full-model extraction, plus one
  triangular solve per tensor for un-whitening (cheap).

**Calibration:** 128–256 chunks × 2048 tokens (262k–524k), wikitext-2
(+optionally C4 mix). One llama.cpp forward pass per capture group.

**Total tonight budget estimate:** capture 1–8 passes ≈ 0.5–1.5 h;
Cholesky ≈ 1 h; extraction ≈ 10 min; requant + eval ≈ as E04c. **~3–4 h
end-to-end.** Feasible.

---

## Implementation recipe — the 27B tonight-feasible pass

**Design sentence:** QERA-exact math via damped-Cholesky whitening,
Grams captured by a patched llama-imatrix, ranks bought in a single
global greedy auction on whitened σ²/byte with a Gavish-Donoho cap.

1. **Instrumentation (the one new piece).** Patch
   `tools/imatrix/imatrix.cpp`: where the collector sees `(tensor name,
   src1 activations)`, add an env-gated mode that accumulates the full
   Gram `S += XᵀX` (fp32, cuBLAS if the buffer is on GPU, else sgemm on
   CPU) for tensors matching a name regex, and writes one sidecar file
   per tensor. ~150 LOC, offline tooling only — does not violate the
   zero-llama.cpp-changes serving story.
2. **Capture plan.** Pass 1: regex = all 5120-dim sites (attn_q input ∪
   attn_output input ∪ ffn_up input), all 64 layers, ~20 GB, 256 chunks
   wikitext-2 (524k tokens). Passes 2–9: `ffn_down` inputs, 8 layers per
   pass (21 GB each). If wall-clock binds, drop to 128 chunks (262k
   tokens — still ≥10× the 25600 dim) and/or capture ffn_down only for
   layers 0–3 and the last 8 plus every 4th in between, diagonal
   elsewhere (the sensitivity literature says first+last down_proj
   dominate).
3. **Whitening.** Per tensor: `S ← S + 0.01·mean(diag(S))·I`; fp64
   Cholesky `S = LLᵀ` (scipy potrf); keep L and its triangular inverse
   application (solve, never invert).
4. **Factors (QERA-exact, Gram route, no giant SVDs).**
   `E = dequant(W_q6_ref) − dequant(W_q2)`; `M = E·L` (GPU fp32);
   rsvd_r(M) → `U, Σ, V`; export `B = U·Σ`, `A = solve(Lᵀ, V)ᵀ` as the
   LoRA-form GGUF exactly as E04 (same conventions, same round-trip
   check at high rank on one layer before the full run).
5. **Rank allocation (replaces uniform r64).** Pool every tensor's
   whitened singular values. Score component i of an m×n tensor
   `σ_i²/(m+n)`; **cap each tensor at its Gavish-Donoho rank**
   (drop components with `σ < 2.858·σ_med`, σ_med estimated from the
   rsvd sketch tail); greedily buy components by score until the byte
   budget (start: same 870 MB as E04c's uniform spend, so the comparison
   is byte-fair). Round per-tensor ranks to multiples of 8 for GEMM
   sanity. Expect the auction to defund o/down-flat tensors and pile
   rank on q/k/gate/up and the outlier down_proj layers — E05's map
   predicts it; let the data decide.
6. **Evaluate** exactly as E04c: {Q2_K-imat + allocated-rank adapter} vs
   {Q3_K_M, IQ3, Q4_K} at equal bytes; PPL + mean-KLD + top-1 + tok/s.
   Ship the whitened-spectrum MP plots into the journal — they are the
   paper's Figure 2 regardless of outcome.
7. **One optional alternation** if time remains: requantize `W − BA`
   (llama-quantize with imatrix) and re-extract once (CALDERA-lite).

**Explicitly SKIP tonight** (ranked by deferred value):
- Output-side/KL curvature whitening (IO-SVD's C matrix) and any
  Fisher/end-loss weighting (GuidedQuant) — needs backprop plumbing.
- Gradient covariance / Kronecker Hessians (KronQ).
- Bit-width reallocation of the base quant (GAMMA/LQ-LoRA ILP) — the
  K-quant type heuristics stay as-is tonight; note as follow-up.
- Differentiable rank (Dobi-SVD) — the greedy auction is the published
  equal at a fraction of the cost.
- Quantizing the factors (CALDERA does 4-bit L/R; our F16-first policy
  stands; Q8_0 factors are a later byte-optimization).
- Residual-adapted metric G_x (ARHQ) — interesting, but it changes the
  capture target; keep raw-activation Grams so one capture serves all
  ranks and both Q2/Q3 bases.
- Optimal singular-value shrinkage (1311.0851) — one-line multiply,
  add later if we're within noise of a baseline.

**Fallback if RAM binds on ffn_down:** block-diagonal S (4 blocks of
6400) — unpublished for whitening, gracefully degrades toward our
diagonal method, and is itself a paper-worthy ablation axis.

---

## Reference index (arXiv IDs)

| ID | Short name | Why it matters here |
|---|---|---|
| 2210.17323 | GPTQ | H=2XXᵀ, 1% damping, Cholesky, 262k tokens |
| 2402.02446 | LQER | diag scaling ≡ our E05; MP observation; 2-bit limits |
| 2410.06040 | QERA | closed-form exact vs approx; full-cov wins <4-bit; Apache-2.0 |
| 2403.07378 | SVD-LLM | Cholesky-of-Gram whitening theorem |
| 2503.12340 | SVD-LLM V2 | type-grouped heterogeneous ratio allocation |
| 2410.21271 | EoRA | eigh-route same optimum; 256×2048 calib; minutes; NC license |
| 2405.18886 | CALDERA | W≈Q+LR alternating with LDLQ; quantized factors |
| 2506.02077 | ODLRI | distinct-role Q/L alternating, 2-bit |
| 2606.01412 | GPTQ-intrinsic LoRA | near-optimality theory for joint quant+low-rank |
| 2605.00140 | ARHQ | residual-covariance metric; eigenvalue flooring |
| 2605.15626 | IO-SVD | double-sided whitening; global greedy component pool |
| 2509.25136 | BALF | budgeted greedy activation-aware factorization |
| 2502.02723 | Dobi-SVD | differentiable rank (rejected for cost) |
| 2311.12023 | LQ-LoRA | Fisher weighting + ILP bit allocation |
| 2306.02272 | OWQ | Hessian-diag column sensitivity metric |
| 2605.18475 | GAMMA | Lagrangian+knapsack global bit allocation |
| 2505.07004 | GuidedQuant | end-loss weights; block-averaged Fisher at scale |
| 2504.07389 | TaCQ | task-circuit saliency (future) |
| 2603.08185 | SERQ | saliency low-rank reconstruction, beats QERA/EoRA |
| 2607.07964 | KronQ | Kronecker-factored Hessian (future) |
| 2601.20745 | Hestia | Hessian-trace QAT signal (context) |
| 2506.01967 / 2309.15531 / 2404.03605 | outlier geography | ffn_down first+last layers dominate |
| 1305.5870 | Gavish-Donoho | 2.858·σ_med hard threshold = per-tensor rank cap |
| 1311.0851 | optimal shrinkage | spiked-model σ shrinkage (one-line upgrade) |
| 2602.07465 | calibration scaling | tokens: knee ~500k–1M, saturation ~4M |
