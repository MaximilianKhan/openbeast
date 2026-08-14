> ⚠ see review/ corrections 2026-08-04

# Structured Alternatives to Dense Low-Rank for Residual Correction

**Purpose.** E04c measured the wall: diagonal-whitened *dense* rank-64 correction captures
0.37 of whitened residual energy at 0.6B (d=1024) but only 0.07 at 27B (d≈5120), because
capture at fixed rank scales as r/d. This survey maps every published *alternative* structured
decomposition of LLM weights or corrections (2022 → mid-2026), scores each on the one question
that matters — **how does capture-per-byte scale with dimension** — and ends with a ranked
shortlist for beating dense rank-r as a *correction to a quantized base* at 27B+.

**Survey date:** 2026-08-03. Companion docs: `survey-lowrank-compression.md` (the dense-LR
lineage LQER→QERA→EoRA→CALDERA), `math-methods.md` (notation: `y = Wx`, `W ∈ R^{m×n}`,
`R = W − dequant(Q)`, `S = E[xxᵀ]`, whitened residual `R' = R·S^{1/2}` or `R·D` diagonal).

---

## 0. The scaling framework (read this first — it decides the shortlist)

### 0.1 The measured two-point observation (demoted from "law", review 2026-08-04)

Our two scale points give, for diagonal-whitened dense rank-r on attn_q/k-class tensors:

```
capture(r, d) ≈ c · r/d        c ≈ 5.9 (d=1024),  c ≈ 5.6 (d=5120)
```

The concentration constant c is what whitening buys over a purely isotropic residual
(isotropic ⇒ capture ≈ r/d exactly, up to Marchenko–Pastur edge effects). c held nearly
constant across a 5× dimension jump — **the whitened residual is "c-concentrated isotropic"
at both scales measured.** Caveat (review F6): n=2 for a one-parameter fit — and c is NOT a
constant of the geometry: our own full-covariance captures give c ≈ 9.6 (0.6B fc r64),
≈ 16.8 (27B mixed fc), ≈ 20.8 (NVFP4 fc) — c is a function of (whitener, base, arch),
measured at one rank on two models under the diagonal whitener. Everything below is
evaluated against this diag-r64 observation, pending the E21-sweep/0.8B regression.

### 0.2 Capture-per-byte is already scale-invariant for dense LR

Bytes of a rank-r F16 pair on an m×n tensor: `2·r(m+n)` vs base tensor `mn·b_q/8`. For square d:

```
byte_fraction f = 2r(m+n)·8 / (mn·b_q) ≈ 32r/(d·b_q)      capture ≈ c·r/d = (c·b_q/32)·f
```

So capture per byte-fraction is **independent of d**. The 27B "collapse" is a fixed-rank
artifact: matching 0.37 capture at d=5120 needs r ≈ 320, at the *same bpw overhead* as r=64
at 0.6B. Corollary: dense LR does not lose to scale per byte — the question is whether any
structure gets **more capture per byte than c·b_q/32**, i.e. beats the constant c, not the law.

### 0.3 The parameter-counting bound

For a *fully isotropic* residual (i.i.d. Gaussian entries), any p-parameter matrix family F
satisfies `E[max_{M∈F} capture(M)] ≲ p/d²` (a p-dimensional manifold cannot align with more
than ~p of the d² i.i.d. energy directions; low-rank, Monarch, Kronecker, sparse all obey it,
and all *achieve* it up to constants). Structures therefore differ only via the **non-isotropic
structure they can exploit**:

| exploitable structure in R | who exploits it | dimension-scaling of the win |
|---|---|---|
| spectral concentration (c) | dense LR after whitening | constant c — measured 5.6× |
| heavy-tailed entries (kurtosis) | sparse component | **d-independent** (tail fraction) |
| quant-block-grid alignment | tile/Kronecker rearrangement | tied to block size, not d |
| cross-layer subspace correlation | shared basis | amortizes basis cost by T layers |
| none (post-hoc rotation) | — | zero, by §0.4 |

### 0.4 The rotation-invariance lemma (kills a whole category as post-processing)

