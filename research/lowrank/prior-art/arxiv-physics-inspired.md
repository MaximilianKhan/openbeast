# arXiv sweep: physics-inspired compression structures — the "spacetime compression" intuition, treated honestly

**Sweep date:** 2026-08-03. **Prompted by:** Max's intuition that "the answer may
lie in obscure physics research in space-time compression," against the measured
wall: diagonal-whitened low-rank correction exhausted at 27B (KLD plateau ~0.14;
recovery fraction rises toward the low-bit cliff, 7.5%→18%, but never jumps a
quant-ladder rung).

**Method of this sweep:** separate the *mathematics that physics produced* (tensor
networks, RG, information geometry, random-matrix/free-probability theory) from
the *physics itself* (metrics, holographic bounds). The first category contains
real, citable compression results. The second contains none, and this file kills
it explicitly so the campaign never relitigates.

**Standing constraint:** every candidate correction must reduce to GEMM-compatible
ops at serve time (llama.cpp LoRA path `y = Q·x + B·(A·x)`, or batched GEMM a
custom kernel could fuse).

**Verdict up front (ranked):**

| Rank | Lane | Object | Verdict |
|---|---|---|---|
| 1 | (c) information geometry | two-sided (K-FAC-style) Fisher whitening of the residual | **HIGH — run this week.** Changes the *metric*, not the structure; serving unchanged |
| 2 | (d) RMT / free probability | MP-bulk spike audit + optimal singular-value shrinkage | **HIGH as diagnosis.** Explains the plateau, yields a per-tensor correctability ceiling + a free quality win |
| 3 | (b) RG / multiscale | two-level correction: global low-rank + block-local low-rank on learned neuron clusters | **MEDIUM-HIGH.** The one genuinely new *structure* with published low-bit evidence (wavelet-domain PTQ) |
| 4 | (a) tensor networks / MERA | MPO/MERA-structured residual at matched bytes | **LOW-MEDIUM.** Published LLM evidence is negative without retraining; run one byte-matched kill test |
| 5 | (e) literal spacetime / holographic bounds | — | **DEAD.** No math object acts on a weight matrix. Do not reopen |

---

## Lane (a) — Tensor networks, MERA, and holography-inspired structure

### The math object

A tensor network re-expresses a big tensor as a contraction of small ones:
matrix product operators (MPO/tensor-train) are a 1-D chain of 4-leg tensors;
tree tensor networks add hierarchy; **MERA** (multiscale entanglement
renormalization ansatz) adds *disentanglers* between hierarchy levels and is the
one with genuine spacetime pedigree — its graph metric is hyperbolic, and
Swingle's observation that MERA discretizes an AdS time-slice is the origin of
the "holography = compression" meme. An MPO contraction is a sequence of
reshapes + small GEMMs, so it is *in principle* serveable.

### Published evidence (numbers)

- **1711.03357** (Hallam, Grant, Stojevic, Severini, Green) — MERA layers
  replacing FC layers in small conv nets (CIFAR-10/100): ~14,000× fewer
  parameters in the replaced layer at <1% accuracy loss, and *beats tensor-train
  at equal compression*. Caveat that kills transfer to us: **trained from
  scratch** — the MERA structure is imposed before learning, not fitted to a
  trained weight. (Companion math: **1912.10572**, MERA↔MPO in scale space.)
- **2401.14109** (CompactifAI, Multiverse Computing) — MPO-tensorization of
  LLaMA-2 7B attention+MLP: ~70% size reduction retaining ~90% accuracy, 27.1 GB
  → 4.1 GB in the mixed int4 variant. Caveat that kills transfer: requires a
  **retraining/"healing" pass**; this is not PTQ. (Independent eval:
  **2507.08836**.)
- **2505.20132** — position paper: tensorization is "powerful but
  underexplored"; honest about the absence of large-scale PTQ wins.
