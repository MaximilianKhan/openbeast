# Adversarial review — statistical and methodological rigor

> RESOLUTIONS (2026-08-04 evening — note added per coherence-audit
> P3.9; historical text below untouched): this review predates
> E24/E26/E27, which resolved the F1/F5/F10-class items — several
> with the sign flips it predicted (the 1.0σ 27B "victory" flipped
> sign under E27's clean provenance; paired stats ran everywhere).
> See experiments/{24,26,27}-*/README.md and round 2:
> adversarial-round2-{paper,experiments}.md + coherence-audit.md.

Reviewer lens: assume every claim is wrong until the evidence defends it.
Scope: `RESULTS_ROLLUP.md`, `JOURNAL.md`, every `experiments/*/README.md`
and `REPORT*.md`, plus the raw `results*.txt` files (which, unlike the
READMEs, carry the printed error bars). Date: 2026-08-04.

Verdict up front: the campaign's *large* effects (bandwidth inversion,
whitening-metric jumps, the IQ1_S recovery, the 0.6B crown's KLD margin,
kernel +44%/+79%) survive scrutiny. But a substantial fraction of the
HEADLINE claims — including the single most-marketed result, "first 27B
ladder victory" — are 0.3–1.5σ events on single runs, several published
numbers are known-stale, the two flagship "laws" rest on 2 points and a
method-inconsistent curve respectively, and the entire quality ledger is
measured on the calibration distribution with zero task evals and zero
held-out corpora, despite the campaign itself measuring (E14-cond) that
its adapters are strongly distribution-specific. Details, each with
severity and the cheapest repair.

Severity scale: **KILLS** = claim must be withdrawn or reworded as
stated; **WEAKENS** = direction may survive but the evidence does not
resolve it / a mandatory caveat is missing; **COSMETIC**.

A note on the statistics used below: σd = sqrt(σ₁² + σ₂²) treats the two
measurements as independent. Because rival configs are scored on the
SAME eval tokens against the SAME reference logits, their errors are
positively correlated and a *paired* per-chunk/per-token test would have
materially more power. That is not a defense of the claims — it is an
indictment of the pipeline: llama-perplexity prints per-chunk rows and a
paired ln(PPL-ratio) column, and the campaign cropped them, keeping only
marginal means ± stderr. Every "unresolved" finding below is unresolved
*on the evidence as recorded*. The cheapest global repair (R1, end) is
to keep per-chunk records and compute paired differences — near-zero
GPU cost — not to rerun everything.

---

## F1. "First 27B ladder victory" (MIXED+fc beats Q3_K_S) is a 1.0σ event — **KILLS (as stated)**

Claim (RESULTS_ROLLUP.md, E10 README, JOURNAL "the campaign's first 27B
victory over a real ladder rung"): MIXED+fc (12.50 GB) beats Q3_K_S
(12.37 GB) "on KLD and PPL at ~equal bytes, top-1 tied."

The recorded numbers (`experiments/10-full-covariance/results.txt`,
`experiments/04b-27b/results.txt`):

| | KLD | PPL20 | top-1 |
|---|---|---|---|
| MIXED+fc | 0.081186 ± 0.003944 | 7.3131 ± 0.26081 | 88.039 ± 0.454 |
| Q3_K_S | 0.087248 ± 0.004605 | 7.4861 ± 0.27025 | 88.039 ± 0.454 |

- KLD: Δ = 0.00606, σd = 0.00606 → **exactly 1.00σ**.
- PPL: Δ = 0.173, σd = 0.375 → **0.46σ**.
- top-1: identical to three decimals (a tie, correctly reported).

Compounding defects: (a) MIXED-bare already sits at 0.0913 ± 0.0055 —
only 0.57σ from Q3_K_S — so the *base mix alone* statistically ties the
rung, and the correction's incremental contribution (0.0913 → 0.0812) is
itself only 1.5σ; (b) MIXED is 1% MORE bytes than Q3_K_S, so even the
sign of a byte-normalized comparison is in play; (c) MIXED was selected
from ≥2 mixed designs (MIXED, MIXED2) and ≥10 corrected 27B configs
post hoc (see F7), so the ~1σ margin is at or below winner's-curse
expectation; (d) the BF16 truth-rescore (F10) never scored Q3_K_S, so
the victory ordering was not verified under the true reference.

Repair: paired per-chunk KLD test (R1) + 100-chunk rerun of just this
pair (~1 h) + Q3_K_S vs BF16 rescore. Until then the honest wording is
"statistically indistinguishable from Q3_K_S at equal bytes" — which is
still a fine result (parity with the ladder from a corrected 2-bit
base), but it is parity, not victory.

## F2. The stale 86.3 tok/s cell — the rollup prints a number the campaign itself retracted — **KILLS (the cell)**

`RESULTS_ROLLUP.md` line 53 and E10's README print MIXED+fc serving as
"15.0GB / 86.3 tok/s" (bolded, flagship row). JOURNAL Block 5:
"same-session A/B proved Q8_0 factors bypass the new paths (82.3 ≈ 82.4
— no-op, no regression; **earlier 86.3 was boost drift**)." The raw log
(`10-full-covariance/results.txt` line 22) confirms: "82.3 tok/s
(pre-fusion: 86.3)". A number the campaign's own later measurement
attributed to GPU-boost drift is still the headline serving figure.
Repair: replace with a locked-clock (nvidia-smi -lgc) median-of-N
measurement; propagate to the E10 README.