For any orthogonal U, V: `σ(U·R·V) = σ(R)`. Rotating a *fixed* residual cannot improve its
low-rank approximability. Rotations (QuIP/QuaRot/SpinQuant family) pay **only when applied
before quantizing the base**, because they change which residual the quantizer produces —
smaller, more Gaussian, and (with deliberate scale-migration, SVDQuant-style) with error
energy *steered into a small subspace*. "Concentrate-then-correct" attacks c itself; it is
the only lever that raises capture-per-byte without new structure at inference time.

### 0.5 Independent corroboration that rank must scale with d

Published successful corrections quietly agree with our law — their winning ranks are
proportional to width, not constant:

- **Meta LRC** (arXiv:2412.07902): rank = 10% of d halves the W4A4 accuracy gap, 30% closes
  it — explicitly fractional-rank, on Llama-2/3, Phi-3, Mixtral.
- **ResQ** (arXiv:2412.14363, ICML 2025): keeps a fixed **1/8 of dimensions** (r = d/8) in
  8-bit — again r ∝ d.
- **CALDERA** (arXiv:2405.18886): ranks 64–256 at 70B *only after* randomized-Hadamard
  incoherence processing + activation-aware objective — sub-proportional rank rescued by
  concentrating first (§F).
- **SVDQuant** (arXiv:2411.05007): rank 32 at d≈3k works because smoothing *migrates* outlier
  energy into the top singular directions before the SVD — engineered concentration, the
  strongest single data point for §0.4's lever.

---

## A. Sparse + low-rank splits (W = Q + S + L, robust-PCA lineage)

Robust PCA (Candès et al. 2009, arXiv:0912.3599) established that a matrix that is
(low-rank + entrywise-sparse) can be exactly split by convex programming. The LLM literature
rediscovered the split from the outlier end:

- **SpQR** (arXiv:2306.03078). Structure: 3–4-bit grouped base + **0.1–1% of weights kept in
  FP16 as an unstructured sparse matrix** (CSR), chosen by sensitivity. Key empirical fact for
  us: a sub-1% sparse set carries the bulk of quantization damage — heavy tails are real and
  the tail *fraction* does not grow with d. Ships CUDA sparse-matvec kernels for batch-1 decode.
- **SqueezeLLM** (arXiv:2306.07629). Dense-and-sparse split: ~0.45% outliers + sensitive
  weights in CSR, non-uniform (k-means) base. Same message, independent codebase.
- **LoSparse** (arXiv:2306.11222). W ≈ L + S with L low-rank, S column-sparse, trained
  (BERT-era). First explicit "low-rank captures the shared bulk, sparse captures the spikes"
  framing.
- **OATS** (arXiv:2409.13652). Training-free W = S + L via alternating thresholding, weighted
  by second moment of inputs (imatrix-equivalent!). SOTA at 50–60% compression on Llama-3,
  Phi-3. Directly reusable math: their alternating step is our Gram-route SVD + magnitude
  thresholding in a loop.
- **HASSLE-free** (arXiv:2502.00899). Unifies the family: minimizes the **activation-weighted**
  error `‖(W−S−L)X‖_F` with alternating closed-form-ish updates. Finding: low-rank captures
  more error per parameter in *most* layers, but the optimum is always a mix — sparsity takes
  the kurtosis, LR takes the spectrum. This is the exact objective our lab already computes.
- **SLiM** (arXiv:2410.09615). One-shot quantization + 2:4 *structured* sparsity + low-rank,
  ships with hardware-friendly N:M patterns.
- **SDQ** (arXiv:2406.13868). Sparse-decomposed quantization for inference efficiency.
- **LOST** (arXiv:2508.02668). Low-rank + sparse for *pre-training* — evidence the split is
  natural for LLM weights, not just residuals.
- **ARCQuant** (arXiv:2601.07475). Not low-rank at all: identifies top-S outlier *channels*,
  re-quantizes their residuals, and **appends them as extra columns to the GEMM**
  (`Y ≈ Q(X)Q(W)ᵀ + Q(R_o)Q(W_o)ᵀ` fused as one (N, K+S, M) GEMM). ≤512 channels total,
  3–9% latency, stock CUTLASS. The "augment the K-dimension" trick is the cleanest fused-kernel
  pattern in this whole survey and applies verbatim to any column-structured correction.

