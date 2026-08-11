# Low-Rank Correction of Quantized Weights: Math & Methods Survey

**Scope.** Prior art and derivations for the OpenBeast plan: aggressively-quantized base `Q` plus a
low-rank correction `A·B` of the residual `R = W − dequant(Q)`, served as `y = Q·x + B·(A·x)` in
llama.cpp/CUDA. Sections: (1) activation-weighted low-rank approximation, (2) is the residual
low-rank?, (3) per-layer rank allocation, (4) quantizer interaction (two-stage vs joint),
(5) numerics for our matrix sizes, (6) implementation order, (7) references.

**Notation.** Column convention throughout: a linear layer is `y = W x`, `W ∈ R^{m×n}` (m = out,
n = in). Calibration activations are columns of `X ∈ R^{n×N}` (N tokens). The second-moment
(uncentered covariance / Gram) matrix is

```
S = E[x xᵀ] ≈ (1/N) X Xᵀ  ∈ R^{n×n},   S ⪰ 0 (PSD).
```

`Ŵ` is any approximation of `W`; `R = W − dequant(Q)` is the quantization residual;
`[M]_r` denotes the best rank-r truncated SVD of `M`; `‖·‖_F` is Frobenius norm.
Papers using the row convention `y = x W` (QERA, LQER, CALDERA) have their formulas transposed
here so everything is in one convention. GPTQ's proxy Hessian `H = X Xᵀ` is `N·S`.

---

## 1. Optimal low-rank approximation under activation-weighted error

### 1.1 The right objective

Minimizing weight error `‖W − Ŵ‖_F` is the wrong objective: what perturbs the network is the
**output** error. Over the calibration distribution,

```
E_x ‖(W − Ŵ) x‖₂²  =  tr[(W − Ŵ) S (W − Ŵ)ᵀ]  =  ‖(W − Ŵ) S^{1/2}‖_F² ,
```

so the layer-wise-optimal objective is a **one-sided (right-)weighted Frobenius norm**:

```
min_{rank(Δ) ≤ r}  ‖(R − Δ) S^{1/2}‖_F                                   (P1)
```

where for our project the matrix being approximated is the residual `R` (Sec. 2), but everything in
this section holds verbatim with `R` replaced by `W` (pure SVD compression, as in SVD-LLM/ASVD).

Two-sided *elementwise* weighted low-rank approximation (`min ‖√F ⊙ (R − Δ)‖_F` for a general
weight matrix `F`) is NP-hard in general (Gillis & Glineur 2011) — this is why the entire
literature uses either one-sided full-matrix weighting (exactly solvable, below) or two-sided
*diagonal* weighting (also exactly solvable, Sec. 3.2).

### 1.2 Closed-form solution: generalized Eckart–Young via whitening

> **BOX 1 (whitening theorem).** Let `T ∈ R^{n×n}` be any invertible factor of `S`, i.e.
> `T Tᵀ = S` (Cholesky factor, or symmetric square root `S^{1/2}`). Then
>
> ```
> Δ* = argmin_{rank ≤ r} ‖(R − Δ) T‖_F  =  [R T]_r · T^{-1}
> ```
>
> with optimal error `‖(R − Δ*) T‖_F² = Σ_{i>r} σ_i²(R T)` — the sum of squared **discarded**
> singular values of the whitened matrix.
>
> *Proof.* Substitute `Z = Δ T`. Since `T` is invertible, `rank(Z) = rank(Δ)`, so (P1) becomes
> the unweighted problem `min_{rank(Z)≤r} ‖R T − Z‖_F`, solved by Eckart–Young: `Z* = [R T]_r`.
> Back-substitute. ∎

This is exactly SVD-LLM's "truncation-aware data whitening" (their Theorem 3.2 / Corollary 3.3:
with `T` the Cholesky factor of `X Xᵀ`, the loss from truncating singular value `σ_i` of `W T` is
exactly `σ_i`, so truncating the smallest ones is optimal), QERA-exact's Theorem 1 (with
`T = S^{1/2}`, the symmetric PSD square root), and EoRA's eigenspace projection
(`T = Q_e Λ^{1/2}` from the eigendecomposition `S = Q_e Λ Q_eᵀ` — they project
`ΔW' = ΔW · Q_e Λ^{1/2}`, SVD in that space, and project back with `Λ^{-1/2} Q_eᵀ`). All three
are the *same* estimator:

