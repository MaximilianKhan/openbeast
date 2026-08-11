# arXiv sweep: outlier structure, rotations, and quantization scaling laws

**Date:** 2026-08-03. **Mission:** explain the E04c capture collapse
(whitened capture@r64: 0.37 at 0.6B → 0.07 at 27B) mechanistically, from the
literature, and extract structural exploits that could restore concentration.

**Verdict up front:** the collapse is not a bug and not bad luck — it is the
predictable interaction of three documented facts: (1) the outlier-channel
head that powers diagonal whitening has roughly **O(1) cardinality** (a
handful of channels regardless of width), while bulk residual energy grows
∝ d; (2) the 27B base was **imatrix-quantized**, and imatrix quantization
minimizes the *same* diagonally-weighted objective our whitening measures —
the residual arrives pre-flattened in our own metric (the exact analogue of
the rotation-quant literature's "incoherent residuals are white"); (3) bigger
models tolerate low-bit better because their residual is closer to benign
isotropic noise (error averaging over more dimensions), so there is genuinely
less recoverable structure per rank. Capture at fixed rank behaves like
**c·(r/d)** with c ≈ 5–6 roughly scale-invariant: 0.37 at r64/d1024 and 0.07
at r64/d5120 are the *same* per-dimension concentration. The exploits are at
the end.

---

## A. Massive activations, attention sinks, outlier channels

### The head is tiny, and its size barely grows with width

- **LLM.int8() / emergent features** ([arXiv:2208.07339](https://arxiv.org/abs/2208.07339),
  [Dettmers' blog](https://timdettmers.com/2022/08/17/llm-int8-and-emergent-features/)):
  outlier feature dimensions are **as few as ~0.1% of all dimensions**, carrying
  values 50–100× the rest. At 6.7B: ~150,000 outlier *values* per sequence but
  confined to **6 feature dimensions**. Emergence is a phase transition between
  6B and 6.7B (layers affected: 65%→100%). Outlier-dim count is monotonic in
  *perplexity* (training quality), only loosely in parameter count — i.e. the
  head count does **not** scale with d.
- **Massive Activations** (Sun et al., [arXiv:2402.17762](https://arxiv.org/abs/2402.17762)):
  *very few* activations (typically <10 per model) reach up to **100,000×** the
  median magnitude; they sit at fixed (channel, token) coordinates, are nearly
  input-independent, and function as learned bias terms that induce attention
  concentration (sinks). Count stays a handful across the model families studied.
- **The Super Weight** ([arXiv:2411.07191](https://arxiv.org/abs/2411.07191)):
  pruning as few as **one scalar weight** (an early-layer `mlp.down_proj`
  entry) raises perplexity by 3 orders of magnitude. Super weights *produce*
  the massive/super activations. Preserving them (plus clipping other
  outliers) is itself a competitive quantization method. Coordinates are
  published per-model. Count: single digits per model, not per layer.
- **Anatomy: The Spike, the Sparse and the Sink** ([arXiv:2603.05498](https://arxiv.org/html/2603.05498)):
  mechanism for injection — SwiGLU FFN acts as a *directional quadratic
  amplifier*: spike channels have one dominant eigenvalue λ⋆ orders of
  magnitude above the rest of the spectrum. Massive activations are injected
  by **early "step-up" blocks** and *neutralized by late "step-down" blocks*
  via additive inverse residual contributions. Outlier magnitude becomes
  *more* pronounced with scale; channel count stays small with near-fixed
  inter-channel ratios.
- **Attention sinks lineage:** StreamingLLM ([arXiv:2309.17453](https://arxiv.org/abs/2309.17453))
  established sink tokens; survey at [arXiv:2604.10098](https://arxiv.org/pdf/2604.10098);
  sinks as gradient regulators ([arXiv:2603.17771](https://arxiv.org/html/2603.17771v2));
  ablate-and-retrain shows models *rebuild* the outliers in protected channels
  ([arXiv:2606.20743](https://arxiv.org/pdf/2606.20743)) — the structure is
  functional, not incidental. Quantizable Transformers
  ([arXiv:2306.12929](https://arxiv.org/abs/2306.12929)) shows sinks are the
  softmax's "no-op" mechanism and can only be trained away, not patched away.

### Implication for diagonal whitening (the core of our E05 win)

Our D = sqrt(imatrix E[x²]) is steep **because of this O(1) head**. The
whitened residual's concentrated subspace has dimension ≈ (# outlier
channels + their interaction terms) — call it k_head, order tens,
**approximately width-independent**. Whitened capture@r ≈
(head energy)/(total energy) once r ≥ k_head, and total energy grows ∝ d
while head energy is fixed-count (even if per-channel magnitude grows).
So the capturable *fraction* at fixed rank dilutes ~1/d. **Do outliers get
relatively weaker at scale? No — individually they get stronger; but the
bulk grows linearly in d while the head count doesn't, so the head's
*energy share* shrinks.** This is exactly our 0.37→0.07 at 5× width — same
per-dimension concentration, five times more bulk to pay for.

---

## B. Incoherence processing and rotations vs low-rank correction

### What rotations do

- **QuIP** ([arXiv:2307.13304](https://arxiv.org/abs/2307.13304)): incoherence
  processing via random orthogonal conjugation + LDLQ adaptive rounding;
  first viable 2-bit.
- **QuIP#** ([arXiv:2402.04396](https://arxiv.org/abs/2402.04396)): randomized
  Hadamard transform (RHT) makes weights **"ball-shaped sub-Gaussian"**, then
  E8-lattice vector codebooks exploit that isotropy. 2-bit at 70B works.
- **QuaRot** ([arXiv:2404.00456](https://arxiv.org/abs/2404.00456)): rotates
  weights *and* activations/KV (computational invariance), "effectively
  eliminating outliers" → W4A4.
- **SpinQuant** ([arXiv:2405.16406](https://arxiv.org/pdf/2405.16406)): learns
  the rotations on the Stiefel manifold (quality varies a lot across random
  rotations — structure matters); ButterflyQuant
  ([arXiv:2509.09679](https://arxiv.org/pdf/2509.09679)) makes them cheap-learnable.
- **FrameQuant** ([arXiv:2403.06082](https://arxiv.org/abs/2403.06082)):
  fusion-frame redundancy for ~2.x-bit; same isotropization idea, redundant basis.

### The tension — mostly undocumented as a theorem, but structurally real

No paper states "rotation destroys low-rank correctability" outright, but the
logic is airtight and three places imply it:

1. QuIP#'s **whole point** is that post-RHT weights (and hence residuals of a
   good rounding scheme) are isotropic sub-Gaussian — a white residual has a
   flat singular spectrum, so **SVD correction of a post-rotation residual
   buys ≈ r/d and no more**. Rotation and low-rank correction compete for the
   *same* structure: rotation spends the outlier head to help the codebook;
   correction wants to spend it on factors.
2. **SVDQuant** ([arXiv:2411.05007](https://arxiv.org/abs/2411.05007)) runs the
   opposite order *deliberately*: migrate activation outliers into weights
   (smoothing), let a 16-bit **low-rank branch absorb the outlier-heavy
   component first**, then 4-bit-quantize the now-tame residual. Low-rank
   capture is explicitly fed BY outliers. (Their Nunchaku engine also proves
   the fused low-rank-branch kernel — our unfused −33% is their motivating
   problem, solved.)
3. **ODLRI** ([arXiv:2506.02077](https://arxiv.org/abs/2506.02077), ACL 2025):
   role-splitting made explicit — low-rank captures *activation-sensitive*
   weights so the quantized backbone sees a matrix without extreme values.
   Again: low-rank first / low-rank owns the outliers.

### The synergy — rotate-then-correct exists and works

**CALDERA** ([arXiv:2405.18886](https://arxiv.org/abs/2405.18886)) is the
proof of coexistence: RHT incoherence → alternating optimization of
`min ‖(Q + LR − W)Xᵀ‖` with LDLQ/E8 for Q **and quantized L,R factors**
(4-bit factors → rank is ~4× cheaper in bytes → they can afford rank 128–256).
Beats prior art below 2.5 bpw on Llama-2 70B. Key reading: after rotation the
residual is *flatter*, so CALDERA compensates by (a) optimizing jointly rather
than correcting post-hoc, and (b) buying more rank with quantized factors.
ResQ ([arXiv:2412.14363](https://arxiv.org/pdf/2412.14363), ICML 2025) is the
subspace variant: PCA on activations, keep the **top d/8 subspace in 8-bit**,
rotate *within* subspaces to kill remaining outliers — high-precision
low-rank *subspace* rather than additive low-rank *term*, provably optimal
mixed-precision under their model.

**Takeaway for us:** order matters. Correct-then-rotate (SVDQuant order) or
jointly-optimize (CALDERA) are the two published winning patterns. Naive
rotate-then-SVD-the-residual is the one pattern nobody ships — because the
rotation launders precisely the structure the SVD would have found. And the
K-quant analogue of "rotation" is **imatrix weighting itself** (see C/D):
any quantizer that minimizes the whitened objective leaves a residual that is
white *in that metric*. Our 27B correction target was Q2_K-**imat** — we were
SVD-ing a residual that llama.cpp had already flattened in our own norm.
The 0.6B 0.37 was measured against naive Q2_K. Part of the collapse is
self-competition, and it is *measurable*: capture@r64 on the 27B **naive**
Q2_K residual vs the imat one would split mechanism (1) from (2).

---

## C. Scaling laws: quantization error, model size, and rank of meaningful subspaces

### Why bigger models tolerate low-bit better

- **k-bit inference scaling laws** (Dettmers & Zettlemoyer,
  [arXiv:2212.09720](https://arxiv.org/abs/2212.09720)): across 19M–176B,
  **4-bit is almost universally optimal** in accuracy-per-total-model-bits;
  the frontier moves with block size and data type, not with scale — i.e.
  bit-tolerance per parameter is roughly scale-free *at 4-bit*, and the
  sub-4-bit regime is where scale differences appear.
- **Low-bit quantization favors undertrained LLMs**
  ([arXiv:2411.17691](https://arxiv.org/abs/2411.17691)): from 1500+
  checkpoints — quantization-induced degradation (QiD) **grows with training
  tokens** and **shrinks with model size**. A 27B trained on the same-ish
  token budget as a 0.6B is far less "converged" per parameter → flatter
  local landscape → weights carry less bits-per-weight of load-bearing
  information → the quantization residual is more noise-like. Fully-trained
  (100T-token) models are predicted to quantize *badly*.
- **Scaling laws for precision** ([arXiv:2411.04330](https://arxiv.org/abs/2411.04330))
  and **PTQ scaling laws** ([arXiv:2410.12119](https://arxiv.org/html/2410.12119v3)):
  same direction — post-training quantization error decreases with model size,
  increases with data:parameter ratio and with finer effective granularity.
  QAT variants: [arXiv:2505.14302](https://arxiv.org/abs/2505.14302),
  ParetoQ [arXiv:2502.02631](https://arxiv.org/pdf/2502.02631),
  compute-optimal QAT [arXiv:2509.22935](https://arxiv.org/pdf/2509.22935).

**Mechanistic reading for our problem:** "big models tolerate 2-bit better"
and "big models' residuals are less low-rank-correctable" are the **same
fact**. The tolerance comes from the residual being closer to isotropic noise
that averages out over more dimensions (blessing of dimensionality); isotropic
noise is exactly what low-rank correction cannot compress. Less harm, less
correctable harm — the prize per corrected byte shrinks together with the
damage. Corollary: at 27B the win condition shifts from "recover energy" to
"recover the *few* directions that matter" (KL-relevant, not Frobenius-relevant).

### Rank of meaningful subspaces vs width

- **Intrinsic dimensionality** (Aghajanyan et al.,
  [arXiv:2012.13255](https://arxiv.org/abs/2012.13255) — see also
  [Li et al. 2018](https://arxiv.org/abs/1804.08838)): d90 for fine-tuning
  **decreases as model size grows** (RoBERTa-L: 207 parameters reach 90% of
  full fine-tuning on MRPC). Task-relevant subspaces grow *slower* than d —
  in fact they shrink absolutely.
- **LoRA** ([arXiv:2106.09685](https://arxiv.org/abs/2106.09685)): r = 1–4
  suffices at GPT-3 175B; rank needs do not grow with width for *adaptation*.
- Caveat: those results are about the **update/task subspace**, not the
  quantization-residual subspace. The literature's implication cuts both
  ways: the *functionally load-bearing* part of any weight perturbation at
  scale is very low-rank (good for us — the KL-relevant residual component
  is small), but the *energy* of the residual is high-rank noise (bad for
  Frobenius-guided allocation). This is the same "energy framing is the
  wall" lesson as E03, now at the level of *which* residual directions to
  chase: chase the ones the intrinsic-dimension literature says exist — few,
  functional, sink/output-coupled — not the energy bulk.

---

## D. KL divergence / top-1 agreement as quantization metrics

Our E04 instrumentation choice (mean-KLD + top-1 agreement + PPL) is the
literature-endorsed stack, with known calibration:

- **Accuracy is Not All You Need** (Microsoft, NeurIPS 2024,
  [arXiv:2407.09141](https://arxiv.org/abs/2407.09141)): compressed models
  with *identical* benchmark accuracy show large **flip rates** (correct↔
  incorrect churn); flips ≈ what users experience; **KL-divergence correlates
  with flips at Spearman 0.981 (MMLU)**. Top-1 agreement is 1 − flip-rate on
  the token level — our 83.6% number is a flips metric.
- **llama.cpp Discussion #4110**
  ([link](https://github.com/ggml-org/llama.cpp/discussions/4110)): ikawrakow's
  case that PPL deltas mislead for quant comparisons; KLD against the
  reference isolates quantization damage from the model's intrinsic
  uncertainty. This is the *upstream maintainers'* preferred lens — our
  eventual PR benchmarks should lead with KLD, which we already do.
- **A KL Lens on Quantization** ([arXiv:2604.13440](https://arxiv.org/pdf/2604.13440)):
  KL as a forward-only per-layer sensitivity signal for mixed precision;
  Kendall τ ≈ 0.79 against downstream, beating SQNR (τ 0.76) — logit-space
  fidelity underperforms probability-space fidelity.
- **Displacement Is Not Direction** ([arXiv:2606.19558](https://arxiv.org/abs/2606.19558v1)):
  the calibration limit — KLD↔benchmark correlation **collapses in the
  near-baseline "silent zone"** (very good quants are indistinguishable by
  KLD rank). Not our regime: Q2-class KLD 0.05–0.5 is exactly where the
  metrics discriminate strongly. But it warns against over-reading small KLD
  deltas once we get corrected-Q2 near Q3_K_M (0.146 vs 0.0555 is safely
  outside the zone; 0.06 vs 0.055 would not be).

---

## Synthesis: why capture collapsed, and what to do about it

### The mechanistic explanation (three stacked mechanisms, all literature-grounded)

1. **Head dilution (dominant, geometric).** Diagonal whitening's power comes
   from an outlier-channel head of near-constant cardinality (LLM.int8: ~6
   dims at 6.7B; Massive Activations: <10; super weights: single digits).
   Bulk whitened-residual energy grows ∝ d; head energy doesn't. Capture at
   fixed r therefore scales ≈ c·r/d: 64/1024 → 0.37 and 64/5120 → 0.07 are
   the *same law* (c ≈ 5–6) at two widths. Nothing broke; we held r fixed
   while d quintupled. **Falsifiable check:** capture-vs-r curves at both
   scales should collapse when plotted against r/d.
2. **Self-competition with imatrix (order-of-operations, fixable).** The 27B
   base was Q2_K-imat: llama.cpp's imatrix quantizer minimizes the same
   diag(E[x²])-weighted error our whitening measures, so its residual is
   pre-whitened — the K-quant analogue of the documented
   incoherence-processing effect (QuIP#'s ball-shaped weights → white
   residual; CALDERA's response: joint optimization + cheap quantized
   factors). The 0.6B 0.37 was against *naive* Q2_K. **Disambiguation
   experiment: measure capture@r64 on 27B naive-Q2_K residual.** Expect
   materially higher than 0.07; the gap quantifies mechanism 2.
3. **Noise-like residuals at scale (thermodynamic, unfixable).** QiD scaling
   laws: larger/undertrained models take less quantization damage because
   the residual behaves like averaging noise. Less damage = less
   *structured* damage = less for any rank to grab. The flip side is that
   the functionally load-bearing residual directions are FEW (intrinsic
   dimension shrinks with scale) — energy capture underestimates achievable
   KL recovery.

### The three most promising structural exploits (ranked)

**E-1. Sparse outlier patching + sink-adjacent targeting (replace rank with
sparsity where the structure is sparse).** The head is O(few) channels at
known, input-independent coordinates (super-weight indices are *published*;
llama-imatrix gives us per-channel E[x²] to find ours). A rank-r factor pair
spends r(m+n) numbers to encode what is actually k rows/columns —
O(k·max(m,n)) with k≪r. Store exact FP16 rows/cols (or a high-precision
override block) for: super-weight coordinates, sink-channel columns of
q/k/v/o, and the early-layer down_proj injectors identified by the
step-up/step-down anatomy (2603.05498). This concentrates bytes where
2402.17762/2411.07191 prove the function lives, and it scales O(1) with
width — immune to head dilution by construction. Precedent: SqueezeLLM's
dense-and-sparse split ([arXiv:2306.07629](https://arxiv.org/abs/2306.07629)),
LLM.int8's mixed-precision decomposition, super-weight-aware quantization.

**E-2. Equal-capture rank allocation: budget rank ∝ d, allocate per-tensor,
and correct the naive base.** The c·r/d law says uniform r64 at 27B was
predictably ~5× short; byte-economics r(m+n)/mn improve exactly ~5× at 27B,
so equal-*capture* rank (r ≈ 300 on 5120-wide tensors, water-filled from the
E04c energy map: q/k/gate/up first, o/down last or never) costs the same
*bpw overhead* that r64 cost at 0.6B. Pair with the order fix from B: either
correct a **naive** (non-imatrix) low-bit base — letting the adapter do the
whitened work the imatrix would have done, CALDERA/SVDQuant-style "low-rank
owns the outliers" — or keep imat + adapter but allocate rank by *KL
sensitivity* (2604.13440's forward-only KL lens) instead of whitened energy.
Quantized factors (Q8_0, later Q4-class per CALDERA) double-to-quadruple
affordable rank at fixed bytes.

**E-3. Full-covariance / activation-subspace whitening for the flat tensors
(the o/down fix, and the road past diagonal's ceiling).** Diagonal E[x²] is
blind exactly where E05 measured flatness (o/down — internal-activation
inputs). ResQ demonstrates the fix at production scale: the top ~d/8
PCA subspace of activations carries the variance that matters; QERA gives
the closed-form optimal factors under full covariance. Concretely: capture
activation Grams for o/down inputs (the one new instrumentation piece,
already on the forced-moves list), whiten by Σ^{1/2} not diag, and expect
concentration to reappear *because the objective stops counting residual
directions that activations never excite* — restoring effective r/d_eff
with d_eff = activation intrinsic dimension, which per Aghajanyan/ResQ grows
much slower than d. This is the mechanism by which whitening can beat head
dilution: shrink the denominator instead of growing the numerator.

**Explicitly deprioritized:** Hadamard rotate-then-SVD-correct as a first
move — the rotation launders the outlier head that both our whitening and
our factors feed on (Section B). Rotation belongs *inside* a joint
CALDERA-style optimizer or *after* sparse patching, not before naive
correction. Named so we don't re-derive it.

### Paper index

| Area | Paper | arXiv |
|---|---|---|
| Outliers | LLM.int8 emergent features | 2208.07339 |
| Outliers | Massive Activations | 2402.17762 |
| Outliers | The Super Weight | 2411.07191 |
| Outliers | Spike/Sparse/Sink anatomy | 2603.05498 |
| Outliers | StreamingLLM (sinks) | 2309.17453 |
| Outliers | Sink survey | 2604.10098 |
| Outliers | Sinks as gradient regulators | 2603.17771 |
| Outliers | Outliers rebuilt after ablation | 2606.20743 |
| Outliers | Quantizable Transformers (sink origin) | 2306.12929 |
| Rotations | QuIP | 2307.13304 |
| Rotations | QuIP# | 2402.04396 |
| Rotations | QuaRot | 2404.00456 |
| Rotations | SpinQuant | 2405.16406 |
| Rotations | FrameQuant | 2403.06082 |
| Rotations | ButterflyQuant | 2509.09679 |
| Rotation+LR | CALDERA | 2405.18886 |
| Rotation+LR | ResQ | 2412.14363 |
| LR-first | SVDQuant (+ Nunchaku fused kernels) | 2411.05007 |
| LR-first | ODLRI role-splitting | 2506.02077 |
| LR-first | LQER (spectrum shaping = our E05) | 2402.02446 |
| LR-first | SqueezeLLM dense+sparse | 2306.07629 |
| Scaling | k-bit inference scaling laws | 2212.09720 |
| Scaling | Low-bit favors undertrained | 2411.17691 |
| Scaling | Scaling laws for precision | 2411.04330 |
| Scaling | PTQ scaling laws | 2410.12119 |
| Scaling | QAT scaling law / ParetoQ / compute-optimal QAT | 2505.14302 / 2502.02631 / 2509.22935 |
| Rank vs d | Intrinsic dimensionality (fine-tuning) | 2012.13255 |
| Rank vs d | Measuring intrinsic dimension (Li et al.) | 1804.08838 |
| Rank vs d | LoRA (r=1–4 at 175B) | 2106.09685 |
| Metrics | Accuracy is Not All You Need (flips) | 2407.09141 |
| Metrics | KL Lens on Quantization | 2604.13440 |
| Metrics | Displacement Is Not Direction (silent zone) | 2606.19558 |
| Metrics | llama.cpp KLD discussion | ggml-org/llama.cpp#4110 |

*Fetched/verified 2026-08-03. Abstract-level verification for starred claims;
CALDERA RHT+quantized-factor details cross-checked against paper PDF and
pilancilab/caldera README; LLM.int8 outlier counts from paper + author blog.*