- **2606.03465** ("Rethinking the Role of Tensor Decompositions in Post-Training
  LLM Compression") — the load-bearing *negative* result: systematic evaluation
  across dense and MoE LLMs finds a "fundamental mismatch between the shared
  subspaces assumed by tensor decompositions and the heterogeneous
  representations learned by modern LLMs." In the post-training regime —
  our regime — tensorization does not beat activation-aware low-rank.
- **2501.19135** — TT-decomposed LLM on a systolic accelerator: hardware
  feasibility exists, but the quality numbers again lean on fine-tuning.

### Why the physics analogy breaks for residuals

MERA's hyperbolic geometry pays off when the tensor's index space has
**locality** — site *i* is near site *i+1*, and correlations decay with
distance, so entanglement across scales is the right organizing principle. A
weight matrix has no such geometry: neuron indices are permutation-symmetric
(any row/column shuffle plus the inverse shuffle downstream is the same
network), so an MPO's assumption that "nearby reshaped indices are more
correlated" is an artifact of an arbitrary reshape. CompactifAI works *because
healing re-trains the network into the imposed geometry*. We cannot re-train.
And our target is worse than W: the whitened residual is close to
rotationally-invariant noise plus a few spikes (see lane d) — the *least*
tensor-network-shaped object imaginable.

### Beast-rank experiment (one day, kill test)

E-TN: take 6 representative 27B tensors (attn `o_proj`, MLP `down_proj` across
depth). Fit (i) dense rank-r whitened SVD and (ii) MPO at **matched byte
budget** (sweep bond dimension), both on the whitened residual. Compare capture
fraction ‖R−R̂‖²_F/‖R‖²_F. PyTorch offline, no serving work. Prediction from
2606.03465 + lane (d): MPO ties or loses. If it loses, this lane is closed with
our own numbers; if it wins anywhere, only then think about kernels.

**Verdict: LOW-MEDIUM.** The hyperbolic-geometry inspiration is legitimate math
with real citations, but every published LLM win requires retraining, and the
2026 systematic study is negative for PTQ. Run the one-day kill test, then close.

---

## Lane (b) — Renormalization group and multiscale correction structure

### The math object

RG is coarse-graining with memory: integrate out fine degrees of freedom,
keep a small set of *relevant* operators per scale, iterate. The deep-learning
connection is old and real — **1410.3831** (Mehta–Schwab, exact mapping
variational RG ↔ stacked RBMs), **1906.05212** (tests of "is deep learning an
RG flow"), **2510.25553** (2025: RG on DNNs by marginalizing low-lying principal
components; connects universality/scaling laws directly to compression),
**1903.00804** (neural-network RG constructing a holographic mapping — the
legitimate half of the holography meme: RG depth *is* the emergent radial
dimension). None of these is a compression algorithm. The *actionable* residue
is the RG **prescription**: don't correct at one scale — allocate correction
budget per scale, coarse structure globally, fine structure locally.

Two concrete multiscale families already exist with numbers:

1. **Wavelet-domain quantization.** **2103.05363** (MWQ) quantizes wavelet
   subbands separately. **2512.00862** (HBLLM, Dec 2025) — Haar-wavelet-enhanced
   **1-bit** PTQ for LLMs: frequency-aware grouping and per-band shared
   statistics, **LLaMA-2-13B perplexity 6.71 at 1.08 bpw** — a real number *at
   the low-bit cliff, in our exact regime*, showing scale-separated treatment
   beats flat treatment when bits are scarce. (**2409.12924**, WaveletGPT, is
   architecture-side multiscale, cited for completeness only.)
2. **Hierarchical matrices (H/H²/HODLR)** — the fast-multipole inheritance
   (**1602.02244**): diagonal blocks dense, off-diagonal blocks low-rank,
   recursively. This is *literally* "fine structure locally, coarse structure
   globally," it is block-GEMM at serve time, and NN-shaped versions exist
   (**1807.01883**, **1808.02376** multiscale NNs on H-matrix nested bases;
   **2409.07028** error-bounded H-matrix NN compression — PINN-focused, no
   large-model numbers, weak evidence). Same caveat as lane (a): H-matrices
   assume index locality; weight matrices need a **learned permutation /
   neuron clustering** before the block structure means anything.
   Adjacent and GPU-proven: **Monarch** (**2204.00595**, block-diagonal ×
   permutation × block-diagonal, expresses all fast transforms, up to 2× faster
   than dense on tensor cores; **2310.12109** Monarch Mixer; **2405.15013**
   butterfly GEMM memory management; **2601.13563** ButterflyMoE).

### Why this attacks *our* plateau specifically

Dense rank-64 spends its entire budget on 64 global directions. The measured
capture collapse (0.37→0.07 at 27B) says the residual's energy is *not* in a few
global directions — it is spread. A two-level structure at the same byte cost
buys: a small global term (the true spikes, lane d) **plus** k block-local
low-rank terms that can capture k·r₂ *different* directions in different neuron
clusters — full-rank-ish in aggregate, cheap per block. This is the only lane
in the sweep that proposes a *structurally* richer correction rather than a
better metric.

### Beast-rank experiment (this week)

E-MS (multi-scale correction), offline first:

1. Cluster rows/cols of each whitened residual (co-activation or graph
   clustering on |R| energy; even fixed contiguous blocks as a null).
2. Fit `R̂ = A·B (global, rank r₁) + blockdiag(A_i·B_i, rank r₂ each, k blocks)`
   by alternating least squares (2–3 iterations suffice); byte-match against
   dense rank r = r₁ + k·r₂·(block_frac).
3. Compare capture fraction; if it wins, serve v0 by materializing the whole
   thing as one **dense LoRA of rank r₁ + k·r₂ whose A,B are block-sparse** —
   llama.cpp's existing adapter path runs it unmodified (wasted FLOPs on
   structural zeros, fine for a quality measurement); the fused
   batched-GEMM kernel is later PR-payload work.

