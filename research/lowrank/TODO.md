# beast-rank TODO — ranked

## DONE 2026-08-03
- [x] **E01 — SVD spectrum census**: H1 confirmed, W is energy-full-rank,
  C@95% ≈ 1.0 everywhere. Plain truncated SVD dead. (E02 demoted to
  optional paper baseline.)
- [x] **E03 — residual rank census** (Q2_K): H2 confirmed, residual
  near-isotropic; unweighted correction loses to Q4_K at equal bytes.
- [x] llama.cpp internals recon: v0 zero-change route VIABLE via LoRA path
  (`prior-art/llamacpp-internals.md`); q/k permutation = silent hazard.

## DONE — the 08-03 critical path (moved from "NOW" 2026-08-04 per
## coherence-audit P2.5: every item had long completed)
- [x] E05a imatrix + E05b whitened census (08-03 night, GO verdict)
- [x] E04 v0 end-to-end incl. round-trip check (same night)
- [x] WikiText-2 corpus pinned (data/wikitext-2-raw)
- [x] Agent recon absorbed (prior-art/, gguf-py IQ2 dequant verified)
- [x] E05 activation-aware whitening → became the fc lane (E10)
- [x] E06 fused kernel → the Phase 1/2/2B/3 trilogy (experiments/14)
- [x] Scale ladder → 0.6B / 0.8B / 27B / 35B MoE (9B GLM died —
  conversion rot)

## NOW — the actual queue (2026-08-04)
- [ ] REVIEW REPAIRS QUEUE below (round-1 + round-2 items)
- [ ] Re-rounder codec targets (directive 2026-08-04e below)
- [ ] 27B two-sided (M3 trailer: hybrid-layer backward + KV bypass)
- [ ] ABLATION-PLAN Tier-1 matrix (T1.1–T1.16 — the submission gate)

## 📌 MAX DIRECTIVE (2026-08-04) — E17 candidate: probability/entropy-
## driven weight RECONSTRUCTION at dequant time

Max's framing, verbatim intent: for weights compressed by our system,
experiment with probability/entropy-driven reconstruction — recovering
higher-quality weights at the GPU/CUDA/kernel-fusion level — grounded in
cutting-edge mathematical research (arXiv-referenced). Even a slight
across-the-board improvement would be a huge win.

Most promising concrete reading (to be validated by a dedicated arXiv
sweep BEFORE building): **posterior-expected dequantization** — treat a
quant code as a noisy OBSERVATION of the true weight and reconstruct
E[w | code, block context, prior] instead of the deterministic grid
point. Grounding lanes to sweep: Lloyd-Max conditional-mean centroids
and their block-context generalizations; rate-distortion/Bayesian
quantization theory; dithered quantization; codec-style context
modeling (DPCM/neighbor-conditioned reconstruction) applied to weight
blocks; maximum-entropy priors from our captured activation Grams.
Why it fits us: (a) it is a pure DEQUANT-TIME change — implementable as
a small correction table or per-block affine inside the fused kernel we
are already building (Phase 1 shipped, Phase 2 in flight), zero extra
weight bytes; (b) it composes with every lever measured so far; (c) our
full-covariance instrumentation supplies exactly the priors such a
reconstructor needs. Also sweep: entropy CODING of codes (ZipNN-style —
a bytes lever, distinct from the quality lever; keep them separate).
Success bar per the campaign standard: byte-fair KLD win vs the same
base without the reconstructor, at both scales.

## 📌 MAX DIRECTIVE (2026-08-04b) — differentiable manifolds & geometries

Explore differentiable-manifold and differential-geometry mathematics as
a foundation for this work: sweep the literature broadly (arXiv +
classical roots), produce an applicability report with a DISCRETE
candidate list, then experiment from it. Three contact points with our
measured results: (1) Riemannian optimization on fixed-rank/Stiefel/
Grassmann manifolds — our factors B,A live on these and have only ever
been fitted with flat closed forms; (2) information geometry — the
whitening arc (diag → full covariance) IS a Fisher-Rao metric
refinement; natural-gradient views may sharpen allocation/alternation;
(3) loss-landscape geometry — curvature/flatness vs quantization
robustness; geodesics between reference and quantized models. Reports:
prior-art/arxiv-manifolds-*.md; consolidated list:
prior-art/MANIFOLD-CANDIDATES.md.

## 📌 MAX DIRECTIVE (2026-08-04c) — incremental rank sweeps