**Params-per-quality math.** A k-entry sparse component with indices costs
`k·(16 + log2(d²)) bits ≈ k·40 bits` (or ~k·17 bits with bitmap at 0.4%+ density) and captures
`η(k)` = the energy fraction in the top-k residual entries. For a heavy-tailed distribution
with excess kurtosis κ, η(k) at fixed *fraction* k/d² is **independent of d** — the only
component in this survey whose capture does not dilute with scale at fixed byte-fraction
beyond dense LR's constant. Whether it beats LR depends on measured κ of *our whitened
residual*: if the top 0.1% of entries hold ≥ a few % of energy, S wins those bytes; the E03/E05
census data can answer this tonight without new instrumentation.

**Inference cost.** Batch-1 decode is bandwidth-bound: adding a 0.3% CSR matvec reads
~0.3%·extra bytes — noise. llama.cpp has **no sparse matvec op** — this is a real new CUDA
kernel (SpQR/SqueezeLLM kernels exist as reference, both open). Alternatively ARCQuant-style
column-append needs zero new kernels if outliers are column-structured.

**As correction:** natural — S is *defined* on the residual. Composes with everything else.

## B. Kronecker / tensor-train / tensor-ring factorizations

- **Kronecker GPT compression** (arXiv:2110.08152) and **KronA** (arXiv:2212.10650, PEFT):
  W ≈ A⊗B. BERT/GPT-2 era; needs retraining to work.
- **TQCompressor** (arXiv:2401.16367). Kronecker + *permutations* to raise alignment,
  GPT-2-scale, retraining required.
- **TensorGPT** (arXiv:2307.00526). Tensor-train of the *embedding* matrix only.
- **CompactifAI** (arXiv:2401.14109, Multiverse). Tensor networks on Llama-2-7B → 30% size,
  ~90% accuracy *after distributed retraining* — the retraining is load-bearing.
- **Saten** (EMNLP 2025): sparse-augmented tensor networks — even the TT camp now adds a
  sparse component (converges with §A) and fine-tunes.
- **ProcrustesGPT** (arXiv:2506.02818). Structured matrices + orthogonal Procrustes
  transforms — the weight is rotated to *fit the structure* before factorizing (a §0.4-legal
  move since the rotation is folded into adjacent tensors).
- **The verdict paper — "Rethinking the Role of Tensor Decompositions in Post-Training LLM
  Compression"** (arXiv:2606.03465, 2026): systematic head-to-head of TT/Tucker/CP/Kronecker
  vs plain SVD vs quantization for *post-training* compression at GPT-J/Llama scale.
  Finding: tensor decompositions **underperform plain SVD at equal parameter count**, and
  everything loses to quantization. Post-training, training-free: the family is measured dead
  as a standalone.

**Params-per-quality math.** Van Loan's theorem: the best `Σᵢ Aᵢ⊗Bᵢ` (k terms, tiles
d₁×d₂ ⊗ d/d₁×d/d₂) is exactly the rank-k SVD of the **rearranged** matrix R̃ (tiles → rows).
Rearrangement preserves Frobenius energy, so Kronecker capture = spectral concentration of R̃.
For a generic residual R̃ is as isotropic as R — hence the 2606.03465 null. **The one untested
exception, and it is ours to test:** GGUF residuals are not generic — they inherit the
**quantization block grid** (256-element superblocks, shared scales). A per-block scale error ε_b
makes the residual restricted to block b approximately `ε_b · round(W_b)` — i.e. **rank-1 in the
tile-rearranged space, coherent across all blocks that share a scale pattern**. Van-Loan-rearranged
SVD with tiles = quant blocks would sweep up *all* scale-grid error with a handful of terms,
which plain SVD (which sees that structure smeared across all d directions) cannot. Cheap
falsification: rearrange the E03 residual tensors to (n_blocks × 256) and look at the spectrum —
one evening, existing data.

**Inference cost.** `y += (A⊗B)x = vec(B·mat(x)·Aᵀ)` — a reshape and two *small dense* GEMMs;
cheaper than the equivalent dense-LR pair and CUDA-trivial (no new primitive, just views).
Tile-rearranged correction similar (block-local rank-1 update = one broadcast FMA per block,
fusable into dequant epilogue — arguably *easier* than LoRA fusion).