## F3. Speed claims vs documented boost drift — which are unsafe — **WEAKENS / one KILLS**

Documented drift: Phase 3 finding 6 — "boost drift between sessions was
up to 9%"; Phase 2B — same-session spread "79.3–81.0 across hours";
Phase 3 session H — fused range 86.5–88.0 on the *same config*.

- **Phase 3 "+2.6% (>20 sigma at ±0.1 run noise)" — the sigma claim is
  indefensible.** The campaign's own logs show ~1.5–2 tok/s within-
  session wander on identical configs; against a realistic σ of 1–2%,
  +2.2 tok/s is a ~1–1.5σ single-pair result. The *direction* has
  corroborating probes (rank-0 ceiling 89.4, multiple fused readings
  86.5–88.0 vs unfused 85.2–85.4), so it is plausibly real — but ">20
  sigma" is false precision and should be struck. WEAKENS.
- **E07 "+ serves FASTER (81.0 vs 66.5)"** — two single measurements
  from different runs with no same-session pairing recorded; the
  mechanism (Q8 reads half the bytes) is sound and 22% > drift, so the
  direction is probably safe, the magnitude is not. WEAKENS.
- **Phase 1 "+42% under contention"** — a single run under uncontrolled
  contention ("shared with the live stack"); unreproducible as stated.
  WEAKENS.
- Safe: bandwidth inversion 99.7 vs 61.0 (63%, far above drift and
  mechanistically forced), 0.6B kernel +44%/+62%/+79% (all ≥5× drift,
  same-session pairs), Phase 2B's honest "+0.4% ≈ noise".
- The rollup's law #2 cites "99.7 vs 61 tok/s" — safe — but the tok/s
  column of the 27B table mixes sessions with no drift annotation
  except one footnote. KILLS only the 86.3 cell (F2); the column needs
  a drift caveat.

Repair: lock clocks, interleave A/B runs N=10, report median ± IQR.
One evening for the whole table.

## F4. Calibration and eval share a distribution; the campaign's own E14 proves this is a live hazard; zero task evals — **KILLS (generality of every ladder-win claim)**

Every quality number in the campaign: imatrix and Grams calibrated on
wikitext-2 **train**; PPL on wikitext-2 **test**; KLD reference logits
on the same wikitext-2 test chunks. Different splits, same corpus, same
distribution. The corrected configs carry 10–925 MB of parameters
*derived from calibration statistics* (whitened-SVD factors, re-rounded
codes, alternated bases), against which the ladder rungs spend only an
imatrix reweighting — a massive capacity asymmetry for fitting the
calibration distribution.

The campaign then MEASURED the danger and did not connect it to its own
headlines: E14-cond shows swapping the Gram corpus moves on-corpus PPL
by 10–27% (code-cond on wiki: 34.90 vs wiki-cond 27.58 — a 27% swing;
`17-task-conditioned/results.txt`). Every contested ladder-win margin
(≤3% PPL, ≤12% KLD) is *smaller than the measured distribution-
sensitivity of the method*. A held-out-corpus eval could plausibly erase
or invert: the 0.6B crown's win over Q3_K_M, the 0.8B alternation win,
MIXED+fc vs Q3_K_S, and all recovery fractions (in magnitude).

