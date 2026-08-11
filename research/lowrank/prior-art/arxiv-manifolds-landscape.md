> ⚠ see review/ corrections 2026-08-04

# Prior art — loss-landscape & weight-space differential geometry, applied to quantization

Date: 2026-08-04. Charter: TODO.md "MAX DIRECTIVE 2026-08-04b", slice 3 of 3
(landscape geometry; slices 1–2 cover Riemannian factor optimization and
information geometry). Question set: (a) does curvature predict quantization
tolerance, (b) is there a low-loss path from reference to a grid-feasible point,
(c) is gauge-fixing before quantization principled, (d) do weights live on
low-dim manifolds, (e) any 2024–26 landscape-aware PTQ that beats GPTQ-class
at 2-bit.

**Verdict up front.** Three lanes are live, two are theory-only, several die
honestly. (1) The single strongest 2025–26 result in scope: replacing the
LAYERWISE Hessian with a Kronecker-sketched GLOBAL (end-loss) Hessian for
adaptive rounding — YAQA — cuts KL to teacher ~30% vs GPTQ/LDLQ with zero
inference cost; this is exactly the upgrade axis for our E13 re-rounder, whose
gain faded 0.6B→27B precisely because its target is layerwise. (2) GPTQ has
now been PROVEN to be Babai's nearest-plane algorithm on the Hessian lattice —
so "the geodesic version of GPTQ" has a name: better approximate CVP under the
Hessian metric, with inherited error bounds. (3) Gauge-fixing before
quantization (scale/rotation/affine chosen to condition the Hessian or flatten
distributions) is now a measured, principled lane (FlatQuant / HeRo-Q /
OSTQuant) — our rejected rotation experiment tested ONE arbitrary gauge, not
the optimized gauge. Meanwhile: sharpness is NOT gauge-invariant (Dinh 2017),
so raw curvature as a per-tensor bit oracle is ill-posed without gauge-fixing
first; mode-connectivity×quantization and geodesic×quantization are measured
GREENFIELD on arXiv (near-zero hits) — theory ammunition for the paper, not a
ready-made method; and the manifold hypothesis for raw weight MATRICES is
unsupported in the literature too — our E01 full-rank null is the expected
answer, and the low-dim structure lives in trajectories, function space, and
the Hessian spectrum instead.

---

## (a) Flatness / sharpness / Hessian spectra vs quantization robustness

### Lineage (the classical spine)

- **Hochreiter & Schmidhuber**, "Flat Minima," *Neural Computation* 9(1):1–42,
  1997 (no arXiv). MDL argument: flat minima = low description length = weights
  tolerate coarse specification. This is literally "flatness = quantizability,"
  stated in 1997.
- **arXiv:1609.04836** — Keskar et al., "On Large-Batch Training: Generalization
  Gap and Sharp Minima" (2016). Revived sharpness; introduced the
  perturbation-based sharpness measure everyone uses.
- **arXiv:1703.04933** — Dinh et al., "Sharp Minima Can Generalize For Deep
  Nets" (2017). **The landmine for our use case:** ReLU/scale
  reparameterization can make any minimum arbitrarily sharp without changing
  the function. Sharpness is gauge-DEPENDENT. Any "curvature predicts bit
  tolerance" claim must first fix the gauge (see (c)) or use a
  reparameterization-invariant measure (Fisher/whitened metric — which is what
  our imatrix whitening already does).
- **arXiv:2010.01412** — Foret et al., SAM (2020). Minimize max-loss in an
  ε-ball; the operational flatness optimizer.
- **arXiv:1712.09913** — Li et al., "Visualizing the Loss Landscape" (2017) —
  filter-normalized directions: an early ad-hoc gauge-fixing so flatness plots
  mean something. Same moral as Dinh.
- **arXiv:1912.02178** — Jiang et al., "Fantastic Generalization Measures"
  (2019): among 40+ measures, sharpness-based ones (PAC-Bayes flavored) are the
  most predictive — but only the gauge-aware variants.

### Hessian spectra at scale

