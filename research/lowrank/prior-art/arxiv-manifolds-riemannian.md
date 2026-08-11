> ⚠ see review/ corrections 2026-08-04

# arXiv sweep: Riemannian / matrix-manifold optimization for low-rank fitting and quantization

**Sweep date:** 2026-08-04 (Max directive 2026-08-04b, slice 1 of 3).
**Scope:** optimization on fixed-rank/Stiefel/Grassmann/flag manifolds, arXiv
1991–2026 plus classical roots (Stiefel 1935, Grassmann 1844, Riemann 1854
lineage); weighted low-rank approximation theory; manifold methods touching
quantization; alternation-with-discrete-set convergence; tooling.
**Our contact points:** (i) factors B,A of the whitened residual correction
live on fixed-rank / Stiefel×GL manifolds and have only ever been fitted with
flat closed forms; (ii) the two-sided Fisher-weighted objective has no
closed form — is Riemannian optimization the right solver?; (iii) E11
alternation = block descent between a manifold factor and a discrete
quantizer-grid set — formalize it; (iv) rank allocation / rank-adaptive
serving geometry.
**Companion docs:** `math-methods.md` (the P1 objective and one-sided closed
form), `arxiv-whitening-allocation.md` (QERA/covariance machinery),
`arxiv-qec-lineage.md` (TwinQuant §), `arxiv-outliers-scaling.md` (rotation
quants), `arxiv-structures.md` (the Grassmannian basis-sharing failure).

---

## Verdict up front

1. **The closed-form boundary is sharper than we stated.** Two-sided
   weighting with *separable* (Kronecker) weights ‖L(R−Δ)M‖_F still has an
   Eckart–Young closed form (whiten both sides, SVD, unwhiten). Closed forms
   die only at **elementwise non-separable weights** F_ij — and that problem
   is NP-hard in general (Gillis–Glineur 1012.0197). The exact diagonal
   Fisher F_ij = E[(g_i x_j)²] is elementwise; its Kronecker (K-FAC-style)
   approximation E[g_i²]·E[x_j²] restores the closed form. **So the first
   move is not a Riemannian solver — it is a one-day diagnostic: how far is
   the measured elementwise Fisher from rank-1 (Kronecker)?** If near, the
   two-sided *closed form* (left grad-whitening added to our fc) is a free
   upgrade and manifold machinery is unnecessary for fitting.
2. **If elementwise weighting matters, the right solver is not
   heavy Riemannian machinery** (embedded fixed-rank RTR, retractions,
   vector transport) **but its degenerate practical form: preconditioned
   factored gradient descent** — ScaledGD (Tong–Ma–Chi 2005.08898), which
   *is* Riemannian GD under the Mishra–Sepulchre quotient metric
   (1405.6055), ~20 lines of PyTorch, no retraction, condition-number-free
   local rate. Weighted-LRA-specific manifold SGD papers exist (2502.14174,
   2503.11833) but add nothing we need beyond ScaledGD + warm start from
   the closed form.
3. **Manifold optimization has already shipped inside LLM quantization —
   as learned rotations, not as factor fitting.** SpinQuant (2405.16406,
   Cayley SGD on SO(n)), OSTQuant (2501.13987), TwinQuant (2606.01556),
   RSAVQ (2510.01240, Fisher-metric-guided VQ that beats QuIP#/VPTQ at
   2-bit). Nobody optimizes the *correction factors* on a manifold under a
   Fisher metric and nobody formulates the quantizer grid as a discrete
   subset of a matrix manifold — both are open, the first is ours to take,
   the second is elegant but unlikely to move KLD (see §d).
4. **Our E11 alternation is provably convergent for free** — descent +
   finitely many code patterns ⇒ codes stabilize, then the factor step is
   exact ⇒ convergence (§d). The KL/PALM and transversality literature
   (0801.1780, Bolte–Sabach–Teboulle, 1401.7569, Budzinskiy 2308.16097)
   adds local-linear-rate language and explains both measured facts:
   diminishing returns after ~2 rounds, and why RR-then-correct (bad
   init, different basin) loses to joint shaping. Paper-section material,
   plus one cheap lever (proximal damping / dithered rounding restarts).
5. **Expectation control:** the capture law (≈5.9·r/d) caps what ANY solver
   gains at fixed rank — solver choice only closes the local-vs-global gap
   of the weighted objective, historically a few percent. The biggest lever
   in this slice is the **metric** (adding the left/grad side), consistent
   with campaign law 4 ("metric quality gates everything"), not the solver.