**As correction:** untested in the literature against residuals (all published work targets W).
Grid-aligned variant is a genuine open lane.

## C. Butterfly / Monarch / block-diagonal structured matrices

- **Pixelated Butterfly** (arXiv:2112.00029), **Monarch** (arXiv:2204.00595),
  **Monarch Mixer** (arXiv:2310.12109). Monarch: `M = P·L·P'·R` with L, R block-diagonal
  (b blocks of (d/b)²), p = 2d²/b params, expressible up to arbitrary rank. Designed for
  *training* efficient layers; projection of a dense W onto Monarch has a closed form
  (per-block SVDs), but all headline results involve training.
- **BLAST** (arXiv:2410.21262, NeurIPS 2024). Block-level adaptive structured matrix:
  block-partitioned W where block (i,j) = Uᵢ Σᵢⱼ Vⱼ — **shared left/right bases per block-row/
  column, tiny per-block cores**. Subsumes low-rank, Monarch, block-diagonal. 50% compression
  of ViT/GPT-2-class models, 70% FLOP cuts — with gradient-based factor fitting + retraining.
- **MoRe** (arXiv:2408.17383). Monarch-parameterized fine-tuning beats LoRA at 10× fewer
  params — evidence Monarch's *expressivity* per param beats LR **when trained with gradients
  against the task loss**, not when fit in closed form to a target matrix.
- **Sparse block-diagonal LLM acceleration** (arXiv:2510.11192) — hardware-side confirmation
  that block-diagonal GEMMs map well to accelerators.

**Params-per-quality math.** For closed-form fitting of a c-concentrated near-isotropic
residual, Monarch/BLAST with p params capture ≈ p/d² — they hit the §0.3 bound but **cannot
exploit spectral concentration as efficiently as global low-rank**, because their basis
directions are confined to block supports; the global top-r singular directions are exactly
what they cannot represent cheaply. At our correction budgets (p ≪ d²) dense LR ≥ Monarch
on capture, with equality only if the residual's energy is block-localized (it isn't — E03
showed spatially flat energy). Where BLAST-type structure wins instead: **large budgets**
(capture ≥ 0.5 wants r ≥ d/10, where dense-LR GEMMs get fat and BLAST's shared-basis-per-
block-row starts amortizing like §D within a single tensor).

**Inference cost.** Grouped/batched GEMM + permutation; fine on CUDA (cuBLAS batched or one
custom kernel), but more launch surface than two skinny GEMMs. No llama.cpp precedent.

**As correction:** no published instance of Monarch/BLAST fitted to quantization residuals.
Their published wins all involve gradient training — for a training-free pipeline they are
strictly dominated by dense LR at small p. Skip unless we later add gradient refinement.

## D. Cross-layer / joint factorization and basis sharing

The only family that changes the *scaling law* rather than the constant:

- **Basis Sharing** (arXiv:2410.03765, ICLR 2025). Consecutive layers share one singular
  basis; per-layer coefficient vectors. Beats SVD-LLM at 20–50% compression on Llama/Mistral.
  Weight-space objective, whitened SVD-LLM-style.
- **The 2026 negative result — Cross-Layer Subspace Coupling** (arXiv:2605.30836). Unifies
  SVD-LLM/Basis Sharing as one Grassmannian optimization; tighter coupling improves weight
  reconstruction up to 46% yet **perplexity severely degrades** vs per-layer SVD-LLM (Pythia
  scale). Mechanism: the residual stream decouples layers at forward-pass time; weight-space
  reconstruction is the wrong objective cross-layer. Their prescription matches our E03 lesson
  exactly: **per-layer activation-weighted reconstruction or bust.**
