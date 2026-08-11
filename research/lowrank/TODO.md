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

## 🛰️ RECON 2026-08-11 — read prior-art/recon-2026-08-11.md BEFORE
## citing any novelty claim; it supersedes all "first"/"unpublished"
## wording in this tree

Nothing measured is falsified; three framings changed. Frozen-grid
re-rounding is published twice (GSQ 2604.18556 — improves SHIPPED
Unsloth GGUFs in-format, code public; ReQuant 2608.07019) →
gguf-refine repositions as the one-shot whitened instantiation and a
GSQ head-to-head becomes MANDATORY (T1.17). Two-sided whitening
published April (OBD-LLM 2604.00821 + KronQ) → M3 = independent
confirmation, first in GGUF residual correction with interpolation
controls. E16 twice-narrowed (TwinQuant both-branches-4bit; SVDQuant
NVFP4+low-rank but diffusion-only, plain pre-quant SVD) → "first LLM
instance, whitened residual on a frozen NVFP4 base, stock adapter
serving". Verified STILL OURS: E17 posterior dequant, capture-vs-width
scaling, E27's interpolation-control methodology, register-neutral
fusion law, MTP/linear-attention calibration.
**GATE: no new GPU speed number until the llama.cpp pin is checked
against #26177 (--fit/NextN miscount, fixed b10152) — the −13.4% MTP
figure may be part #25489, part #26177.**

## NOW — recon-adjusted queue (2026-08-11)

PAPER-MATH LANE (pre-GPU, ranked by expected KLD-per-day; L# = recon
lever ids):
- [x] L2 GlowQ shared-A — DONE 2026-08-11 as E28/E28b/E28c: capture GO
  (+12%/+7% at byte parity) but KLD-NULL at parity (surrogate-vs-
  outcome #6); parity NOT flipped (CONTROL t=+2.35 stands); bytes
  variant (r128 shared, −17% adapter bytes at predicted KLD-tie) =
  E28c, see JOURNAL. Experiment: experiments/28-glowq-shared-a/.
- [x] L3 SRR k-split — part 1 DONE 2026-08-11 (E29 proxy): whitened-W
  76.6% capturable @k128 at 27B (E01's raw 0.6B null does NOT
  transfer to the whitened metric at scale — finding); absmax proxy
  weak (−4.5%). Part 2 (full SRR-as-adapter build, byte-matched vs
  MIXEDfc/CONTROL) pre-registered + building same day.
  Experiment: experiments/29-srr-split/.
- [ ] L1 BaKron: derive the K-quant-constrained (superblock-scale)
  two-sided recursion; estimate two-sided-vs-diag refinement gain from
  cached activation Grams + the 196 grad Grams. Doubles as the T1.12
  estimator-repair path (test OBD-LLM's 10% dampening recipe on cached
  statistics). (1–2 days)
- [ ] L6 ReQuant/GSQ math: prove our whitened objective generalizes
  ReQuant's dL = −dq·g + dq²·H_jj scoring; bound the MSE-vs-whitened
  gap from cached stats. (paper-days)
- [ ] L10 cached-spectra micro-checks (hours each): SVDQuant order
  duel (plain-SVD-of-W vs whitened-NVFP4-residual capture @r32);
  ARCQuant channel-vs-rank duel; LoRaQ INT8-adapter-at-2r vs FP16-at-r
  byte model; ARCHead lm-head capture at 0.6B/0.8B; DuQuant++
  16-aligned rotation compose-vs-cannibalize; RR code-stream entropy;
  floor-fraction restatement of decode numbers; vocab-pruning byte
  bound (AdaptFM rank-2 lever); KronQ µ-incoherence of gradient Grams.
- [ ] L7 AlphaQ per-expert tail exponents on cached 35B-A3B spectra +
  EAQuant token-starvation quantification from cached routing stats.
- [ ] L8 Muon-provenance axis folded into T1.10 (optional
  Moonlight-16B-A3B capture point as the out-of-family test).

UPSTREAM WINDOW (this week — the audience is assembled):
- [ ] Post confirmation comments on #23575 (ACTIVE 23-comment thread;
  #26903 shows maintainers hitting the pain) + #23476 + #21037, armed
  with AdaptFM ammo (rank 2 kept MTP FP16; rank 6's recurrent-state
  rollback hazard). Supersedes the "rewrite draft #2" wording below.
- [ ] L5: port llama.cpp PR #23258's dual-context MTP capture into the
  gradmatrix/imatrix harness → real Grams for MTP/NextN tensors.
  (2–3 days, CPU + short validation)

GPU QUEUE (re-gated, order matters):
- [ ] #26177 pin check FIRST (gate above), then re-measure MTP tok/s
- [ ] T1.17 GSQ head-to-head (the one new mandatory benchmark)
- [ ] REVIEW REPAIRS QUEUE below (remaining round-1 + round-2 items)
- [ ] Re-rounder codec targets (directive 2026-08-04e below — now with
  BaKron metric upgrade + vLLM 4-over-6/ScaleSweep scale search +
  MXFP4 column)
- [ ] 27B two-sided (M3 trailer: hybrid-layer backward + KV bypass) —
  now framed as OBD-LLM confirmation-at-scale
- [ ] ABLATION-PLAN Tier-1 matrix (T1.1–T1.17 — the submission gate)

PAPER REPAIRS (wording; do before freezing any section):
- [ ] Reframe the three claims per recon (gguf-refine, M3 two-sided,
  E16 twice-narrowed); cite TwinQuant Fig. 1 beside capture-vs-width;
  add DAM non-orthogonality check + Bid-Up monotone bounds to the
  theory section.
- [ ] PDF pulls: OBD-LLM (full — residual application is
  lane-reported), DAM 2607.20434, SERQ 2603.08185, 2512.17073 (their
  fitting metric), AdaptFM straggler repos (September re-check),
  openPangu-2.0-Pro quant chapter, Noah's Ark author re-watch
  pre-submission.

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
  → URGENT per recon 2026-08-11: #23575 thread is active (23
  comments) and #26903 shows maintainers hitting the MTP-calibration
  pain themselves — see UPSTREAM WINDOW in the NOW queue above.

## 📌 MAX DIRECTIVE (2026-08-04e) — re-rounder codec targets
NVFP4 codec for e13 (nearest-of-8 E2M1 lookup, frozen FP8 scales;
output byte-compatible NVFP4 — no export conflict; favorable prior per
E16's covariance-structured-residual finding, tempered by the 4.5-bpw
tier law) + Q3_K/Q4_K codecs. Goal: `gguf-refine` covers the full
format family, K-quants through NVFP4.
RECON ADDENDA (2026-08-11): metric upgrade path = BaKron's two-sided
Kronecker recursion at GPTQ cost (L1); NVFP4 scale-side search space =
vLLM PR #45187's 4-over-6 candidates + ScaleSweep init, replayed
offline in our whitened metric, numerics cross-checked against
FlashInfer #3932 (L4); add an MXFP4 codec column (E8M0 32-elem blocks
— Kimi K3 and DeepSeek-V4 experts ship it natively; the QAT→GGUF grid
mismatch and V4-expert use cases are gguf-refine's two new named
targets, L9).