> **BOX 2 (factor-invariance).** The solution `Δ* = [R T]_r T^{-1}` does not depend on which
> factor `T` of `S` is used. *Proof.* Any two factors satisfy `T₁ = T₂ O` with
> `O = T₂^{-1} T₁` orthogonal (since `T₁T₁ᵀ = T₂T₂ᵀ`). Truncated SVD commutes with right
> multiplication by an orthogonal matrix: `[M O]_r = [M]_r O`. Hence
> `[R T₁]_r T₁^{-1} = [R T₂ O]_r O^{-1} T₂^{-1} = [R T₂]_r T₂^{-1}`. ∎
>
> Consequence: choose the factor for *numerics*, not accuracy. Cholesky (SVD-LLM) is 2× cheaper
> than eigendecomposition (EoRA/QERA) and lets you apply `T^{-1}` by triangular solve instead of
> explicit inversion.

**Serving factors.** From `U Σ Vᵀ = SVD(R T)`:

```
B = U_{:,1:r} Σ_{1:r}      (m×r)         y = Q·x + B·(A·x)
A = V_{:,1:r}ᵀ T^{-1}      (r×n)
```

(or split `Σ^{1/2}` into both factors, ASVD-style, to balance dynamic range before storing A/B in
low precision — with F16 storage the split is irrelevant, with Q8 it helps).

### 1.3 Inverse-free formulation (recommended for our pipeline)

The published recipes factor `S` and invert the factor. Both steps can be eliminated:

> **BOX 3 (inverse-free / Gram form).** Let `G = R S Rᵀ ∈ R^{m×m}` (PSD), and let
> `U_r ∈ R^{m×r}` be its top-r eigenvectors. Then the optimizer of (P1) is the S-metric
> projection
>
> ```
> Δ* = U_r U_rᵀ R      ⇒      B = U_r ,   A = U_rᵀ R .
> ```
>
> *Proof.* With `M = R T` and SVD `M = U Σ Vᵀ`: `G = M Mᵀ = U Σ² Uᵀ`, so the top-r eigenvectors
> of `G` are the top-r left singular vectors of `M`. Then
> `Δ* = [M]_r T^{-1} = U_r Σ_r V_rᵀ T^{-1} = U_r U_rᵀ (U Σ Vᵀ) T^{-1} = U_r U_rᵀ M T^{-1}
> = U_r U_rᵀ R`. ∎
>
> No Cholesky, no `S^{1/2}`, no inverse, no n×n factorization at all — `S` only ever appears in
> the product `R S Rᵀ`, and damping for invertibility is unnecessary (PSD suffices). The price:
> forming `G` squares the condition number, so the *small* singular directions of `RT` are
> inaccurate — but we only keep the top r, where fp32 (with fp64 accumulation of `G`) is ample.

Sanity check `S = I`: `U_r` are the top left singular vectors of `R` and `U_r U_rᵀ R = [R]_r`. ✓

**Captured-energy diagnostic for free:** `eigvals(G) = σ_i²(R T)`, so the whitened-energy curve
`Σ_{i≤r} λ_i / Σ_i λ_i` — the quantity that governs rank choice (Sec. 2, 3) — falls out of the
same eigendecomposition.

### 1.4 Estimating S, and numerical stability

- **Accumulation.** `S ← S + xxᵀ` per token, in **fp64** (activation outlier channels span ~3–4
  orders of magnitude; fp32 accumulation over 10⁵–10⁶ tokens loses the small channels). QERA
  and SVD-LLM both accumulate `XXᵀ` incrementally instead of storing `X`. 32–128 calibration
  sequences suffice in every paper surveyed (LQER: 32; QERA shows quality improves monotonically
  with more — use ≥128 if cheap).
- **Damping.** If a factor/inverse of `S` *is* needed (published-form pipeline, or diagnostics),
  `S` is often numerically singular (dead channels, N < n effective). Standard practice (GPTQ
  `percdamp`): `S ← S + λ I`, `λ = 0.01 · mean(diag S)`. Cholesky requires PD, so damping is
  mandatory there; with eigendecomposition, clamp `λ_i ← max(λ_i, ε λ_max)` (ε ≈ 1e-6) and use
  the pseudo-inverse. Damping biases the metric toward plain Frobenius by `λ/λ̄` — at 1% it is
  measurement noise.
- **Cholesky vs eigh**, when you need a factor: Cholesky is `n³/3` flops vs `~9n³` for eigh,
  and `T^{-1}` is applied by `trsm` (triangular solve) — never form the explicit inverse.
  Eigendecomposition is the right choice only when you also want the spectrum of `S` itself
  (whitening diagnostics, effective-rank measurement) or a pseudo-inverse of a genuinely
  rank-deficient `S`. With BOX 3, neither is needed on the critical path.
