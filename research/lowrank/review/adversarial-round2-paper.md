# Adversarial review, round 2 — referee simulation on the paper draft

Reviewer lens: hostile-but-fair venue referee + citation checker. Scope:
`paper/draft/00-06.md` (7 files) against `MASTER-TABLE.md`,
`RESULTS_ROLLUP.md` (corrections header), `PROTOCOL.md`,
`experiments/*/README.md` + `REPORT*.md` (E01/03/07/10/13/15/20/22/23/
24/25/26/27), `paper/{OUTLINE,ABLATION-PLAN,DEPLOYABLE-WINS,
references}.md`, and round-1 `review/adversarial-{claims,stats,code}.md`.
Date: 2026-08-04. Severity scale as round 1: **KILLS** / **WEAKENS** /
**COSMETIC**.

**Verdict up front: MAJOR REVISION** (reject-as-submitted at a top-tier
venue; salvageable at a measurement/systems venue after the fixes
below). The results sections are unusually honest and §4.3 is properly
E27-fresh — but the abstract and introduction still sell the *pre-E27*
story their own §4.3 has retracted, the free-lever headline is
contradicted by a measured held-out result that appears nowhere in the
draft, E26's two nulls (and the four-fold surrogate-vs-outcome theme
they complete) are missing entirely, one abstract statistic is
misattributed, and the paper lacks the scholarly apparatus (ablation
disclosure, reproducibility statement, three load-bearing citations,
consistent figure plan, anonymization) that a referee checks first.

---

## Part 1 — Number audit (internal consistency)

### 1.1 Spot-check ledger

47 numbers traced draft → cited source. **VERIFIED (sample of 40):**

- Abstract/§1.1 bandwidth inversion: 22.4→10.9 GB file, 61→99.7 tok/s;
  §1.1's 23.6→13.0 GB VRAM (ROLLUP 27B header + row 1). ✓
- Abstract/§1.3/§3.1 free lever @0.8B: −15% PPL, −34% KLD, +6.3 pt,
  t = 30–45; paired rows −0.1637 ± 0.0036 (t=−44.9, 557/580),
  −0.1668 ± 0.0055 (t=−30.3, 40/40), +6.26 ± 0.45 (t=+14.1)
  (E24 README, tables 1–2). ✓
- §3.1 0.6B: 43.33/0.766/58.2 → 35.54/0.556/64.0; ~20σ/~19σ unpaired
  (ROLLUP; stats §F15). ✓  27B: 0.1529→0.1461, top-1 +1.0 pt,
  0.78σ/1.4σ, PPL 7.891→7.952 (ROLLUP; stats §F8/F24). ✓
- §3.2 Kronecker: −0.1154 ± 0.0042 (t=−27.7); +0.0483 ± 0.0031
  (t=+15.5); +0.0475 ± 0.0058 (t=+8.2); −0.69 ± 0.42 unresolved;
  ~35% surrogate cut, bit-exact vs brute force (E24). ✓
- §3.3: t=+17.6, 133/580; 26.80/25.94/25.27 triad; capture 0.78
  (E24; E13 README line 58). ✓
- §4.1: 50–80% of rank for 95% energy, attn_q 1.09×, stable rank
  50–200 (E01); r90 ≈ 62%, 26–44% @5.26 bpw (E03). ✓
- §4.2: 0.37 @d1024 / 0.07 @d5120; c = 9.6/16.8/20.8 (stats §F6). ✓
- §4.3 27B (all E27 README + results-paired): 0.0863 ± 0.0054 @12.50 GB
  vs 0.0845 ± 0.0044 @12.37 GB; KLD t=0.6 (10/20), top-1 t=1.0, NLL
  t=−2.5; sign flip −0.0061→+0.0018; CONTROL 0.0723 ± 0.0026, t=1.9
  (16/20), beats Q3_K_S |t|≥2.5; increment −0.0081 (t=−5.9), +0.65 pt
  (t=+2.5); IQ3_XS 0.0666 ± 0.0025, t=−2.8; provenance tax 0.002–0.003;
  capture 0.42; control at −0.016% bytes; recipe matches. ✓ (MASTER-
  TABLE 27B block is E27-consistent throughout. ✓)