- **arXiv:1611.07476** — Sagun et al., "Eigenvalues of the Hessian in Deep
  Learning" (2016); **arXiv:1706.04454** — Sagun et al. (2017): spectrum =
  huge near-zero bulk + few outliers. The near-zero bulk IS the local flat
  manifold of the minimum — the space lever (b)/(c) exploit.
- **arXiv:1901.10159** — Ghorbani et al., Hessian eigenvalue density via
  stochastic Lanczos (2019). The tool for measuring our per-tensor spectra
  cheaply.
- **arXiv:1811.07062**, **arXiv:1901.08244** — Papyan: full deepnet Hessian
  spectra; outliers have hierarchical (class/cross-moment) structure — i.e.
  the sharp directions are FEW and structured. Consistent with our capture law
  (r/d scaling): the functional error concentrates in a thin subspace only
  after metric correction.

### Sharpness × quantization directly

- **arXiv:2111.12273** — Liu et al., "SAQ: Sharpness-Aware Quantization"
  (2021): treat quantization as a weight perturbation inside SAM's ε-ball;
  QAT-time. **arXiv:2210.07171** — SQuAT (BERT); **arXiv:2310.13315** — ZSAQ
  (zero-shot PLMs); **arXiv:2205.12694** — "Train Flat, Then Compress" (2022):
  SAM-trained models prune/quantize measurably better — the cleanest evidence
  that flatness ⇒ quantization tolerance. All require training: not our
  budget, but they establish the correlation we want to EXPLOIT at PTQ time.
- **arXiv:1905.03696** — HAWQ; **arXiv:1911.03852** — HAWQ-V2 (Hessian-trace
  weighted mixed precision); **arXiv:1909.05840** — Q-BERT. The original
  "curvature predicts which tensors tolerate low bits." Modern LLM form of the
  same signal is the imatrix/Fisher diagonal — i.e. our measured-alloc (E07)
  already sits downstream of this idea with a better (measured) metric.
- **arXiv:2411.07191** — "The Super Weight in LLMs" (2024): a handful of
  scalar weights, concentrated in **mlp.down_proj**, whose perturbation
  destroys the model. Independent corroboration of our measured sensitivity
  geography (ffn_down / first / last layers) from the mechanistic side.
- **arXiv:2606.09012** — "Understanding QAT: Gradients at Quantized Weights
  Bias to the Low-Loss Basin" (2026). Geometric model: training follows a
  low-loss "river" in a basin; **PTQ fails exactly when grid step ≳ basin
  width** (rounding exits the basin); STE-QAT works because gradients at the
  quantized point sense the valley walls. Gives a measurable per-tensor
  diagnostic: grid-step-to-basin-width ratio — a landscape-native sensitivity
  predictor to test against our geography.
- **arXiv:2510.07018** — sharpness-aware data generation for zero-shot quant
  (2025); **arXiv:2505.04877** — SAM for transferable mixed-precision policies
  (2025); **arXiv:2503.11078** — flatness in generative models improves
  robustness to quantization (2025). Peripheral but confirm the correlation
  repeatedly.

### The statistical-mechanics deep end (discrete weights, dense clusters)

The oldest and deepest theory that quantization-robust minima exist and where:

- **arXiv:1509.05753** — Baldassi et al., "Subdominant Dense Clusters ... in
  Neural Networks with Discrete Synapses" (PRL 2015): in BINARY-weight
  networks, typical solutions are isolated (unreachable), but exponentially
  rare DENSE CLUSTERS of solutions exist and are exactly what algorithms find.
- **arXiv:1605.06444** — Baldassi et al., "Unreasonable Effectiveness ...
  Accessible States and Robust Ensembles" (2016): formalizes LOCAL ENTROPY —
  count solutions in a ball, not loss at a point — as the right robustness
  measure; spawned **arXiv:1611.01838** — Entropy-SGD (Chaudhari et al. 2016)
  and **arXiv:2006.07897** — Pittorino et al., entropic GD (2020).
- **arXiv:1905.07833**, **arXiv:2107.01163**, **arXiv:2304.13871** — Baldassi
  et al.: wide flat minima are coalescences of high-margin solutions; structure
  persists with discrete AND continuous weights.
