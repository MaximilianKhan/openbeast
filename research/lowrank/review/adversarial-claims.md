# Adversarial review — claims vs evidence, fairness of comparisons

Reviewer lens: every headline attacked for byte-fairness, rival selection,
novelty, and arithmetic. Scope: `RESULTS_ROLLUP.md`, `JOURNAL.md`, all
`experiments/*/README.md`, `upstream/*.md`, `prior-art/MANIFOLD-CANDIDATES.md`.
Date: 2026-08-04. Verification performed against the local llama.cpp checkout
and the ggml-org/llama.cpp GitHub tracker via `gh` (WebSearch budget was
already exhausted by the campaign's own sweep agents — itself a finding, see
F22). Severity scale: **KILLS** (headline is wrong or indefensible as
stated), **WEAKENS** (true but materially overstated / missing a control),
**COSMETIC**.

The campaign's null-keeping culture is genuinely good — most raw tables are
honest. The damage is concentrated in the *framing* layer (rollup verdicts,
"crown"/"victory"/"law"/"first" language, and statements distilled for Max)
and in the upstream drafts' novelty claims.

---

## 1. Byte-fairness and serving-cost accounting

### F1. The 0.6B "crown" is dominated by the ladder's own interpolation — the comparison rung is chosen from below only

- **Claim** (ROLLUP: "0.6B verdicts: rung reached at byte-parity, jumped at
  +4–10% bytes"; E10: "clear win on both metrics" at +10%): alt2+fc r128-Q8
  (382 MB, PPL 25.27, KLD 0.209) beats Q3_K_M (347 MB, 26.06, 0.238).
- **Defect**: the crown sits at 382 MB, *between* Q3_K_M (347) and Q4_K_M
  (397, PPL 23.14, KLD 0.066). The ladder interpolation at 382 MB is
  ≈ PPL 24.0 / KLD ≈ 0.118 — the crown loses to it on both metrics, badly
  (25.27 vs 24.0; 0.209 vs 0.118). And interpolation is not a fiction here:
  the campaign itself built mixed-quant bases at 27B (MIXED, MIXED2) and
  explicitly measured against "the 12.5 GB ladder interpolation" (JOURNAL,
  night block). The same standard was never applied at 0.6B. A ~382 MB
  mixed quant (Q3_K_M with a few tensors at Q4_K) was never built as a
  control. Q4_K_M itself, at +3.9% more bytes than the crown, is 3.2× better
  on KLD — the exact margin class (+4–10%) the rollup celebrates in the
  other direction. E21 quietly concedes this ("Q4_K_M … reasserts above
  ~400 MB — r160+ does not beat it byte-fair") but the rollup verdict does
  not carry it.
- **Severity**: **KILLS** the "crown/jumped the rung" framing. The honest
  statement is: *corrected Q2_K creates points above the discrete rung below
  it, inside the 347–397 MB gap, while remaining below the ladder's own
  interpolation line and losing decisively to the rung above at ≈ equal
  bytes.*
- **Repair**: (a) build the missing control — a mixed-quant base at 382 MB
  — before any "beats the ladder" sentence at 0.6B; (b) restate the rollup
  verdict as "non-dominated vs the discrete ladder in one 50 MB window";
  (c) show the Pareto plot (planned F9) with the interpolation line drawn.

### F1b. "Rung reached at byte-parity" — it wasn't

- **Claim** (ROLLUP): byte-fair alt2+fc r64-Q8 (339 MB) "reached" the rung.
- **Defect**: 26.58 vs 26.06 PPL is a 2% loss at −2.3% bytes, and KLD was
  never measured for that config ("—" in the table). "Reached" is doing a
  lot of work; E10's own phrasing ("0.52 from the rung") is the honest one.
- **Severity**: WEAKENS. **Repair**: use E10's phrasing; measure the KLD.

### F2. Serving cost is priced nowhere in the 0.6B/0.8B claims — and the crown's stock-serving tax is −33%

- **Claim**: crown configs are "stock-servable, zero llama.cpp changes."
- **Defect**: on *stock* llama.cpp the adapter costs −33% to −41% decode at
  0.6B (E04: 667 → 444/407 tok/s) and −20% at 27B even after Phase 2
  (draft issue #2's own numbers). The ~0-tax numbers (702 tok/s) exist only
  on the *local, non-upstreamed* fused-kernel build. So the two headline
  claims — "beats the rung" and "zero code changes" — are never true
  simultaneously: byte-win + stock serving = pay 20–40% decode; byte-win +
  full speed = requires the private kernel patches. The 0.6B/0.8B rollup
  tables have no tok/s column at all, so this trade is invisible exactly
  where the crown claims live. The quant rungs being "beaten" pay zero tax.
- **Severity**: **WEAKENS→KILLS** depending on venue. For an upstream demo
  ("beat same-bytes rung at ≤10% decode cost" — the campaign's own recorded
  gate from `prior-art/upstream-landscape.md`) the 0.6B crown currently
  FAILS its own gate on stock llama.cpp.
- **Repair**: add tok/s (stock and fused) columns to both small-model
  tables; state every ladder claim as "at X% decode cost on stock / Y% with
  our kernel (unmerged)".

### F3. On the mission's own metric — VRAM — the 27B "ladder victory" evaporates

- **Claim** (ROLLUP/JOURNAL/memory): "geometry-aware MIXED+fc beats Q3_K_S
  (first ladder victory)" at ~equal bytes (12.50 vs 12.37 GB **file**).
- **Defect 1 (file≠VRAM)**: the adapter path inflates file→VRAM overhead.
  Measured: MIXED+fc 12.50 GB file → **15.0 GB VRAM** (Δ2.5); Q3_K_M
  13.50 file → 15.3 VRAM (Δ1.8). At *equal VRAM* (15.0 vs 15.3) the ladder
  rung Q3_K_M crushes the flagship on quality (KLD 0.0555 vs 0.0812).
  Q3_K_S's VRAM was never measured; by the bare-quant Δ≈1.8–2.1 pattern it
  would sit ≈14.2 GB — i.e. *below* the flagship's 15.0. The whole campaign
  is framed as VRAM reduction ("memory-poor" thesis, README ¶1); the
  victory is claimed on file bytes, the one axis that isn't the mission.
- **Defect 2 (dead rung)**: Q3_K_S is strictly dominated *within the
  campaign's own table* by IQ3_XS (12.26 GB, 0.0656, 89.4% — fewer bytes,
  better everything). Beating the one rung nobody would serve, while losing
  to the adjacent rung that everyone would, is not "a ladder victory"; it
  is a win over the ladder's known-worst step. (Upstream tracker context:
  IQ3-class superiority over Q3_K_S at equal-or-fewer bytes is
  long-established; cf. issue #5856 tracking IQ3_XXS quality.)
- **Defect 3 (no error bars)**: the victory margin is KLD 0.0812 vs 0.0872
  (7%) and PPL 7.313 vs 7.486 on a 20-chunk slice, with zero confidence
  intervals anywhere in the campaign. llama-perplexity prints ± estimates;
  none are quoted. The Phase-3 report's own 8-chunk PPL carries ±0.39.
- **Severity**: **KILLS** "first ladder victory" as stated. Survives as:
  "MIXED+fc is within the IQ3 band on file bytes, behind IQ3_XS, and behind
  Q3_K_M at equal VRAM."
- **Repair**: measure Q3_K_S and IQ3_XS VRAM; recompute the Pareto on VRAM;
  attach KLD/PPL uncertainties; retire the "victory" sentence unless it
  survives on the VRAM axis with error bars.

### F4. E15 "frontier by default / runs in the 10 GB VRAM tier" — file-size/VRAM conflation plus an unmeasured rival

- **Claim** (E15/ROLLUP): IQ1_S+fc at 8.24 GB is "frontier-by-default
  <10 GB"; "no ladder rung below IQ2_XS produces usable output."
- **Defect**: 8.24 GB is *file* bytes. The nearest measured VRAM datapoint
  (IQ2_XS+adapter) is 12.43 GB VRAM for a 10.25 GB file; by that overhead
  the IQ1_S+fc build serves at roughly 10.3–10.5 GB VRAM — i.e. NOT inside
  a 10 GB card. VRAM for the E15 config was never measured. And the "no
  usable rung below IQ2_XS" claim was asserted without measuring the actual
  equal-byte rivals: IQ1_M (~8.1 GB) and IQ2_XXS (~8.9 GB) exist in
  llama.cpp and were never built. "Only game in town" is a claim about
  rungs that were not run.
- **Severity**: **WEAKENS** (KILLS the "10 GB VRAM" phrasing specifically).
- **Repair**: measure VRAM for the E15 config; run IQ1_M and IQ2_XXS bare
  as the equal-byte controls; restate against them.

### F5. Calibration and evaluation share a distribution — and E14 proves the adapters exploit exactly that

- **Claim**: crown configs "beat the rung" on wikitext PPL/KLD.
- **Defect**: adapters are fitted to wikitext-train Grams and evaluated on
  wikitext-test; the rungs spend their (far fewer) calibration-fitted
  degrees of freedom the same way, but an 86 MB adapter is a much larger
  distribution-fitted object than a per-block scale table. The campaign's
  own E14-cond measures the effect: swapping the Gram distribution moves
  PPL from 27.58 to 34.90 on wiki — the adapter's win margin (26.06 vs
  25.27 ≈ 3%) is small against a demonstrated ~26% distribution
  sensitivity. E16 admits "task evals remain the final arbiter"; no crown
  claim carries that caveat, and no task eval exists in the campaign.
- **Severity**: **WEAKENS** every ladder-relative claim at both scales;
  combined with F1's interpolation loss it makes the 0.6B crown fragile.
- **Repair**: score the crown vs the rung on one held-out *off-wikitext*
  eval (the code-test corpus already exists in E14) before any external
  claim.

## 2. The crown recipes as WIN claims

### F6. E22 "clean ladder win on a second architecture" — same defect as F1, sharper

- **Claim** (E22/memory): fc-r128q8+alt1 (523 MB, KLD 0.147) "BEATS Q3_K_M
  (0.167) on all metrics at +9% bytes — first clean cross-arch ladder win."
- **Defect**: Q4_K_M sits at 543 MB / KLD 0.040 — the "beaten" ladder, at
  +3.8% more bytes than the crown, is 3.7× better. Ladder interpolation at
  523 MB ≈ KLD 0.080; the crown is 0.147. E22's own finding 3 concedes the
  Q4 tier "rules, as everywhere" — but the headline sentence, the rollup,
  and the memory entry all say "clean win" without it. "Loses at parity
  (fc-r128q8 ties the rung at +9% bytes... actually the tie IS at +9%),
  wins only with more bytes, and is dominated from above at +4%" is the
  honest sentence.
- **Severity**: **KILLS** "clean ladder win"; the transfer result (the
  method's machinery works unchanged on a second arch) survives and is the
  real finding.
- **Repair**: rename the finding "method transfers; same competitive window
  (between rungs 3 and 4) as 0.6B"; add the interpolation/mixed-quant
  control at 523 MB.

### F7. The recovery-fraction "curve" 7.5% → 18% → 41% (F7 figure) mixes estimators

- **Claim** (ROLLUP/E15): recovery fraction vs base bpw = 7.5% @Q2_K → 18%
  @IQ2_XS → 41% @IQ1_S — "the cliff-gradient law."
- **Defect**: the three points use different correctors: diagonal
  alloc-Q8 (Q2_K point), diagonal (IQ2_XS point), but full-covariance
  mixed-rank r192 (IQ1_S point). Held to one estimator (fc r128q8) the
  curve is 20% → 28% → 41% — still monotone, but half the drama; the
  published shape exaggerates the gradient ~2× at the left end.
- **Severity**: WEAKENS (the qualitative law survives; the figure as
  numbers doesn't).
- **Repair**: recompute all three points with the same corrector recipe;
  plot both series if the diag points are kept.

## 3. Cherry-picked rivals

Covered by F3 (Q3_K_S at 27B) and F1/F6 (rung-below-only comparisons).
One addition:

### F8. "MIXED2 ties IQ3_XXS" is a loss stated as parity

MIXED2+fc: 11.70 GB / KLD 0.0970. IQ3_XXS: 11.48 GB / 0.0962. More bytes,
slightly worse KLD, no error bars — that is a strict domination by the
rival, not a tie. E10's own point 3 ("parity, not superiority") is the
right register; the rollup's "corrected-Q2_K ≈ IQ-codebook efficiency"
rounds a loss up to equivalence. COSMETIC→WEAKENS. Repair: "matches within
noise at +2% bytes (uncertainty unquantified)".

## 4. The capture "law" c≈5.9

### F9. Two points, one ratio, one confound-denied — "law" is not earned

- **Claim** (ROLLUP law #1, paper spine): "Capture ≈ 5.9·(r/d) …
  first measured here. Governs every result above."
- **Defects**:
  1. The constant is fit on ONE pair (0.6B r64/d1024 → 0.37; 27B r64/d5120
     → 0.07) and "verified" by the same pair (0.07×5=0.35). Any monotone
     decreasing function of d fits two points. A third scale point existed
     the same day (E22's 0.8B) and its capture is conspicuously unreported.
  2. "Confound-free pair" (JOURNAL) is false: the pair differs in
     architecture (dense vs hybrid-attention), calibration size, and
     base-quant mix, not just width.
  3. Their own data breaks constancy of c *within* scale: at 27B, capture
     at fixed rank varies 0.36 → 0.42 → 0.52 across bases (Q2_K / mixed /
     NVFP4, E16) — a 44% swing in "c" with d fixed; and the metric moves it
     more (diag 0.37 → fc 0.60 at 0.6B). c is a property of
     (whitener, base, arch), not a constant of nature.
  4. The linear-in-r reading is contradicted by E05's own table (capture is
     strongly concave in r: attn_q 0.54 at r16 vs law-style 0.09). The
     rollup does say "at fixed rank," but the paper-figure framing
     ("two scales, one line," F8) invites the linear reading.
  5. "Predicted 3× in print, first measured here" is unverifiable from the
     lab's own artifacts and was not independently confirmed (search budget
     exhausted); at minimum, the r/d-dilution mechanism is standard
     spectral reasoning and adjacent measurements exist across the
     EoRA/LQER/rank-allocation literature the lab itself catalogs.
- **Severity**: **WEAKENS** hard as a "law"; KILLS "governs every result"
  (the fc and NVFP4 results are governed by the whitener and base, per
  their own numbers).
- **Repair**: compute the 0.8B capture point tonight (data exists); report
  c per (whitener, base); demote to "capture at fixed rank fell ≈5× when
  width grew 5×, consistent with constant-cardinality outlier heads" until
  ≥3 widths on one whitener/base are measured.

## 5. "First / unclaimed / unpublished" claims

### F10. The KV-backprop "upstream seam of the first order" is publicly documented — llama.cpp issue #21037

- **Claim** (JOURNAL block 5, MANIFOLD M3, draft issue #4): "Upstream seam
  discovered en route: set_rows has no backward … llama-finetune trains
  with ZERO attention-score gradients into wk/wv. Upstream-issue material
  of the first order."
- **Defect**: ggml-org/llama.cpp issue **#21037** ("llama-finetune: 5
  cascading training bugs", closed stale 2026-05-11) documents as its
  Bug 2: "SET_ROWS missing from backward pass … used for KV cache writes",
  including a proposed gradient implementation ("the gradient of scatter is
  gather") and a view-allowlist fix. The draft cites no prior report. Note
  also the two reports *disagree* on failure mode: #21037 observed a loud
  GGML_ASSERT abort in the backward build; the draft claims silent zero
  gradients via leaf disconnection. Both may be true on different paths,
  but the draft's "easy to miss because losses still go down" narrative
  must be reconciled with the public abort before posting, or a maintainer
  will do it for you.
- **Severity**: **KILLS** the novelty claim; the *analysis* (which
  gradients are zero vs merely missing; the exact-bypass workaround for
  n_kv==n_tokens) retains value as a follow-up comment, not a new issue.
- **Repair**: rewrite draft #4 as a comment/reopen referencing #21037,
  leading with what is new (the silent-path analysis + gradient-norm
  instrumentation evidence + bypass patch).

### F11. The MTP-imatrix blindspot: both the bug AND the proposed fix already have open upstream PRs

- **Claim** (E09: "New upstream bug discovered"; JOURNAL: "an
  upstream-worthy gap"; ROLLUP: "Upstream issue drafts … MTP-imatrix
  blindspot; IQ2-blocks-MTP"): llama-imatrix never runs the NextN layer;
  very-low-bit quants hard-fail; proposed fixes 1–3 in draft #1.
- **Defect**: **#23476** (open, 2026-05-21) "imatrix: optionally activate
  MTP/NextN draft head during collection" is draft fix (3) implemented,
  with the identical error message quoted; **#23575** (open, 2026-05-23)
  "llama-quantize: use static quantization level for tensors missing from
  imatrix data" is draft fix (1) implemented, explicitly motivated by
  MTP-enabled GGUFs, with an alternative PR #23258 also linked. The failure
  class (never-executed tensors → "Missing importance matrix" refusal) was
  reported as far back as #6249 (2024). Draft #1 is a duplicate of an
  active, months-old upstream conversation the recon sweep
  ("upstream-landscape") failed to surface.
- **Severity**: **KILLS** the discovery claim and the draft as an issue.
  The `--tensor-type "blk\.64\.=q5_k"` workaround remains a useful comment
  on #23575, and the "pin the draft head high" argument is a genuine
  contribution to that thread.
- **Repair**: delete draft #1; comment on #23575/#23476 instead; correct
  JOURNAL/ROLLUP language from "discovered" to "independently rediscovered
  (upstream: #23476/#23575)".

### F12. "Nobody has published low-rank-on-NVFP4" — the qualifier is load-bearing and the rollup drops it

- **Claim** (E16: "Nobody has published low-rank-on-NVFP4 for LLMs";
  ROLLUP: "unpublished composition" — unqualified).
- **Defect**: SVDQuant (arXiv:2411.05007) IS a 4-bit base + low-rank
  branch, and its Nunchaku engine shipped NVFP4 support for Blackwell in
  2025 — i.e. low-rank-on-NVFP4 is deployed, published work in diffusion
  models; the lab's own kernel notes cite SVDQuant/Nunchaku repeatedly. The
  defensible claim is only the narrow one ("…for LLM PTQ error
  correction"), and the rollup's unqualified "unpublished composition"
  overreaches. Verification of any 2026 LLM-side NVFP4+low-rank preprint
  was NOT possible (search budget exhausted) — treat the claim as
  unswept, not cleared.
- **Severity**: WEAKENS (KILLS the unqualified rollup phrasing).
- **Repair**: restore the "for LLMs" qualifier everywhere; run the arXiv
  check before the paper; cite SVDQuant/Nunchaku-NVFP4 as the adjacent
  published composition.

### F13. "Full-vs-diag at 2-bit on 7B+ is UNMEASURED in the literature"

- **Claim** (E10/JOURNAL, sourced to the co-design mining agent).
- **Defect**: CALDERA (2-bit, LLaMA-2/3 7B–70B) minimizes the
  calibration-Gram-weighted objective ‖(W−Q−LR)X‖ — full-covariance
  weighting at 2-bit on 7B+, measured and published, with theory; LQER is
  the diagonal counterpart at similar scales. A cross-paper comparison
  therefore exists; what is plausibly novel is only a *same-pipeline
  ablation* (and on GGUF K-quants specifically). The lab's own
  references.md knows all these papers, and QERA is cited as the ablation
  source that "full covariance wins below ~4-bit" — which sits awkwardly
  next to "unmeasured."
- **Severity**: WEAKENS. **Repair**: claim "first same-pipeline
  full-vs-diagonal ablation on llama.cpp K-quants at 2-bit," nothing wider.

### F14. Alternation / E11 / M12 "greenfield"

- **Claim** (M12: "claim the greenfield"; ROLLUP law #3 presented as a
  campaign discovery).
- **Defect**: alternating quantize↔low-rank is LoftQ (ICLR 2024) — and
  references.md admits it verbatim ("our alternation protocol descends from
  it"), with CLoQ shown *beating* LoftQ's 5-round alternation at INT2, and
  CALDERA implementing best-iterate tracking (the theory note itself says
  so). So the lab's private record is honest while the rollup/JOURNAL
  present "joint shaping > greedy composition" as a measured discovery
  ("twice measured", law #3) without lineage. The M12 theorem is a
  finite-set + best-iterate termination argument — correct, elementary, and
  only "greenfield" in the narrow black-box-requantizer framing.
- **Severity**: COSMETIC→WEAKENS (internal docs honest; outward framing
  isn't). **Repair**: attach "(LoftQ-descended; novelty = black-box
  llama-quantize grid + whitened metric)" wherever law #3 or E11 is
  headlined.

## 6. E22 style claims

### F15. "~35 minutes" and "zero code changes" are survivorship-filtered

- **Claim** (E22): "Total wall time from first capture to final verdict:
  ~35 minutes"; "everything ran from existing tools … with zero code
  changes."
- **Defect**: (a) the clock starts at "first capture," excluding BF16
  acquisition, and covers only the three lanes that worked; (b) the
  two-sided lane — part of the experiment's stated pipeline — failed
  SILENTLY (gradmatrix zero-file bug) and is "BLOCKED pending" code
  changes; "zero code changes" is true only because the lane that needed
  them was dropped from the headline; (c) "existing tools" includes the
  locally patched llama-imatrix (Gram capture) — existing to this lab, not
  to llama.cpp. A reader hears "stock llama.cpp, 35 minutes"; reality is
  "our patched toolchain, 35 minutes for the lanes that succeeded."
- **Severity**: WEAKENS. **Repair**: "~35 min for the one-sided lanes on
  our patched toolchain; two-sided lane blocked by a tooling bug."

### F16. OUTLINE's "fully-reproducible stock-llama.cpp pipeline (zero code changes)" is false for every fc result

Serving is stock; *capture is not* — every full-covariance number depends
on the private `LLAMA_IMATRIX_GRAM_DIR` C++ patch, gradmatrix numbers on a
private tool plus E20 backward-graph cuts, and near-tax-free decode on the
private fused kernels. "Zero code changes" was true of E04's diagonal v0
and died the night E10 was built; the claim survived in OUTLINE and E22
unchanged. WEAKENS (KILLS if it reaches the paper). Repair: "stock serving
path; capture and kernels require our patches (to be upstreamed/released)".

## 7. Upstream drafts — what maintainers will find

### F17. Draft #2 (LoRA decode overhead) asserts a mechanism the campaign itself falsified the next day

- **Claim** (draft title/body, written 08-03): overhead is "launch-bound
  (rank-independent) … the signature of per-op launch overhead."
- **Defect**: Block 5 (08-04): "Launch-overhead hypothesis falsified
  honorably: launches were free under graph replay; the wall was tail-load
  serialization." The draft was never updated. Posting it as-is hands
  maintainers a diagnosis its own authors have disproven; the r16-slower-
  than-r64 anomaly (407 vs 444) is presented as mechanism evidence with a
  single run and no variance. Also #11410 ("llama-bench: allow benchmarking
  lora impact", open) is the natural home for the harness offer and is
  uncited.
- **Severity**: **KILLS** the draft's mechanism section as written (the
  measurements and the fusion proposal survive).
- **Repair**: rewrite with the Phase-2 root cause (tail-load
  serialization; prefetch-before-grid-sync), cite #11410, add run-to-run
  variance.

### F18. Draft #1 and draft #4 — see F11 and F10: both duplicate existing public work and must not be posted as new issues.

## 8. The compression-multiple statements to Max

### F19. "4.3–4.7× won" — arithmetic checks out only against a baseline nobody serves, on the wrong axis, and the ladder already delivers the same multiple

- **Audit**: BF16 file = 54.66 GB. 54.66/12.50 (MIXED+fc) = 4.37×;
  /11.79 (fc+alt) = 4.64×; /11.48 (IQ3_XXS) = 4.76×; /12.26 (IQ3_XS) =
  4.46×. So the 4.3–4.7 range = BF16-file ÷ corrected-build-file. Four
  problems:
  1. **Baseline**: the production baseline is Q6_K (22.8 GB file /
     23.6 GB VRAM) — the multiple actually banked by the campaign's work
     is 1.8× file, and on the mission metric (VRAM) **1.57×**
     (23.6 → 15.0).
  2. **Axis**: file bytes, not VRAM (F3's Δ-inflation applies — the
     corrected builds carry ~0.7 GB extra VRAM overhead vs bare quants).
  3. **Fidelity qualifier**: at 4.37× the fidelity is KLD ≈0.081 / 88%
     top-1 vs Q6 (≈0.084 vs BF16 truth) — a real quality tier below the
     reference, and the statement "won" carries no such asterisk.
  4. **Attribution**: stock IQ3_XS delivers 4.46× at *better* fidelity
     (0.0656/89.4%) with no adapter, no patches, no decode tax — the
     multiple was already on the shelf. What the method won at 27B relative
     to the ladder is, by the campaign's own tables, approximately nothing
     (parity at best; F3/F8).
- **Severity**: **KILLS** "4.3–4.7× won" as a statement of the method's
  achievement. Correct statements: "the *rig* can serve the 27B at 4.4×
  below BF16 file size at ~88–89% top-1 — mostly via llama.cpp's stock
  ladder; our correction adds parity-class points and wins only sub-2.5 bpw
  where no rung exists (E15, with F4's caveats)."
- **Repair**: any multiple quoted to Max or in the paper must name
  (baseline, axis: file|VRAM, fidelity at that point, and the stock-ladder
  multiple at equal fidelity) in the same sentence.

### F20. beastrank.py smoke "1.81× vs Q8 ref" quotes a multiple with a proxy (capture 0.77) and no quality metric

Same disease in miniature: a compression ratio with no KLD/PPL attached.
COSMETIC (it's labeled a smoke test) but it is the tool's advertised
one-liner and will be quoted. Repair: make the tool's report print
KLD-vs-ref by default, never capture alone.

## 9. Smaller findings

- **F21 (stale number in the headline table)**: ROLLUP lists MIXED+fc at
  "86.3 tok/s"; JOURNAL block 5 records that 86.3 was "boost drift" and
  the same-session A/B measured 82.3–82.4. The rollup shipped the number
  its own journal disowned. WEAKENS (it flatters the flagship vs Q3_K_M's
  89.2). Repair: 82.4, or remeasure locked-clock.
- **F22 (sweep reliability)**: the novelty sweeps exhausted the session's
  entire 200-call WebSearch budget (MANIFOLD-CANDIDATES notes both agents
  hit the cap), yet F10/F11 show the *GitHub tracker* — the cheapest, most
  load-bearing search of all — was never queried for the upstream claims.
  All "unclaimed/unpublished" statements should be treated as unswept until
  a dedicated fresh-budget session verifies them.
- **F23 (JOURNAL integrity)**: the final "append-only" entry is corrupted —
  the closing paragraph splices a stale fragment ("(1) per-tensor rank
  allocation …") from an earlier block after the sign-off. COSMETIC, but
  it is the provenance document. Repair: mark the fragment as a known paste
  error in the next entry (append-only fix).
- **F24 (E13 RFC framing)**: "re-rounding is the best free lever" and the
  RFC case lead with 0.6B (−18% PPL); at 27B the same lever made PPL
  *worse* (7.891 → 7.952) with a 4% KLD gain. The rollup row shows it but
  the "Artifacts ready for daylight" pitch omits the production-scale
  regression. WEAKENS. Repair: RFC leads with the 27B number and the codec
  gap, or waits for the Q3_K/Q4_K codecs.
- **F25 (laws #2 and #5)**: "Bandwidth inversion" is textbook
  memory-bound batch-1 decode measured at one model pair — fine as a
  measurement, overdressed as a discovered "law"; "every lever shrinks
  with scale" is two scale points. COSMETIC.

---

## Verdict

The measurement layer of this campaign is unusually honest; the claim
layer is running ahead of it. Three habits caused nearly every defect:
(1) comparing only against the rung *below* while the rung above sits
within ±4% bytes; (2) claiming on file bytes what the mission defines in
VRAM and decode; (3) declaring novelty from arXiv sweeps without querying
the GitHub tracker of the very project being upstreamed. All three are
fixable in one evening with hardware already warm: build the two missing
mixed-quant controls (382 MB at 0.6B, 523 MB at 0.8B), run IQ1_M/IQ2_XXS
bare, measure VRAM for E15/Q3_K_S/IQ3_XS, add uncertainty to the two
knife-edge wins, and convert drafts #1/#4 into comments on #23575/#23476
and #21037.