GEMM check: block-diagonal low-rank = batched thin GEMM; permutation = free
(bake into A/B). Fully serveable.

**Verdict: MEDIUM-HIGH.** RG-as-analogy is unfalsifiable vibes; RG-as-budget-
allocation-across-scales is a concrete structure with 1-bit LLM evidence
(HBLLM) and a GPU-native format (Monarch/H-matrix). This is the genuinely new
structure candidate from the whole sweep.

---

## Lane (c) — Information geometry: the metric is wrong before the structure is

### The math object

The parameter space of a probabilistic model is a Riemannian manifold with the
Fisher information metric F; to second order, `KL(p_θ ‖ p_{θ+δ}) ≈ ½ δᵀF δ`.
Our campaign target *is* KLD. Therefore "compression as geodesic projection in
curved information space" has exactly one actionable consequence, and it is not
exotic: **the low-rank fit should minimize the Fisher-weighted norm of the
residual, not an activation-whitened Frobenius norm.** Diagonal imatrix
whitening (E[x²]) is a crude approximation to one Kronecker factor of the
layerwise Fisher block. The full layerwise Gauss–Newton/Fisher block is
`F_layer ≈ E[g gᵀ] ⊗ E[x xᵀ]` (output-gradient covariance ⊗ input covariance) —
K-FAC. We currently use a diagonal approximation of the *right* factor and
**ignore the left factor entirely**.

### Published evidence (numbers)