- **Convention traps.** (i) llama.cpp/GGUF stores most weights so that the kernel computes
  `y = W x` with `W` row-major `m×n`; `S` lives on the **input** dimension n (25600 for a
  5120×25600 up/gate projection is *wrong* — check which dim is `in` per tensor). (ii) QERA/LQER
  papers use `y = xW`; their "left-multiply by S" is our right-multiply.

### 1.5 The diagonal shortcut (ASVD / LQER / QERA-approx) — and the imatrix connection

Under the assumption `E[x_i x_j] ≈ 0 (i≠j)` (QERA Assumption 1; empirically the normalized
off-diagonals in LLaMA activations are small but *not* negligible at low bit-width), `S` is
diagonal and the whitening factor is

```
T = diag(s),   s_j = sqrt(E[x_j²])        (QERA-approx, Theorem 2)
```

ASVD uses `s_j = (mean_t |x_j(t)|)^α`, α ≈ 0.5; LQER uses a max-of-means magnitude profile
normalized as `s_i = a_i / sqrt(min(ā)·max(ā))` (their Eq. 14). These are heuristic variants of
the same idea; QERA-approx is the principled one (it is the exact solution under Assumption 1).

> **Pipeline note (important for us).** llama.cpp's **imatrix** file already stores per-channel
> sums of squared activations — i.e. exactly `diag(S)` up to normalization. QERA-approx's scaling
> `s_j = sqrt(E[x_j²])` is therefore **sqrt(imatrix)**. Milestone 1 of our pipeline needs no new
> calibration machinery at all: `B, A` from the SVD of `R · diag(sqrt(imatrix))`, un-scale
> `A ← A · diag(1/s)`.

How much does full-covariance whitening buy over diagonal? QERA's ablations: at 4-bit, exact ≈
approx (≤0.1 ppl); at 3-bit and especially 2-bit, QERA-exact wins clearly (e.g. +6.05% accuracy
over LoftQ at 2-bit fine-tuning; −0.28 WikiText2 ppl vs LQER on LLaMA-3.1-70B 4-bit at rank 32).
Rule of thumb: **diagonal is enough at ≥4 bit effective; go full-covariance below that.**

---

## 2. Is the quantization residual low-rank?

### 2.1 In weight space: no. (Theory)

For a b-bit uniform/round-to-nearest quantizer with step Δ, the residual entries are
approximately i.i.d. mean-zero, variance `σ² ≈ Δ²/12`, weakly correlated with `W`. The singular
value distribution of an i.i.d. `m×n` matrix follows Marchenko–Pastur: a flat bulk supported on
`[σ(√n−√m), σ(√n+√m)]` (m ≤ n), with **no separated spikes**. The energy captured by any rank-r
approximation is bounded by

```
Σ_{i≤r} σ_i² / ‖R‖_F²  ≤  r·σ_max²/(nmσ²)  ≈  r (√n + √m)² / (nm)
                        =  r (1/n + 1/m + 2/√(nm)).
```

> **BOX 4 (rank-capture bound, our sizes).** For `m×n = 5120×25600`:
> `(1/n + 1/m + 2/√(nm)) ≈ 4.1e-4`, so a rank-64 SVD of the *raw* residual captures **at most
> ~2.6%** of `‖R‖_F²` — and in practice ~1% because the bulk is nearly flat, not edge-
> concentrated. Correcting the raw residual by SVD is fundamentally a ~1%-of-energy operation.

Published measurements agree: LQER observes the raw-error spectrum "decays slowly, following the
Marchenko–Pastur distribution of random matrices," and needs `k ≈ 600` (d=4096 model) for
near-lossless W4 when unscaled. RILQ measures that 2-bit error demands ~8–16× the rank of
3-bit error for equal weight-space fidelity, and at 2-bit plain SVD compensation is strongly
rank-sensitive (ppl σ = 0.69 across ranks 16–256 vs 0.04 at 3-bit), needing rank ≈ 256 to
match what their method does at rank 16. ZeroQuant-V2's LoRC (plain SVD of E, the original of
this family) accordingly reports only modest recovery.

### 2.2 In the activation metric: effectively yes. (Why the trick works)

The object we actually need to approximate is `R T` (or equivalently spectrum of `R S Rᵀ`), not
`R`. Activation second-moment matrices in LLMs have **steeply decaying spectra** — a handful of
outlier / "massive-activation" channels carry orders of magnitude more energy than the rest, so
`S` has small *effective* rank `ρ_eff = (Σλ_i)²/Σλ_i² ≪ n`. Write `S = Σ_i λ_i q_i q_iᵀ`; then