---

## (0) The lineage, compressed

- **Riemann 1854** (Habilitationsvortrag) → manifolds with metric;
  **Grassmann 1844** (Ausdehnungslehre) → Gr(k,n) as the space of
  k-subspaces; **Stiefel 1935** (Comm. Math. Helv. 8) → St(n,p), the
  orthonormal k-frames, originally for vector fields on manifolds.
- **Luenberger 1972** (gradient projection along geodesics), **Gabay 1982**
  (JOTA: minimizing differentiable functions on manifolds) — first
  optimization-on-manifolds proper.
- **Brockett 1991** (LAA 146: double-bracket isospectral flows — matrix
  diagonalization as a Riemannian gradient flow) and **Helmke–Moore 1994**
  (*Optimization and Dynamical Systems*) — the "since 1991" root: linear
  algebra computations recast as flows on orbit manifolds.
- **Smith 1994** (Fields Inst.: Riemannian Newton/CG), then the field's
  founding paper: **Edelman–Arias–Smith, physics/9806030** — *The Geometry
  of Algorithms with Orthogonality Constraints* (SIAM J. Matrix Anal. 1998):
  Stiefel and Grassmann geometries (both quotient views), geodesics,
  parallel transport, Newton and CG; eigenvalue/subspace algorithms as
  special cases.
- **Absil–Mahony–Sepulchre 2008** (*Optimization Algorithms on Matrix
  Manifolds*, Princeton) — the AMS book: **retractions** (first-order
  geodesic surrogates: QR, Cayley, polar) and **vector transport** replace
  exact geodesics/parallel transport; makes everything implementable.
  Riemannian trust-region: Absil–Baker–Gallivan, FoCM 7, 2007. Global
  rates for RGD/RTR: Boumal–Absil–Cartis 1605.08101; cubic regularization
  on manifolds 1806.00065. Textbook of record now: **Boumal 2023**, *An
  Introduction to Optimization on Smooth Manifolds* (CUP, free online).
- **Riemannian SGD**: Bonnabel 1111.5280 (a.s. convergence on manifolds);
  Riemannian Adam/AMSGrad: Bécigneul–Ganea 1810.00760; variance reduction:
  Kasai/Sato 1605.07367, 1702.05594, 1703.04890; recent adaptive framework
  Sakai 2409.00859; saddle escape 1906.04321.
