# beast-rank TODO — ranked

## 📄 WRITTEN UP 2026-10-09 — paper/final/paper.pdf + ../FINDINGS.md

The campaign is written up with what was measured by 2026-09-14 plus the
E32 controls that landed 09-11..09-14 and had never been journaled (FineWeb
KLD, PPL@2048, the IQ3 capability pair). Nothing below this block was run
for the write-up. Still open, and stated as open in the paper's §7
(§6 before the 2026-10-09 revision renumbered the sections):
- [ ] T1.1 calibration-mix grid · T1.6 small-scale interpolation controls ·
  T1.7 held-out pass over the ladder comparisons · T1.9 recovery curve,
  recipe fixed · T1.13 equalization ablations · T1.15 sampling bound
- [ ] E32: IQ2 capability pair (started 09-17, not completed); a capability
  run of OUR arm; single-slot churn floor for the coding suite
- [ ] third model width for E33; single-step 27B re-round pair
- [ ] re-rounder codecs: Q3_K / Q4_K / I-quants / NVFP4 (→ `gguf-refine`)
- [ ] before an arXiv submission: a venue decision and Max's read. The
  reference list is now full entries from arXiv metadata (all 50 ids resolve
  and match the text, `paper/final/refs.json`, 2026-10-09); left there: confirm
  the three withheld venues (LoftQ, LQ-LoRA, Punica — `refs/review.json`)

### Experiments the 2026-10-09 referee review asks for (none run; GPU needed)

CPU reanalysis of the existing logs is done (`experiments/35-final-reanalysis/`);
these are the items the logs cannot supply. Each is named in the paper's §7.2.
- [ ] **Re-rounder on the ladder, paired (referee 1, 6).** Re-rounded Q2_K vs
  IQ2_M / IQ3_XXS at 27B under single-step BF16 provenance, same chunks and
  reference (needs the single-step 27B re-round pair, also open above). At
  0.8B the paired answer already exists from logs: IQ3_XXS at 412 MB beats
  re-rounded Q2_K at 436 MB by 0.0657 KLD, t = +10.8 (E35 A3). Add IQ2_M at
  0.8B to bracket the byte point from below.
- [ ] **Capability replication (referee 4).** Three more single-slot runs
  per IQ3 arm (GSQ-RCO IQ3_S, UD-IQ3_S) plus a run of OUR corrected arm, so
  the +12 can be read against a same-file run-to-run spread; confirm whether
  the Q5 band runs were single-slot.
- [ ] **KLD at a 2048-token context (referee 4)** for the four E32 artifacts,
  to separate context length from metric. Paired PPL@2048 is done from logs
  (t = −3.33, −4.15, n = 145; E35 A5).
- [ ] **MoE rescoring (referee 9): NOT warranted in the form raised.** E35 A4
  read the reference's tensor types: `ffn_down_exps` is Q5_K ×37 / Q6_K ×3 in
  UD-Q4_K_M, not Q4_K, so the control is not near-lossless by construction.
  What stays open is a BF16-referenced MoE table at n ≥ 40, if a BF16 of the
  35B-A3B is ever obtained.
- [ ] **Baselines for the re-rounder (referee 13):** free-grid GPTQ at equal
  bits; a learned-rounding method; (stretch) a vector/lattice 2-bit quantizer.
- [ ] **A non-Qwen model (referee 13)**, and the third width E33 needs.
- [ ] **Held-out / replicated 27B ladder pair (referee 5).** The n = 100
  margin over Q3_K_S sits entirely in chunks 41–100 (E35 A1.3); repeat the
  corrected-vs-Q3_K_S-vs-control triple on a second corpus (this is T1.7).

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

## 🛰️ RECON 2026-09-08 — read prior-art/recon-2026-09-08.md; it
## re-ranks this file's queue (section E there is authoritative)

Headlines: #26177 gate CLOSED (pin was never contaminated — GPU lane
unblocked); NEW gate = #28068 GDN-norm fix (b10829): rebase kernels
branch before the next measurement campaign, never mix pre/post-b10829
rows; SchurQuant narrows gguf-refine to "first IN-FORMAT superblock
scale+code refinement, whitened, one-shot"; AWSRC forces the
equivalence-class scoping sentence + adds the seeded-basis capture duel;
Muon plausibly IN-FAMILY (Qwen 2608.30320) → E30 spectrum-flatness
3.8-vs-3.6 is the new cheapest experiment; E16 artifacts now
downloadable (Unsloth NVFP4-GGUF incl. Qwen3.8-27B) and the window is
visibly shrinking; T1.17 runs against GSQ's RELEASED checkpoints (their
loop needs H100s — measured positioning fact); E27 got independent
support (2609.01587); E17/capture-scaling/E27-controls/fusion-law/MTP-
calibration all re-verified still-ours.

## NOW — recon-adjusted queue (2026-08-11) [SUPERSEDED by recon-2026-09-08 §E]

PAPER-MATH LANE (pre-GPU, ranked by expected KLD-per-day; L# = recon
lever ids):
- [x] L2 GlowQ shared-A — DONE 2026-08-11 as E28/E28b/E28c: capture GO
  (+12%/+7% at byte parity) but KLD-NULL at parity (surrogate-vs-
  outcome #6); parity NOT flipped (CONTROL t=+2.35 stands); bytes
  variant (r128 shared, −17% adapter bytes at predicted KLD-tie) =
  E28c, see JOURNAL. Experiment: experiments/28-glowq-shared-a/.
- [x] L3 SRR k-split — DONE 2026-08-11 (E29, both parts): whitened-W
  76.6% capturable @k128 at 27B (E01's raw 0.6B null does NOT transfer
  to the whitened metric at scale — finding); full SRR-as-adapter
  build TIES the flagship (t=+0.99) and loses to CONTROL (t=+5.15) —
  order doesn't matter. SESSION SYNTHESIS: equal-byte EQUIVALENCE
  CLASS of low-rank mechanisms at 27B (correction ≈ carve ≈ shared
  basis; only TYPE allocation escapes) — new claim, ours, measured
  three ways pre-registered. Experiment: experiments/29-srr-split/.
- [ ] L1 BaKron: derive the K-quant-constrained (superblock-scale)
  two-sided recursion; estimate two-sided-vs-diag refinement gain from
  cached activation Grams + the 196 grad Grams. Doubles as the T1.12
  estimator-repair path (test OBD-LLM's 10% dampening recipe on cached
  statistics). (1–2 days)
- [x] L6 DONE 2026-09-09 (scope widened to ReQuant/GSQ/SchurQuant +
  OBD-LLM scoping): paper/theory-L6-family-subsumption.md + draft
  integration (§2.2/§2.3/intro/§4.3b). Remaining tail: [EYEBALL] PDF
  verification of agent-read facts before freeze; MSE-vs-whitened
  numeric gap bound from cached stats still open (fold into T1.12).
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
- [x] POSTED 2026-09-08 (Max-approved; thread closed since recon — on-record confirmation): #23575 (was ACTIVE 23-comment thread;
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
- [x] Task eval — DONE 2026-08-11 (T1.8): HellaSwag full-val trio;
  silent zone MEASURED (flagship-vs-S paired tie at ΔKLD≈0.005;
  Q3_K_M visible z=+3.5 at ΔKLD≈0.027, KLD ordering corroborated).
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