- **Mechanism donors from PEFT** (structure, not compression results):
  **VeRA** (arXiv:2310.11454) — one *frozen random* pair (A,B) shared by every layer, per-layer
  only two scaling vectors (d+r params); **NOLA** (arXiv:2310.02556) — per-layer weights are
  linear combos of shared random bases (params = #coefficients, fully decoupled from d);
  **LoRA-XS** (arXiv:2405.17604) — W_l += B·C_l·A with shared/frozen B, A and a tiny r×r
  trainable core C_l. These prove the *serving pattern* (shared factors resident once, tiny
  per-layer cores) at fine-tuning quality; nobody has published it for quantization-error
  correction.
- **ESPACE** (arXiv:2410.05437, NVIDIA) and **Dobi-SVD** (arXiv:2502.02723, ICLR 2025) —
  activation-side projection: compress the *activation* space with a calibrated projector;
  weight factorization falls out by associativity. Activation-centric = automatically
  activation-weighted; ESPACE gets 50% compression at GPT3-22B with retraining.
- **MoE-SVD** (2025) — SVD sharing across *experts* (high inter-expert redundancy, a special
  case of cross-layer sharing that does work, because experts see the same input distribution).
- **Cross-layer error compensation** (arXiv:2607.14630) and **QEP** (arXiv:2504.09629) —
  propagate/compensate quantization error *across* layers during quantization; orthogonal to
  structure but confirms cross-layer error correlation is real and exploitable.
- **Cousin literature that quietly validates residual-specific sharing:** finetune-delta
  compression — **BitDelta** (arXiv:2402.10193, "your fine-tune may only be worth 1 bit":
  sign matrix + one scale per tensor ≈ good enough) and **Delta-CoMe** (arXiv:2406.08903,
  mixed-precision by singular-value magnitude: 16-bit for top vectors, 3/2-bit for the tail,
  beats both pure-LR and pure-quant baselines). Delta-CoMe's result — **quantize the long tail
  of singular vectors instead of truncating it** — transfers verbatim to our correction factors
  and multiplies any structure's capture-per-byte by ~2–4× (F16 → ~4-bit factors).

**Params-per-quality math (the big one).** Shared pair (A ∈ R^{r×n}, B ∈ R^{m×r}) across T
tensors + per-tensor core C_l (r×r):

```
bytes_dense-per-tensor  = 2rd      bytes_shared-marginal = r²  (+ 2rd/T amortized)
equal total bytes over T tensors:  r_shared ≈ sqrt(2·r_dense·d)   (for r² ≪ 2rd, T large)
```

At d = 5120, r_dense = 64: r_shared ≈ 810 — a **12× rank amplification** at equal bytes.
Capture from a shared basis, though, is NOT c·r_shared/d; it is
`Σᵢ cos²θᵢ`-weighted — the overlap of the shared subspace with each layer's whitened residual
subspace. Two regimes: if per-layer residual subspaces are uncorrelated (random), a shared
r-basis captures only r/d per layer (no better than dense at equal *marginal* cost, and we
lose c) → total loss. If they overlap substantially (plausible: all layers read the same
residual stream, the imatrix whiteners are visibly similar across layers, and rotation methods
work precisely because outlier directions are global), amplification wins by up to
sqrt(2d/r_dense). **The decisive quantity is measurable from data we already have:** stack the
top-k whitened right singular vectors of each layer's residual (E05 artifacts), compute
pairwise principal angles / the spectrum of the stacked Gram — one script, no GPU. The
2605.30836 failure does not directly kill this: their coupling constrained the *reconstruction
of W* in weight space; ours keeps per-layer C_l fit to the per-layer activation-weighted
objective (shared basis only constrains the *span*, the objective stays per-layer — exactly
what their post-mortem prescribes).

**Inference cost.** Identical GEMM shapes to dense LR plus one tiny r×r GEMM; A, B live in
VRAM **once** for the whole model (extra VRAM win). llama.cpp's LoRA path would need a small
extension (shared-tensor adapters), or export per-layer (B·C_l, A) pairs materialized — which
forfeits the byte win, so the real deployment needs the C_l form: a modest gguf/ggml change,
no new math kernels.

## E. Block-wise / group low-rank (low-rank per column group)

- **BLAST** (arXiv:2410.21262) again — its shared-basis-per-block-row IS group low-rank with
  intra-tensor sharing.
- **FLRQ** (arXiv:2601.05684) — flexible per-layer rank via sketching; confirms adaptive rank
  ≫ uniform rank (our "870 MB mostly wasted" finding, published).
- **TileQ** (arXiv:2605.09281) — 2D-tiled low-rank quantization for MoE.
- **Group-quant literature** (M-ANT arXiv:2502.18755, dynamic grouping arXiv:2509.03054,
  two-stage grid arXiv:2602.02126) — group-wise *quantization* keeps getting finer; the
  residual's structure is increasingly grid-shaped (feeds §B's grid-aligned idea).