```
‖R T‖_F² = Σ_i λ_i ‖R q_i‖²  ,
```

i.e. whitened residual energy is activation-eigenvalue-weighted, and is dominated by the top
`~ρ_eff` directions `R q_i`. Even a Marchenko–Pastur (isotropic) residual therefore has a
**spiked** whitened spectrum with numerical rank ~ρ_eff: the correction does not need to fix the
residual, only its action on the subspace where activations live. This is the honest answer to
"is the residual low-rank?": *no in ‖·‖_F, approximately yes in the metric that governs outputs*,
with effective rank inherited from the activation covariance, not from the residual.

Published empirics, translated to rank budgets:

| Source | Setting | Rank needed |
|---|---|---|
| LQER (raw SVD of E) | LLaMA-7B W4, near-lossless ppl | k ≈ 600 |
| L²QER (diag-scaled) | LLaMA-7B W4A8 | **k = 32–64** (ppl Δ ≈ +0.2 vs FP16) |
| L²QER | W2A8 | k = 256 |
| QERA-exact | LLaMA-3.1-70B 4-bit | k = 32 (beats LQER by 0.28 ppl) |
| EoRA | LLaMA-3-8B, 2:4 sparse and/or 3–4 bit | k = 64–512 sweep; k=128 typical (+3.3% ARC-E over plain SVD at same rank) |
| CALDERA | 2.1–2.4 bit total | k = 64–256, factors themselves 4-bit |
| RILQ | 2-bit, plain SVD | k ≈ 256 to match their rank-16 trained method |