- §4.3 small scale: 25.27/0.209 vs 26.06/0.238 (~5σ, Bonferroni-
  surviving per §F7); 0.147 vs 0.167 (~3σ est., PPL 1.6σ per §F11);
  interpolation ≈0.118 @382 MB / ≈0.080 @523 MB (claims §F1/F6);
  Q4_K_M 3–4× (0.066 vs 0.209; 0.0396 vs 0.1467). ✓
- §4.4: 20/28/41% (ROLLUP corr. 6); IQ1_S 1.56 bpw/7.44 GB; 0.891→
  0.527, +8.5 pt, 16.5σ; r512/r256, 2.52 GB, 52%; 10.50/0.431 @9.96 GB
  vs 7.891/0.153 @10.86 GB; carrier ~0.28 KLD/GB; IQ1_M/IQ2_XXS never
  built; 10.3–10.5 GB VRAM projection (E15; claims §F4). ✓
- §4.5 MoE: 4.6σ (18/20); +0.0257 ± 0.0038 @6.9σ (0/20); Q3_K_S
  +0.7 GB ~2×; ~4× per byte; SH 0.090/0.086 vs 0.0625 floor, 0.022 vs
  0.0156; per-expert 0.19–0.36 vs 0.7+ dense; 91% of params; 512×2048;
  256 experts / 8 routed (E23 README). ✓
- §4.6: 27.58/0.318 vs 31.29/0.420 vs 28.46 (~10σ/2.6σ); 0.1460→0.1282
  (~2σ); 34.17 vs 31.29; attn_k 0.0128 ΔKLD/MB (E07 table + JOURNAL
  242); 0.352 ≈ 0.346; alloc 0.5σ; Spearman −0.29/+0.39 (E20 REPORT
  111–119); two-sided 27.27. ✓
- §4.7: 2.746 vs 3.061 (~8σ); 34.90 vs 27.58 (27%); 10–27% band. ✓
- §5: 667→444/407; 476→685 (+44%); 394→638 (+62%); −6.6%→+0.4%;
  391→702 (+79%); 84.7→86.9 (+2.6%), ceiling +4.9%, rank-0 probe 89.4;
  86.3→82.3 drift strike; 9% drift; 794→950 (+19.6%); 12,996 cases,
  string-identical generation (E25 REPORT 103/115/119); +42% contention
  single-run caveat carried; SHA 0ef6e55ed. ✓
- §2: diag match 2e-8 (E10 line 29); 65,536 pairs / ~256 per expert /
  8× deficient / 1.3e-4 (E23 capture, arithmetic checks); 7e-15,
  1.3e-15, 9e-16, 9,548 infs, 25% blocked-metric excess (adversarial-
  code §A3/A4/B1); r64-Q8 31.26 vs 31.29; Spearman 0.981 (2407.09141);
  #21037/#23476/#23575 handled as confirmations per §F10/F11. ✓

### 1.2 MISMATCHES found (7)

**M1 — Abstract, sentence "MoE expert residuals are mutually
near-orthogonal, defeating shared-basis amortization at 6.9 paired
sigma." KILLS (the sentence).** The 6.9σ belongs to a *different*
comparison: per-expert-adapter vs the trivial promotion control
(+0.0257 ± 0.0038, 0/20 — E23 paired table). The shared-basis defeat is
a *capture-level* result (0.090/0.086 vs 0.0625 floor; 0.022 vs 0.0156)
plus PE-vs-SH at 4.2σ; SH even *improves* the base at 2.7σ. A referee
who opens E23 sees the abstract pinning its statistic to the wrong row.
Fix: "…are mutually near-orthogonal (shared-basis capture barely above
the random-basis floor), and per-expert correction loses to a trivial
quant-promotion control at 6.9 paired sigma."