- **2207.00112** (FWSVD, Hsu et al., ICLR'22) — row-wise Fisher weighting before
  SVD: 9–30% parameter reduction with near-original task accuracy where plain
  SVD collapses; explicitly shows *larger* reconstruction error but *better*
  task accuracy — proof that the Frobenius metric is misaligned and Fisher is
  the fix.
- **2505.17974** (GFWSVD, 2025) — scalable **Kronecker-factored** Fisher
  weighting, improving on FWSVD's row-only weighting. This is the two-sided
  upgrade path in the literature already.
- **2306.07629** (SqueezeLLM) — Fisher-diagonal sensitivity drives non-uniform
  quantization grids for LLMs; evidence Fisher weighting is computable at LLM
  scale from modest calibration gradients.
- Cross-layer coupling (the part even K-FAC drops): **2504.09629** (Quantization
  Error Propagation) and **2604.07955** (ResComp) show layerwise-independent
  objectives leave recoverable error on the table — relevant later, not this
  week.

### Why this attacks our plateau specifically

The measured plateau is a plateau *in the diagonal-whitened metric*. QERA
(2410.06040, already in `arxiv-whitening-allocation.md`) showed diagonal → full
input covariance matters below 4-bit. The information-geometry lane says the
next rung is adding the **left (output-gradient) factor**: directions the next
layers amplify get corrected first. Concretely the fit becomes
`Δ* = G^{-1/2}·[G^{1/2} R S^{1/2}]_r·S^{-1/2}` with `G = E[ggᵀ]`,
`S = E[xxᵀ]` — a two-sided generalized Eckart–Young, closed form, same A·B
output. **Serving is unchanged.** All cost is offline: one backprop pass over
the calibration set with per-layer hooks (layerwise, checkpointed — feasible for
27B on the 5090 since we never need the full model's optimizer state, only
per-layer `g` statistics).

The mystical version ("quantization as measurement in curved space,"
true geodesic projection) has **no published algorithm** beyond this
second-order expansion; nothing to cite, nothing to build. The expansion is the
whole content.

### Beast-rank experiment (this week)

E-IG: collect per-layer `E[ggᵀ]` (or its diagonal as a cheap first cut) on the
standard calibration set; refit the same rank-64 corrections under the two-sided
metric; measure KLD vs the 0.14 plateau. Zero serving changes — pure offline
refit, direct A/B against the exhausted baseline.

**Verdict: HIGH — rank 1.** Cheapest genuinely-new math in the sweep, directly
aimed at the KLD objective, literature already validates each ingredient at LLM
scale, and it composes with lanes (b) and (d) instead of competing.

---

## Lane (d) — Random matrix theory and free probability: the shape of the wall

### The math object

Marchenko–Pastur (MP) describes the singular spectrum of pure noise; the
**BBP transition** (Baik–Ben Arous–Péché, **math/0403022**) says a rank-1 signal
buried in noise produces a detached eigenvalue iff its strength exceeds a sharp
threshold — below it, the spike is *information-theoretically invisible to
spectral methods*, and low-degree-polynomial hardness results (**2301.05331**,
spiked-model detection) indicate no efficient algorithm recovers it at all.
Free probability (**Pennington–Worah, NeurIPS'17**; **1912.00827**;
**2605.03634** free decompression) is the calculus for computing these spectra
through nonlinearities and products. Heavy-tailed universality
(**1810.01075**, **1901.08276** Martin–Mahoney; **2002.06716**; **2507.17912**
SETOL) says trained LLM weight ESDs are heavy-tailed power laws, *not* MP — the
spikes-plus-bulk picture needs the heavy-tailed threshold, not the Gaussian one.
And **2411.01974** (extensive-rank denoising *beyond rotational invariance*) is
the frontier result: rotation-invariant estimators (RIE) — which includes
*every* whitened-SVD-truncation method, ours included — have a computable
optimal error, and beating it requires exploiting non-invariant structure
(rows/columns/sparsity), i.e., exactly what outlier-aware and block-structured
methods do.

### What it says about beast-rank (this is the plateau's autopsy)

1. **The correctable content of a residual is its above-threshold spikes,
   full stop** — for any rotation-equivariant correction. If the whitened 27B
   residual has s spikes above the (heavy-tailed) bulk edge and s ≪ 64, then
   rank 64 wastes (64−s) directions fitting bulk noise that generalizes to
   nothing — which is precisely what a capture collapse 0.37→0.07 with a KLD
   floor looks like. LQER (**2402.02446**) exploits the same physics in the
   forward direction: its diagonal scaling exists to *push* residual spectra
   toward few-spike shapes. The rest of the correction lineage (QERA
   **2410.06040**, CALDERA **2405.18886**, LRC **2412.07902**, EoRA
   **2410.21271**) all implicitly live under this ceiling.
2. **Why recovery fraction rises toward the cliff (7.5%→18%):** as the base
   quantizer coarsens, the residual stops being quantization *noise* and starts
   containing W's own heavy-tailed structure — more mass crosses the BBP
   threshold, more spikes become recoverable. The trend is the theory working,
   and the theory also says it saturates: the bulk is never recoverable.
3. **Incoherence processing is the anti-strategy, and clarifies ours.** QuIP
   (**2307.13304**), QuIP# (**2402.04396**), QuaRot (**2404.00456**), SpinQuant
   (**2405.16406**), ButterflyQuant (**2509.09679**, learnable butterfly
   rotations — note the lane-(b) structure reappearing) all *Gaussianize* W by
   random/learned rotation before quantizing. That trades away every spike: the
   post-rotation residual is maximally MP-like, i.e., **maximally
   uncorrectable by low-rank**. Two coherent regimes exist — (A) rotate, then
   quantize well, accept no correction; (B) don't rotate, keep structure, and
   correct spikes. Mixing them (rotating and then hoping for a low-rank
   correction) is theoretically incoherent. Beast-rank is regime B; this lane
   proves regime B has a computable ceiling.
4. **One free win: optimal shrinkage.** We hard-truncate; the RMT-optimal move
   also *shrinks* kept singular values toward the signal value the noise
   inflated (Gavish–Donoho **1305.5870** hard threshold, **1405.7511** optimal
   shrinkage). One line of offline code, guaranteed non-negative improvement in
   expectation.

### Beast-rank experiment (this week, cheap)

E-RMT (spectrum audit): for every 27B tensor, plot the ESD of the whitened
residual; fit the bulk (MP and power-law fits — Martin–Mahoney tooling
`weightwatcher` does this off the shelf); count above-edge spikes s per tensor.
Deliverables: (i) a per-tensor **correctability ceiling** s·(m+n)·bytes — the
principled rank-allocation map we currently lack; (ii) hard-truncation →
optimal shrinkage swap; (iii) the decision datum: if Σs at IQ2 is small, the
plateau is *proven* fundamental for rotation-equivariant corrections, and the
campaign's remaining moves are exactly the non-equivariant ones (lane b blocks,
row/col outliers per 2411.01974).