Working hypothesis for our targets (Q3/Q4 base, d_model ≈ 5–6k): **r = 32–64 with activation
weighting recovers most of the recoverable ppl; r = 128–256 only pays at ≤2.5-bit effective.**
Below ~3 bit the residual grows and even weighted low-rank saturates (RILQ's argument) — that
regime wants gradient-based compensation or joint optimization (Sec. 4), not a bigger r.

### 2.3 Caveat: distribution shift

`Δ*` is optimal *for the calibration distribution*. All surveyed methods report robustness with
32–128 web-text sequences, and QERA shows monotone improvement with calibration size (LQER's
behavior is erratic in that sweep — its max-based scale profile is noisier than a mean). Use
mixed-domain calibration (we care about code + chat) and hold out a slice to verify
`‖(R−BA)X_holdout‖` actually drops proportionally.

---

## 3. Per-layer rank allocation

### 3.1 The clean marginal-utility rule (derivation)

Whitening makes marginal utility *exact at the layer level*: by BOX 1, giving layer ℓ one more
rank reduces its expected squared output error by exactly `σ²_{ℓ, r_ℓ+1}` (the next whitened
singular value = next eigenvalue of `R_ℓ S_ℓ R_ℓᵀ`). The storage cost of one rank is
`c_ℓ = bytes_per_elt · (m_ℓ + n_ℓ)`. If layer output errors entered the end loss equally, the
optimal allocation under budget `Σ r_ℓ c_ℓ ≤ C` would be the greedy/water-filling rule:

> **BOX 5 (whitened water-filling).** Keep every whitened direction (ℓ, i) with
> `w_ℓ · σ²_{ℓ,i} / c_ℓ ≥ τ`, choosing threshold τ (binary search) to meet the budget.
> Equivalently: greedily award ranks to `argmax_ℓ w_ℓ σ²_{ℓ, r_ℓ+1} / c_ℓ`. Greedy is optimal
> because the per-layer gain sequence `σ²_{ℓ,1} ≥ σ²_{ℓ,2} ≥ …` is non-increasing (diminishing
> returns ⇒ the relaxed problem is a fractional knapsack with concave utilities).

The catch is `w_ℓ`: layer output MSE is *not* equally weighted in the end loss. Options, in
increasing cost:

1. `w_ℓ = 1` (pure whitened energy). Already normalizes for activation scale; decent baseline
   but known failure mode: over-allocates to early layers whose errors get re-absorbed.
2. **First-order sensitivity**: `w_ℓ ≈ E[‖∂L/∂y_ℓ‖²]` (output-gradient energy per layer) — one
   backward pass over calibration data. This is the Fisher row-scale of Sec. 3.2 aggregated per
   layer.
3. **Empirical ablation**: measure ΔPPL from correcting layer ℓ alone at rank r (or from
   *removing* its correction) and fit `w_ℓ`. Most faithful, ~L·(few) forward passes.

### 3.2 Fisher-weighted decomposition (LQ-LoRA) — what it adds over activation weighting

Elementwise Fisher `F_ij = E[(∂L/∂W_ij)²]` with `∂L/∂W = δ xᵀ` (δ = ∂L/∂y) gives, assuming
`δ_i ⟂ x_j`,

```
F_ij ≈ E[δ_i²] · E[x_j²]   (rank-1 Fisher)   ⇒   √F ⊙ Δ = D_row Δ D_col ,
D_row = diag(E[δ_i²]^{1/2}),  D_col = diag(E[x_j²]^{1/2}).
```

Two-sided **diagonal** weighting is exactly solvable (unlike general two-sided): SVD
`D_row (R) D_col = UΣVᵀ`, then `B = D_row^{-1} U_r Σ_r^{1/2}`, `A = Σ_r^{1/2} V_rᵀ D_col^{-1}`.
Note `D_col` is precisely QERA-approx's activation scaling — **Fisher weighting = diagonal
activation weighting + output-row gradient weighting**. LQ-LoRA implements exactly this row/col
reduction (their D_row/D_col from row/col means of √F) and finds it matters little at 3 bits but
"especially effective at 2.5 bits." So: adopt row-weighting only when we push ≤Q2-class bases.

### 3.3 Published allocation algorithms

- **ASVD STRS**: per-layer sensitivity = ppl on calibration data as a function of truncation
  ratio ∈ {0.1..0.9}, assume layer independence, binary search a global sensitivity threshold to
  hit the parameter budget. Simple, O(L·|ratios|) ppl evals; our Box-5 rule is the same idea with
  the ppl probe replaced by the analytically exact whitened spectrum.
- **LQ-LoRA ILP**: choose per-matrix quantization config c ∈ C (243 combos of bits/blocksize)
  minimizing Σ error(i,c)·X[i,c] s.t. Σ storage ≤ budget, one config per matrix. Directly
  reusable with "config" = (base quant type, rank r ∈ {0,16,32,64,128}) and error = whitened
  residual energy left after correction — a few thousand binary variables, trivially solvable
  (or by the greedy of Box 5, which is exact here because utilities are concave in r).
- **LAARA / UniRank / Bayesian-opt allocation / LASER** (2025–26): Fisher-signal or search-based
  allocators for the same problem; LAARA's practical notes transfer: raw Fisher scales vary
  wildly across projection types → normalize per-type and log-compress before ranking.
- **Module-type empirics** (RILQ): FFN matrices are rank-critical, attention projections
  rank-redundant — expect the allocator to discover the same; a cheap prior is to cap attention
  ranks at half the FFN rank.

---

## 4. Interaction with the quantizer: two-stage vs joint

The design space, in increasing coupling:

**(a) Two-stage analytic (Q first, then corrected).** `Q = quant(W)` (with imatrix/Hessian-aware
rounding), then `B,A` from Sec. 1 on `R = W − dq(Q)`. ZeroQuant-V2-LoRC (unweighted), LQER,
QERA, EoRA all live here. QERA's key point: given a *fixed* Q, the activation-weighted closed
form is **optimal** — nothing iterative can beat it *for that Q* under the layer-output metric.

**(b) Weight-space alternation (LoftQ / LQ-LoRA).** Iterate
`L₁L₂ ← SVD_r(W − Q)`; `Q ← quant(W − L₁L₂)`; stop when reconstruction error rises (typically
converges in "several steps"). Improves ‖W − Q − L₁L₂‖_F, i.e. lets the *quantizer's* grid spend
its dynamic range on what the low-rank part didn't absorb. LQ-LoRA ablations: alternation helps
consistently (their Table 2: 87.3 vs QLoRA's 86.1 at 3 bits) and uses ranks more productively
than QLoRA (rank ablation, Table 5). But QERA's Figure-1 analysis is a genuine caution:
LoftQ-style iterations minimize *weight* error, and its layer *output* error does not decrease
monotonically in rank — alternation on the wrong norm can go sideways.

**(c) Activation-weighted joint alternation (CALDERA).** `min_{Q,L,R} ‖(Q + LR − W) Xᵀ‖_F²` with
all three factors quantized; outer loop alternates `Q ← LDLQ(W − LR)` (GPTQ-family quantizer with
cross-column error feedback from the LDL decomposition of H) and `(L,R) ← LPLRFactorize(W − Q)`;
inner LPLR alternates the two closed-form weighted least-squares
`L̃ = R Wᵀ… = (W−Q) H Rᵀ (R H Rᵀ)^{-1}` and `R̃ = L^† (W−Q) H H^†` with quantization after each
solve, tracking the best iterate (monotone by construction, no global guarantee). Theorem 4.1
splits the error into a rank-truncation tail plus a quantizer term shrinking as
`(2^{B_Q}−1)^{-2}` — the formal statement of the rank-vs-bits trade. Config that wins:
B_Q = 2, B_L = B_R = 4, k = 64–256, 2.1–2.4 bits/param total, with a randomized Hadamard
transform first to Gaussianize entries.

**What the ablations actually say two-stage loses.** At ≥3-bit effective: little. QERA-exact
(pure two-stage, closed form) beats LQER and matches/beats iterative baselines at 4-bit with
rank 32; EoRA (two-stage) posts its large gains without any alternation; LQ-LoRA's Fisher and
alternation margins shrink to ~0 at 3.0 bits on 70B-class models. At ≤2.5-bit effective: joint
methods clearly win (CALDERA is SOTA in <2.5 bpp precisely because Q's grid and the factors are
co-adapted; RILQ shows two-stage SVD saturates entirely at 2-bit). Intuition: at high bits, R is
small and nearly independent of the correction; at very low bits, which residual the quantizer
*leaves behind* is strongly shapeable, so ordering matters.

> **BOX 6 (verdict for our pipeline).** llama.cpp constrains Q to legal GGUF k-quant types with
> block-local scale fitting (imatrix-weighted least squares per block, no cross-column feedback).
> Plan: **two-stage analytic first** (imatrix k-quant → BOX 3 correction) — it captures nearly
> all available gain at Q3/Q4 targets and keeps the quantizer stock. Then add **one cheap
> alternation round**: requantize `W − B·A` with the same imatrix, recompute the correction,
> keep iff whitened error decreases (LQ-LoRA stopping rule). This is offline-only, ~2× quantize
> cost, and is the entire practical content of joint optimization until/unless we chase ≤Q2.
> If we later quantize A/B below 8 bits, adopt CALDERA's inner LPLR (quantize-after-each-solve)
> for the factors.

One llama.cpp-specific subtlety: k-quant block scales are refit when requantizing `W − BA`, so
alternation genuinely changes Q (it's not a no-op as it would be for a frozen grid); conversely
the correction changes which weights sit at block extremes — this is exactly the coupling
alternation exploits.

---

## 5. Practical numerics for 5120×25600-class matrices

### 5.1 Sizes and costs (fp32 unless noted)

Per tensor `m×n = 5120×25600` (worst case; qkv/o are 5120×5120-ish):

| Object | Size |
|---|---|
| `W`, `R` fp32 | 524 MB |
| `S` (n=25600) fp32 / fp64 | 2.62 / 5.24 GB |
| `G = R S Rᵀ` (m=5120) fp64 | 210 MB |
| economy SVD workspace (gesdd) | ~2–3× W |
| `A,B` r=64 fp32 | 7.9 MB |

- **Full economy SVD** of `R T` (5120×25600): ~`O(m²n) ≈ 6.7e11` flops core cost → seconds on a
  Blackwell GPU (cuSOLVER gesvdj/gesvda or torch.linalg.svd), ~1–3 min/CPU (MKL gesdd,
  multithreaded). Fully feasible even for ~400 tensors; hours on CPU, minutes on GPU.
- **Recommended instead — BOX 3 route:** form `G = R S Rᵀ` (`m n² + m² n ≈ 4e12` flops — do on
  GPU in fp32 with fp64 accumulation via chunked `(R S) Rᵀ`), then `eigh` of a 5120×5120 fp64
  matrix (~2 s). No SVD of a rectangular matrix, no factor of `S`, no inverse anywhere. Extract
  `B = U_r`, `A = U_rᵀ R`.
- **Randomized SVD** (`torch.svd_lowrank`, Halko–Martinsson–Tropp): only worth it if we skip the
  Gram route. Two warnings: (i) the default `niter=2` power iterations are calibrated for fast-
  decaying spectra; whitened residual spectra decay but the raw part is flat, so use
  `q = r + 128` oversampling and `niter ≥ 8`, or accuracy of the trailing kept directions
  suffers; (ii) it randomizes the energy diagnostic. At our sizes the exact routes are cheap —
  prefer them; keep randomized SVD for d_model ≥ 12k models.
- **Precision discipline:** accumulate `S` and `G` in fp64; eigh in fp64; everything else fp32.
  Never run the whitening chain in bf16 — the whole point is resolving a residual ~2⁻⁴ the size
  of W, and outlier channel scaling adds ~3 more orders of dynamic range.

### 5.2 Storing A and B

Overhead for rank r on an `m×n` tensor: `r(m+n)` params = `r(m+n)/(mn)` relative. For
5120×25600, r=64: 1.5% of weight count →

| A/B format | Added bits/weight-param | Quality (published) |
|---|---|---|
| F32 | 0.48 | reference |
| **F16** | **0.24** | indistinguishable (universal choice for eval) |
| Q8 (MXINT8 / Q8_0) | 0.12–0.13 | LQER runs *all* results with 8-bit factors: near-lossless |
| 4-bit | 0.06 | EoRA: −0.4 pt accuracy at rank 512; sometimes acts as regularizer |
| 3-bit | 0.045 | EoRA: −1.5 pt — not worth it |

Recommendation: **F16 GGUF tensors first** (zero risk, trivial in llama.cpp, +0.24 bpw at r=64);
move to Q8_0 once the pipeline is validated (halves overhead, LQER-grade evidence it's safe).
If factors go to 4-bit, split `Σ^{1/2}` across A and B (ASVD absorption) to balance ranges, and
switch the factor solve to quantization-aware LPLR (Sec. 4c).

Serving cost at batch-1 decode (memory-bound): extra bytes read per token = bytes(A)+bytes(B).
For r=64 F16 on the 5120×25600 tensor: 7.9 MB vs ~74 MB for the Q4_K base → ~10% bandwidth
overhead; Q8_0 factors → ~5.5%. FLOP overhead `2r(m+n)/(2mn) ≈ 1.5%` — negligible. The two
extra GEMVs are tiny and should be fused/streamed alongside the base matmul; EoRA ships a fused
CUDA kernel achieving up to 1.4× vs naive separate launches — same trick applies in ggml
(concatenate `A` rows into the same graph node, or custom op).

### 5.3 Whole-pipeline accounting

For a 27B-class model (~60 layers × ~7 tensors): S-accumulation is one forward pass over
~128×2k tokens with hooks (dominant cost: the forward itself). Per-tensor correction ~5–15 s on
GPU → under an hour end-to-end; LQER reports 1.2 A100-hours for LLaMA-33B with a weaker
pipeline, so this is the right order of magnitude. Everything is embarrassingly parallel across
tensors. Keep per-tensor artifacts (`eigvals(G)`, chosen r, holdout whitened error) — they feed
the allocator and the eval gate.

---

## 6. Algorithms we should implement, in order

1. **`S`-accumulation / imatrix bridge.** Hooked forward pass storing fp64 `diag(S)` per linear
   input (or parse existing llama.cpp imatrix files directly); optional full `S` behind a flag.
   Deliverable: per-tensor `s = sqrt(E[x²])`.
2. **Diagonal-whitened residual correction (QERA-approx ≡ L²QER).** `R = W − dq(Q)` from the
   stock imatrix k-quant; SVD of `R·diag(s)`; `B = U_r Σ_r`, `A = V_rᵀ diag(1/s)`; store F16.
   Fixed r = 64 everywhere. This is the minimum shippable increment — measure ppl vs base quant.
3. **Full-covariance correction via the inverse-free Gram form (BOX 3).** fp64 `S`,
   `G = R S Rᵀ`, top-r eigh, `B = U_r`, `A = U_rᵀ R`. Expect gains over (2) mainly at Q3 and
   below; keep (2) as the fallback where `S` (25600²) is annoying.
4. **Rank allocation (BOX 5).** Emit `eigvals(G)` for all tensors; greedy/threshold allocation of
   a global rank budget with cost `m+n`, weight `w_ℓ = 1` first; then calibrate `w_ℓ` per
   module *type* (attn vs FFN) from a small ppl ablation. Compare against uniform r at equal
   bytes — this is where published methods find their next 20–30% of quality.
5. **One-round alternation.** Requantize `W − BA` with the same imatrix → recompute correction →
   accept iff holdout whitened error decreases (LQ-LoRA stopping rule). Cheap; expected to
   matter at Q2_K/IQ2-class bases only.
6. **Factor quantization to Q8_0** with ASVD-style `Σ^{1/2}` splitting; verify vs F16 within
   noise (LQER evidence says it will be).
7. **Stretch (≤2.5-bit regime only):** CALDERA-style inner LPLR for quantized factors, and Fisher
   row-weighting (LQ-LoRA D_row) — gated on actually pursuing Q2-class bases, where two-stage
   demonstrably saturates (RILQ).
8. **Eval gate throughout:** per-tensor whitened-energy-captured curves + holdout
   `‖(R−BA)X‖/‖RX‖`, model-level ppl and 2–3 tasks, at fixed total bytes (base bits + factor
   bytes) so comparisons are budget-fair — the honest baseline is always "same bytes spent on a
   bigger base quant."

---

## 7. References

- **SVD-LLM** — Wang, Zheng et al., *SVD-LLM: Truncation-aware Singular Value Decomposition for
  LLM Compression*, ICLR 2025. arXiv:2403.07378. (Cholesky whitening; Thm 3.2 loss ≡ σ_i;
  sequential low-rank parameter update.)
- **ASVD** — Yuan et al., *ASVD: Activation-aware Singular Value Decomposition for Compressing
  LLMs*, arXiv:2312.05821. (Diagonal scaling `S_ii = (mean|X_i|)^α`, α=0.5; STRS binary-search
  rank allocation; Σ-splitting.)
- **LQER / L²QER** — Zhang, Cheng, Luk, *LQER: Low-Rank Quantization Error Reconstruction for
  LLMs*, ICML 2024. arXiv:2402.02446. (Diag scale Eq. 14; MP observation for raw error; k=32–64
  W4, k=256 W2; MXINT8 factors; hardware costing.)
- **QERA** — Zhang et al., *QERA: an Analytical Framework for Quantization Error
  Reconstruction*, ICLR 2025. arXiv:2410.06040. (Closed forms: exact `(R_xx^{1/2})` and approx
  diagonal; critique of LoftQ-style iteration; calibration-size monotonicity.)
- **EoRA** — Liu et al. (NVIDIA), *EoRA: Fine-tuning-free Compensation for Compressed LLM with
  Eigenspace Low-Rank Approximation*, arXiv:2410.21271. (Eigendecomposition route to the same
  estimator; rank 64–512 sweeps; 4-bit factor quantization; fused CUDA kernel, 1.4×.)
- **CALDERA** — Saha, Sagan, Srivastava, Goldsmith, Pilanci, *Compressing LLMs using Low Rank
  and Low Precision Decomposition*, NeurIPS 2024. arXiv:2405.18886. (Joint `min‖(Q+LR−W)Xᵀ‖`;
  LDLQ + LPLR alternation; Thm 4.1 error split; B_Q=2, B_L=B_R=4, wins <2.5 bpp.)
- **LQ-LoRA** — Guo, Greengard, Xing, Kim, *LQ-LoRA: Low-rank plus Quantized Matrix
  Decomposition for Efficient Language Model Finetuning*, ICLR 2024. arXiv:2311.12023.
  (Weight-space alternation + stopping rule; rank-1 Fisher → D_row/D_col weighted SVD; ILP
  budget allocation; Fisher matters ≤2.75 bits.)
- **ZeroQuant-V2 / LoRC** — Yao et al., arXiv:2303.08302. (Original quantize-then-SVD-residual;
  unweighted.)
- **RILQ** — Lee et al., *RILQ: Rank-Insensitive LoRA-based Quantization Error Compensation*,
  AAAI 2025. arXiv:2412.01129. (2-bit error is high-rank: 8–16× rank vs 3-bit; SVD saturation
  measurements; model-level activation-discrepancy training as the escape hatch.)
- **LoftQ** — Li et al., arXiv:2310.08659. (Alternating init for quantized LoRA fine-tuning.)
- **Weighted low-rank NP-hardness** — Gillis & Glineur, *Low-Rank Matrix Approximation with
  Weights or Missing Data is NP-hard*, SIAM J. Matrix Anal. 2011. arXiv:1012.0197.
- **Randomized SVD** — Halko, Martinsson, Tropp, *Finding Structure with Randomness*, SIAM Rev.
  2011. arXiv:0909.4061. (Basis for torch.svd_lowrank; power-iteration guidance for flat
  spectra.)
- **GPTQ** — Frantar et al., arXiv:2210.17323. (Hessian `H = XXᵀ`, percdamp convention, LDLQ
  lineage used by CALDERA.)
- Allocation follow-ups (2025–26, skimmed): LAARA arXiv:2607.19391 (Fisher normalization
  caveats); UniRank arXiv:2606.21847; Bayesian-opt allocation arXiv:2405.10616; LASER
  arXiv:2606.00573; SERQ arXiv:2603.08185 (saliency-aware residual reconstruction, notes
  LLaMA-3 spectra stay flat past rank 256 — consistent with Sec. 2.1).

*Derivations in Boxes 2–5 (factor invariance, inverse-free Gram form, MP rank-capture bound for
our shapes, whitened water-filling) are ours; verify Box 3 numerically against a direct
`[RT]_r T^{-1}` implementation as the first unit test of the pipeline.*