- **arXiv:1803.05407** — SWA (Izmailov et al. 2018): weight averaging lands in
  wider optima — cheap flatness at inference time.

**Applicability:** local entropy under GRID-SCALE dither (loss spread over K
randomized roundings) is a gauge-robust, forward-pass-only per-tensor
quantizability probe — a direct competitor to our measured per-kind
sensitivity table, with a real theory behind it.

## (b) Mode connectivity & low-loss manifolds — quantization as constrained descent

- **arXiv:1802.10026** — Garipov et al., "Loss Surfaces, Mode Connectivity,
  and Fast Ensembling" (2018); **arXiv:1803.00885** — Draxler et al.,
  "Essentially No Barriers" (2018): independent minima are connected by simple
  low-loss curves — the low-loss set is one connected manifold, not isolated
  points.
- **arXiv:1912.05671** — Frankle et al., LINEAR mode connectivity + lottery
  tickets (2019); **arXiv:2110.06296** — Entezari et al., permutation
  conjecture (2021); **arXiv:2209.04836** — Ainsworth et al., Git Re-Basin
  (2022): after permutation alignment, even straight lines are low-loss.
- **arXiv:2505.23681** — "Understanding Mode Connectivity via Parameter Space
  Symmetry" (2025): derives connectivity FROM the symmetry group topology —
  the modern synthesis of (b) and (c). **arXiv:2506.22712** — generalized LMC
  for transformers (permutations → orthogonal/invertible maps, 2025).
- **The measured gap:** arXiv search "mode connectivity" × quantization
  returns essentially nothing applicable (2 hits, both off-topic), and
  "geodesic" × quantization for NN weights returns zero. **Nobody has
  published "quantize by walking the low-loss manifold to the nearest
  grid-feasible point." Greenfield for the paper.**
- Closest practical relatives (discrete-path flavored, PTQ budget):
  - **arXiv:2505.11695** — **Qronos** (2025): sequential rounding that
    explicitly corrects PREVIOUS layers' quantization error while shaping the
    future — a discretized path through weight space rather than independent
    nearest-point rounding; beats GPTQ-class on Llama-3.
    **arXiv:2508.04853** — provable error bounds for OPTQ and Qronos (2025).
  - **arXiv:2605.04738** — OSAQ (2026): moves weights along the Hessian's
    (approximate) NULL SPACE to absorb outliers before quantization — i.e.
    descent along the layerwise low-loss manifold toward a more quantizable
    point, with zero functional change to first order. This is the layerwise
    shadow of the geodesic idea, and it is PTQ-cheap.
  - **arXiv:1810.00861** — ProxQuant (2018); **arXiv:1706.02379** — "Training
    Quantized Nets: A Deeper Understanding" (2017); **arXiv:2405.14852** —
    PV-Tuning (2024): the QAT/proximal lineage = continuous relaxations of the
    constrained-descent view; need training budget.
  - **arXiv:2604.03420** — "Zero-Shot Quantization via Weight-Space
    Arithmetic" (2026): "quantization vectors" — the weight-space DIRECTION
    that makes a donor model quantization-robust transfers to receiver models
    (up to +60 top-1 points at 3-bit on ViTs). Task-arithmetic/LMC machinery
    applied to quantization robustness; the one paper actually standing in
    this gap.
- **arXiv:2606.23607** — LMC & merging at billion-parameter scale (2026):
  feasibility evidence that connectivity machinery works at our model sizes.

## (c) Weight-space symmetries as gauge freedom

- Classical gauges: **arXiv:1506.02617** — Path-SGD (Neyshabur 2015,
  scale-invariant metric); **arXiv:1902.10416** — Equi-normalization (Stock
  2019 — explicitly motivated by compression); Dinh (above) as the reason
  gauge choice matters.
