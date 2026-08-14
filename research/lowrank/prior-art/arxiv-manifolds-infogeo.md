> ⚠ see review/ corrections 2026-08-04

# Prior art: information geometry & statistical manifolds applied to model compression

Slice 2 of 3 of the differentiable-manifolds directive (TODO 2026-08-04b).
Research date: 2026-08-04. Scope: Fisher–Rao metric and natural gradient
(Amari lineage → K-FAC → LLM-scale), compression as KL projection
(e-/m-duality), information-geometric quantization of parameter
manifolds, rate-distortion × Fisher information, geodesics/transport
between models. Companion slices: `arxiv-manifolds-*.md` (Riemannian
factor manifolds; loss-landscape geometry); consolidated list goes to
`MANIFOLD-CANDIDATES.md`.

Our measured anchors (RESULTS_ROLLUP.md, 2026-08-03/04):

- Whitening arc: diag imatrix → full activation Gram was worth more than
  doubling rank (0.6B KLD 0.420 → 0.318 at r64, −24%; 27B 0.1460 →
  0.1282, −12%).
- Allocation: per-kind *measured* sensitivity beats energy water-filling,
  which is actively HARMFUL (law 4).
- Joint shaping > greedy composition: alternation beats
  RR-then-correct, twice measured (law 3).
- Eval stack is mean KLD vs reference logits — i.e. KL(ref‖quant).
- Every lever shrinks 0.6B → 27B (law 5).

## Verdict up front

1. **Our whitening arc is a textbook Fisher-metric refinement sequence,
   and the literature names the next two rungs precisely**: (i) the
   *output-side* Kronecker factor (gradient covariance T — K-FAC's
   second factor; we have only ever used the input factor S), and (ii)
   *fractional* whitening exponents — ULoRA/CG-LoRA [2607.26247]
   measured that the optimal curvature-preconditioning power is strictly
   between 0 and 1. Both are one-day experiments on Grams we partly
   already capture. Full FIM and block-tridiagonal K-FAC are priced out
   at LLM scale (quartic; even Anthropic's influence-function work
   [2308.03296] stopped at EKFAC on a 52B model — which is itself rung
   (iii): exact diagonal rescale in the Kronecker eigenbasis).
2. **The formal version of our measured-sensitivity allocation exists in
   closed form**: b_t* = b₀ + log₂RMS(θ_t) + ½log₂(f̄_t) [2505.12988],
   from KL ≈ ½Σ F_ii δ_i² and E[δ²] ∝ 2^(−2b). It also *explains* law 4:
   energy water-filling keeps the RMS term and drops the Fisher term —
   when sensitivity anti-correlates with magnitude (it does), energy
   allocation points the wrong way. RateQuant [2605.06675] adds the
   honest empirical refinement: the decay base is quantizer-specific
   (measured β ∈ [3.6, 5.3], not 4), so fit it per quant kind.
