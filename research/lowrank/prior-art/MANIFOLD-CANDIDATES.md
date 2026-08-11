> ⚠ see review/ corrections 2026-08-04

# MANIFOLD-CANDIDATES — the discrete experiment list (Max directive
# 2026-08-04b, consolidated from the three geometry sweeps)

Sources: `arxiv-manifolds-riemannian.md` (~70 papers),
`arxiv-manifolds-infogeo.md`, `arxiv-manifolds-landscape.md` (~90
papers). Own measurement folded in: **G_S log-det statistic on the 27B
Grams = 0.30–0.72 nats/dim of correlation structure the diagonal
whitener leaves on the table (attn_output highest, 0.57 mean)** — the
two-sided/full-metric lane has measured headroom before any build.

Cross-slice verdict in one line: **the metric is the money, not the
manifold machinery** — every ranked winner upgrades an objective/metric
in a pipeline we already run; pure geometric solvers are single-digit
gains at best.

## The list (ranked by expected KLD per engineering-day)

| # | Candidate | Attacks | Expected | One-day test |
|---|---|---|---|---|
| M1 | **YAQA-style global-Hessian rounding** (2505.22988, Kronecker-sketched) — ✖ RUN (E24, on 0.8B not the listed 0.6B): **anti-helpful** with our single-pass measured T — gives back a third of the input-only win at t=+8..+15 paired; solver verified bit-exact, the ESTIMATOR is at fault (estimator-class vs broken-capture confounded — round-2 review F6). Follow-up = the estimator repair: T-shrinkage / bigger capture / the 0.6B clean-gram rerun (ABLATION-PLAN T1.12). [outcome recorded 2026-08-04 per coherence-audit P1.8 — do NOT rebuild this as an open experiment] | E13's layerwise-target ≠ scored-KLD mismatch (its 27B fade) | ~30% KL cut vs GPTQ-class in print | 0.6B: Kronecker sketch + re-round, KLD vs E13 |
| M2 | **Closed-form Fisher bit allocation** b*=b₀+log₂RMS+½log₂F (2505.12988) — ⚠ FIRST PASS RUN (block 5): with an input-activation-only Fisher proxy the formula assigns ffn_down 1.17 bits (and goes negative at tails) — it starves the measured load-bearing tensor, reproducing our documented input-side-proxy trap at closed-form speed. VALID ONLY with grad-side Fisher → folded into M3's capture. alloc.json saved (do not build from it). | E07 allocation without probe sweeps | conditional on M3 | rerun after grad-Gram capture |
| M3 | **Two-sided (grad-side) whitening** — ✅ BUILT & MEASURED (block 5): llama-gradmatrix tool captures T=Σggᵀ on quantized weights (196/196 Grams, gates passed; found upstream seam: llama.cpp training never backprops the KV cache — llama-finetune trains with zero attention-score grads into wk/wv! — independently found, already public as #21037 Bug 2; comment there, not a new issue). Grad-ONLY diag is a trap (Spearman −0.29), two-sided +0.39 — the metric law's third confirmation. Two-sided fc r64 (factor-balanced against F16 overflow — 9548 infs before the fix): **PPL 27.27 vs one-sided 27.58** at equal bytes, capture 0.76 vs 0.60. Real, ~1%. 27B needs hybrid-layer backward + KV-bypass at scale — next session. | metric refinement | ~1% at 0.6B r64 | done; 27B queued |
| M4 | **Fisher-Lloyd fusion of E13∪E17** (SMML 2604.05241: Voronoi assignment + KL centroids, iterated) — ✖ RUN (E26, on 0.8B not the listed 0.6B): **NULL** — no codes fixed point reached within the pre-registered 8-iteration budget (92/96 tensors in a ~0.1% limit cycle); 2.6% further surrogate gain, ZERO end-to-end effect (paired \|t\|≤1.3). See experiments/26-lloyd-gauge/README.md. [recorded 2026-08-04 per coherence-audit P2.1] | re-rounder and reconstruction tables as one fixed-point algorithm | supersedes both halves; unpublished as a package | 0.6B: iterate reround↔refit-tables to convergence |
| M5 | **Principled gauge-fixing** (HeRo-Q 2601.21626 / FlatQuant lineage: rotation chosen to shrink top Hessian eigval) — ✖ RUN (E26, on 0.8B not the listed 0.6B): on qwen35 only DIAGONAL folds exactly (every rotation site sits behind learnable-gamma RMSNorm); full-strength diagonal equalization is strongly **ANTI-HELPFUL** (t=+28..+60 paired, 0/40 KLD chunks) — format interaction with Q2_K's shared 4-bit sub-scales; the no-imatrix control + alpha sweep that would pin the mechanism are queued (ABLATION-PLAN T1.13). See experiments/26-lloyd-gauge/README.md. [recorded 2026-08-04 per coherence-audit P2.1] | base-quantizer error under Q2_K+fc — the lane we rejected under an ARBITRARY gauge | measured wins at 2-bit in print | 0.6B: optimized foldable rotation, then our full stack on top |
| M6 | **Damped Bregman-proximal alternation** (2507.09428, with proof) | alt3+ convergence; principled version of our 2-round stop | small; theory value high (law 3 formalized) | swap into e11 loop, 3 rounds, stop-on-worse |
| M7 | **Block-homogeneity channel permutation** (RPTQ lineage) | K-quant block-scale error; offline, zero runtime, llama.cpp-native | free bpw-efficiency; unmeasured on K-quants | permute → requant → KLD, 0.6B |
| M8 | **Logit-space m-projection corrector** (convex!) | output-layer correction as exact information projection | cheap new lever; convex solve | 0.6B: fit + serve via output-tensor adapter |
| M9 | **Dither-spread quantizability probe** (local-entropy lineage) | forward-pass-only allocation oracle (no grads) | corroborates/replaces sensitivity probes | 7 kinds × dither runs, correlate with kind-probe |
| M10 | **ScaledGD/weighted-ALS factor fitting** (2005.08898) | ONLY if M3's rank-1 diagnostic fails (elementwise Fisher) | single digits | 20-line GPU ALS warm-started from closed form |
| M11 | **Fisher-merged per-workload adapters** (slice-2 survivor) | E14-cond's off-distribution cliff | softer specialization trade | merge wiki+code adapters, re-run 2×2 |
| M12 | **E11 convergence theorem** (finite-codes argument — ours) | paper theory section | zero compute; claim the NARROW greenfield (black-box-requantizer framing only — alternation is LoftQ-descended, best-iterate tracking is in CALDERA; review F14) | write the proof |

## Killed with cause (do not relitigate)

Raw-sharpness/HAWQ allocation (not gauge-invariant — Dinh 1703.04933);
literal geodesic quantization + full-FIM metrics + NG optimizers inside
closed-form fits (priced out); desingularization/flag/completion stacks
(wrong regime); SAM-QAT (wrong lifecycle); rebasin/curve-finding
(permutation gauge is KLD-neutral here); Wasserstein geometry;
Chentsov-as-tool. Mode-connectivity × quantization and grid-as-manifold
descent: GREENFIELD, claimable in the paper, but capped as non-KLD
levers — cite, don't build.

## Notes

- E01's full-rank null now has a citation wall (weight matrices are not
  near low-dim manifolds in the literature either; structure lives in
  trajectories/function space).
- Super-weights concentrating in ffn_down (2411.07191) independently
  confirms our sensitivity geography.
- Both sweep agents hit the session WebSearch budget and completed via
  the arXiv export API — all IDs title-verified there.
