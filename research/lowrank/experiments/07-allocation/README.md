> ⚠ see review/ corrections 2026-08-04

# E07 — rank allocation: energy water-filling vs measured sensitivity

E04c's uniform-rank null forced this experiment: spend correction bytes
only where they pay. Two competing allocation signals:

- **H5a (cheap):** greedy water-filling on whitened eigenvalues per byte —
  optimal IF whitened energy is commensurable across tensors.
- **H5b (expensive but grounded):** allocate by MEASURED functional
  sensitivity — ΔKL from correcting each tensor kind in isolation.

## Machinery

`allocate_adapter.py`: pass-1 caches top-K whitened spectra + bases
(rsvd) for every tensor; pass-2 global greedy under a byte budget; pass-3
emits mixed-rank adapters (alpha=1, rank folded into A — convention
validated on 0.6B: mixed hand-map PPL 31.28 ≈ uniform-r64 31.29).

## Results so far (0.6B, Q2_K-imat base, 80 MB budget = uniform-r64 bytes)

| allocation | PPL | vs uniform r64 |
|---|---|---|
| uniform r64 (baseline) | 31.29 | — |
| hand rank-map (q/k=128, v/gate/up=64, o/down=16) | 31.28 | tie |
| **greedy energy water-filling (kmax 192)** | **34.17** | **WORSE** |

**Named result: cross-tensor energy water-filling FAILS.** The greedy
piled rank ≤192 onto q/k/v (whose whitened energies are numerically
loud) and zeroed 80 tensors including most attn_o/ffn_down — exactly the
tensors diagonal whitening UNDERRATES (their inputs are internal
activations where diag(E[x²]) is a poor covariance model). Whitened
eigenvalues are not comparable across tensors: activation scale ≠
functional importance. Uniform rank is an accidental regularizer.

**Consequence:** allocation must be grounded in measured sensitivity →
the kind-probe (7 adapters, each correcting ONE projection kind at r64,
ΔKL measured per kind per byte) runs next; its weights re-rank the
greedy. If kind-level probes work, layer-level probes are the refinement.

## 27B

Pass-1 spectra + two budgets in flight: 870 MB (= uniform-r64 bytes,
tests allocation-vs-uniform at scale) and 2640 MB (= FULL byte gap to
Q3_K_M — the honest maximum-strength diagonal-whitening shot; if this
loses, full-covariance whitening is definitively the next front).

## Kind-sensitivity probe (0.6B, r64 per kind in isolation, 40-chunk KLD)

Base: Q2_K-imat, KLD 0.766 / top-1 58.2%.

| kind | KLD | ΔKLD | MB | ΔKLD per MB |
|---|---|---|---|---|
| attn_k | 0.672 | **0.094** | 7.3 | **0.0128** |
| attn_v | 0.712 | 0.054 | 7.3 | 0.0074 |
| ffn_up | 0.710 | 0.056 | 14.7 | 0.0038 |
| ffn_gate | 0.715 | 0.051 | 14.7 | 0.0035 |
| attn_q | 0.729 | 0.037 | 11.0 | 0.0034 |
| ffn_down | 0.723 | 0.043 | 14.7 | 0.0029 |
| attn_o | 0.749 | 0.017 | 11.0 | 0.0015 |

**Additivity holds:** Σ isolated ΔKLD = 0.352 vs jointly-measured 0.346
(all-kinds r64) — corrections compose near-independently, so greedy
allocation on measured weights is theoretically justified. attn_k is
4–9× everything per byte; the energy greedy had starved nothing it
should have fed EXCEPT down (mid-pack, 0.0029 — real, not zero).
(Review 2026-08-04: single runs, per-pair σd ≈ 0.011 — attn_k's lead is
safe, but the middle ranking v/up/gate/down/q spans 0.019 and is partly
noise-ordered; treat the policy as a heuristic.)

**Column-patch duel (E08 exploit #1, down/o where diagonal whitening is
weakest):** col-ffn_down KLD 0.732 vs SVD 0.723; col-attn_o 0.749 vs
0.749. **SVD holds at 0.6B** — exact loud-column restoration does not
beat optimal directions despite the metric's known bias. Rematch at 27B
(columns are SVD-free ⇒ cheap) queued for next session — the outlier
head strengthens with width, so scale may flip this one.

## v2 architecture (post-mortem of the killed monolith)

Pass-1 spectra now cached to disk (`pass1_cache.py`, resume-safe);
`emit_alloc.py` emits any {policy × budget × factor-dtype} adapter in
seconds. Measured-sensitivity policy: per-kind energy scales calibrated
so each kind's total energy/byte equals its MEASURED ΔKLD/byte. Lesson
recorded: never bind an expensive computation to a policy choice.

## Result — 27B, transferred-prior policy, Q8 factors (2026-08-03 end of night)

(Review 2026-08-04: "measured-alloc" at 27B means TRANSFERRED-PRIOR
alloc — the sensitivity weights are the 0.6B kind-probe values
hard-coded in `emit_alloc.py`, never re-measured at 27B, and E08/E20
both show kind-level geometry shifts with scale. The table row labels
below are kept as emitted.)

| config | adapter MB | PPL20 | mean KLD | top-1 | VRAM MiB | tok/s |
|---|---|---|---|---|---|---|
| Q2_K bare | — | 7.891 | 0.1529 | 83.6% | 12965 | 99.7 |
| + uniform r64 F16 (E04c) | 870 | 7.869 | 0.1460 | 84.4% | 13800 | 66.5 |
| **+ measured-alloc Q8** | **867** | **7.763** | **0.1415** | **84.6%** | 13849 | **81.0** |
| + measured-alloc Q8 (uncapped) | 960* | 7.765 | 0.1420 | 84.7% | — | — |
| Q3_K_M (the bar) | +2640 | 7.243 | 0.0555 | 90.1% | 15311 | 89.2 |

\* the 2640 MB budget could only SPEND 960 MB — every tensor exhausted
its cached spectrum (kmax 128–192), and the extra 90 MB bought nothing.

**Point-estimate wins at equal bytes** (review 2026-08-04: the quality
margins are UNRESOLVED — allocation-vs-uniform KLD is 0.50σ and the
"tripled PPL recovery" Δ0.128-vs-Δ0.022 is 0.26σ unpaired; the tok/s
comparison is two single measurements from different runs, though the
mechanism — Q8 factors read half the bytes — is sound and 22% exceeds
drift): allocation+Q8 improves every point estimate vs uniform-F16 and
serves faster (81.0 vs 66.5 tok/s). The E07 recipe (0.6B-transferred
weights + Q8 + greedy) beats E04c's uniform adapter on point estimates
(not resolved individually) and costs seconds to emit from a cache.

**The paradigm verdict:** marginal value above rank ~130 is ≈ zero (960
≈ 867 MB quality). With every within-paradigm lever pulled — uniform vs
measured allocation, F16 vs Q8, budget to saturation — diagonal-whitened
correction plateaus at KLD ≈ 0.14 on this model, far above Q3_K_M's
0.0555. **The diagonal-whitening well is EMPTY at 27B.** This is the
boundary of the paradigm, established by measurement. What remains
requires information diagonal whitening cannot see: full activation
covariance (blueprint in `prior-art/arxiv-whitening-allocation.md`) or
quantizer/corrector co-design (ProjQ-style noise shaping). That is the
next front, and the first experiment of the next session.