3. **KL(ref‖quant) minimization over a corrector family IS an
   m-projection**, and our fullcov least-squares corrector is already
   its closed form under the per-layer Gaussian surrogate — the
   projection reading of ASVD/OATS/FWSVD made explicit in [2507.09428].
   The same paper supplies the geometric account of law 3: the
   generalized Pythagorean theorem (project-then-project decomposes)
   holds only for flat submanifolds; the quant lattice is not one, so
   greedy RR-then-correct provably leaves divergence on the table that
   joint projection (our alternation ≈ Amari's em algorithm) recovers.
4. **Quantization of a parameter manifold has a 60-year-old optimal
   theory**: MML/SMML. Optimal cells are Fisher–Rao Voronoi regions and
   optimal codepoints are KL/Bregman centroids ≙ Fisher-weighted
   conditional means [2604.05241; Wallace–Freeman 1987]. This is the
   theory under E13 (re-rounding = one Fisher-Lloyd assignment step) and
   E17 (per-code biases = the centroid condition), and it says both
   should be iterated to a fixed point.
5. **Kills**: "quantize along the geodesic" as a literal mechanism;
   Chentsov invariance as a design tool; natural-gradient *optimizers*
   (SOAP/Muon/Shampoo) for our closed-form fits; full-FIM anything;
   Wasserstein-side geometry. Details in the kill list.

---

## (0) Classical roots — the load-bearing theorems

- **Rao 1945** (Bull. Calcutta Math. Soc. 37): the Fisher information
  matrix is a Riemannian metric on a parametric family; Cramér–Rao.
  Distance between nearby models is measured in nats of
  distinguishability, not in parameter units. Our entire campaign
  premise ("functional error, not weight error") is this sentence.
- **Chentsov 1972** (translated AMS 1982; extended by Ay–Jost–Lê–
  Schwachhöfer, Prob. Theory Rel. Fields 2015): the Fisher metric is the
  *unique* metric (up to scale) invariant under sufficient statistics /
  Markov morphisms. Consequence for us: any "importance weighting" that
  is not Fisher-derived is either equivalent to Fisher or wrong under
  reparameterization. Foundational comfort; zero engineering content.
- **Amari 1998**, "Natural Gradient Works Efficiently in Learning"
  (Neural Computation 10(2)); **Amari & Nagaoka 2000**, *Methods of
  Information Geometry*; **Amari 2016**, *Information Geometry and Its
  Applications* (Springer). Dually flat manifolds, e-/m-connections,
  generalized Pythagorean theorem, e-/m-projections, and the **em
  algorithm** (Amari, Neural Networks 8(9), 1995): alternating
  e-projection onto the data manifold and m-projection onto the model
  manifold. Our alternation (base re-quant ↔ factor refit) is an
  em-shaped coordinate algorithm on the product {lattice × low-rank}.
- **Second-order KL expansion** — the single most used identity in this
  file: KL(p_θ ‖ p_{θ+δ}) = ½ δᵀF(θ)δ + O(‖δ‖³). Everything in (c)/(d)
  is this identity plus a source-coding argument.
- **MDL/MML lineage**: Wallace & Boulton 1968; **Wallace & Freeman
  1987** (JRSS-B 49(3)): optimal parameter-space quantization cell
  volume ∝ 1/√det F; **Rissanen 1996** (IEEE Trans. IT 42(1)):
  stochastic complexity with the ∫√det F dθ term; **Balasubramanian
  1997** (Neural Computation 9(2)), "Statistical Inference, Occam's
  Razor...": the number of *distinguishable* distributions in a region
  is its Fisher volume over the cell volume — the razor counts models at
  Fisher resolution. This is the counting argument behind every
  bits-vs-KL trade below.
- **Graf & Luschgy 2000**, *Foundations of Quantization for Probability
  Distributions* (Springer LNM 1730): asymptotic quantization theory —
  the E[δ²] ∝ 2^(−2b) law and companding — imported wholesale by
  [2505.12988].

## (a) Fisher–Rao & natural gradient in deep learning — what the metric ladder says comes next

### a.1 The metric approximation ladder

- **K-FAC** — Martens & Grosse, [arXiv 1503.05671] (ICML 2015). Layer
  Fisher ≈ E[ggᵀ] ⊗ E[xxᵀ] = T ⊗ S: an *output-gradient* covariance
  Kronecker-multiplied with the *input-activation* covariance. Read
  against our arc: imatrix diag = diag(S); our fullcov Gram = full S;
  **we have never touched T**. The K-FAC paper itself defines the next
  rungs (block-tridiagonal inter-layer coupling) — priced out at our
  scale, see kills.
- **EKFAC** — George et al., [arXiv 1806.03884] (NeurIPS 2018): keep the
  Kronecker eigenbasis, replace the Kronecker eigenvalues with the
  *exact* Fisher diagonal in that basis. Provably a better Fisher
  approximation at negligible extra cost. The transferable lesson,
  proven twice in that lineage: **diagonal-in-the-right-basis beats
  richer-structure-in-the-wrong-basis** — our diag→fullcov measurement
  is an instance (fullcov = exact diagonal in the whitened basis of S).
- **LLM-scale evidence** — Grosse et al., "Studying Large Language Model
  Generalization with Influence Functions", [arXiv 2308.03296]: EKFAC
  run at up to 52B params. Existence proof that two-sided Kronecker
  metrics (S *and* T, per layer) are tractable at our flagship scale
  from calibration-set passes. FIMA-Q [arXiv 2506.11543] (ViT PTQ) uses
  a diagonal-plus-low-rank FIM — a different middle rung (structural,
  not basis-change); its KL↔FIM reconstruction loss is the same
  second-order identity we already live on.
- **Optimizer branch (context, mostly non-applicable)**: Shampoo [arXiv
  1802.09568], SOAP [arXiv 2409.11321] (Adam in Shampoo's eigenbasis —
  EKFAC's lesson again), Muon ("Muon is Scalable for LLM Training",
  [arXiv 2502.16982]), Purifying Shampoo [arXiv 2506.03595], "SOAP,
  Muon, and Beyond" [arXiv 2607.20548], Gauss-Newton study [2510.09378],
  optimizer scaling laws [2602.07712], symmetry-aware curvature
  [2606.00442]. These matter for *iterative training*; our fits are
  closed-form. See kills — with one exception routed to slice 1
  (Riemannian factor fitting).

### a.2 The bullseye: K-FAC-whitened subspace extraction

- **ULoRA / CG-LoRA** — "Between Gradient and Natural Gradient: A
  Continuum of LoRA Initializations", [arXiv 2607.26247]. They extract
  low-rank subspaces from K-FAC-whitened quantities, with a *whitening
  exponent* α: α=0 no whitening, α=1 full inverse-square-root (natural
  gradient). Measured on RoBERTa/T5/Llama-2-7B: **the optimal α is
  strictly intermediate — neither 0 nor 1 — and intermediate values are
  consistently more robust.** Two direct imports for us: (1) they whiten
  with BOTH factors S and T before SVD — the two-sided version of our
  one-sided fullcov; (2) our S^(1/2) whitening exponent is an untested
  hyperparameter; fractional powers are free to try in the existing
  pipeline.

### a.3 Does the geometry predict our measured diag-vs-fullcov gap?

Yes, and cheaply. The KL cost of using diag(S) instead of S as the
projection metric is governed by the non-diagonality of S; the clean
per-layer statistic is

  G_S = ½·(log det diag(S) − log det S)  = KL( N(0,S) ‖ N(0,diag S) )

(≥ 0, zero iff S already diagonal). Prediction: per-layer diag→fullcov
KLD gains rank-correlate with G_S — testable *today* from the Grams we
already captured, no new runs. And the same statistic computed on the
untouched factor, G_T from gradient covariance, **prices the two-sided
upgrade before we build it** (RateQuant's AM/GM-of-sensitivities
screening statistic is the scalar cousin of this test). The scale-shrink
of the gap (−24% at 0.6B → −12% at 27B) is consistent: wider layers →
more self-averaging → Grams closer to well-conditioned → smaller G_S;
law 1 (capture ≈ 5.9·r/d) and law 5 share this mechanism.

## (b) Compression as projection on statistical manifolds

### b.1 The anchor

- **"On Information Geometry and Iterative Optimization in Model
  Compression: Operator Factorization"** — Shumaylov, Tsiaras,
  Stylianou, [arXiv 2507.09428]. Compression = choosing a low-compute
  submanifold and projecting onto it in the divergence that matters.
  Explicitly: argmin_{θ∈M_<r} KL(p_θ̃‖p_θ) ≈ argmin ½(θ−θ̃)ᵀI(θ̃)(θ−θ̃),
  and the existing methods are graded as crude-to-less-crude
  approximations of this projection: FWSVD [arXiv 2207.00112] (diagonal
  FIM, then column-summed — crudest), TFWSVD (diagonal FIM), ASVD/OATS/
  SVD-LLM-style activation whitening (= projection under a per-layer
  Gaussian surrogate; they pointedly ask whether Gaussian is right when
  activations are multimodal/outlier-heavy). Two results we can use:
  - **Pythagoras only when flat**: D(p₁‖p₂) = D(p₁‖p₂*) + D(p₂*‖p₂)
    requires (local) flatness of the nested submanifolds. The quant
    lattice is not flat ⇒ greedy sequential projection (round, then
    correct) is not optimal ⇒ **law 3 (joint > greedy, alternation wins,
    RR-then-correct loses) is a theorem-shaped fact, not an accident.**
    This goes in the paper's theory section.
  - **Prop 4.3, Bregman-proximal iteration**: W_{n+1} ∈ argmin { (1/αₙ)
    D_{Fₙ}(W, Wₙ) + ⟨W, ∇L(Wₙ)⟩ + λ·rank(W) } — monotone descent and
    convergence to critical points under a Fisher-generated Bregman
    proximal term; hard thresholding is the prox of rank. This is our
    alternation *with a principled damping term* — the theory-backed way
    to run alt3+ without oscillation.

### b.2 e-/m-duality applied to our stack

Our eval metric is KL(ref‖quant): minimizing it over a corrector family
is an **m-projection** of the reference onto that family. Facts with
teeth:

- For exponential families, the m-projection is **moment matching** of
  expected sufficient statistics, and it is the *unique* projection
  along the m-geodesic (Amari–Nagaoka ch. 3; Information-projection,
  standard). The LLM's softmax head is an exponential family in the
  logits. A corrector that acts **affinely in logit space** (per-logit
  affine, or low-rank on lm_head) therefore has a *convex* exact-KL
  fitting problem — global optimum, characterized by matching expected
  features between ref and corrected model on the calibration
  distribution. No Gaussian surrogate needed at the output layer. The
  m-projection of expectation-propagation lore (Minka; Walsh EP note)
  is the same operation.
- For *internal* layers the exact m-projection is intractable; the
  per-layer Gaussian surrogate turns it into weighted least squares —
  i.e. **our fullcov corrector already is the closed-form m-projection
  under that surrogate**. The refinement axis is therefore not the
  solver but the surrogate: (i) Fisher-weight it two-sidedly (a.2), (ii)
  fix the output layer exactly (convex, above).
- **Alternation as em**: e-step ≙ re-quantize base given corrector
  (project onto the realizable/lattice manifold), m-step ≙ refit
  corrector given base (project onto the model family). Amari 1995
  characterizes convergence on dually flat geometry; with the lattice
  non-flat, the proximal damping of b.1 is the fix the theory
  prescribes.
- **A Complete Decomposition of KL Error** [arXiv 2410.11964] (refined
  information / mode-interaction selection) — decomposes KL error into
  ordered interaction contributions; a possible instrument for
  attributing our residual KLD to layers/kinds, but heavy machinery;
  watch, don't build.

## (c) Information-geometric quantization of parameter manifolds

- **SMML asymptotics** — "Information Geometry and Asymptotic Theory for
  SMML Estimators", [arXiv 2604.05241]. Strict MML = jointly choosing a
  discrete codebook of models and an assignment, minimizing assertion
  entropy + conditional cross-entropy. Results: optimal partitions are
  (pullbacks of) **weighted Fisher–Rao Voronoi tessellations**; for
  exponential families the codepoints satisfy moment-matching and are
  **KL/Bregman centroids** (cells = convex polyhedra in
  sufficient-statistic space). Translation to our stack: an optimal
  weight quantizer under functional loss is Fisher-Voronoi assignment +
  Fisher-centroid codepoints — i.e. **Lloyd–Max run under the Fisher
  measure**, which is exactly the closure of two things we measured
  separately: E13 re-rounding (one assignment step) and E17 per-code
  biases (one centroid step: conditional means, biases 1.5–2.5% of grid
  step). The theory says: *iterate them to a fixed point; both are
  halves of one algorithm.*
- **Wallace–Freeman 1987 / Rissanen 1996 / Balasubramanian 1997** (see
  §0): cell volume ∝ 1/√det F ⇒ bits spent on a region should grow like
  ½log det F — the same ½log₂F term as (d)'s allocation rule, derived
  from counting distinguishable models instead of rate-distortion. Two
  independent derivations, one formula; that's the kind of theory you
  trust.
- **SqueezeLLM** — [arXiv 2306.07629]: sensitivity-weighted (diagonal
  Fisher) k-means codebooks for LLM weights = the practical scalar
  Fisher-Lloyd quantizer, shipped in 2023. Evidence the centroid half
  alone pays at LLM scale; nobody has shipped the *iterated* version
  against k-quant grids (our E13+E17 fusion would be first, in
  llama.cpp formats).
- FIMA-Q [2506.11543] again: block reconstruction under a DPLR-FIM
  metric — a PTQ-side sibling; ViT-scale only.

## (d) Rate-distortion × Fisher information — the theory that predicts KLD-vs-bpw

- **"Optimal Formats for Weight Quantisation"** — Orr, Ribar, Luschi
  (Graphcore), [arXiv 2505.12988]. The chain: KL(p_θ‖p_θ̃) ≈ ½Σᵢ
  F_ii δᵢ² (diagonal, sampled empirical Fisher, 1024×4096 WikiText-103
  tokens, sampled not teacher-forced targets); quantization theory gives
  E[δᵢ²] = ε_t²·σ̂_t²·2^(−2b_t); minimize total KL under Σ n_t b_t = B ⇒
  **b_t* = b₀ + log₂ RMS(θ_t) + ½ log₂ f̄_t** (4× Fisher or 2× RMS ⇒ +1
  bit). Results: block-absmax + sparse outliers win among fixed-length
  formats; uniform-grid + lossless entropy coding wins overall;
  Fisher-allocation saves ~0.25 bits/param on Llama-3.1-8B, improves 8
  of 11 models (Llama-3.1/3.2, Qwen-2.5 incl. 0.5B, Phi-4, Gemma-3).
  **This is the formal statement of our law 4** — and the explanation of
  why energy water-filling is harmful: it is the b_t* formula with the
  Fisher term deleted, i.e. allocation by RMS alone, which misallocates
  whenever f̄ anti-correlates with magnitude (typical: attn vs ffn).
- **Predicted curve shape**: KL(b) ≈ C·4^(−b) in the perturbative
  regime, C = ½Σ F_ii ε²σ². Against our 0.6B ladder: Q3_K_M→Q4_K_M KLD
  ratio 0.238/0.066 = 3.6 over ≈0.9–1.0 bpw — dead on 4^0.95 ≈ 3.7. Below
  ~3 bpw the measured curve flattens (0.766/0.238 = 3.2 over ~1.3 bpw
  vs predicted 6.1; 27B flatter still) — as expected where the
  second-order expansion breaks and quant *families* change across
  rungs.
- **RateQuant** — [arXiv 2605.06675] (KV-cache, but the math is ours):
  the honest handling of that family-dependence. Model D(b) = α·β^(−b)
  with **per-quantizer calibrated β (measured 3.6–5.3)**; reverse
  water-filling closed form b_i* = b̄ + (ln w_i − ln w̄)/ln β with
  gradient-based sensitivities w; misusing one quantizer's β on another
  *inverts* the marginal-gain ordering — a plausible root cause for
  energy-water-filling harm, independent of the missing-Fisher-term
  cause. Bonus: **AM/GM ratio of sensitivities predicts the allocation
  prize before running anything** (~2.0 on Qwen3 heads).
- **Isik, Weissman, No** — "An Information-Theoretic Justification for
  Model Pruning", [arXiv 2102.08329] (AISTATS 2022). R(D) for NN
  weights: output-perturbation bounded by ℓ₁ distortion of normalized
  weights; weights fit a **Laplacian** source; the R(D)-optimal
  reconstruction marginal is a **spike-at-zero + Laplacian** ⇒ *the
  optimal compressor is sparse* — pruning is a theorem, not a trick.
  Plus **successive refinement** (SuRP): Laplacian sources are
  successively refinable, so one bitstream serves every rate point.
  Two hooks for us: (i) theoretical cover for the sparse-A localization
  lever (factors live on 2–5% of channels — arxiv-kernels.md); (ii) the
  parked **rank-adaptive serving** idea is exactly successive
  refinement — order factor columns/codes so a prefix of the stream is
  a valid lower-rate model.
- Also: Gao et al., "Rate Distortion for Model Compression: From Theory
  to Practice" (ICML 2019, PMLR v97/gao19c) — first R(D) framing,
  one-layer achievability, Gaussian weight assumption (criticized and
  fixed by Isik et al.); "Towards Optimal Compression: Joint Pruning and
  Quantization" [arXiv 2302.07612] — Fisher-derived joint
  prune/quantize allocation.

## (e) Geodesics & parallel transport between models

- **Fisher merging** — Matena & Raffel, [arXiv 2111.09832] (NeurIPS
  2022): diagonal-Fisher-weighted parameter averaging = Laplace-
  approximate posterior product.
- **Fréchet averages** — [arXiv 2604.27155]: merging = Fréchet (Karcher)
  mean under a chosen geometry; Fisher merging drops out as the
  Gaussian/quadratic localization of the Fisher-metric Fréchet mean;
  identifies the **LoRA quotient-manifold geometry** (B,A defined up to
  GL(r): (BG, G⁻¹A)) — that part belongs to slice 1 and is flagged for
  MANIFOLD-CANDIDATES. Cites empirical evidence that Fisher–Rao
  geodesics in distribution space track low-loss parameter paths (the
  mode-connectivity bridge).
- **Fisher–Rao-manifold LLM merging** — [arXiv 2603.04972]: Karcher mean
  on the Fisher–Rao manifold via a norm-preserving spherical proxy +
  fixed-point iteration; motivation is anti-collapse (Euclidean blends
  shrink activation variance/effective rank). Survey context: [arXiv
  2410.12927], [arXiv 2603.09938], ODE view [2605.19409].
- **Mode connectivity** — Garipov et al. [arXiv 1802.10026]; linear mode
  connectivity Frankle et al. [arXiv 1912.05671]; Git Re-Basin [arXiv
  2209.04836]. Relevant here only as the empirical face of "low-loss
  paths ≈ m-/e-geodesics" — the load-bearing use of landscape flatness
  for quantization robustness is slice 3's brief.
- **Is "quantize along the geodesic" meaningful?** Mostly no.
  Quantization is projection onto a discrete lattice, not motion along a
  curve; there is no path parameter to discretize. The two non-inert
  readings: (i) in dually flat coordinates the e-geodesic between quant
  and reference is *linear interpolation of logits* — so a corrector
  trained on KL-to-reference is already "moving along the e-geodesic";
  the phrase adds nothing our KLD corrector doesn't do. (ii) The one
  real use of transport machinery for us is **merging E14-cond
  per-workload adapters** (Fisher/Karcher mean of the code- and
  wiki-conditioned correctors) to buy back the measured off-distribution
  cliff — that survives into the candidate list; the rest is killed.
- Framing bonus for the paper: the Fisher metric is
  **data-distribution-dependent** — E14-cond's +10.3%
  on-distribution / steep off-distribution result is literally "the
  corrector was fit as a projection under a different metric." Metric
  mismatch, quantified.

---

## RANKED CANDIDATES

Ranked by (expected gain × cheapness × theory-confidence). Baselines
referenced are RESULTS_ROLLUP.md numbers.

### 1. Closed-form Fisher allocation with calibrated decay (2505.12988 + 2605.06675)

- **Problem**: bit/rank allocation — replaces/validates E07's measured
  sensitivity sweeps, explains law 4.
- **Expected gain**: match E07 (27B KLD 0.1415 vs bare 0.1529) with
  *zero sweep runs*, possibly beat it (E07 sweeps per-kind, the formula
  allocates per-tensor); at minimum, an allocation oracle that scales to
  configs we can't afford to sweep. AM/GM of per-tensor ln(RMS²·f̄)
  screens the prize in 5 minutes.
- **1-day 0.6B design**: (1) diagonal Fisher per tensor from imatrix-
  style gradient pass with *sampled* targets (their measured
  correction); (2) fit β per quant kind from our existing ladder KLDs
  (5 points, no new runs); (3) allocate b_t* under the alt2+fc r96 byte
  budget (361 MB); (4) build the mix, measure KLD vs 25.87 PPL /
  0.239 KLD baseline. Falsifier: if formula-alloc ≥ measured-alloc KLD
  at equal bytes, the perturbative theory is insufficient at 2–3 bpw and
  we say so in the paper.

### 2. Two-sided (K-FAC) whitening + fractional exponent (2607.26247, 1503.05671, 1806.03884, 2308.03296)

- **Problem**: the corrector metric — the named next rung of our
  diag→fullcov arc.
- **Expected gain**: the diag→fullcov step bought −24% KLD at 0.6B; the
  T-side gain is a fresh instance of the same mechanism, sized by G_T
  (§a.3). Realistic expectation: a fraction of the S-side gain (T is
  typically better-conditioned), plus a free few-% from tuning the
  whitening exponent α off 1/2. Even a null result is publishable as
  "the input Gram saturates the Kronecker metric for this problem."
- **1-day 0.6B design**: morning — compute G_S and G_T per layer from
  captured Grams + one gradient-capture pass; check G_S rank-correlates
  with measured per-layer diag→fullcov gains (validates the predictor);
  if G_T ≪ G_S everywhere, STOP (predicted null, half a day saved).
  Afternoon — refit r64/r128 with T^(α/2)-weighted left side, α ∈
  {0.25, 0.5, 1}, KLD vs fullcov r128 0.246 baseline.

### 3. Fisher-Lloyd fixed point: E13 ∪ E17 as one algorithm (2604.05241, 2306.07629, Wallace–Freeman)

- **Problem**: re-rounding + reconstruction — closes the loop the SMML
  theory says is one algorithm (Voronoi assignment ↔ KL-centroid
  codepoints).
- **Expected gain**: E13 alone: −27% KLD free at 0.6B; E17 biases alone:
  survived kill-test at 1.5–2.5% of grid step. Iterating
  assign↔centroid under the (full-cov) Fisher measure is the fixed
  point both approximate; expect a few more % KLD at zero bytes, and a
  clean theory story ("SMML-optimal dequant for k-quants") for the
  upstream RFC.
- **1-day 0.6B design**: on Q2_K codes, alternate (i) E13 re-round under
  fullcov weighting, (ii) recompute per-code conditional-mean biases,
  3–5 rounds, KLD after each vs E13's 0.556. Watch for the known
  hazard: assignment under a *better* metric than the serving dequant
  uses can regress (the Phase-2B lesson pattern) — score with the LUT
  path in the loop.

### 4. Exact m-projection logit corrector (Amari–Nagaoka; §b.2)

- **Problem**: a NEW lever — output-side correction with no Gaussian
  surrogate; convex by exponential-family structure.
- **Expected gain**: unknown; targets the residual KLD that layerwise
  surrogates can't see (surrogate-vs-true-KL gap). Cheap enough that
  even +2–3% KLD at ~0 bytes justifies it; composes with everything.
- **1-day 0.6B design**: freeze alt2+fc r128 (KLD 0.209); fit rank-8/16
  logit-space corrector ΔW_head = B_h A_h by direct minimization of
  KL(ref‖model) on 2M calibration tokens (convex ⇒ any optimizer,
  guaranteed global); evaluate on HELD-OUT chunks — the overfit check is
  mandatory and is the kill-test (lm_head correctors are notorious
  calibration-memorizers).

### 5. Bregman-proximal damped alternation (2507.09428 Prop 4.3)

- **Problem**: alternation depth — alt2 is the crown; does alt3+ pay if
  damped the way the theorem wants?
- **Expected gain**: alternation bought 25.99→25.27 PPL at r128;
  undamped extra rounds presumably oscillate (why we stopped at 2). The
  proximal term (1/α)·D_F(W, Wₙ) guarantees monotone descent; even
  +0.1–0.2 PPL more from rounds 3–6 extends a headline result.
- **1-day 0.6B design**: add Fisher-Bregman proximal penalty (fullcov
  Gram as F) to both half-steps; run 6 rounds at r128-Q8; plot
  KLD-per-round; success = monotone curve beating 25.27; failure =
  plateau at alt2, which itself is a tidy "alternation converges in 2
  steps" paper sentence.

### 6. Fisher-merge of per-workload correctors (2111.09832, 2603.04972, 2604.27155)

- **Problem**: E14-cond's off-distribution cliff (code-adapter great on
  code, bad on wiki).