- **CoSpaDi** (arXiv:2509.22075) — sparse *dictionary* factorization (W ≈ D·X, X column-sparse,
  calibration-guided): a middle point between LR and sparse; column-sparse coefficient matrix
  over a shared dictionary = group-LR with learned groups.

**Params-per-quality math.** Partition columns into G groups (d/G each), rank r_g per group:
capture = Σ_g e_g·c_g·(r_g·G/d) where e_g = group's energy share. Beats global LR only if
energy or spectrum concentration varies strongly *by column group after whitening* — but
whitening explicitly equalizes column energies, so the expected win is small; what remains is
finer-grained rank allocation (real but incremental — same lever as per-tensor water-filling,
one level down). At equal params it can also *lose* global structure (a global direction costs
G group-directions).

**Inference cost.** G smaller GEMM pairs — worse launch profile than one pair (we measured
launch-dominance at r16 already). Would want a single batched kernel.

**As correction:** fine, but expected to be a refinement (≤1.5×), not a scaling-law change.

## F. Rotation-based outlier redistribution — and rotation+low-rank combos

Rotation-only lineage (all modify the *base quantization*, per §0.4):

- **QuIP** (arXiv:2307.13304) / **QuIP#** (arXiv:2402.04396) / **QTIP** (arXiv:2406.11235) —
  incoherence processing via random orthogonal/Hadamard conjugation + lattice/trellis codes;
  2-bit viable at 70B. The E8/trellis codebooks are the strongest pure-base competitor our
  corrected-Q2_K must beat.
- **QuaRot** (arXiv:2404.00456) — Hadamard rotations; **R1 folds into adjacent weights at zero
  runtime cost**, only down_proj/o_proj need online Hadamards. W4A4 near-lossless at 70B.
- **SpinQuant** (arXiv:2405.16406) — *learned* rotations (Cayley-optimized), beats random
  Hadamard by a consistent margin.
- **FrameQuant** (arXiv:2403.06082) — fusion frames: quantize in a redundant (~1.1×
  overcomplete) frame representation; robustness from redundancy, ~2.2 effective bits.
- **DuQuant** (arXiv:2406.01721) — rotation + zigzag *permutation* to spread outliers.
- **OSTQuant** (arXiv:2501.13987) — orthogonal + scaling transform jointly learned.
- **Grid-aligned upstart:** **LoPRo** (arXiv:2601.19675) — **block-wise** permutation +
  Walsh–Hadamard rotations (blocks = quant groups, salient blocks exempted) + a rank-1-sketch
  mixed-precision LR component; 2–3-bit SOTA on Llama-2/3, Mixtral in 2.5 h, "superior accuracy
  with significantly lower rank." Block-wise rotation keeps the transform *inside* the quant
  grid → no online-Hadamard tax, foldable per-block.

Rotation + low-rank combos (the direct competitors for our slot):

- **CALDERA** (arXiv:2405.18886) — W ≈ Q + LR with QuIP#-style incoherence on Q *and* LR
  factors themselves quantized (4-bit); sub-2.5-bit regime at 70B.
- **SVDQuant** (arXiv:2411.05007) — smooth (scale-migrate) activations→weights, take rank-32
  SVD of the *scaled* weight (top directions now hold the migrated outliers), quantize the
  remainder to 4-bit; Nunchaku fused kernel eliminates the LR branch's latency (their unfused
  measurement: LR branch alone ≈ 50% of quantized-GEMM time — matches our −33%).
- **ResQ** (arXiv:2412.14363) — PCA of activation covariance → top-d/8 subspace kept 8-bit,
  rest 4-bit, random rotation *within* each subspace; provably optimal split under their model;
  beats SpinQuant by up to 33% ppl.
- **Meta LRC** (arXiv:2412.07902) — joint optimization of Q and LR correction targeting
  *activation* quantization error; the r ∝ d datapoint of §0.5.