**Verdict: HIGH as diagnosis, MEDIUM as cure.** This lane most likely does not
break the plateau — it *explains* it, bounds it per-tensor, hands us a free
shrinkage win, and redirects budget toward the structures that can escape the
RIE bound. That is worth a week on its own.

---

## Lane (e) — The kill list (do not relitigate)

### Literal spacetime metrics / warp / wormhole "compression" — DEAD

A spacetime metric is a solution of the Einstein field equations on a 4-manifold;
its "compression" of space is a statement about proper distances measured by
physical rulers, and it is generated by stress-energy, not by data. A weight
matrix is not a field on a manifold: it has no locality, no diffeomorphism
invariance, no stress-energy tensor, and no ruler. More fatally, any attempt to
port the *mathematics* (a coordinate change that "shrinks" a region) is an
invertible map — and invertible maps compress nothing: Shannon and Kolmogorov
bounds are coordinate-invariant. There is no arXiv literature applying metric
"compression" to parameter storage because there is no object for it to act on.
Zero papers, zero math objects, zero experiments. Closed.

### Quantum-gravity holographic bounds as storage claims — DEAD

The Bekenstein and holographic (Bousso) bounds limit the entropy that can be
contained in a physical region given its area/energy — of order 10⁶⁹ bits/m² —
and are statements about quantum gravity's state counting, not encoding schemes.
A GPU's weight storage sits ~40 orders of magnitude below the bound; the bound
is vacuously satisfied and prescribes nothing. AdS/CFT's "the boundary encodes
the bulk" is a duality between two specific physical theories with a
*known dictionary*; it is not an algorithm for re-encoding arbitrary matrices at
lower dimension, and the ML papers in that space (**1802.08313** Deep Learning
and AdS/CFT, **2511.22522** AdS/Deep-Learning II) use neural nets to *solve
holographic inverse problems* — the arrow points the other way. The only
legitimate residue of holography for compression is structural inspiration via
MERA's hyperbolic geometry and RG's emergent radial dimension (**1903.00804**),
which lanes (a) and (b) already treat — and lane (a)'s PTQ evidence is negative.
Closed.

---

## Composite recommendation (how the lanes stack)

The lanes compose into one pipeline rather than competing:

```
E-RMT audit (d)  →  how many spikes exist per tensor (rank-allocation map + ceiling)
E-IG metric (c)  →  fit those spikes in the two-sided Fisher metric (KLD-aligned)
shrinkage  (d)   →  shrink, don't truncate (free)
E-MS blocks (b)  →  spend the budget the audit says is wasted on bulk
                    on block-local structure instead (escapes the RIE bound)
E-TN        (a)  →  one-day byte-matched kill test, then close
```