- **arXiv:2202.03038** — Pittorino et al., "Deep Networks on Toroids:
  Removing Symmetries Reveals the Structure of Flat Regions" (2022). **The
  principled statement of "gauge-fix BEFORE measuring flatness":** quotient
  out scale+permutation, then flatness metrics and mode-connectivity structure
  become meaningful and mutually consistent. Directly licenses a gauge-fixed
  version of any curvature-based sensitivity map.
- Teleportation: **arXiv:2205.10637** — Zhao et al., "Symmetry Teleportation"
  (2022); **arXiv:2305.13404** — Zhao et al. (2023): move along the symmetry
  orbit (loss-invariant) to change gradient/curvature properties. Quantization
  version: teleport to the orbit point of maximal quantizability — same math,
  different objective.
- The LLM-PTQ gauge stack (what "rotation lane" should have been):
  - Scale gauge: **arXiv:2211.10438** — SmoothQuant; **arXiv:2306.00978** —
    AWQ (both 2022–23): per-channel scale moved across the nonlinearity-free
    seam — the simplest measured gauge fix.
  - Rotation gauge: **arXiv:2307.13304** — QuIP (incoherence);
    **arXiv:2404.00456** — QuaRot; **arXiv:2405.16406** — SpinQuant (LEARNED
    rotations beat Hadamard — gauge OPTIMIZATION beats gauge convention);
    **arXiv:2406.01721** — DuQuant (2024).
  - Affine/objective-driven gauge (2024–26, the principled tier):
    **arXiv:2410.09426** — FlatQuant: per-layer learnable affine transforms
    chosen so weight/activation distributions are FLAT (quantization-friendly);
    "flatness matters" is their literal thesis. **arXiv:2501.13987** —
    OSTQuant: orthogonal+scaling transforms optimized for a global
    quantization-space-utilization objective. **arXiv:2601.21626** — HeRo-Q
    (2026): learnable rotation-compression chosen to REDUCE THE TOP HESSIAN
    EIGENVALUE before quantizing — gauge-fixing to maximize flatness, exactly
    the question this slice was asked; strong W3A16 results (70.15% GSM8K,
    Llama-3-8B). Diagnoses the "low (layerwise) error, high (end) loss"
    failure — the same divergence our KLD-vs-PPL discrepancies show.
  - Permutation gauge for BLOCK quants: **arXiv:2304.01089** — RPTQ (2023):
    channel reordering/clustering before quantization (activations, 3-bit
    first). For K-quant WEIGHT blocks the analogue is unexplored on our stack:
    reorder input channels so 16/32-wide blocks are magnitude-homogeneous
    (block absmax scale wastes bits on heterogeneous blocks); permutation is
    absorbable offline into the producing layer's output ordering.
- **arXiv:2606.00442** — "Exploiting weight-space symmetries for approximating
  curvature" (ICML 2026): average curvature estimates over the symmetry orbit
  → better structured Hessian approximations at fixed cost (recovers
  Shampoo/Muon as special cases). Could sharpen our Gram estimates at the same
  calibration budget.
- **arXiv:2506.22809** — BayesLoRA (2025): breaks the GL gauge freedom of
  low-rank factors (B,A) ↦ (BG, G⁻¹A) for automatic rank selection — the
  gauge story for OUR correction factors, not just the base weights.

## (d) Manifold hypothesis for weights — verdict on E01

- **arXiv:1804.08838** — Li et al., "Intrinsic Dimension of Objective
  Landscapes" (2018): TRAINING succeeds in random subspaces of dim ~10²–10³.