**M2 — §4.4: "the adapter's second and third gigabytes bought ~0.10 KLD
per GB". WEAKENS (wrong unit, flatters the adapter ~2×).** E15: the
second+third GB (0.80→2.52 GB, i.e. +1.72 GB) bought 0.527→0.431 =
0.096 KLD **total** — ~0.056 KLD/GB, not 0.10/GB. The true carrier:
adapter exchange contrast is ~5×, not ~3×. Fix: "…bought ~0.10 KLD in
total (~0.06 per GB) where a gigabyte of carrier bits buys ~0.28."

**M3 — Abstract + §1.2: 27B parity quoted from the retracted legacy
measurement. KILLS (staleness; see Part 2, S1).** "~1 sigma" /
"KL-divergence margin of 1.0 sigma unpaired, top-1 agreement *exactly*
tied at 88.039%" cites E10 — but the draft's own §4.3 (E27) replaced
this: paired KLD tie t=0.6 with the legacy margin's **sign flipped**
(−0.0061 → +0.0018), and top-1 is 88.65 vs 88.24 (t=1.0 tie), not
"exactly tied". The intro contradicts the results section.

**M4 — §1.3(3): "the constant varies 9.6-20.8 … [source:
RESULTS_ROLLUP.md]". COSMETIC (mis-citation).** The rollup's correction
5 parameterizes c as 0.36–0.60×d/r; the 9.6–20.8 figures are from
review/adversarial-stats §F6. §4.2 cites both correctly; §1.3 cites
only the source that doesn't contain the number.

**M5 — §4.5: "captures 0.090 … on up/gate stacks". COSMETIC.** E23:
gate 0.090, up 0.086. Write "0.086–0.090" or cite per-stack.

**M6 — §5.1: "−33% to −41% at 0.6B (667 → 444/407)". COSMETIC
(arithmetic + internal inconsistency).** 407/667 = −39%, not −41%
(error inherited from round-1 claims §F2). Worse: §5.2 states the tax
as "−45% → −20%" (Phase-2 session, different baseline) with no
reconciliation — a referee will ask which tax number is the paper's.
Fix: one footnote tying −33/−39% (E04 session) to −45% (Phase-2
session, higher bare baseline) via boost/context drift.

**M7 — §6.4: "one lever that is free everywhere and now paired-resolved
at two scales". WEAKENS.** Paired-resolved at *one* scale (0.8B, E24;
replicated paired by E26's repro gate). The 0.6B result is unpaired
legacy (~20σ unpaired — safe, but not "paired-resolved"); 27B pending.
And "free everywhere" — see Part 3, R2.

### 1.3 Figure references vs OUTLINE's list. WEAKENS (as a package).

OUTLINE plans F1–F6 (+F7 rev-3, F8/F9/F10 rev-4). Draft usage:

- **F4 (VRAM-vs-PPL Pareto) is orphaned** — referenced nowhere in the
  draft, yet it is the mission-axis figure (round-1 claims §F3 showed
  the file-bytes/VRAM conflation was the campaign's worst habit). Either
  draw it or delete it from the plan; a referee will ask for exactly
  this plot.
- **F3 double-booked:** OUTLINE F3 = residual-energy vs rank (E03),
  used correctly in §4.1 — but §3.1 cites "[F3 companion table]" for
  the re-rounding three-scale summary. Renumber the §3.1 table.
- **F5/F6 misused in §5.4:** OUTLINE F5 = tok/s vs rank, F6 =
  fused-kernel schematic; §5.4 cites "[F5, F6]" for the serving-tax
  *ledger*. Either the ledger is a new figure (F11) or the OUTLINE
  list is stale — reconcile.
- F1, F2, F7, F8, F9, F10 usages are consistent with the plan. ✓

---

## Part 2 — Staleness audit (pre-E27 / pre-E26 framings)

§4.3 was rewritten for E27 and is clean. The rest was not:

**S1 — Abstract ¶1 + §1.2 carry the legacy 27B story. KILLS.**
Details in M3. Additional defect: the abstract's walls sentence
attaches "under the ladder's interpolation line" only to the 0.6–0.8B
wins — but E27's *new headline negative* is that the interpolation
control matches-or-beats the flagship **at 27B too** (t=1.9 control's
favor, and control beats Q3_K_S on all three metrics). E27's own README
instructs: "The paper's 27B section should lead with the control." §4.3
complies; the abstract and §1.2 do not mention the 27B control at all.
Fix (abstract): "…the corrected configurations reach paired-tie parity
with the adjacent rung at 27B (the legacy 1σ 'victory' flipped sign
under clean provenance) and never exceed the ladder's own mixed-quant
interpolation at equal bytes — a control we build at 27B and MoE
scale…" Fix (§1.2): replace the E10-sourced sentence with the E27
numbers and cite `experiments/27-bf16-rederivation/README.md`.

**S2 — §6.1: "Q3_K_S was never BF16-rescored". KILLS (the sentence).**
E27 rebuilt Q3_K_S one step from BF16 and scored it against BF16 truth
(0.0845 ± 0.0044) — the very number §4.3 headlines. The limitation
paragraph is describing the legacy tables without saying so, and as
written contradicts §4.3. Same paragraph, same fix for "several 27B
levers … pending paired tests" (still true — allocation, re-round,
alternation — but say "the *remaining* legacy 27B levers").