- **CoQuant** (arXiv:2604.26378) — joint weight–activation subspace projection for
  mixed precision.
- **SERQ** (arXiv:2603.08185) — 2026 saliency-weighted LR error reconstruction (EoRA-family
  refinement: importance-weighted whitening instead of plain activation second moments).
- **Delta-CoMe** (arXiv:2406.08903) — see §D; mixed-precision *within* the correction factors.

**Params-per-quality math.** Rotations cost ~0 params (folded) or 0 params + O(d log d)
runtime (online Hadamard). Their value is multiplicative on everything else: incoherent
quantization shrinks ‖R‖ (QuIP theory: ~log-factor tighter proxy loss) and deliberate
scale-migration raises the correctable concentration c → ĉ. SVDQuant's r=32-suffices result
implies ĉ ≫ 10 is achievable when you *choose* what the residual contains, vs our passive
c ≈ 5.6. This is the highest-leverage change to our pipeline that requires no new inference
structure at all (R1-fold + per-channel scale migration are GGUF rewrites; the corrected model
is mathematically equivalent and llama.cpp-servable **today**, minus down/o_proj which keep
the stock path).

**CUDA feasibility.** Fold-only subset: zero kernel work. Full QuaRot: needs an online
Hadamard ggml op (small, well-understood kernel; no upstream precedent yet — a plausible PR2.5).

## G. Mixture / routing of corrections

- **MiLo** (arXiv:2504.02658) — "mixture of low-rank compensators" for 3-bit MoE. Reality
  check: compensators are **per-weight-matrix, not routed and not shared** — the "mixture"
  is a *rank-allocation policy* (Dense-r for attention/dense layers, Kurtosis-r for outlier-
  heavy experts, Frequency-r for hot experts). HQQ base + truncated-SVD residual, ~10
  alternating iterations; compensators themselves INT3-quantized; +1.4% memory recovers 87%
  of Mixtral-8×7B INT3 loss. Ships zero-waste 3-bit Tensor-Core kernels (1.2× vs MARLIN).
  Verdict: MiLo is *our exact architecture* on MoE, plus the lesson: quantize the factors,
  allocate rank by kurtosis and traffic.
- **Bandwidth-efficient adaptive MoE via low-rank compensation** (arXiv:2512.17073) — stream
  low-rank compensators for offloaded experts (compensator as *upgrade path*, not resident).
- **MoE-LoRA family** (PERFT arXiv:2411.08212, DR-LoRA arXiv:2601.04823, MoE²-LoRA
  arXiv:2607.21978, confidence-adaptive routing arXiv:2607.26052) — routed low-rank *adapters*
  for fine-tuning; routing multiplies expressivity per *active* param.

**Params-per-quality math.** Routing K bases with k active gives compute ∝ k, but **bytes ∝ K**
— capture-per-byte does not improve, only capture-per-FLOP. Our binding constraint is VRAM
bytes (bandwidth inversion: fewer bytes = faster decode), so routing solves the problem we
don't have. Exception: MoE targets, where per-expert corrections with traffic-weighted rank
(MiLo) is the right shape for the 35B-A3B model.

---

## Ranked shortlist — what can beat dense rank-r per byte at 27B+ residual correction

Ordering metric: expected capture-per-byte gain × evidence strength × cost to falsify in this
lab. Baseline: diagonal-whitened dense LR, capture ≈ 5.6·r/d, F16 factors.

**1. Quantize the correction factors (Delta-CoMe / CALDERA / MiLo move).** Not a new
structure — a 2–4× multiplier on *any* structure's capture-per-byte, with three independent
published confirmations (CALDERA 4-bit factors, MiLo INT3 factors at 37.5% of INT8 size,
Delta-CoMe mixed-precision tail). F16 → Q8/Q4 factors doubles-to-quadruples our effective rank
at fixed bytes: r64-F16 bytes buy r128–r256 quantized. Zero inference change (llama.cpp
adapters can be Q8_0 already). Falsify: rerun E04c with Q8_0/Q4 factors — hours.