- **arXiv:2012.13255** — Aghajanyan et al. (2020): fine-tuning intrinsic
  dimension is tiny (→ LoRA's justification).
- **arXiv:2103.11154** — Li et al., DLDR: "Low Dimensional Landscape
  Hypothesis is True" (2021) — 40-dim training subspaces from trajectory PCA.
  **arXiv:1812.04754** — Gur-Ari et al., "Gradient Descent Happens in a Tiny
  Subspace" (2018); **arXiv:2405.16002** — "Does SGD really happen in tiny
  subspaces?" (2024, the honest pushback). **arXiv:2305.17446** — fine-tuning
  happens in tiny task-specific subspaces (2023).
- Weight-space learning: **arXiv:2110.15288** — hyper-representations
  (Schürholt 2021); **arXiv:2603.10090** — "A Survey of Weight Space Learning"
  (2026); **arXiv:2605.18632** — position: weight space as a generative
  modality (2026); **arXiv:2606.13276** — "Different Layers, Different
  Manifolds" (2026): different transformer layers prefer different manifold
  geometries (Stiefel vs Gram) — a hint that per-layer GEOMETRY CHOICE, like
  our per-kind sensitivity, is real structure.
- **Synthesis for E01:** the literature's low-dimensionality claims are about
  TRAJECTORIES, UPDATES (fine-tuning deltas), GRADIENTS, and FUNCTION space —
  never about a trained weight matrix being low-rank/near-manifold in Frobenius
  geometry. Our E01 full-rank null is the expected result, not an anomaly. The
  usable low-dim structure is exactly where we found it: in the METRIC-warped
  residual (whitened capture law), matching the Hessian bulk+outlier picture in
  (a). No revival of the truncated-SVD lane; E01 stays closed with a stronger
  citation wall.

## (e) Second-order / landscape-aware PTQ, 2024–2026 (beyond the layerwise Hessian)

- **arXiv:2505.22988** — **YAQA** (Tseng et al., ICML 2026): adaptive rounding
  against Kronecker-factored sketches of the FULL-MODEL Hessian (end-to-end KL
  to teacher), not the layerwise activation Gram. **~30% lower KL to teacher
  than GPTQ/LDLQ at equal bits; reportedly below QAT error, zero inference
  overhead.** The single most direct upgrade to our E13 re-rounder, and the
  best available explanation for why E13's gain faded at 27B (layerwise target
  ≠ scored objective; the mismatch grows with depth).
- **arXiv:2507.18553** — "The Geometry of LLM Quantization: GPTQ = Babai's
  Nearest Plane" (ICLR 2026): GPTQ back-to-front is EXACTLY Babai's
  nearest-plane algorithm for CVP on the lattice defined by the Hessian
  Cholesky factor; inherits Babai's error bounds (no-clipping regime). So:
  E13 (one Newton step) = one Babai step; "the geodesic version" = better
  approximate CVP under the Hessian metric (basis reduction, randomized
  rounding, limited sphere decoding on the worst blocks) — with 40 years of
  lattice literature behind it.
- **arXiv:2603.11021** — Leech lattice VQ (van der Ouderaa, 2026): 24-dim
  optimal sphere packing, GPTQ-style Hessian correction reinterpreted as
  **retraction onto a product of spheres** — literal differential-geometry
  language (retraction = manifold-optimization primitive) inside a
  state-of-the-art quantizer; beats QuIP#, QTIP, PVQ.
- **arXiv:2607.07964** — KronQ (2026): Kronecker-factored Hessian for
  bidirectional incoherence + mixed-precision allocation — the (a)+(c)+(e)
  synthesis arriving in the literature.
- **arXiv:2505.07004** — **GuidedQuant** (ICML 2025): end-loss gradient
  saliency (per-output-group Fisher, cross-weight dependencies kept) plugged
  into existing quantizers + monotone non-uniform scalar codebook (LNQ);
  improves SOTA across scalar/vector/W-A schemes. Cheap upgrade path for our
  measured-alloc weighting.
- **arXiv:2505.11695** — Qronos (see (b)); **arXiv:2508.04853** — its theory.
- **arXiv:2309.01885** — QuantEase; **arXiv:2406.17542** — CDQuant (2023–24):
  coordinate-descent refinement past GPTQ's single sweep — the cheap
  "multiple steps along the constraint set" already exists and composes with
  any objective above.
- **arXiv:2407.10032** — LeanQuant (2024–25): loss-error-aware non-uniform
  grids fixing inverse-Hessian-diagonal outlier failure; scales to 405B.
- **arXiv:2506.11543** — FIMA-Q (2025): KL↔FIM connection + **diagonal-plus-
  low-rank Fisher** — independent rediscovery of our diag→fullcov whitening
  ladder, in ViT PTQ. Confirms the metric-refinement arc has legs; cite as
  parallel evidence.