The "KLD silent zone" caveat is cited twice in the campaign's own record
(JOURNAL block 2: "near-baseline KLD has a 'silent zone' — final claims
will need task evals"; E16: "Task evals remain the final arbiter") and
then absent from RESULTS_ROLLUP's verdicts and all five "laws". The
entire 27B endgame (KLD 0.055–0.12) sits *inside* that zone. Zero task
evals were run in ~20 hours of campaigning.

Would survive an OOD eval: E01/E03 spectra (data-free), bandwidth
inversion (data-free), kernel correctness/speed (data-free), the
qualitative whitening-metric hierarchy (diag < fullcov, resolved at
both scales), qualitative cliff-gradient monotonicity. At risk: every
"beats/ties rung X" claim, all recovery-fraction magnitudes, E13's free
wins, the E17 LUT effect size.

Repair (cheapest decisive): PPL-only pass on two held-out corpora (a C4
slice + the existing code-test set) for {crown configs + their rival
rungs} at both scales — no logits needed, <1 h GPU; then one task eval
(llama.cpp's built-in --hellaswag or --multiple-choice) for the 27B
flagship pair, a few hours.

## F5. The recovery-fraction curve 7.5% → 18% → 41% mixes methods across its points — **WEAKENS (the steepness), KILLS (the printed curve as a single-variable law)**

Rollup: "Recovery-fraction curve (F7): 7.5% @Q2_K → 18% @IQ2_XS → 41%
@IQ1_S." Points 1–2 are **diagonal**-whitened alloc-Q8 adapters
(0.1529→0.1415; 0.2642→0.2175); point 3 is a **full-covariance** mixed-
rank adapter (0.891→0.527). Recomputed with the consistent full-cov
recipe from the campaign's own tables: Q2_K 0.1529→0.1217 = **20%**
(fcv2-r128q8), IQ2_XS 0.2642→0.1905 = **28%**, IQ1_S = **41%**. The
consistent curve is 20/28/41 — monotone, real, but ~3× shallower at the
first point than the printed 7.5/18/41. The dramatic "steep cliff
gradient" is substantially an artifact of switching whitening metrics
mid-curve. (JOURNAL even records the fc-IQ2 28% figure, then the rollup
quotes the diagonal 18% next to the fc 41%.) Adapter budgets also vary
(866/866/799 MB) and rank mixes differ. Repair: relabel F7 with the
fc-consistent points, or plot both curves labeled by method; zero new
compute (all numbers already exist in `results.txt` files).

## F6. The capture law "c ≈ 5.9·(r/d)" is a one-parameter fit to TWO points, and the campaign's own fc data refutes its universality — **KILLS (as a "law"), WEAKENS (as a diag-r64 observation)**

Rollup law #1: "Capture ≈ 5.9·(r/d) … first measured here. Governs
every result above." Evidence: exactly two (model, rank) pairs — 0.6B
diag r64/d1024 → 0.37; 27B diag r64/d5120 → 0.07. The constant is fit
on point 1 and "verified" on point 2 with rounded arithmetic
(0.07×5 = 0.35 vs 0.37 — and 5120/1024 = 5, so even the check is one
noisy ratio). Defects: (a) n=2 for a one-parameter law through the
origin = zero residual degrees of freedom; (b) "d" is not a single
number at 27B (554 tensors, input dims 5120–25600; the law never states
which d); (c) the campaign's OWN full-covariance captures violate the
constant badly: 0.6B fc r64 → 0.60 ⇒ c = 9.6; 27B mixed fc r128 → 0.42
⇒ c ≈ 16.8; NVFP4 fc r128 → 0.52 ⇒ c ≈ 20.8. So c is not a constant of
the geometry — it is a function of the whitening metric and the base,
measured at one rank on two models. (d) E22's 0.8B was available as a
third point and never used for the law. "Governs every result above" is
rhetoric. Repair: fit capture vs r/d across the E21 rank sweep (8
points, already measured), the 0.8B, and per-tensor 27B cache spectra
(already on disk) — a real regression with a CI, one afternoon of
python, no GPU.

## F7. Post-hoc selection / multiple comparisons on the contested margins — **WEAKENS (systemic)**

Config generation was sequential and adaptive: at 0.6B, ≥15 corrected
configs were tried against the same Q3_K_M rung (r16/32/64/128 diag,
fc-r64/128, fcalt1, fcalt2 r64/96/128, RR, RR+fc, alt1+RR+fc, ProjQ,
two-sided, Q8 variants); at 27B, ≥10 against Q3_K_S/IQ3_XXS/IQ3_XS.
The winners (fcalt2-r128, MIXED+fc) were then headlined against the
rung. With per-config KLD σ ≈ 0.004–0.006, selecting the best of ~10
tries inflates the expected spurious margin to ~1.5–2σ ≈ 0.008–0.01 —
*larger* than the observed MIXED+fc margin (0.006) and comparable to
the r96 margins. Specific casualties:

- **fcalt2-r96 "non-dominated at +4% bytes"** (E10: "PPL win, KLD dead
  heat"): r96 was an explicit post-hoc interpolation run after r64
  (byte-fair, LOST by 0.52 PPL) and r128 (won at +10% bytes). The "PPL
  win" is 25.87 ± ~0.22 vs 26.06 ± 0.233 → **0.58σ**. The KLD "dead
  heat" (0.2386 vs 0.2378) is honest. As a selected 0.58σ point it
  cannot carry "first non-dominated configuration" weight. WEAKENS.
- **MIXED vs MIXED2**: two designs, better one headlined (F1).
- **E11/E13 stopping rules fired on sub-noise deltas**: alternation
  round 2 "decelerating" = 30.58→30.50 (Δ0.08, ~0.2σ); the stop-on-
  worse "certificate" (M12) is a theory about exact argmins applied to
  measurements with σ ≈ 0.27. COSMETIC-to-WEAKENS.
- Survives selection correction: the 0.6B crown fcalt2-r128 vs Q3_K_M —
  KLD 0.2086 vs 0.2378, Δ ≈ 0.029, σd ≈ 0.006 → **~5σ**, and PPL 2.4σ;
  even Bonferroni over 15 tries leaves it standing (on-distribution;
  F4 still applies). IQ3_XS's crown (3.3σ over MIXED+fc) also stands.

Repair: pre-register the config grid for the next campaign; report the
number of configs tried next to every "best-of" table; apply a max-of-N
null or simple Bonferroni to contested margins.

## F8. The 27B lever ledger: "every lever real but scale-shrunk" — half the levers are individually unresolved at 27B — **WEAKENS**

Unpaired σ audit of the 27B Q2_K-base progression (rollup table rows,
`04b-27b/results.txt`, `13-rerounder/results.txt`):

| lever (27B) | Δ KLD | σd | sigmas | resolved? |
|---|---|---|---|---|
| + r64 diag (E04c) | 0.0069 | 0.0090 | 0.76 | no (self-declared null — fine) |
| + measured-alloc vs uniform (E07) | 0.0045 | 0.0089 | 0.50 | **no** — yet headlined "strictly better", "tripled the PPL recovery" (PPL Δ = 0.106, 0.26σ) |
| + E13 re-round | 0.0068 | 0.0087 | 0.78 | **no** (top-1 +1.0pt = 1.4σ); rollup prints it as a table row win |
| + col-patch vs alloc | 0.0032 | 0.0088 | 0.36 | tie, correctly reported |
| diag → fullcov (r64) | 0.0178 | 0.0084 | 2.1 | yes |
| fc → fc+alternation | 0.0075 | 0.0072 | 1.04 | **no** — "27B alternation composes (−6%/round)" is a 1σ claim; law #3's "twice measured" is once-measured (0.6B: resolved at ~6σ KLD) |
| NVFP4 + fc | 0.0180 | 0.0060 | 3.0 | yes |
| IQ2_XS + alloc | 0.0467 | 0.0111 | 4.2 | yes |
| IQ1_S + fc | 0.364 | 0.022 | 16.5 | yes |

So at 27B the resolved levers are the metric (fullcov) and correction-
on-weak-bases; allocation-vs-uniform, re-rounding, and alternation are
individually inside their error bars. Law #5 ("every lever shrinks with
scale") is directionally supported, but the quantitative decay figures
(−6%/round, −4.4% KLD free, etc.) are noise-level numbers presented at
face precision. Repair: R1 paired tests would likely resolve several of
these cheaply; otherwise widen to 100 chunks (σ shrinks ~√5) for the
lever ladder only.

## F9. 20-chunk 27B PPL resolves almost nothing; cross-scale statements mix bases, chunk counts, and references — **WEAKENS**

All contested 27B PPL20 values (7.24–7.51) carry ±0.25–0.32; every
adjacent comparison in the endgame table is <1σ on PPL. Verdict
sentences nonetheless cite PPL wins ("beats Q3_K_S on KLD and PPL").
Meanwhile the 0.6B ledger uses full-corpus PPL (±0.20–0.27) and
40-chunk KLD vs Q8_0, while 27B uses 20-chunk PPL and 20-chunk KLD vs
Q6_K. Cross-scale claims (law #5's decay fractions; the "0.37 → 0.07
capture collapse" narrative; recovery-fraction comparisons between the
0.6B 85%-gap-recovered figure and 27B fractions) therefore compare
numbers with different references, different eval sizes, and different
precisions, without ever propagating either error bar into the derived
ratio. None of the derived quantities (recovery %, decay %, capture
ratios) is printed with a CI anywhere in the campaign. Repair: 100-chunk
27B evals for the handful of load-bearing rows; propagate errors into
every derived fraction (pure arithmetic).

## F10. Reference-shift arithmetic: "all configs shift by ≈ that constant; every ordering holds" — asserted, not measured, and the rescore that exists contradicts the constancy — **WEAKENS**

Block 4 claims the Q6-reference contamination is "negligible, measured"
(Q6 vs BF16: KLD 0.0032 / 97.6%) and JOURNAL: "all configs shift by ≈
that constant; every ordering and conclusion of the campaign HOLDS
under truth-scoring." Defects: (a) KL is not additive — there is no
theorem that KL(BF16‖Q) ≈ KL(BF16‖Q6) + KL(Q6‖Q); (b) the actual
rescore (`16-nvfp4-composition/bf16-rescore.txt`) covers only FOUR
configs, and the shifts are NOT constant: Q3_K_M +0.0015, IQ3_XS
+0.0033, MIXED+fc +0.0038 — a 2.5× spread, of the same order as the F1
victory margin; (c) Q3_K_S — the rung the flagship claims to beat — was
never rescored, so the one ordering that mattered was not verified;
(d) the Block-4 table mixes provenance: every 27B base is a REQUANT of
the Q6_K artifact, except Q4_K_M which was quantized from BF16 — so
"Q4_K_M wins the byte class" compares a single-quantized rival against
double-quantized composites (a confound in the rival's favor, unstated
in the rollup; E22 tacitly concedes the issue by advertising itself as
"no reference asterisk anywhere"). Repair: rescore the contested rows
(Q3_K_S, Q3_K_M, MIXED-bare, alt) vs BF16 — logits exist, ~minutes each
— and rebuild the 27B ladder rungs from BF16 once for the paper.

## F11. The 0.8B alternation win (0.147 vs 0.167) — probably real, but recorded with NO error bars, single run, conditional selection — **WEAKENS**

`22-qwen35-08b/results.txt` records KLD/top-1 with no ± at all (the
only results file in the campaign without them). Estimated from same-
magnitude 40-chunk KLDs elsewhere (σ ≈ 0.004–0.005 each): ΔKLD 0.0201,
σd ≈ 0.006–0.007 → **~3σ** — likely outside joint bars on KLD; top-1
+1.92pt at σd ≈ 0.8 → ~2.4σ; but PPL 22.79 vs 23.28 at σd ≈ 0.30 →
**1.6σ**, so "beats it on ALL THREE metrics" overreaches by one metric.
The alternation round was also run *after* observing round-0 tie the
rung (0.168 vs 0.167) — conditional continuation, mild selection. And
F4 applies in full (wikitext-calibrated, wikitext-scored). Repair:
re-log with error bars (rerun is ~minutes at 0.8B), paired test, plus
one held-out corpus.

## F12. Single-run structure: what actually needs repetition and what doesn't — **COSMETIC (quality), WEAKENS (speed, calibration)**

No experiment was repeated. For the quality pipeline this is less fatal
than it sounds: greedy PPL/KLD on fixed corpora with deterministic
kernels is reproducible bit-for-bit, so "N-seed reruns" of the same
artifact would return the same number. The REAL unexplored variance
axes are: (a) **calibration sampling** — which 100/64/40 chunks of
wikitext-train feed the imatrix/Grams; nobody measured adapter-quality
variance across calibration subsamples, and every downstream number
inherits that unknown; (b) **eval sampling** — addressed only by the
printed stderr, never by a second corpus (F4); (c) **speed** — genuinely
noisy (F3) and measured once or in one same-session pair. Repair:
3-fold calibration-resample for one flagship config (0.6B: <1 h total)
to bound axis (a); it has never been bounded.

## F13. Kind-sensitivity weights feeding E07 allocation are noise-ordered in the middle — **COSMETIC-to-WEAKENS**

The E07 kind probe's per-kind ΔKLDs (0.6B, single runs, σd ≈ 0.011
unpaired per delta): attn_k (0.094) is safely separated, but the middle
ranking — attn_v 0.054 / ffn_up 0.056 / ffn_gate 0.051 / ffn_down 0.043
/ attn_q 0.037 — spans 0.019 with per-pair σd ≈ 0.011, and attn_o's
0.017 is ~1.5σ from zero. The "measured sensitivity" allocation policy
is built on point estimates whose ordering is partly noise — consistent
with its downstream advantage over uniform being unresolved (F8, 0.5σ).
The additivity check (0.352 vs 0.346) is fine. Repair: R1 pairing, or
accept the policy as heuristic and stop calling the weights "measured"
without CIs.

## F14. E21 "the geometry is SMOOTH … no landing spots" claims a resolution the data doesn't have — **COSMETIC**

Eight single-run points with full-corpus σ ≈ 0.22–0.27; adjacent
marginal deltas from r96 onward are −0.27…−0.71, i.e. 1–2× the unpaired
joint σ. Concavity/monotonicity of the fitted trend is credible;
"no landing spots" (absence of small non-monotonic structure) is an
assertion below the measurement's resolution. Paired stats would likely
fix this for free (shared corpus). Repair: R1.

## F15. Assorted resolved-claim confirmations (for balance)

Verified as statistically solid on the recorded evidence (on-
distribution; F4 caveat still applies): Q2_K-imat cliff vs bare (43.33
vs 258); E13 0.6B re-round (43.33→35.54, ~20σ on PPL; KLD 0.766→0.556
~19σ); fullcov vs diag at 0.6B r64 (27.58 vs 31.29, ~10σ) and its
beats-double-rank claim (vs 28.46, 2.6σ PPL / 2.5σ KLD); the 0.6B crown
fcalt2-r128 vs Q3_K_M (~5σ KLD); ProjQ vs naive RR+fc (2.7σ);
IQ1_S/IQ2_XS/NVFP4 correction gains (3–16σ); IQ3_XS's crown (3.3σ);
Q8-factor parity (correctly framed as parity); E14-cond's +10.3%
(σ ≈ 0.028 per cell → ~8σ); the shared-basis and water-filling nulls
(large margins). The campaign's nulls are, ironically, its most
statistically secure results.

---

## Repair plan, priced (cheapest first)

- **R1 — paired statistics, retroactive where possible, standard
  henceforth.** Keep per-chunk PPL/KLD rows; compute paired Δ with a
  per-chunk bootstrap. Resolves or kills F1, F8's three 1σ levers, F13,
  F14 at near-zero GPU cost (reruns of contested pairs: minutes at
  0.6B, ~1 h total at 27B).
- **R2 — strike/replace known-bad numbers now:** the 86.3 cell (F2),
  ">20 sigma" (F3), "all three metrics" at 0.8B (F11), the F7 curve's
  mixed-method points (F5). Editing, zero compute.
- **R3 — held-out corpora:** PPL-only on C4-slice + code-test for crown
  configs and rival rungs, both scales. <1 h GPU. The single cheapest
  test that could falsify the campaign's central claims (F4).
- **R4 — BF16 truth for the contested 27B rows** (Q3_K_S, MIXED-bare,
  alt, Q3_K_M) + rebuild rungs from BF16 for the paper (F10). ~1 evening.
- **R5 — 100-chunk 27B eval** for the endgame table only (σ ÷ √5); one
  evening; makes the KLD column actually discriminating.
- **R6 — one task eval** (--hellaswag / multiple-choice) for flagship
  vs rung at 27B (F4's silent-zone clause). Hours.
- **R7 — capture-law regression** across E21 sweep + 0.8B + 27B cache
  spectra with CI (F6). Python only.
- **R8 — speed protocol:** locked clocks, interleaved A/B, N=10,
  median ± IQR (F3). One evening.
- **R9 — calibration-resample bound** (F12a): 3 subsamples, 0.6B, <1 h.
- **R10 — process:** pre-registered config grids; report N-configs-tried
  beside every best-of table; CIs on all derived fractions (F7, F9).

## What the paper can honestly claim today

Parity with the K-quant ladder at 27B from a corrected 2-bit base (not
victory); a resolved local win at 0.6B/0.8B **on the calibration
distribution**, pending held-out confirmation; a monotone (20/28/41%)
recovery gradient toward the cliff under full covariance; a resolved
metric hierarchy (diag < fullcov < two-sided, at 0.6B); the bandwidth
inversion; the kernel trilogy's 0.6B gains and a plausible-but-
unresolved 27B gain; and a capture-vs-r/d *hypothesis* with two diagonal
points. That is still a real paper. It is not the paper the rollup's
bold rows currently advertise.