- **Expected gain**: a single merged adapter that keeps most of the
  +10.3% on-distribution win at a fraction of the off-distribution
  cost — the standard Fisher-merging result, applied for the first time
  (to our knowledge) to *quantization correctors*.
- **1-day 0.6B design**: diagonal-Fisher-merge the code- and
  wiki-conditioned r64 adapters (per-param Fisher from each calibration
  set); evaluate the 2×2 (merged vs each) on both eval sets vs E14's
  2.746/3.061 numbers. Upgrade path if promising: Karcher-mean via
  2603.04972's spherical proxy.

### 7. Successive-refinement factor ordering (2102.08329)

- **Problem**: the parked rank-adaptive-serving idea, now with theory.
- **Expected gain**: product feature, not KLD: one shipped artifact
  whose byte-prefixes are valid lower-rate models (load rank to fit
  VRAM). Laplacian successive refinability says the rate penalty of
  prefix-nesting can be ~0.
- **Design (defer past 1 day)**: order factor columns by Fisher-weighted
  energy; verify KLD of prefix-loads matches independently-fitted
  smaller ranks. Only after candidates 1–3 land.

## KILL LIST — elegant but inert, killed honestly

- **"Quantize along the geodesic"** (literal): no path parameter exists;
  projection ≠ transport. The e-geodesic reading collapses into what the
  KLD corrector already does. Dead (survivor extracted as candidate 6).