- **arXiv:2601.11663** — "Activation Sensitivity as a Unifying Principle"
  (2026): formal bridge between AWQ scales, GPTQ Hessians, and Fisher
  criteria — useful related-work glue for the paper.
- Context from our other sweeps (quant-time codebooks, not landscape):
  QuIP# (2402.04396), QTIP (2406.11235), AQLM (2401.06118), GPTVQ
  (2402.15319), BayesQ (2511.08821, see arxiv-entropy-reconstruction.md).

---

## RANKED candidates (technique → beast-rank problem → expected gain → one-day experiment)

1. **Global-Hessian (Kronecker-sketched) re-rounding — YAQA-style E13v2**
   [2505.22988, 2607.07964]. Problem: E13's gain collapsed 0.6B→27B (−27% →
   −4.5% KLD) because its target is the layerwise Gram, not the scored KLD.
   Expected: YAQA's measured ~30% KL cut vs GPTQ-class is the ceiling; even
   half of it at 27B would be the biggest single quality lever we have left.
   One-day: 0.6B — A-side factors = our captured Grams (already on disk);
   B-side = Lanczos/sketch of output-side factor from ~64 backprop samples;
   re-round Q2_K codes against the Kronecker objective; byte-fair KLD vs
   E13's 0.556. Go/no-go for the 27B run.
2. **Lattice-CVP upgrade of the re-rounder** [2507.18553, +2603.11021 as
   precedent]. Problem: E13 = one Babai step, no ordering theory. Expected:
   free, provable improvements from act-order/basis choice; small-but-cheap
   (GPTQ→act-order historically worth a few % KLD). One-day: implement their
   improved ordering + clipping-aware bound check inside our re-rounder at
   0.6B; measure vs E13 and vs ProjQ-deflated (25.94).
3. **Null-space pre-shaping (layerwise "walk the flat manifold, then round")**
   [2605.04738, 2205.10637, 2505.23681]. Problem: nearest-point rounding from
   a bad start; our rejected rotation lane lacked an objective. Method: before
   rounding, add ΔW confined to the bottom eigenspace of the whitened Gram
   (functionally ~invisible), chosen to minimize block heterogeneity /
   grid misfit. Composes with fc correction and E13. Expected: real but
   modest at 2-bit (wide bins); OSAQ shows the mechanism absorbs outliers.
   One-day: 0.6B, bottom-k eigenspace projection + grid-misfit objective,
   requantize Q2_K, KLD vs bare + vs E13.
4. **Principled gauge-fixing before quantization (rotation lane, resurrected
   with an objective)** [2601.21626, 2410.09426, 2501.13987, 2405.16406].
   Problem: our rotation experiment tested a fixed gauge (one Hadamard-like
   choice) and lost; the literature says OPTIMIZED gauges win at W3/W2.
   Expected: the biggest gains in the field at 2–3 bit sit here, BUT it
   changes the serving path (transform must ride our fused kernel — feasible,
   it is exactly a fixed extra factor). One-day (measurement only): 0.6B,
   fit per-tensor diagonal+orthogonal gauge minimizing whitened grid error,
   quantize, score offline with transform folded in numpy — decide whether
   the kernel work is priced in before building it.