If rank stays a feature of the final method, run fine-grained
incremental rank sweeps per configuration to find what "lands well" —
Max is (rightly) uncertain of the system's actual geometry. What we
know: coarse points exist (16/32/64/96/128/192/256/512 scattered across
E04-E15) and the r/d law fits their envelope, but (a) knees/crossovers
appear where we did look closely (r96 byte-parity crossover at 0.6B;
the pre-fix rank-64 kernel cliff), (b) Q8 packing quantizes rank to
%32, (c) kernel occupancy makes serving cost rank-structured, so
non-monotonic sweet spots are plausible. Design: pick the frozen best
recipe per scale, sweep r in steps of 32 (Q8) across [32, 512] on 0.6B
and [32, 256] on 27B, measure KLD + tok/s per point, plot
quality-per-byte AND quality-per-tok/s response curves; flag any point
beating its neighbors beyond error bars. Cheap on 0.6B (cache-based
emit makes each point seconds + one eval); 27B from the spectra caches.
Also feeds paper figure F5 (tok/s vs rank) with real curvature.

## PARKED / IDEAS
- KV-cache low-rank (Palu) — different axis, compounds with weight
  compression; out of scope for pass 1.
- MoE expert-matrix sharing via joint factorization — 35B-A3B would be the
  testbed; pass 2 material.
- Rank-adaptive serving (load more rank when VRAM allows) — the "10×" end
  of the range likely lives here + extreme-quant hybrid.

## 🔴 REVIEW REPAIRS QUEUE (2026-08-04 adversarial review — do before new claims)
- [x] Paired per-chunk statistics for MIXED+fc vs Q3_K_S — DONE in E27
  (2026-08-04 evening, BF16-pure: KLD tie t=0.6, PPL edge t=−2.5;
  legacy 1.0σ margin flipped sign). 0.8B alt1 vs Q3_K_M still [ ].
- [ ] Mixed-quant interpolation CONTROLS at 0.6B/0.8B crown byte points
  — the 27B dense control now EXISTS (E27: matches-or-beats the
  flagship at equal bytes); small-scale controls still open.
- [x] Held-out corpus PPL, FIRST PASS (free lever @0.8B): R7 arc DONE
  2026-08-04 — RR anti-generalizes narrow (code 3.470 vs bare 3.083);
  mixed-calibration mitigation indicated but confounded (see
  DEPLOYABLE-WINS + E22 HELDOUT-METHOD.md). Still [ ]: crown configs +
  27B held-out; third-corpus extrapolation (T1.11); RR-mixed2
  KLD/top-1 (T1.14). [split 2026-08-04 per coherence-audit P2.6]
- [ ] Task eval — still ZERO anywhere in the campaign (T1.8).
- [ ] KLD silent-zone caveat into every near-baseline comparison.
- [x] BF16-rescore Q3_K_S — SUPERSEDED by E27's full BF16-1step rebuild
  of the 27B ladder (Q3_K_S 0.0845 ± 0.0044 vs BF16 truth). Still [ ]:
  VRAM measures for Q3_K_S/IQ3_XS/E15; strike remaining stale speed
  cells anywhere else they hide.
- [x] Cache provenance fingerprint, codes-dir half: e13b `--codes-dir`
  meta.json fails loudly (E24 B8 repair); E26 state dirs fingerprinted.
  Still [ ]: pass1_cache/spectra caches — AND `e27_extract.py`
  REGRESSED the rule (--cache-dir keyed by tensor name only, round-2
  experiments F4): add e13b's 12-line fingerprint block before any
  resumed 27B extraction. [split 2026-08-04 per coherence-audit P2.7]
- [ ] emit_alloc: 27B kind-probe (replace 0.6B transferred priors);
  guard --columns with --gram-dir (wrong-column selection latent bug).
- [ ] gradmatrix: MUL_MAT_ID branch for MoE grams (prereq for 35B rung);
  per-expert count semantics in load_imatrix.
- [ ] Upstream: post confirmation-comments on #23476/#23575 and #21037
  (Max's account) instead of new issues; rewrite draft #2 mechanism.

## 📌 MAX DIRECTIVE (2026-08-04e) — re-rounder codec targets
NVFP4 codec for e13 (nearest-of-8 E2M1 lookup, frozen FP8 scales;
output byte-compatible NVFP4 — no export conflict; favorable prior per
E16's covariance-structured-residual finding, tempered by the 4.5-bpw
tier law) + Q3_K/Q4_K codecs. Goal: `gguf-refine` covers the full
format family, K-quants through NVFP4.