All of it serves through the existing LoRA GEMM path (block-diagonal terms as
block-sparse A·B for v0; fused batched-GEMM kernel only if quality earns it).

The honest summary of Max's intuition: **spacetime itself contributes nothing —
but the mathematics physicists built to *tame* spacetime-adjacent problems
(RG's scale separation, MERA's hierarchy, BBP's detectability threshold, the
Fisher metric) contains one likely metric upgrade (c), one plateau autopsy with
a free win (d), and one new structure worth building (b).** The intuition was
pointing at the right library, wrong shelf.

## Full citation list

| arXiv ID | Short name | Lane |
|---|---|---|
| 1711.03357 | MERA compact NNs (CIFAR) | a |
| 1912.10572 | MERA ↔ MPO in scale space | a |
| 2401.14109 | CompactifAI (MPO LLaMA-2 + healing) | a |
| 2507.08836 | CompactifAI independent eval | a |
| 2505.20132 | Tensorization: powerful, underexplored | a |
| 2606.03465 | Rethinking tensor decompositions for PTQ (negative) | a |
| 2501.19135 | TT-LLM on systolic accelerator | a |
| 1410.3831 | Mehta–Schwab variational RG ↔ deep learning | b |
| 1906.05212 | Is deep learning an RG flow? | b |
| 2510.25553 | RG for DNNs: universality + scaling laws | b |
| 1903.00804 | Neural-network RG holographic mapping | b, e |
| 2103.05363 | MWQ multiscale wavelet quantization | b |
| 2512.00862 | HBLLM: Haar-wavelet 1-bit PTQ (13B, 6.71 ppl @ 1.08 bpw) | b |
| 2409.12924 | WaveletGPT (architecture-side, completeness) | b |
| 1602.02244 | FMM as hierarchical low-rank | b |
| 1807.01883 / 1808.02376 | Multiscale NNs on H-matrix nested bases | b |
| 2409.07028 | Error-bounded H-matrix NN compression (weak evidence) | b |
| 2204.00595 | Monarch structured matrices | b |
| 2310.12109 | Monarch Mixer | b |
| 2405.15013 | Butterfly sparse GEMM on GPU | b |
| 2601.13563 | ButterflyMoE | b |
| 2207.00112 | FWSVD Fisher-weighted SVD | c |
| 2505.17974 | GFWSVD Kronecker-factored Fisher | c |
| 2306.07629 | SqueezeLLM (Fisher sensitivity at LLM scale) | c |
| 2504.09629 | Quantization error propagation | c |
| 2604.07955 | ResComp: rethinking residual errors | c |
| math/0403022 | BBP phase transition | d |
| 2301.05331 | Spiked-model detection thresholds | d |
| 1810.01075 / 1901.08276 | Martin–Mahoney heavy-tailed self-regularization | d |
| 2002.06716 | Predicting model quality from spectra | d |
| 2507.17912 | SETOL semi-empirical theory of learning | d |
| 2411.01974 | Extensive-rank denoising beyond rotational invariance | d |
| 2605.03634 | Free decompression, algebraic spectral curves | d |
| 1912.00827 | RMT for mixtures of nonlinearities | d |
| 1305.5870 / 1405.7511 | Gavish–Donoho threshold / optimal shrinkage | d |
| 2307.13304 / 2402.04396 | QuIP / QuIP# incoherence processing | d |
| 2404.00456 / 2405.16406 | QuaRot / SpinQuant rotations | d |
| 2509.09679 | ButterflyQuant learnable rotations | b, d |
| 2402.02446 | LQER: scale-shaped residual spectra | d |
| 2410.06040 / 2405.18886 / 2412.07902 / 2410.21271 | QERA / CALDERA / LRC / EoRA (correction lineage, see other files) | d |
| 1802.08313 / 2511.22522 | Deep learning ↔ AdS/CFT (arrow points the other way) | e |

*(Pennington–Worah, "Nonlinear random matrix theory for deep learning," is
NeurIPS 2017; cited without arXiv ID.)*