**2. Concentrate-then-correct: scale migration + foldable rotations before base quantization
(SVDQuant/QuaRot-R1/LoPRo lane).** The only lever that attacks the constant c itself (§0.4);
SVDQuant proves engineered concentration lets rank 32 do what passive rank ~500 would;
CALDERA proves it at 70B/2-bit; LoPRo shows block-wise (quant-grid-respecting) rotations
avoid the online-Hadamard tax. Fold-R1 + per-channel migration are pure GGUF rewrites —
servable by stock llama.cpp. Falsify: apply a folded rotation + smoothing α-sweep to the 27B,
requantize Q2_K, measure whitened concentration c and the KLD triangle. Days, no new kernels.

**3. Sparse outlier component: W ≈ Q + S + L (SpQR/OATS/HASSLE-free).** The only structure
with **dimension-independent** capture (tail energy fraction, §A math); activation-weighted
alternating fit is published (HASSLE-free, OATS) and matches our existing objective; composes
with 1 and 2. Cost: llama.cpp needs a sparse-add kernel (SpQR's exists as reference) — the
real price, so it ranks below the two zero-kernel wins. Falsify first with data already on
disk: measure η(top-0.1%, 0.5%, 1%) of whitened 27B residual energy — if η(0.5%) ≲ 5%, kill it.

**4. Cross-layer shared basis with per-layer cores: W_l ≈ Q_l + B·C_l·A (VeRA/LoRA-XS
structure, Basis Sharing evidence, 2605.30836 guardrails).** The only structure that changes
the *scaling law*: r_shared ≈ sqrt(2·r_dense·d) at equal bytes ⇒ ~12× rank amplification at
d=5120. High variance: the win exists iff whitened residual subspaces overlap across layers,
and the 2026 coupling paper proves weight-space sharing without per-layer activation objectives
fails. Our variant keeps per-layer activation-weighted C_l, dodging their failure mode.
Falsify before building anything: principal angles between per-layer whitened residual
subspaces from E05 artifacts — one CPU script. If mean cos² ≳ 0.3 at useful k, this is the
paper-grade novelty of the campaign.

**5. Quant-grid-aligned tile rearrangement (Van Loan SVD with tiles = GGUF superblocks).**
Novel, unpublished (2606.03465 killed generic tensor decompositions but never tested
grid-aligned rearrangement on *residuals*); mechanism is concrete (block-scale error is
rank-1 in rearranged space); inference is two small GEMMs / a per-block FMA epilogue —
easier to fuse than LoRA. Ranked 5th only on evidence (pure conjecture until the spectrum
is plotted). Falsify: rearrange E03 residuals, plot spectrum vs unrearranged — one evening.

**6. Group/block low-rank with allocation (FLRQ/BLAST-lite).** Real but incremental —
finer-grained water-filling, expected ≤1.5×, worse launch profile. Fold its one good idea
(adaptive rank everywhere, including *zero* rank for o/down-class flat tensors) into the
main pipeline and skip the rest.

**7. Monarch/butterfly/BLAST as correction.** No closed-form capture advantage on a
c-concentrated isotropic residual (§C math); every published win requires gradient training.
Dead for a training-free pipeline; revisit only if we add a fine-tuning stage.

**8. Kronecker/TT/tensor-ring, generic.** Measured dead post-training (arXiv:2606.03465).
Only the grid-aligned special case (#5) survives.

**9. Routed mixtures of corrections.** Improves capture-per-FLOP, not capture-per-byte;
we are byte-bound with FLOPs to spare (bandwidth inversion). Keep MiLo's rank-policy ideas
(kurtosis/traffic allocation, quantized factors) and its 3-bit kernel tricks; discard routing.
Re-open only for the 35B-A3B MoE.

**Composite bet:** the structure that beats dense rank-r at 27B is not one alternative but the
stack 1+2+3: *rotated/migrated base → Q2-quantized with imatrix → sparse top-0.1% outlier fix
→ quantized-factor low-rank on the concentrated remainder*, with 4 as the scaling-law
wildcard to measure this week and 5 as the cheap lottery ticket. Every element has a
published existence proof at ≥7B; no element requires a new llama.cpp kernel except the
sparse add (and #2/#1 alone may already close the gap to Q3_K_M).