- **Full FIM / block-tridiagonal K-FAC metrics**: quartic in layer
  width; the 52B EKFAC ceiling [2308.03296] is the measured frontier.
  Dead at 27B; EKFAC-style eigenbasis-diagonal is candidate 2's α-knob
  territory anyway.
- **Chentsov/invariance theory as a tool**: uniquely justifies Fisher —
  then hands you nothing to compute. Paper-introduction material only.
- **NG optimizers (SOAP/Muon/Shampoo) inside our fits**: our
  half-steps are closed-form weighted LS — already exact under the
  surrogate; preconditioners precondition nothing. Only revives if we
  ever do end-to-end QAT refinement (not this campaign). Routed note:
  Muon's orthogonalized updates ARE relevant to slice 1's
  Stiefel-manifold factor fitting.
- **SMML exact partitions**: NP-hard combinatorial tessellation; only
  the centroid/Voronoi *conditions* (candidate 3) are computable.
- **Wasserstein / entropy-relaxed transport geometry** (Amari's own
  late-career bridge, Springer Info-Geo 2018): no contact with any
  measured problem of ours; different distortion physics.
- **KL-error interaction decomposition [2410.11964]**: instrument, not
  lever; heavy; revisit only if paper reviewers demand attribution.

## Cross-links

- Law 3 now has a theory paragraph (Pythagoras-fails-off-flat, §b.1) —
  wire into `paper/OUTLINE.md` theory section alongside law 1's capture
  scaling.
- Law 4 = deleted-Fisher-term water-filling (§d) — same destination.
- Law 5 gets a mechanism candidate: larger d ⇒ smaller per-param Fisher
  trace and better-conditioned Grams ⇒ shallower perturbative gains
  (G_S statistic, §a.3) — measurable, goes in candidate 2's morning.
- LoRA quotient geometry (2604.27155) and Muon/Stiefel → slice 1.
- Flat-basin ⇒ quantizability, loss-landscape curvature → slice 3.