**S3 — §6.2: E27 described in future tense. WEAKENS.** "Only the
frontier configurations are then re-derived at 27B from its BF16" —
this landed (E27) and is load-bearing in §4.3. Also "held-out corpus
plus one task evaluation … queued": the R7 held-out arc has a landed
first pass (`22-qwen35-08b/heldout-results.txt`, DEPLOYABLE-WINS arc)
whose result is *material*, not pending (see R2). Rewrite §6.2 to
credit E27 as landed and the R7 seed as landed-with-findings.

**S4 — E26 is absent from the entire draft. KILLS (completeness of the
paper's own central theme).** Grep confirms: no Lloyd, no gauge, no
equalization anywhere in draft/*.md. What is lost:

- (a) **The fourth surrogate-vs-outcome datapoint.** §3.2/§4.6 present
  the theme with three instances and call it "the campaign's fourth
  recurring lesson" — E26-M4 is the cleanest instance of all: a 2.6%
  further descent of the *same* whitened surrogate produced exactly
  zero end-to-end change (|t| ≤ 1.3 on all metrics), while the same
  harness resolves RR-vs-bare at t = −27..−47. The quotable law the
  draft lacks: *below some floor, whitened-proxy descent stops
  predicting KLD.* This belongs in §3 (it also closes the "would a
  servable Lloyd beat single-pass RR?" question a referee will ask
  after §3.1) and in §4.6's list.
- (b) **The equalization null (M5).** Full-strength diagonal
  equalization on an imatrix-weighted quantizer is anti-helpful at
  t = +28..+60 (0/40 chunks), with a *measured* mechanism (importance
  double-counting + 32× sub-block dynamic range) and a bit-exact
  pow2 gauge fold (equalized BF16 ≡ stored BF16: KLD 0.000000). This is
  a publishable negative directly relevant to the AWQ/SmoothQuant
  lineage and it is the paper's strongest-t result. It also carries a
  reusable methods nugget (pow2 folds are exact gauge transformations).
- (c) **§2.4's convergence story needs E26's measurement.** M4 found
  the codes↔scales alternation *never* reaches a codes fixed point
  (92/96 tensors in a ~0.1% limit cycle at the iteration cap) — the
  certificate's best-iterate framing survives, but Claim 3's
  "fixed point where no code crosses a decision boundary" language
  should cite the measured limit-cycle behavior as what actually
  happens at the floor.
- (d) A free reproducibility asset: E26's repro gate rebuilt the E22
  base 335/335 tensors byte-identical and re-derived the E13 artifact
  byte-identical — exactly the kind of statement Part 3 R7 asks for.

**S5 — references.md still sells the retracted result. COSMETIC (but
poisonous if copied).** The #8831/ik#15 row says "our byte-fair
MIXED+fc > Q3_K_S result is the direct empirical answer" — post-E27
that is parity, not >. §1.4's version ("the skeptic was mostly right")
is correct; fix the bibliography row before anyone cites from it.

---

## Part 3 — Referee attacks (what gets this rejected)

**R1 — The abstract omits the on-distribution weakness entirely.
KILLS at any serious venue.** Every quality claim in the abstract
(15%/34% free, parity, wins-from-below, recovery fractions) is
calibration-distribution-only (wikitext train/test), with zero task
evals and zero held-out corpora behind the headline numbers — the
draft's own §6.1 says so, PROTOCOL §7 mandates a held-out corpus for
generality claims, and the campaign's own E14-cond measured 10–27%
corpus sensitivity (larger than every contested margin). §4.3 carries
the asterisk; the abstract and §1.3 do not. One clause fixes it:
"…all quality results are measured on the calibration distribution
(wikitext-2 train/test split); held-out and task evaluation are the
stated gating repairs (§6)."

**R2 — The free-lever headline is not conditioned on calibration
breadth, and the measured counterexample is missing. KILLS.** The
draft's single most marketable claim — "free everywhere", "improves …
at identical bytes", "millions of deployed K-quant files carry this
headroom today" (abstract, §1.3, §6.4) — is contradicted by the
campaign's own R7 held-out first pass (DEPLOYABLE-WINS ⚠ + ✅ blocks):
**wikitext-calibrated re-rounding is WORSE than bare on held-out code
(PPL 3.470 vs 3.083)** — the free lever *anti-generalizes* under
narrow calibration — and is rescued only by true-interleaved 2:1 mixed
calibration (wiki 33.09→29.84 AND code 3.083→2.785), at a modest
on-distribution cost (−10% vs −15%). The calibrated final claim
("re-rounding improves what it is calibrated on; calibrate broadly,
improve broadly; the tool ships with a held-out gate or not at all")
appears **nowhere in the draft** — not in §3, not in §6.1, not in
§6.3's `gguf-refine` pitch. This is worse than an omission: a referee
who finds heldout-results.txt in the artifact will read the abstract's
"free everywhere" as claim-shopping. Fix: (i) abstract — "one lever
that is free at fixed calibration breadth"; (ii) §3.1 gains a
paragraph: the held-out arc (flaw → mechanism: pure fit to calibration
covariance with no anchor beyond the grid → mixed-calibration rescue,
both corpora, PPL-only, single ratio — labeled a rescue demo per
ABLATION-PLAN §1.1); (iii) §6.3's tool paragraph inherits
"mixed-calibration default + held-out validation gate".

**R3 — Novelty vs CALDERA/LQER/EoRA/QERA: mostly adequate, one
overreach. WEAKENS.** §1.4 states the delta crisply and narrowly
(same-pipeline ablations on production K/I-quants vs the strongest
deployed baseline; frozen-grid re-rounder as a deployable tool class;
MoE orthogonality negative; the fused serving path; upstream #8831
answered) — this survives round-1 §F13/F14 discipline. Remaining
exposure: (a) "no tool in this class existed on our tracker sweep"
(§1.3) — round-1 §F22 established the sweeps exhausted their search
budget and missed tracker-resident prior art twice; DEPLOYABLE-WINS
claims a 2026-08-04 tracker sweep. State the sweep's scope+date in a
footnote or soften to "we are not aware of". (b) A referee will
observe the positive novelty reduces to the re-rounder + measurement
venue + negatives; preempt by foregrounding what none of
CALDERA/LQER/EoRA report: interpolation-control comparisons and
I-quant rivals (their ladders are RTN/GPTQ strawmen — this is the
paper's sharpest differentiator and §1.4 undersells it in one clause).

**R4 — Protocol-vs-evidence inconsistencies a referee will catch.
WEAKENS (two instances).**
- §2.5(2) promises KLD/top-1 at "≥40 chunks"; the paper's flagship 27B
  evidence (E27, §4.3) is 20-chunk. E27's own README carries the
  caveat ("rows exist to settle ORDERINGS"); §4.3 dropped it. Add the
  clause and the T1.5 100-chunk plan.
- §2.5(1) states "the reference is the BF16 artifact" flatly; §4.5's
  MoE numbers are scored against the **vendor Q4_K_M** (no BF16 exists
  on the rig; E23 discloses this prominently, §4.5 not at all).
  PROTOCOL §0's "targeted reference" rule covers it — but the draft's
  §2.5 doesn't state that rule, so §4.5 as written violates §2.5. Fix
  both: state the targeted-reference rule in §2.5 and disclose E23's
  provenance + ≥2.7σ-paired-margins defense in §4.5.

**R5 — No ablation disclosure. WEAKENS→KILLS depending on venue.**
ABLATION-PLAN's census exists (51 lever-at-scale cells: 17 CLEAN /
15 CONFOUNDED / 19 MISSING; three named worst confounds; a priced
T1 matrix of ~30 GPU-h that the plan itself calls the gate for
"paper submittable") — and the draft neither includes an ablation
table nor cites the plan. §6.2's rerun-matrix paragraph hand-waves at
it. Referees reward exactly this table (the draft's nulls are its
cleanest cells) and punish its absence once they notice, e.g., that
the recovery curve's three points still vary adapter budget and rank
mix (ABLATION-PLAN worst-confound #1 — §4.4 discloses the metric
switch but not the budget/rank-mix confound), or that the 27B
allocation row varied three factors at once (#2 — §4.6 does disclose
this one). Fix: promote the census (condensed: lever × scale × verdict)
into §6 or an appendix, and add the budget/rank-mix caveat to §4.4.

**R6 — Two-point capture observation in the abstract. WEAKENS.** It is
honestly hedged everywhere ("a two-point observation, not a law") —
good — but ABLATION-PLAN T1.10 prices the upgrade regression (E21
sweep + 0.8B + cached 27B spectra) at **~4 CPU-hours, no GPU**. A
referee will ask why a headline abstract claim rests on two confounded
points when the fix is an afternoon of python the authors themselves
scheduled. Run T1.10 before submission or demote the observation out
of the abstract.

**R7 — No reproducibility/artifact statement. WEAKENS→KILLS at
artifact-track venues.** §5.4 pins a SHA for kernels; nothing else.
Every fc number depends on the private Gram-capture patch, gradient
Grams on llama-gradmatrix, near-tax-free serving on unmerged kernels
(§2.3 admits "capture, however, is *not* stock" — good), and §6.1
discloses cache-fingerprint hazards. What is missing is one paragraph:
what will be released (patches, tools, adapters, logits, per-chunk
logs), under what license, and the byte-identical-rebuild evidence
(E26's repro gate; E24's provenance gate) that the pipeline is
re-runnable. The campaign has unusually good raw material for this
paragraph; the draft just never writes it.

**R8 — Anonymization and voice. KILLS (double-blind), WEAKENS
(single-blind).** §4.4: "**Max's** founding pure-factorization dream in
its last servable form" — a personal name in the results section.
"the campaign" (~10×), "twenty hours of measurement" (§6.4), "Methodology
v2", "[source: research/lowrank/…]" repo-path citations, and the
"(Working title, OUTLINE rev-4/5 lineage.)" line under the title are
all internal-voice leaks; "heretic-v2" (an uncensored community
finetune) needs a neutral model-provenance footnote or a swap to the
base checkpoint for the camera-ready story. Fixes: "the founding
pure-factorization hypothesis"; "our measurement campaign" → "this
study"; convert [source:] brackets to real citations/appendix pointers.

**R9 — The two-point capture observation's cousin: §3.1's rigor-order
sentence. COSMETIC.** "We report it at three scales, in increasing
order of statistical rigor" — the order is legacy-0.6B → paired-0.8B →
*unresolved*-27B; rigor rises then falls. Say "in order of scale" or
reorder.

---

## Part 4 — Missing content

**C1 — Related-work coverage vs the load-bearing dozen. WEAKENS.**
references.md §"The load-bearing dozen (the intro MUST cite)" — draft
grep: **RILQ (2412.01129), SVDQuant (2411.05007), and SVD-LLM
(2403.07378) appear nowhere in the draft.** RILQ is the published
diagnosis of the paper's central wall (2-bit error is inherently
high-rank; layer-local correction saturates) — its absence from
§4.1/§4.2 is a gift to a referee hunting for "known result". SVDQuant
is the fusion-cost template (§5's 50%→5–10% precedent) and the
engineered-concentration contrast for why rank-32 works elsewhere and
not here. SVD-LLM is the Cholesky-whitening-at-scale theorem §2.2
silently relies on. Three sentences fix all three. Also carry
references.md's own verification flags into the citation pass: the
draft *load-bearingly* cites 2606.01412 (Bid-Up monotonicity, §2.3 —
"tables didn't survive HTML, pull PDF"), 2606.00494 (ProjQ, §2.4/§6.3 —
"numbers conflict across extracts"), and 2604.07955 (ResComp, §4.4 —
"extract lossy, verify before citing").

**C2 — Deployable-wins narrative: integrated except its caveat.
WEAKENS.** Win 2 (kernel patch set, who-improves-concretely, upstream
bundle) is well integrated (§1.3(4), §5, §6.3). Win 1's *held-out
caveat and resolution* — the most decision-relevant content in
DEPLOYABLE-WINS — is the R2 hole. The "quality becomes memory" framing
(stay a tier lower on 8–16 GB cards) is also absent and is the
cleanest consumer-impact sentence the paper could carry; consider one
line in §3.1.

**C3 — Limitations honesty: good, two gaps. WEAKENS.** §6.1 is
genuinely strong (discloses code hazards, transferred priors, blocked
whitening, boost drift). Gaps: (a) no mention of the RR held-out
anti-generalization (R2) — the one limitation with a measured
counterexample; (b) the alternation evidence grade (ABLATION-PLAN
§1.6: confounded at every scale; round-2 stop fired on a 0.2σ delta;
0.8B round run conditionally with no recorded error bars) is not
carried into §6.1 even though §4.8 leans on "measured at two scales
and paired-confirmed at a third" — the paired third (E24, t=+17.6) is
the *ordering* claim, not the alternation-rounds claim; say so.

**C4 — E27's refinement of the MIXED-bare story. COSMETIC.** E27:
MIXED bare *loses* to Q3_K_S paired (t=+3.6) under clean provenance —
refining round-1 §F1a ("the base mix alone ties the rung"). §4.3
doesn't mention it; it strengthens the paper's own
correction-increment-is-real point (the adapter, not the mix, closes
the gap to parity) and costs one clause.

---

## Priced repair list (draft-edit only unless noted)

1. Rewrite abstract ¶1: E27 parity + sign-flip + 27B control clause;
   fix M1's 6.9σ attribution; add R1's on-distribution clause; condition
   the free lever per R2. (One evening, zero compute.)
2. §1.2: swap E10 legacy numbers for E27; cite E27.
3. Insert the R7 held-out arc into §3.1 + §6.3 (numbers exist).
4. Add E26 (S4): Lloyd null → §3, fourth datapoint → §4.6, equalization
   null → new §4.x or §3 sidebar, limit-cycle cite → §2.4.
5. Fix M2's per-GB unit; M4's citation; M5/M6 cosmetics; S2/S3 tense.
6. Figure plan: draw or drop F4; renumber §3.1's table; reconcile F5/F6.
7. Add ablation-census table (from ABLATION-PLAN §1) + reproducibility
   paragraph (E26/E24 gates, release list).
8. Cite RILQ/SVDQuant/SVD-LLM; resolve the three flagged PDFs.
9. De-anonymize-proof: "Max's" out, [source:] brackets → citations.
10. Compute-cheap upgrades before submission: T1.10 regression (4 CPU-h,
    R6) and T1.3 paired RR@27B (1.5 GPU-h — §3.1's 27B row is the only
    free-lever scale point still unpaired).

## What survives untouched

§4.3's 27B rewrite is exemplary (control-first, sign-flip disclosed,
increment separated from verdict); §2's caveat discipline
(blocked-whitening, not-stock capture, gradient cut) is referee-proof;
§5's drift/same-session hygiene and the §4 nulls (water-filling,
shared-basis, MoE control, IQ1 asymptote) are the strongest-evidence
content in the paper and are framed at exactly the right strength. The
paper's thesis sentence (§6.4: "the walls around the corrections, the
freedom inside the grids") is earned by the record — once the abstract
stops selling the pre-E27, pre-held-out version of it.