5. **Local-entropy / basin-width sensitivity oracle** [1605.06444, 1611.01838,
   2606.09012 + Baldassi cluster]. Problem: our sensitivity geography is
   measured post-hoc per kind; an a-priori, forward-pass-only predictor would
   let beastrank.py allocate without the sweep. Method: per-tensor loss spread
   under K grid-scale dithered roundings (= local entropy at the grid radius;
   also 2606.09012's grid-step/basin-width ratio). One-day: 0.6B, 8 dithered
   roundings × per-tensor, correlate with the measured per-kind sensitivity
   table; if Spearman is high, wire into beastrank.py allocation.
6. **Block-homogeneity permutation gauge** [2304.01089 precedent, permutation
   symmetry lineage]. Problem: K-quant/IQ block scales waste bits on
   magnitude-heterogeneous 16/32-blocks. Method: offline input-channel
   reordering (absorbed into producer's row order; residual-stream seams
   limit which tensors qualify — q/k permutation hazard already mapped in
   llamacpp-internals.md). Expected: small, free at runtime, unexplored for
   GGUF weight blocks. One-day: 0.6B ffn tensors only (no RoPE seam),
   greedy magnitude-clustering permutation, requantize, KLD.
7. **Qronos-style cross-layer sequential correction** [2505.11695,
   2508.04853]. Problem: our alternation is within-tensor; quantization error
   propagates BETWEEN layers uncorrected until the fc adapter mops up.
   Expected: Qronos beats GPTQ-class on Llama-3; overlaps partially with what
   our correction adapter already absorbs — honest risk of redundancy.
   One-day: 0.6B, sequential layer order with error-feedback calibration
   activations, then fc on top; KLD vs alternation crown 25.27.
8. **End-loss saliency for allocation** [2505.07004]. Problem: measured-alloc
   (E07) uses layerwise signals. Expected: GuidedQuant's block-diagonal
   end-loss Fisher is a drop-in reweighting of our existing allocation;
   incremental. One-day: reweight E07 allocation with end-loss gradient
   saliency at 0.6B, compare 7.763-equivalent.
9. **Quantization-vector transfer** [2604.03420]. Problem: every new model
   costs a full calibration+correction pass. If the "robustness direction"
   transfers across our model family (heretic variants), beastrank.py gets a
   warm start. Speculative at LLM scale (shown on ViTs). One-day: extract
   delta on 0.6B, test on a sibling checkpoint.
10. **Symmetry-averaged Grams** [2606.00442]. Better curvature estimates at
    fixed calibration budget by orbit-averaging. Cheap to try when we next
    regenerate imatrices; expected effect: noise reduction, not a new lever.

## Honest kills

- **SAM-family quantization (SAQ/SQuAT/ZSAQ) as a lane** — all training-time;
  our charter is PTQ + adapters. Keep only as evidence that flatness ⇒
  quantizability. KILLED for the build queue.
- **Raw Hessian sharpness as a per-tensor bit oracle** — ill-posed under
  reparameterization (Dinh 1703.04933); must be gauge-fixed (Pittorino
  2202.03038) or metric-corrected. Our whitened metric already IS the fix;
  naive eigenvalue league tables would be a regression. KILLED as stated;
  survives only in gauge-fixed form (candidates 4–5).
- **HAWQ-style trace allocation** — superseded on our stack by measured
  per-kind sensitivity (which we showed beats energy water-filling); the
  trace is a coarser proxy of signals we already measure. KILLED.
- **Garipov-style curve finding / git-rebasin for quantization** — curve
  finding needs training epochs; permutation alignment solves a two-model
  problem we don't have (and permutation alone doesn't change nearest-point
  rounding error; it only matters through block-scale structure — which is
  candidate 6, not rebasin). KILLED as-is.
- **Truncated-SVD revival via manifold hypothesis** — the literature agrees
  with E01: no low-dim manifold for raw weight matrices; low-dim structure is
  trajectory/function/metric-side. E01 stays closed, now with citations.
- **Geodesic quantization as importable prior art** — measured absence on
  arXiv (two off-topic hits). Nothing to import; everything to write. This is
  a paper-positioning asset, not a method source.

## Cross-references

- E13 re-rounder results: RESULTS_ROLLUP.md (0.6B 35.54 free; 27B fade).
- Whitening-as-metric arc: slice 2 (arxiv-manifolds-infogeo, information
  geometry) + arxiv-whitening-allocation.md.
- Factor-manifold optimization (Stiefel/fixed-rank): slice 1
  (arxiv-manifolds-riemannian).
- Posterior-mean dequant (E17): arxiv-entropy-reconstruction.md — BayesQ
  (2511.08821) is the bridge paper between that sweep and this one.
- Consolidated candidate list feeds prior-art/MANIFOLD-CANDIDATES.md.
