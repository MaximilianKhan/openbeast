# Adversarial review, round 2 — the post-review experiments (2026-08-04/05)

Lens: the experiments run AFTER round 1 (review/adversarial-{stats,claims,
code}.md), which imposed: paired per-chunk statistics (R1), same-size
comparators + interpolation controls (claims-F1/F6), single-step provenance
(stats-F10 / code-B4), pre-registered grids (stats-F7 → PROTOCOL rule 8),
cache fingerprints (code-B8 → PROTOCOL rule 2), and held-out corpora
(stats-F4/R3). These new experiments CLAIM protocol-v2 compliance. Scope:
E23 (MoE), E24 (YAQA-lite), E26 (Lloyd + gauge), E27 (BF16 re-derivation),
the E22 held-out arc (heldout-results.txt + paper/DEPLOYABLE-WINS.md), and
MASTER-TABLE.md's new rows — READMEs, results files, and the code
(e23_extract_moe.py, e13b_yaqa.py, e26_lloyd.py, e26_equalize.py,
e27_extract.py, paired_stats.py). Date: 2026-08-05.

Severity: **KILLS** (claim must be withdrawn/reworded as stated),
**WEAKENS** (direction may survive; evidence or wording does not),
**COSMETIC**.

## Verdict up front

The measurement layer improved dramatically. Every paired statistic I
recomputed independently from the raw logs — E27 (3 pairs), E24 (2 pairs),
E23 (2 pairs) — reproduces the published numbers exactly; the cumulative-
differencing recovery in paired_stats.py is mathematically exact and its
print-rounding noise is bounded and negligible (checked, §V). The E26
reproduction gates (335/335 byte-identical rebuild of the E22 base;
byte-identical RR rebuild) are the best provenance practice in the
campaign. The nulls are honest and the flagship "victory" was voluntarily
re-tested and demoted. This is what a repaired lab looks like.

The residual damage is concentrated in three places: (1) the held-out
"RESOLUTION" — the single most deployment-relevant new claim — is
confounded, methodologically unrecorded, and quietly proves a calibration-
sampling variance that undermines other margins; (2) mechanism sentences
("double-counting", "K-FAC anti-helpful", "fixed point does not exist",
"provenance tax measured") that outrun their evidence by exactly the
round-1 pattern; (3) protocol-v2 compliance that is claimed but violated
in specific, checkable ways (un-fingerprinted cache in the flagship tool,
20-chunk KLD, self-attested pre-registration, VRAM still absent).

---

## Part I — the held-out arc (E22 heldout-results.txt + DEPLOYABLE-WINS)

### F1. The "✅ RESOLUTION: coverage, not intrinsic" is confounded, its
### method is unrecorded, and it never re-tested the thing that failed —
### **KILLS (the resolution as recorded); the ⚠️ caveat itself stands**

The arc: wikitext-calibrated re-rounding is WORSE than bare on the code
corpus (3.470 vs 3.083 — a genuine, large, honestly-reported alarm); a
mixed 2:1 wiki:code recalibration then "beats bare on BOTH corpora"
(wiki 33.09→29.84, code 3.083→2.785), and DEPLOYABLE-WINS concludes
"coverage, not intrinsic … calibrate broadly, improve broadly." Four
independent defects:

**(a) The wiki-only-vs-mixed comparison changed three things at once.**
`data/gram08b` (wiki-only) has count = 20480 tokens (40 chunks);
`data/gram08b-mixed` has count = 24576 (48 chunks). So mixed-vs-wiki
confounds corpus composition with calibration budget (+20%) AND chunk
sampling. The arc's own accidental control proves the confound is
material: the first "mixed" run (the bug — 48 sequential chunks, 100%
wiki) produced wiki-RR PPL **27.54 vs the published wiki-only 28.09**
(heldout-results.txt line 6). Changing ONLY the calibration
chunking/budget moved the free lever by **0.55 PPL (~2%)** — the first
and only measurement of review-F12a's "unbounded axis" (calibration-
sampling variance), and it sits unremarked in a results file. It is
larger than several published 0.8B margins (e.g. the alternation-vs-fc
gap, 22.79 vs 23.03). The published tradeoff quantification ("−10% vs
−15% wiki") is computed against the 40-chunk baseline; the equal-budget
comparison (mixed2 29.84 vs mixed1 27.54) gives a different, larger
on-distribution cost (~8%→~2.3 PPL). The qualitative both-corpora-win
survives (margins ~5–8σ unpaired, so protocol rule 4's paired
requirement is not triggered); the numbers attached to it do not.

**(b) The resolution's method exists nowhere.** No script, no capture
log, no README section documents how gram08b-mixed was built: which
corpus files, what interleave, what chunking. `grams.json` carries no
provenance metadata (PROTOCOL rule 2: cache dirs carry fingerprints —
violated; rule 11: method + raw outputs retained — violated). In
particular it CANNOT be verified from the record that the code slice in
the calibration came from code-train and not from code-test — i.e. that
the "held-out" corpus stayed held out of the run that resolved the
held-out finding. For the artifact DEPLOYABLE-WINS calls "the paper's
generalization table seed," this is disqualifying until documented.

**(c) "Calibrate broadly, improve broadly" is an extrapolation claim
supported only by interpolation.** After mixing, BOTH eval corpora are
inside calibration coverage. The original alarm was about UNSEEN data.
Whether mixed-calibrated re-rounding still anti-generalizes on a third,
uncalibrated corpus (the actual deployment question, and PROTOCOL rule
7's requirement for generality claims) was never tested. What was shown:
re-rounding improves what it is calibrated on. What is claimed: broad
calibration yields broad improvement. The gap between those is exactly
the gap that produced the alarm.

**(d) The deployable variant is half-measured.** RR-mixed2 has PPL only —
no KLD, no top-1, on either corpus. The fully-measured free-lever row
(28.09 / 0.323 / 72.7, t=30–45) is the narrow-calibration variant the
campaign itself declared "ships with a held-out gate or not at all."
See F11 for where that lands in MASTER-TABLE.

Repair (cheap): write the mixed-calibration recipe down (or rebuild it
scripted, ~minutes at 0.8B); rerun RR-mixed2's KLD (minutes); add ONE
third corpus PPL (C4 slice — already priced in round-1 R3, <1h); restate
the tradeoff against the equal-budget control; log the 27.54-vs-28.09
sampling-variance bound as a named finding (it is a useful result!).

---

## Part II — E27 (the BF16 re-derivation)

The experiment itself is the review's R4 repair executed well: verified
truth-logits gate, single-step bases, the missing interpolation control
finally built, paired stats throughout, and the legacy "victory" honestly
flipped to parity. Three defects in its claims layer, one in its tool.

### F2. "The F10 provenance tax is real, measured" — the attribution is
### confounded by a simultaneous imatrix change, and the table's own
### Q4_K_M row is the counterexample — **WEAKENS (verdict 4's clause)**

Claim: "Single-step vs requant-of-requant, measured: every rebuilt rung
scores 0.002–0.003 KLD better than its legacy Q6-requant sibling."
The rebuild changed TWO things per rung: provenance (one step from BF16
vs requant-of-Q6) AND the imatrix (new 48-chunk BF16-capture Grams vs
the legacy Q6-derived capture). Round 1 (claims-B4/stats-F10) records
that legacy **Q4_K_M was already BF16-single-step** — yet it improved by
the same 0.0025 (0.0211-class → 0.0186) under the rebuild. For that row
the entire delta is imatrix/calibration, not provenance. So the uniform
0.002–0.003 improvement is consistent with "the new imatrix is worth
~0.0025 everywhere" with a provenance tax of ~zero — the opposite
attribution. The sign-flip of the flagship margin is likewise
provenance+imatrix+fresh-adapter, jointly. Repair (one run): requant one
rung from the Q6 artifact WITH the new imatrix; the three-way split then
isolates the tax the claim names. Until then: "rebuild tax (provenance +
calibration), 0.002–0.003."

### F3. The control verdict is stated past its recorded statistics, and
### "the ladder's own interpolation line" is one designed point —
### **WEAKENS (wording; the direction survives robustness checks)**

The new headline negative — CONTROL ≥ MIXED+fc at equal bytes — rests on
paired KLD t = +1.9, n = 20: below the campaign's own 2σ bar, on half
the chunks PROTOCOL rule 3 demands (waived in a caveat), over a per-chunk
KLD distribution the log itself shows is violently heavy-tailed (max
3.82 vs median 0.029). I recomputed and stress-tested it: one chunk
(#16, d = +0.150) inflates the sem; dropping it gives t ≈ +3.0, and the
sign test on the recorded 16/20 resolves at p ≈ 0.012. So the direction
is very likely real — but the RECORD as computed does not license
"matches-or-beats"/"is ≥" under the standard that killed the 1.0σ
"victory" in round 1; symmetry is the whole point of having a standard.
Also: (i) "the ladder's own interpolation LINE" is one point, designed
using the campaign's own measured kind-sensitivities (build_bases.sh:
"attn_k (the campaign's most sensitive kind)") — no vendor ships it; it
is a geometry-aware mixed base, i.e. a product of the same research
program minus the adapter. That actually SHARPENS verdict 3 ("base
mixing is the winning move") and should be framed as such, not as the
ladder's own property. (ii) Cherry-pick audit came back clean: exactly
one control was built and measured (one gguf, one quantize log, byte
count predicted a priori in the script); and since a deliberately strong
control only strengthens an existence-proof negative, the design bias is
legitimate. Repair: 40-chunk rerun of the triangle (~20 min) or print
the sign/robust test next to the t; rename the row "designed same-byte
mixed-quant control."

### F4. e27_extract.py reintroduces the un-fingerprinted cache — the
### review's B8 defect, in the tool of the compliance-flagship experiment
### — **KILLS-if-touched (latent); compliance claim false on rule 2**

PROTOCOL rule 2 (verbatim): cache dirs carry a provenance fingerprint;
consumers refuse mismatches. e13b_yaqa.py implements it (`--codes-dir`
meta.json, refuses loudly); e26_lloyd.py implements it (`--state-dir`,
includes algo/damp/iters). e27_extract.py — written AFTER both, for the
experiment whose header cites PROTOCOL v2 — caches per-tensor factors in
`--cache-dir` keyed by tensor name only: no meta.json, no ref/base/gram
identity, silent reuse on resume against a different base. This is the
exact "single most likely future silent-wrong" from code-B8. No
published number is impugned (fc-cache was created fresh for this run),
but the flagship rerun's tool violates the rule its experiment claims,
and the next resumed 27B extraction is one wrong flag from plausible
garbage. Repair: copy e13b's 12-line fingerprint block.

### F5. Residual E27 compliance gaps — **COSMETIC-to-WEAKENS**
- KLD on 20 chunks vs rule 3's ≥40 — acknowledged, but the heavy tail
  above is the demonstration of why the rule exists.
- VRAM absent: PROTOCOL §0 requires compression "file and VRAM both" and
  round-1 claims-F3 showed the adapter path inflates VRAM ~0.7 GB vs
  bare quants — at equal VRAM the control/rungs beat the flagship by
  MORE. Direction-safe for the negative verdicts, but §0's own letter
  ("not a result" without it) applies to every C column shipped.
- "matches the 0.6B/0.8B finding (controls beat crowns)": no
  interpolation control was ever BUILT at 0.6B/0.8B (MASTER-TABLE lists
  it as pending). The small-scale "finding" is an inference from Q4-tier
  domination, not a measured control. Cite it as such.

---

## Part III — E24 (YAQA-lite)

### F6. "Single-pass empirical K-FAC factors are anti-helpful" claims an
### estimator-CLASS failure from an estimator-COVERAGE failure —
### **WEAKENS (named result 2)**

The measured fact is solid (recomputed: kron-vs-input dNLL +0.0483 ±
0.0031, t = +15.5; dKLD t = +8.2, 2/40 chunks): THIS T made things
worse. But this T was known-defective before the run: the qwen35 capture
carries the delta-net gradient cut, which biases the output-gradients of
the 18 attn_gate/ssm_out layers directly and — because the bias
propagates backward — contaminates T for essentially all 96 re-rounded
tensors; plus 16 384 tokens for up-to-4096-dim T factors. The README
lists these as hypotheses (a)–(d), "none checked tonight," and names the
discriminating experiment (0.6B: exact Grams, no cut, classic arch) as a
follow-up — then still prints "YAQA-lite with single-pass empirical
K-FAC factors is anti-helpful" as a named result, and "our measured T is
a worse guide than no row metric at all" as if T-quality were the
resolved variable. It is not resolvable from this run: estimator class
vs broken capture are perfectly confounded. MASTER-TABLE's scoping
("anti-helpful with current T estimates") is the correct sentence;
promote it into the README's named result. Repair: the 0.6B rerun the
README itself specifies (cheap — grams exist), or reword.

Credit where due: the Kronecker LDLQ derivation is correct (I verified
the nested-sweep algebra against the kron(U_S,U_T) recursion; test_kron
covers bit-exactness), the diagonal-T no-op lemma is right and worth
keeping, and the surrogate-improves/outcome-worsens observation is a
genuinely good datapoint regardless of which hypothesis wins.

---

## Part IV — E26 (Lloyd + gauge)

### F7. The M5 mechanism claim ("imatrix double-counting") is contradicted
### by the experiment's own construction, and the discriminating ablations
### were declined by citing F7-the-rule — **WEAKENS (mechanism only)**

The negative result is bulletproof (t = +28..+60 paired, 0-2/40 chunks;
the pow2 gauge gate — equalized BF16 vs BF16: KLD 0.000000 — is
excellent methodology). The MECHANISM sentence is not. "With the imatrix
already weighting the fit, folding sqrt(diag) importance into the
columns double-counts importance … the same correction applied twice"
describes a configuration that was never run: e26_equalize.py exactly
transforms the imatrix (in_sum2' = in_sum2/s², pow2-exact), so in the
equalized run the fit weights are FLATTENED and importance is counted
once — in the columns. In exact arithmetic the weighted objective is
gauge-invariant; the only live mechanism in this experiment is the
format interaction (up to 32× dynamic range injected into 16-element
sub-blocks that Q2_K's shared 4-bit sub-scales cannot span, and the loss
of an informative weighting for the affine fit). The diagnostic offered
as "measured, not argued" (eq/bare imatrix-weighted error 1.06–1.68,
untouched control exactly 1.000) shows the fit got worse — it does not
discriminate double-counting from range-blowup, and cannot, since the
former was not present. The two experiments that WOULD discriminate
(equalization without imatrix, where "double-count" predicts the harm
vanishes; an alpha sweep, where AWQ-lineage predicts an interior
optimum ≈0.5 and double-count predicts optimum 0) were declined as
"post-hoc sweep (F7)". That misreads round 1: F7 forbids post-hoc
SELECTION for headline wins; it does not forbid diagnostic ablations for
a mechanism assertion — used this way, protocol compliance becomes an
evidence shield. Note also the pre-registered point (alpha = 1, full
strength) is precisely where the equalization literature expects harm,
so the general "equalization and imatrix-weighted fitting are not
complementary" exceeds one known-extreme exponent. Repair: run the
no-imatrix equalization control (one quantize + one eval, <1 h) or cut
the mechanism to "moving importance from fit weights into column scales
is strongly harmful for Q2_K's shared sub-scale format."

### F8. M4 "the hypothesized fixed point does not exist" — asserted from
### an 8-iteration cap — **COSMETIC-to-WEAKENS**

92/96 tensors hit the pre-registered cap of 8 iterations inside a ~0.1%
limit cycle. Non-convergence in ≤8 steps of a non-monotone alternation
(greedy codes-step + fp16-rounded scales-step) is evidence of a limit
cycle at the floor — not of non-existence (best-iterate tracking, more
iterations, or higher-precision scale storage were not tried; the README
says so itself under follow-ups). The operative NULL is solid and
well-powered (paired |t| ≤ 1.3 on n=580/40 while the same harness
resolves RR-vs-bare at t = 27–47), and the fourth surrogate≠outcome
datapoint is the real finding. Say "no fixed point within the
pre-registered budget; limit cycle" and the claim is airtight.

---

## Part V — E23 (MoE) and the shared statistics machinery

### F9. E23: verified clean; two mechanism-wording nits — **COSMETIC**

The decisive negative (PE loses the promotion control at 6.9σ paired,
0/20 chunks; recomputed exactly) is the best-executed experiment in the
batch: falsifiers pre-stated and triggered as specified, servability
measured not assumed (stock exit 0 vs measured segfault 139 + one-branch
patch), byte accounting exact, the counts.mean() MoE-imatrix trap
(code-B10) genuinely bypassed, and the extractor math correct (I
verified the projection-form optimality and the shared-basis argmin;
deq3's ne-order reversal is right). Nits: (i) "the 256 experts' residual
column spaces are essentially mutually orthogonal" is dimensionally
impossible in a 512-dim output space — what the numbers show (shared
capture ≈ random floor) is that the POOLED residual spectrum is nearly
isotropic; (ii) the low per-expert captures (0.36/0.19) are measured
under the POOLED whitener, a disclosed approximation of each expert's
conditional input covariance (~256 samples/expert made per-expert Grams
infeasible) — some of the "unstructured noise" reading may be whitener
mismatch, mostly for down stacks. Neither touches the verdict. Protocol
deviations (vendor Q4_K_M as reference — no BF16 exists; 20-chunk KLD;
16-chunk Grams; serving tax unmeasured) are all disclosed and all
direction-safe at 2.7–8.2σ margins.

### F10. paired_stats.py: attacked and cleared, with two residual risks —
### **COSMETIC**

The cumulative-differencing recovery (per-chunk_k = k·cum_k −
(k−1)·cum_{k−1}) is exact for equal-weight chunks; I re-derived and
recomputed seven published comparisons across three experiments from the
raw logs — every mean, sem, t, and chunk-win count matches to the printed
digit. Print-rounding amplification (O(k) on the differenced stream) is
real but bounded: the 4-decimal PPL stream at k=580 contributes
per-chunk σ ≈ 7e-4 against an actual per-chunk σ ≈ 0.05 — negligible,
and it can only inflate sems (conservative for wins, mildly anti-
conservative for declared nulls; at these magnitudes, immaterial).
Residual risks: (i) the harness prints chunk-win counts but never tests
them — on n=20 heavy-tailed KLD the t-test is fragile in both directions
(F3's outlier masks a real effect here; elsewhere it could manufacture
one); add the sign test it already has the data for. (ii) Equal-weight
telescoping silently assumes equal evaluated tokens per chunk — true for
this fixed-n_ctx pipeline, wrong the day someone feeds variable-length
chunking; assert it or document it.

---

## Part VI — cross-cutting

### F11. MASTER-TABLE integrity: the free-lever row advertises the
### variant the campaign itself declared undeployable — **WEAKENS**

The 0.8B row "Q2_K + E13 re-round (free) | 28.09† | 0.323† | 72.7†"
carries no marker that the campaign's own R7 pass measured THIS artifact
WORSE than bare off-distribution (heldout code 3.470 vs 3.083), and the
† footnote ("RR is the zero-byte serving lever") repeats the deployment
framing without the held-out gate. The mitigated variant (mixed2) is not
tabled and has no KLD/top-1 anywhere (F1d). The paper's Table-1-in-
waiting currently shows the narrow-calibration numbers with the
deployment story and neither the caveat nor the deployable variant's
cost (~29.84 wiki). One row-note fixes it. Same class: the table cites
"E24 §6" (no such section) and PROTOCOL §0's "file and VRAM both" is
unmet in every compression column (flagged pending, but §0 as written
makes them provisional).

### F12. "Pre-registered" is self-attested, artifact-free, and drifted —
### **WEAKENS (every use of the word)**

Pre-registration appears throughout E23/E24/E26 ("pre-registered cap of
8", "single pre-registered kron config", "pre-registered config, no
sweep"). The only prior document is prior-art/MANIFOLD-CANDIDATES.md,
which pre-specifies missions at one-line granularity — and specifies
**0.6B** for both M4 and M5, which were run on 0.8B with no recorded
deviation note. Every binding detail (cap=8, t-damp=1e-3, pow2
restriction, alpha=1, r=32, the E27 control recipe) first appears in the
post-run README that declares it pre-registered. Nothing here suggests
actual post-hoc selection (the E27 control audit in F3 came back clean;
the single-config claims are consistent with the artifact record), but
unverifiable pre-registration is precisely the round-1 F7 disease with
better vocabulary. Repair: timestamped registration entries (the
append-only JOURNAL already exists for this) BEFORE the run; one line
each.

### F13. DEPLOYABLE-WINS residuals — **COSMETIC-to-WEAKENS**
- "No equivalent tool class exists (tracker-swept 2026-08-04)" — a
  one-phrase novelty attestation, no sweep artifact (queries/hits), from
  the lab whose round-1 sweeps missed #21037/#23476/#23575 on the same
  tracker (claims-F10/F11/F22). Retain the sweep record or drop the
  sentence.
- Win 1's "~40% of the quality gap to the NEXT tier" is actually ~50% on
  the recorded numbers (0.8B KLD: (0.490−0.323)/(0.490−0.167) = 51.6%;
  PPL 51%; 0.6B PPL 45%) — an UNDERclaim; fine, but it suggests the
  fraction was never recomputed after the numbers firmed up.
- The Win-1 headline block still carries the unqualified t=30–45
  free-win framing with the held-out caveat appended below as a
  chronological patch rather than integrated into the claim ("free ON
  the calibration distribution" belongs in the first paragraph).

---

## What survives untouched (attacks run and failed)

- All recomputed paired statistics (E27 ×3, E24 ×2, E23 ×2): exact match.
- E26's reproduction gates: byte-identity claims verified as recorded.
- E27 single-step provenance + truth-logits verification gate: real.
- E23's negative and E26-M4/M5's end-to-end negatives at their stated
  sigmas; E24's kron-hurts measurement (as scoped to "this T").
- The Kronecker LDLQ algebra, the E23 3D extractor optimality, the M5
  pow2 gauge-exactness construction (KLD 0.000000 end-to-end), the
  diagonal-T no-op lemma.
- E27 control cherry-pick suspicion: one control designed a priori by
  byte-matching, measured once; design bias is legitimate for an
  existence-proof negative.
- The flagship demotion (victory → parity) — voluntarily executed and
  correctly worded in E27 verdict 1.

## Repair list, priced

1. Document/rebuild the mixed-calibration recipe + RR-mixed2 KLD +
   one third-corpus PPL (F1) — <1.5 h, unblocks the deployment claim.
2. Fingerprint e27's --cache-dir (F4) — 12 lines.
3. Requant one rung from Q6 with the new imatrix (F2) — one run.
4. 40-chunk E27 triangle rerun or sign-test reporting (F3) — ~30 min.
5. 0.6B kron rerun (F6) and no-imatrix equalization control (F7) —
   ~1 h each; both discriminating experiments are already specified in
   the READMEs that skipped them.
6. Reword: "fixed point within budget" (F8); pooled-metric caveat +
   "isotropic, not orthogonal" (F9); MASTER-TABLE RR row-note (F11).
7. Timestamped pre-registration henceforth (F12) — zero compute.
8. Retain the tracker-sweep artifact or cut the novelty line (F13).