- **Stiefel-specific scalability**: Cayley-transform SGD/Adam (Li et al.
  2002.01113, ICLR'20 — the optimizer SpinQuant uses); retraction-free
  "landing" method (Ablin–Peyré 2102.07432 — matmul-only, for dims where
  QR/SVD retractions hurt). Sobering bound: **linear/quadratic optimization
  over Stiefel (and flag) manifolds is NP-hard** (Lai–Ye–Lim 2507.02839) —
  all of this is local; warm starts matter (ours: the closed form).

### Fixed-rank manifold geometries (the B·A home turf)

Four parameterizations of {X : rank(X) = r}, all with mature algorithms:

| geometry | representative | notes |
|---|---|---|
| Embedded submanifold, tangent = UMVᵀ+U_pVᵀ+UV_pᵀ | Vandereycken 1209.3834 (LRGeom, Riemannian CG for completion) | cleanest; SVD-based retraction O((m+n)r²) |
| Quotient (three-factor / polar / subspace-projection) | Mishra et al. 1209.0430; R3MC 1306.2672; Mishra 1211.1550; two Newton methods Absil et al. 1209.0068; Grassmann-based RTRMC (Boumal–Absil, NIPS 2011; no arXiv — journal: LAA 475, 2015) | metric can be *tailored to the cost* — the door to weighted objectives |
| Flat factored (B,A) + scaled metric | **ScaledGD, Tong–Ma–Chi 2005.08898**; formal bridge = **Riemannian preconditioning, Mishra–Sepulchre 1405.6055** | preconditioners (AAᵀ)⁻¹,(BᵀB)⁻¹ ⇒ rate independent of cond(X); no retraction needed at all |
| Desingularized bounded-rank variety | Khrulkov–Oseledets 1612.03973; **Rebjock–Boumal 2406.14211** (joint global+local guarantees); tensor version Gao–Peng–Yuan 2411.14093 | fixes the "apocalypse" failure mode of embedded fixed-rank methods (Levin–Kileel–Boumal 2107.03877): sequences converging to rank-deficient points where stationarity silently fails |
| Rank-adaptive | Gao–Absil 2103.14768 | grows/shrinks r during optimization |

**Distillation for us:** our residual is energy-full-rank (E01) and r is
fixed by the byte budget, so rank-drop apocalypses are a non-risk and the
desingularization elegance buys nothing. The row that matters is row 3:
for a *quadratic-in-each-factor* objective, quotient-metric GD ≡ ScaledGD,
and the alternating exact minimization we already do (whitened ALS) is
itself the classical variable-projection/Gauss-Seidel algorithm on the
Grassmannian (Terray 2505.03347 makes that identification explicit).

---

## (b) Weighted low-rank approximation: where closed forms end and what wins beyond

### b.1 The objective taxonomy (the section that answers Max's question)

With residual R (m×n), correction Δ = B·A, rank ≤ r:

1. **Unweighted** min‖R−Δ‖_F → Eckart–Young–Mirsky 1936. Dead for us (E03).
2. **One-sided** min‖(R−Δ)S^{1/2}‖_F → closed form (whiten right, SVD,
   unwhiten). Our fullcov fc. QERA-exact is this (see whitening doc).
3. **Two-sided separable** min‖L(R−Δ)M‖_F, L,M ≻ 0 → **still closed form**:
   Δ* = L⁻¹·[L R M]_r·M⁻¹. This is the generalized Eckart–Young under any
   Kronecker weight W = (MMᵀ)ᵀ⊗(LLᵀ) on vec(R−Δ); solvable since Mirsky;
   geometry treated in Manton–Mahony–Hua 2003 (*The geometry of weighted
   low-rank approximations*, IEEE TSP 51(2)).
4. **Elementwise / general PSD weight** min Σ_ij F_ij (R−Δ)_ij² → **no
   closed form; NP-hard in general** (Gillis–Glineur 1012.0197, via
   biclique reduction; even with binary weights = matrix completion).
   Srebro–Jaakkola (ICML 2003, *Weighted Low-Rank Approximations*) is the
   founding practical paper: EM / weighted ALS, notes the loss surface has
   spurious local minima once weights are non-uniform.

**The Fisher case.** Exact diagonal (empirical) Fisher for a linear layer:
F_ij = E[(∂ℓ/∂W_ij)²] = E[g_i² x_j²] — elementwise, class 4. The K-FAC
factorization F ≈ E[g g ᵀ]⊗E[x xᵀ] (or its diagonal E[g_i²]·E[x_j²]) is by
construction the **nearest Kronecker / rank-1 approximation of the weight
matrix U_ij = E[g_i² x_j²]** — class 3, closed form. FWSVD (Hsu et al.
2207.00112, ICLR'22) hit exactly this wall for LM compression and chose
row-sums of Fisher (one-sided diagonal) *explicitly because* the general
Fisher-weighted problem has no analytic solution — i.e., the field's
standard answer to our question has been "retreat to a separable
approximation," not "bring a Riemannian solver."

**⇒ Decision gate for the campaign (cheap, decisive):** estimate U_ij on
the 0.6B with a few hundred calibration sequences (needs backward passes —
new instrumentation, minutes of compute), and measure σ₂(U)/σ₁(U) per
tensor. Near-rank-1 U ⇒ take the class-3 closed form (add left
grad-whitening L = diag(E[g_i²])^{1/2} — or full E[ggᵀ]^{1/2} — to fc) and
skip iterative solvers for fitting entirely. Heavy-tailed U ⇒ class 4 is
real money and §b.2 applies. Note E07 (measured-alloc) and law 4 both hint
the left side carries signal — output rows are NOT equally loss-critical,
and our current fc treats them as if they are.

### b.2 Solvers for class 4 (elementwise), ranked by fit to us

- **Weighted ALS / EM** (Srebro–Jaakkola 2003): each half-step is a set of
  m (resp. n) independent r×r weighted least-squares solves — for us
  O((m+n)r³ + mn r²) per sweep; at 5120×17408, r=128 ≈ 1.5 T-flop/sweep,
  seconds on our GPU, a few sweeps from the closed-form warm start.
  **Guarantees:** Li–Liang–Risteski 1602.02262 — with weights bounded in
  spread (F_max/F_min not too large) and good init, weighted ALS converges
  linearly to a near-global optimum. Fisher spreads are large but we clip
  (imatrix practice already clips); this is the theory-backed workhorse.
  Runtime refinements: Song et al. 2306.04169 (approximate ALS sweeps),
  Li et al. 2502.16912 (subquadratic regimes), Woodruff 2406.02431
  (relaxed solutions), Ban et al. 1911.06958 (regularized, parameterized
  by statistical dimension), Dutta 1511.00649/1703.06303 (ADMM-style).
- **ScaledGD / preconditioned factored GD** (2005.08898 + 1405.6055):
  gradient of the weighted loss + scaled metric; one line different from
  plain GD; local linear rate independent of κ(Δ). Best when we later make
  the objective non-quadratic (KLD-through-the-network fine-tuning of
  factors) where ALS half-steps stop being closed-form.
- **Variable projection on Grassmann** (Terray 2505.03347; classical
  Ruhe–Wedin): eliminate A analytically, optimize the column subspace of B
  on Gr(r,m) with Gauss–Newton/LM; best-in-class per-iteration progress,
  heavier per-iteration cost; the right tool if ALS stalls on ill-
  conditioned Fisher weights.
- **Manifold SGD for weighted LRA** (Xu–Wu 2502.14174; Yang et al.
  2503.11833, adaptive step sizes): 2025 theory papers proving retraction-
  based SGD convergence on this exact problem class. Nothing algorithmic
  we can't get from the two rows above; cite, don't build.
- **Embedded fixed-rank RTR/CG** (1209.3834 lineage w/ weighted metric;
  robust variants 2008.07740): correct but heavyweight; the quotient/
  factored routes dominate for our shapes.

**Honest expected yield:** at 0.6B, moving fc from class 2 (right-only)
to class 3 (two-sided closed form) is the metric upgrade — by analogy
with diag→fullcov (worth more than doubling rank) a **−3…−10% KLD** guess
is defensible but unproven. Class 3→4 (true elementwise via ALS) closes a
local-optimality gap only; literature and our capture law both say small
single digits at best. At 27B, law 5 shrinks everything further.

---

## (c) Manifolds meeting quantization: what exists

### c.1 Learned rotations = Stiefel optimization in production (nearest shipped art)

- **SpinQuant, 2405.16406** (Liu et al., Meta; ICLR'25): quantization
  error of W4A4 depends strongly on *which* orthogonal rotation is folded
  into the weights (quality varies widely across random rotations —
  cross-ref outliers doc); optimizes R1,R2 ∈ SO(n) with **Cayley SGD
  (2002.01113)** on the task loss, folds them into adjacent weights;
  closes most of the gap to FP16 vs Hadamard-only QuaRot (2404.00456).
- **OSTQuant, 2501.13987** (Hu et al., ICLR'25): orthogonal *and* scaling
  equivalent transforms optimized under a "quantization space utilization"
  objective with KL-Top calibration loss; W4A4KV4 SOTA line.
- **2511.22316** (Xiao et al., 2025): *closed-form* rotations for W4A4 —
  sidesteps STE non-smoothness of gradient descent on Stiefel; cheap
  alternative to SpinQuant's training loop.
- **TwinQuant, 2606.01556** (ICML'26): jointly *learns* the outlier
  subspace + residual decomposition on Stiefel×GL manifolds so both
  components are quantization-friendly, with a fused dual kernel — already
  digested in `arxiv-qec-lineage.md`; it is the closest thing to
  "manifold-optimize the factors jointly with the quantizer" in print.
- Related family: ButterflyQuant (learned butterfly orthogonal factors,
  outliers doc), FrameQuant 2403.06082 (fusion frames), LoPRo 2601.19675
  (permutation+rotation preconditioning of the *residual*, qec doc).

**Fit to beast-rank:** weight-only 2-bit K-quants also suffer
rotation-dependent grid mismatch; a folded rotation reshapes the weight
distribution each super-block sees. Composes upstream of everything we do
(base quant, E13 re-round, fc, alternation). Constraint: llama.cpp has no
runtime rotation hooks, so only *foldable* rotations (QuaRot-style
absorption through RMSNorm) are eligible; per-block interaction with
Q2_K's 16-element sub-blocks is unmeasured — that's the experiment.

### c.2 Riemannian metrics driving the quantizer itself

- **RSAVQ, 2510.01240** (Xu et al., 2025) — the flagship of this theme:
  treats parameter space with the **FIM-induced Riemannian metric**;
  (i) EDSG projects quantization error onto low-sensitivity directions
  along the negative *natural-gradient* direction (error feedback in the
  Fisher geometry), (ii) WCSG allocates bits per channel by FIM curvature.
  2-bit LLaMA-3-8B: beats VPTQ and QuIP# by ~0.4 PPL. Two direct reads
  for us: our E13 re-rounder currently scores candidates in the whitened
  ℓ2 metric — RSAVQ says score them in the (diagonal) Fisher metric; and
  its WCSG independently reinvents our measured-sensitivity allocation
  (law 4 convergent evolution — good sign, and good related-work cite).
- **Mirror-descent view of quantization** (Ajanthan et al. 1910.08237):
  projections onto the quantized set under a Hessian-metric (Bregman)
  geometry; **ProxQuant** (Bai et al. 1810.00861): quantization as a
  proximal operator inside descent. Both are the principled versions of
  "alternate descent with rounding" — theory scaffolding for §d and for
  any joint-descent E-experiment.
- **RCO, 2605.00649** (2026): compression budget constraints (softmax-
  relaxed) form a smooth Riemannian manifold → first-order methods with
  *exact* budget enforcement, no hyperparameter search. An alternative
  formalism for our byte-budget rank/bit allocation; our measured
  allocation already beats water-filling, so this is automation value,
  not KLD value.
- Periphery (touched, not applicable): Q-RGT 2506.07351 (quantized
  *gradients* for decentralized manifold optimization — different
  problem); Grassmann gradient quantization for federated learning
  1910.03865; LVQ on SPD manifolds 2102.00667; VQ on data manifolds
  1907.03875; Bregman-metric clustering 1810.10770.

### c.3 The gap nobody fills

No paper formulates the quantized-weight set (product of scaled integer
grids, scales continuous) as a discrete fiber bundle / lattice inside a
matrix manifold and does "quantization-aware Riemannian descent" with
guarantees. The nearest formal objects are: alternating projections onto
a nonconvex pair {rank-r} ∩ {grid} (§d), mirror-descent/prox views
(above), and MIQP treatments of GPTQ-style rounding. **Assessment: open
and elegant, but at fixed byte budget the binding constraint is the
capture law, not solver optimality — a genuinely novel formulation here
is paper-material for us, not a KLD lever.**

---

## (d) Alternation between a manifold and a discrete set — E11, formalized

Our E11: f(q,s,B,A) = ‖L(W − D(q,s) − BA)M‖² with q ∈ ∏(integer grids),
s continuous scales; alternate (1) re-round q,s given BA (E13 whitened
greedy per block = an *inexact/quasioptimal* projection), (2) closed-form
whitened SVD for B,A (an *exact* block minimizer).

- **Convergence, the elementary argument (ours to state in the paper):**
  both steps are non-increasing; q ranges over a finite set; each q-fix
  gives a unique factor optimum ⇒ objective strictly decreases until the
  code pattern repeats ⇒ codes stabilize in finitely many rounds, after
  which the procedure is stationary. Monotone convergence guaranteed;
  quality of the limit is not (NP-hard problem class — Gillis–Glineur,
  Lai–Ye–Lim).
- **Rates and structure from the literature:**
  - KL framework: Attouch–Bolte–Redont–Soubeyran 0801.1780 and PALM
    (Bolte–Sabach–Teboulle, Math. Prog. 144, 2014) — proximal alternating
    minimization on semialgebraic problems converges to critical points
    with finite length; both our sets (bounded-rank variety, finite grid ×
    polyhedral scale set) are semialgebraic, so adding proximal damping
    (tiny quadratic tether to the previous iterate) buys the full theory.
  - Local linear rate: Lewis–Luke–Malick (FoCM 9, 2009) and
    Drusvyatskiy–Ioffe–Lewis 1401.7569 — alternating projections between
    nonconvex sets converge linearly given *transversality* of the
    intersection; inexact projections preserve this (1811.01298), and
    **Budzinskiy 2308.16097** works out exactly the low-rank case with
    quasioptimal projections (which is what truncated whitened SVD and
    greedy re-rounding are). Kruger 1701.08246 refines the transversality
    condition; Chen et al. 2605.17384 even builds retractions *from*
    alternating projections.
  - Alternating minimization with weights: Li–Liang–Risteski 1602.02262
    (guarantee under weight-spread bound); classic completion ALS theory
    Jain–Netrapalli–Sanghavi 1212.0467.
- **What the theory explains that we measured:** (i) two rounds capture
  nearly everything (linear rate with strong contraction ⇒ geometric
  residual gains); (ii) RR-then-correct < joint (different basins — local
  methods on NP-hard problems are init-dominated; closed-form fc-first is
  the better init, matching "joint shaping > greedy," law 3); (iii)
  ProjQ-deflated re-round's win over naive re-round is a projection-order
  effect the alternating-projections view predicts.
- **Cheap unexplored lever:** dithered/randomized rounding in step (1) as
  a basin-escape move (simulated-annealing-flavored restarts over the
  finite code set), plus the proximal tether. Half-day experiment, riding
  the existing E11 harness; expect ε, but it completes the story.

---

## (e) Tooling reality check (5120×17408-class tensors)

- **Manopt** (MATLAB, Boumal et al. 1308.5200): most complete manifold zoo
  (fixedrankembeddedfactory etc.); wrong language for our stack.
- **Pymanopt** (Townsend et al. 1603.03236, JMLR'16): FixedRankEmbedded,
  Stiefel, Grassmann + autodiff (numpy/PyTorch/JAX backends); fine at
  0.6B tensor sizes (1024×3072) for *validation runs*; per-iteration SVD
  retractions and CPU-centric plumbing make it painful at 27B shapes.
- **Geoopt** (Kochurov et al. 2005.02819): GPU-native PyTorch, Riemannian
  Adam (1810.00760), Stiefel (Euclidean+canonical), product manifolds —
  **no fixed-rank manifold**. The right tool for the rotation candidate
  (SpinQuant-style Cayley/QR retraction on 5120×5120 is routine on our
  card), wrong tool for factor fitting.
- **McTorch** (1810.01811): stale; skip.
- **Verdict:** for factor fitting, hand-roll — weighted ALS and ScaledGD
  are ~20–40 lines of dense PyTorch each, no retraction, no library;
  per-sweep cost at 27B worst-case tensor ≈ 1–2 s on our GPU. Use geoopt
  only if we run the learned-rotation experiment with Riemannian Adam
  instead of borrowed SpinQuant code; pymanopt only to sanity-check a
  gradient. (Sizes like ours are why the landing method 2102.07432 exists,
  but at 5120 dims classical retractions are still cheap enough.)

---

## RANKED applicable candidates

Ordered by (expected KLD movement at our scales) × (cost to test). Byte
budgets held fixed in every design; success bar = byte-fair KLD vs the
same pipeline without the candidate, 0.6B first, 27B only on a 0.6B win.

### C1 — Two-sided (grad-side) whitening: closed form first, ALS only if the diagnostic demands it ★ top pick
- **Technique:** add left whitening L = (E[ggᵀ]+λI)^{1/2} (or its diagonal)
  to the fc objective ⇒ Δ* = L⁻¹[L R S^{1/2}]_r S^{-1/2}. Escalate to
  weighted ALS (Srebro–Jaakkola + Li–Liang clipping) / ScaledGD only if
  the Kronecker gap is measured to be large.
- **Attacks:** two-sided-Fisher factor fitting — the named open problem;
  upgrades the metric of EVERY fc/alternation configuration in the rollup.
- **Prior art:** class-3 closed form (§b.1); FWSVD 2207.00112 (retreated to
  one-sided — we go past it); RSAVQ WCSG (Fisher curvature works at 2-bit).
- **Expected gain:** −3…−10% KLD at 0.6B if output-row sensitivity is as
  non-uniform as E07 suggests; shrinks at 27B (law 5). Honest risk: E[ggᵀ]
  estimation noise at small calibration budgets could *hurt*; damping λ
  and diag-first mitigate.
- **One-day design (0.6B):** (1) hook backward passes on the calibration
  corpus to bank per-tensor E[g_i²] and E[ggᵀ] (new ~80-line collector
  next to the imatrix reader); (2) measure the Kronecker-gap diagnostic
  σ₂/σ₁ of U_ij = E[g_i²x_j²] per tensor — this alone settles the "is
  Riemannian needed" question and is publishable either way; (3) refit
  alt2+fc r128-Q8 with diag-L, then full-L; (4) 40-chunk KLD vs the 0.209
  crown. Stretch: one weighted-ALS polish pass from the closed-form start
  to price the class-3→4 gap directly.

### C2 — Foldable learned rotation (SpinQuant lineage) under Q2_K + fc
- **Technique:** fold R ∈ SO(n) pairs through RMSNorm-adjacent weights
  (QuaRot algebra), R optimized by Cayley SGD (2002.01113) or geoopt
  Riemannian Adam against block-wise whitened quantization error of the
  *rotated* weights; Hadamard init.
- **Attacks:** the base quantizer's grid mismatch — the part of the error
  budget our correction never touches (recovery fraction stuck at 7.5% at
  Q2_K says base error dominates there).
- **Prior art:** 2405.16406, 2404.00456, 2501.13987, 2511.22316 (closed-
  form shortcut), quality-varies-across-rotations evidence in outliers doc.
- **Expected gain:** at 4-bit W4A4 the published wins are large; for
  weight-only K-quants the honest expectation is moderate — Hadamard-
  rotated weight-only wins in print are a few % PPL. Unknown interaction
  with 16-wide sub-block scales is the real experiment. Composes with E13
  + fc + alternation.
- **One-day design (0.6B):** rotate → quantize Q2_K-imat (imatrix must be
  recollected in rotated basis — half the day) → E13 → fc r128 → KLD.
  Arms: identity / random Hadamard / 200-step Cayley-learned. If Hadamard
  alone moves KLD ≥5%, the learned arm gets a second day.

### C3 — Fisher-metric re-rounding (RSAVQ's EDSG absorbed into E13)
- **Technique:** E13 candidate scoring swaps whitened-ℓ2 for the diagonal
  Fisher metric (weights E[g_i²x_j²] from C1's collector); optional
  natural-gradient error-feedback ordering across blocks (error of block
  k projected out of the metric for k+1 — act-order's geometric cousin).
- **Attacks:** the free-lever lane (re-rounding is our best free win:
  −27% KLD at 0.6B); also feeds the E17 LUT lane with a principled metric.
- **Prior art:** RSAVQ 2510.01240 (beats QuIP#/VPTQ at 2-bit with exactly
  this metric), mirror-descent justification 1910.08237.
- **Expected gain:** small-single-digit KLD % on top of E13; free at
  serve time, so any measurable win ships.
- **One-day design:** C1's stats + a 30-line E13 patch; rerun the 0.6B
  re-round table (bare, +fc, +alt2); 40-chunk KLD.

### C4 — Alternation theory package (formalize E11, +proximal/dither variant)
- **Technique:** write the finite-codes convergence proof + KL/PALM and
  transversality framing (§d); implement proximal tether + one dithered-
  restart arm.
- **Attacks:** paper §theory (referee-proofing the method) + a cheap shot
  at leaving the current E11 basin.
- **Expected gain:** ε KLD (be honest: alt2 is already near its basin
  floor); high paper value, near-zero engineering risk.
- **Design:** half-day writing, half-day for the dither arm on the
  existing E11 harness at 0.6B.

### C5 — Flag-manifold nested factors for rank-adaptive serving
- **Technique:** fit the correction on a flag Fl(r₁<r₂<…; ℝⁿ) (Ye–Wong–Lim
  1907.00949; nestedness machinery Szwagier–Pennec 2502.06022; robust
  variants Mankovich 2401.04071) so every prefix of the factor columns is
  simultaneously near-optimal → ONE stored r128 adapter serves r∈{32,64,
  96,128} by truncation, loadable per VRAM headroom.
- **Attacks:** the parked "rank-adaptive serving" product idea — not KLD.
  Whitened SVD already gives nested optimality for class 2/3 objectives;
  flags only add value once C1's ALS (class 4) or alternation breaks
  nestedness. KLD-neutral by construction at full rank.
- **Design (when product-driven):** truncation-curve comparison (KLD vs r
  for prefixes of an r128 fit: SVD-nested vs flag-refit); one day, but
  only after a class-4 fitter exists.

### C6 — Joint descent replacing alternation (ProxQuant/STE + factored GD)
- **Technique:** simultaneous updates: codes via prox/STE (1810.00861),
  scales+factors via ScaledGD, one loss (whitened or C1 metric).
- **Attacks:** law 3 taken to its limit — fully joint shaping.
- **Honest expectation:** the 2-block alternation with exact substeps is
  already a strong optimizer of the same landscape; published joint-vs-
  alternating gaps at this problem size are small, and STE noise can lose
  to exact half-steps. Rank below C1–C3; one day only if C1's metric
  upgrade re-opens the gap between alt2 and the (new) fully-joint optimum.

### Rejects (elegant, won't move KLD)
- **Desingularization / apocalypse-safe geometry** (2406.14211,
  2107.03877, 1612.03973): our rank is fixed and residuals full-energy —
  the pathology it cures cannot occur in our pipeline. Cite in the paper's
  related work, do not build.
- **Rank-adaptive Riemannian completion machinery** (2103.14768) and
  embedded fixed-rank RTR/CG stacks (1209.3834 lineage): completion is a
  10%-observed-entries regime; we have dense residuals + closed-form warm
  starts — the machinery's advantages evaporate.
- **RCO budget manifolds** (2605.00649): our measured-sensitivity
  allocation already beats analytic allocation (law 4); revisit only for
  automation at ladder scale.
- **Intrinsic Muon / Stiefel-LoRA fine-tuning optimizers** (2605.09238,
  2508.17901, 2607.25299, 2604.00733, 2601.09185, 2602.17809): training-
  time optimizers for learned adapters; our factors come from linear
  algebra on frozen weights — different lifecycle. Relevant only if we
  ever fine-tune correction factors end-to-end (E14-cond successor).
- **Grassmannian cross-layer basis sharing:** already measured a failure
  in the field (structures doc §joint-basis: weight-error −46%, PPL
  degrades) — reinforces that metric > geometry ambition.

---

## Citation index (touched in this sweep)

| ref | what |
|---|---|
| Stiefel, Comm. Math. Helv. 8 (1935) | orthonormal frame manifolds |
| Brockett, LAA 146 (1991) | double-bracket flows |
| Helmke–Moore (1994); Smith (1994) | optimization ↔ dynamical systems; Riemannian Newton/CG |
| physics/9806030 | Edelman–Arias–Smith, geometry of orthogonality constraints |
| Absil–Mahony–Sepulchre (2008); Boumal (CUP 2023) | retractions/transport; modern textbook |
| Absil–Baker–Gallivan FoCM 7 (2007); 1605.08101; 1806.00065 | RTR; global rates; cubic reg |
| 1111.5280; 1810.00760; 1605.07367; 1702.05594; 1703.04890; 2409.00859; 1906.04321; 2501.18164 | Riemannian SGD/Adam/VR/saddles |
| 2002.01113; 2102.07432; 2507.02839 | Cayley SGD; landing; Stiefel NP-hard |
| 1209.3834; 1209.0430; 1211.1550; 1306.2672; 1209.0068; Boumal–Absil NIPS'11/LAA'15 | fixed-rank geometries + completion solvers |
| 1405.6055; 2005.08898 | Riemannian preconditioning; ScaledGD |
| 1612.03973; 2406.14211; 2107.03877; 2411.14093; 2103.14768 | desingularization; apocalypses; rank-adaptive |
| 2311.07404; 2303.00096 | PL/trust-region fast local convergence |
| 1907.00949; 2502.06022; 2401.04071; 2303.13501 | flag manifolds: optimization, nestedness, robust PCA, averaging |
| Eckart–Young (1936); Mirsky (1960); Manton–Mahony–Hua IEEE TSP 51 (2003) | (weighted) closed forms |
| Srebro–Jaakkola ICML (2003); 1012.0197; 1602.02262; 1212.0467 | weighted LRA: EM, NP-hardness, ALS guarantees |
| 2306.04169; 2502.16912; 2406.02431; 1911.06958; 1511.00649; 1703.06303; 2505.03347 | weighted LRA solvers/runtimes; variable projection |
| 2502.14174; 2503.11833 | manifold SGD for weighted LRA (2025 theory) |
| 2405.16406; 2404.00456; 2501.13987; 2511.22316; 2403.06082; 2606.01556; 2601.19675 | rotation/subspace-learned quantization |
| 2510.01240 | RSAVQ: Fisher-Riemannian VQ, 2-bit SOTA |
| 1910.08237; 1810.00861 | mirror-descent view; ProxQuant |
| 2605.00649 | budget-constraint manifolds (RCO) |
| 0801.1780; Bolte–Sabach–Teboulle Math.Prog. 144 (2014); Lewis–Luke–Malick FoCM 9 (2009); 1401.7569; 1811.01298; 1701.08246; 2308.16097; 2605.17384 | alternating minimization / projections theory |
| 2207.00112 | FWSVD — Fisher-weighted SVD, the one-sided retreat |
| 2310.11028; 2311.12023; 2310.08659; 2405.18886 | LPLR; LQ-LoRA; LoftQ; CALDERA (alternation instances, see other docs) |
| 1308.5200; 1603.03236; 2005.02819; 1810.01811 | manopt; pymanopt; geoopt; McTorch |
| 2605.09238; 2508.17901; 2607.25299; 2604.00733; 2601.09185; 2602.17809; 2606.05484; 2603.20632 | Stiefel/manifold training-time optimizers (rejected lifecycle) |
| 2506.07351; 1910.03865; 2102.00667; 1907.03875; 1810.10770; 2008.07740 | periphery: quantized-gradient/VQ-on-manifold variants |
