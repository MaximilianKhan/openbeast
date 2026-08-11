# beast-rank — consolidated results rollup
> ⚠ round-2 corrections 2026-08-04 (review/adversarial-round2-*.md,
> review/coherence-audit.md applied)
> ⚠ RECON 2026-08-11 (prior-art/recon-2026-08-11.md): no measured
> number below changes, but the novelty FRAMINGS do — frozen-grid
> re-rounding is now published twice (GSQ improves shipped GGUFs
> in-format, code public; ReQuant), two-sided whitening was published
> in April (OBD-LLM/KronQ), and E16 is twice-narrowed (TwinQuant;
> SVDQuant ships NVFP4+low-rank for diffusion). Verified still ours:
> E17, capture-vs-width, E27's interpolation-control methodology, the
> register-neutral fusion law, MTP/linear-attn calibration. Read the
> recon before quoting any "first"/"unpublished" line from this file.

## ⚠️ ADVERSARIAL REVIEW CORRECTIONS (2026-08-04, review/*.md — read
## before citing ANY verdict below; raw tables stand, framings repaired)

1. **"First 27B ladder victory" → PARITY.** MIXED+fc vs Q3_K_S is a
   ~1.0σ unpaired margin, top-1 exactly tied; on equal VRAM Q3_K_M
   dominates the flagship. Paired per-chunk stats queued. → DONE (E27,
   BF16-pure): KLD/top-1 paired TIES (t=0.6/1.0), PPL edge (t=−2.5);
   the legacy KLD margin FLIPPED SIGN under clean provenance; the 27B
   interpolation CONTROL matches-or-beats the flagship at equal bytes.
   [corrected 2026-08-04 per review/coherence-audit.md P2.4]
2. **0.6B/0.8B "crown wins" → wins-from-below only.** The interpolation
   control (mixed-quant at matched bytes) was never built at small
   scale; ladder interpolation at 382 MB ≈ 0.118 vs crown 0.209. The
   honest claim is: the method TRANSFERS and competes in the narrow
   between-rungs window; Q4-tier rungs dominate above it.
3. **Serving tax is not priced in crown claims** — on stock llama.cpp
   the adapter costs decode throughput (0.6B −33% unfused); tax-free
   numbers require our local fused kernels. "Beats the rung" and
   "stock llama.cpp" must not be claimed together.
4. **86.3 tok/s (MIXED+fc) was GPU-boost drift — real: 82.3** (fused
   Phase-3 same-session: 86.9 vs 84.7 unfused). Struck below.
5. **"Capture law c≈5.9" → diag-r64 OBSERVATION** (two points; c varies
   0.36-0.60×d/r across bases/whiteners; "confound-free" retracted).
6. **Recovery curve, method-consistent (all fullcov): 20% → 28% → 41%**
   (the 7.5% first point was diagonal whitening — metric switch).
7. **Calibration/eval share wikitext-2** (train/test split only): all
   ladder comparisons pit calibration-fitted adapters against
   reweighted-only rivals with no held-out corpus and no task evals.
   Generality unproven; E14-cond itself measured 10-27% corpus swings.
8. **"4.3-4.7× won" → basis-dependent:** vs BF16 file ≈ 4.4×; vs our
   Q6 serving artifact 1.8× file / **1.57× VRAM** at 88% top-1 — and
   stock IQ3_XS offers a comparable multiple at better fidelity,
   adapter-free. The 2-4× band is REACHED, not "won".
9. **27B "full covariance" is block-diagonal for ffn_down** (8×; +25%
   true-metric excess measured on the surrogate); "measured-alloc" at
   27B used 0.6B kind sensitivities (relabeled: transferred-prior
   alloc). NVFP4+fc-vs-IQ3_XS ordering WITHDRAWN (inside the Q6↔BF16
   contamination constant). Kernel "bit-correct" → PPL-digit-identical.
10. **Upstream drafts corrected:** MTP-imatrix blindspot and KV-backprop
    are ALREADY REPORTED upstream (#23476/#23575, #21037) — drafts
    converted to confirmation-comments; draft #2's launch-overhead
    mechanism was falsified by our own Phase 2 and is rewritten.

**What the review left standing clean:** every named null, the
bandwidth inversion, fullcov-vs-diag (~2σ+), the 0.6B crown's on-
distribution KLD win (~5σ), IQ1_S recovery (41%), the 0.6B kernel
gains (+44/+62/+79%), all codec/math correctness (verified against
brute force), and the E22 transfer result as a transfer result.

One page, every measured configuration, 2026-08-03 → 08-04 (day session +
4-hour block + 8-hour night campaign). Sources: experiments/*/README.md
(each has method + reproduction commands). Metrics: wikitext-2 PPL
(full corpus at 0.6B, 20-chunk at 27B), mean KLD + top-1 agreement vs
reference logits (40-chunk at 0.6B vs Q8_0; 20-chunk at 27B vs Q6_K).

## 0.6B (Qwen3-0.6B, reference Q8_0: PPL 21.94)

| config | total MB | PPL | KLD | top-1 |
|---|---|---|---|---|
| Q2_K bare (no imat) | 296 | 258.07 | — | 35.1% |
| Q2_K-imat bare | 296 | 43.33 | 0.766 | 58.2% |
| **Q2_K-imat + E13 re-round (free)** | 296 | 35.54 | 0.556 | 64.0% |
| IQ2_M | 265 | 65.70 | — | 52.9% |
| IQ3_XXS | 279 | 43.27 | 0.734 | 60.5% |
| Q3_K_M (the rung) | 347 | 26.06 | 0.238 | 76.2% |
| Q4_K_M | 397 | 23.14 | 0.066 | 86.3% |
| diag r64 | 377 | 31.29 | 0.420 | 68.5% |
| diag r128-Q8 | 382 | 28.46 | — | — |
| fullcov r64 | 377 | 27.58 | 0.318 | 72.9% |
| fullcov r128-Q8 | 382 | 25.99 | 0.246 | 75.7% |
| alt2 + fc r64-Q8 (byte-fair) | 339 | 26.58 | — | — |
| **alt2 + fc r96-Q8** | **361** | **25.87** | **0.239** | **76.1%** |
| **alt2 + fc r128-Q8 (crown)** | **382** | **25.27** | **0.209** | **77.9%** |
| RR + fc r128-Q8 | 382 | 26.80 | 0.274 | 74.4% |
| alt1 + RR + fc r128-Q8 | 382 | 25.84 | 0.245 | 75.9% |
| round-trip check (r1024) | 1884 | 22.07 | — | — |

0.6B verdicts: rung reached at byte-parity, jumped at +4–10% bytes;
alternation×2 + full-cov + Q8 is the crown; re-rounding is the best
free lever; greedy RR-then-correct loses to joint shaping.

## 27B (heretic-v2, reference Q6_K 22.4 GB: PPL 6.99; VRAM 23.6 GB, 61 tok/s)

> **⚠ LEGACY TABLE (requant-of-Q6, Q6-referenced logits) — SUPERSEDED
> by MASTER-TABLE.md §27B / experiments/27-bf16-rederivation (E27,
> BF16-pure, paired stats).** The pairs 0.0555/0.0540 (Q3_K_M) and
> 0.0656/0.0666 (IQ3_XS) are different PROVENANCES of the same nominal
> measurement — cite the E27 numbers. Kept for the provenance-tax
> comparison only. [banner added 2026-08-04 per coherence-audit P1.1]

| config | GB | PPL20 | KLD | top-1 | VRAM/tok-s |
|---|---|---|---|---|---|
| Q2_K-imat bare | 10.86 | 7.891 | 0.1529 | 83.6% | 13.0GB / 99.7 |
| + E13 re-round (free, Q2_K codes only) | 10.86 | 7.952 | 0.1461 | 84.6% | — |
| + diag r64 (E04c) | 11.73 | 7.869 | 0.1460 | 84.4% | 13.8GB / 66.5 |
| + measured-alloc Q8 (E07) | 11.71 | 7.763 | 0.1415 | 84.6% | 13.8GB / 81.0 |
| + col-patch r64 (E08) | 11.73 | 7.779 | 0.1447 | 84.6% | — |
| + fullcov v2 r64 | 11.73 | 7.817 | 0.1282 | 85.4% | — |
| + fullcov v2 r128-Q8 | 11.79 | 7.789 | 0.1217 | 86.1% | — |
| + fc + alternation | 11.79 | 7.707 | 0.1142 | 86.1% | — |
| IQ2_XS bare (MTP@q5k) | 9.38 | 8.854 | 0.2642 | 78.5% | 12.4GB* / 88.2* |
| + diag alloc-Q8 | 10.25 | 8.404 | 0.2175 | 80.6% | — |
| + fullcov v2 r128-Q8 | 10.31 | 8.349 | 0.1905 | 82.8% | — |
| IQ3_XXS | 11.48 | 7.512 | 0.0962 | 87.9% | — |
| MIXED2 (IQ3-ffn) + fc | 11.70 | 7.439 | 0.0970 | 87.8% | — |
| Q3_K_S | 12.37 | 7.486 | 0.0872 | 88.0% | — |
| **MIXED (Q3K-ffn) + fc** | **12.50** | **7.313** | **0.0812** | **88.0%** | **15.0GB / 82.3** (86.3 struck: drift) |
| IQ3_XS (the 27B crown) | 12.26 | 7.275 | 0.0656 | 89.4% | — |
| Q3_K_M | 13.50 | 7.243 | 0.0555 | 90.1% | 15.3GB / 89.2 |
| Q4_K_M | 15.9† | — | — | — | — |

\* IQ2_XS serving measured with the diag alloc adapter. † file size only.

27B verdicts (rewritten 2026-08-04 per coherence-audit P1.2; the old
"first ladder victory" line contradicted this file's own header and
E27): MIXED+fc = paired PARITY with Q3_K_S (E27: KLD t=0.6 tie, top-1
tie, PPL edge; the legacy margin flipped sign); the 27B interpolation
CONTROL matches-or-beats the flagship at equal bytes; IQ3_XS keeps the
crown (paired, t=−2.8 vs Q3_K_S); every lever real but scale-shrunk;
corrected-Q2_K ≈ IQ-codebook efficiency at equal tensors.

## Block 4 additions (2026-08-04 morning, BF16 truth-scored)

> ⚠ Rows E27 rebuilt (Q4_K_M, and every ladder rung) are SUPERSEDED by
> MASTER-TABLE §27B (BF16-1step); these are requant-era measurements.
> [same banner class as the 27B table above — coherence-audit P1.1]

| config | GB | KLD vs Q6 | top-1 | note |
|---|---|---|---|---|
| Q6_K vs BF16 truth | 22.4 | 0.0032 | 97.6% | reference contamination: negligible, measured |
| IQ1_S bare | 7.44 | 0.891 | 61.9% | |
| **IQ1_S + fc-mix (E15)** | **8.24** | **0.527** | **70.4%** | 41% recovery; 8.24 GB FILE / ~10.3–10.5 GB VRAM projected; equal-byte rivals (IQ1_M, IQ2_XXS) unbuilt ["frontier-by-default <10 GB" struck 2026-08-04 — file/VRAM conflation, coherence-audit P1.4] |
| NVFP4 bare | 15.77 | 0.0805 | 88.5% | |
| **NVFP4 + fc (E16)** | **16.69** | **0.0625** | **89.9%** | −22% KLD, capture 0.52 — best-correcting base; no LLM-PTQ instance found (novelty sweep INCOMPLETE — review F12; "unpublished" softened per coherence-audit P2.14) |
| Q4_K_M (from BF16) | 16.84 | 0.0211 | 94.4% | wins the byte class |

Recovery-fraction curve (F7): 7.5% @Q2_K → 18% @IQ2_XS → 41% @IQ1_S
(mixed-whitener series, RETIRED — see correction 6 above; the
fc-consistent curve is 20/28/41. Left in place only so the retired
figure is findable — coherence-audit P1.5).
Fused kernel Phase 1: shipped, bit-correct, parity at r64 under CUDA
graphs (+10% r16, +42% under contention); Phase 2 (A·x into quantize)
was the payoff step (landed — see the Phase 3 bullet in Block 5;
tense fixed per coherence-audit P3.6). KV: non-constraint for this
arch (262k ctx fits corrected builds in ~20 GiB total).

## Block 5 (2026-08-04, extended clock)

- **Kernel Phase 3 (dp4a sidecar + register-neutral epilogue): 0.6B
  +79% (391→702); 27B flagship +2.6% (84.7→86.9), all windows fused,
  ceiling-probe-bounded (+4.9% max), digit-identical, kill-switched.**
  Root-cause law: fusion must be register-neutral (smem-park epilogue
  state) — occupancy, not traffic, was the 27B wall. Trilogy complete;
  next prize is allocator-level GRAPH_OPT concurrency (+19% base-model
  class).
- **Kernel Phase 2B (Q8_0 factor paths): 0.6B Q8 r128 394 → 638 tok/s
  (+62%).** At 27B the honest mechanism surfaced: unfused Q8 B·t already
  rides int8 dp4a upstream, so fusion regressed until size-gated (final
  +0.4%, gates green, PPL digit-identical both scales). Phase 3
  (quantized-t dp4a epilogue) launched — and landed, see the Phase 3
  bullet above (tense fixed per coherence-audit P3.6).
- **Kernel Phase 2: r64 adapter decode 476 → 685 tok/s (+44%), tax −45%
  → −20%, rank-flat, bit-identical.** Launch-overhead hypothesis
  falsified (the wall was tail-load serialization; prefetch-before-
  grid-sync is the fix and the generalizable idiom). Upstream GRAPH_OPT
  interleave bug found+fixed (+19% base-model lever). Next wall priced:
  allocator-level stream concurrency (≈800 prize). 27B flagship gains
  pending Q8_0-factor kernel path (Phase 2B — since landed, see above;
  same-session A/B proved current no-op for Q8 factors, no regression).
- **ProjQ-deflated re-round: theory confirmed** (25.94 vs naive 26.80);
  alternation retains the co-design crown (25.27).
- **E14-cond (task conditioning): +10.3% on-distribution at equal bytes**
  (code-conditioned 2.746 vs wiki-conditioned 3.061 on code); steep
  off-distribution cost; per-workload adapters = product candidate.
- **E17 reconstruction lane: kill-test SURVIVED** (per-code biases
  1.5-2.5% of grid step, systematic across kinds); context-modeling and
  storage-coding lanes measured dead/parked by the sweep. LUT rides the
  fused kernel next.
- **beastrank.py: the method as one command** — smoke-proven end-to-end
  (0.6B → 1.81x vs Q8 ref in ~6 min, capture 0.77).

## The laws and observations the campaign measured
## (retitled 2026-08-04 — item 1 was retracted to an observation by
## this file's own correction 5; coherence-audit P1.3)

1. **Capture-vs-width OBSERVATION (two points, diag r64): capture fell
   ~5× as width grew ~5×** (0.37 @d1024 → 0.07 @d5120). NOT a law: the
   fitted constant varies ~9.6–20.8 (0.36–0.60×d/r) across whiteners
   and bases in our own data; a regression across the E21 sweep + 0.8B
   + cached 27B spectra is queued (ABLATION-PLAN T1.10) before any
   functional form is claimed. ["Governs every result above" struck —
   it was retracted by correction 5 above.]
2. **Bandwidth inversion** — smaller weights decode FASTER at batch 1
   (99.7 vs 61 tok/s); compute is the free resource, accuracy the only
   price (Max's compute-rich/memory-poor thesis, measured).
3. **Joint shaping > greedy composition** — alternation beats
   best-standalone-base at equal bytes, twice measured.
4. **Metric quality gates everything** — diag → fullcov whitening was
   worth more than doubling rank at 0.6B; per-kind measured sensitivity
   beats energy water-filling (which is HARMFUL).
5. **Every lever shrinks with scale** — correction, re-rounding,
   alternation all decay from 0.6B to 27B; mature imatrix quants sit
   near their local optimum.

## Artifacts ready for daylight

- Upstream drafts: `upstream/` — three files, all now
  CONFIRMATION-COMMENTS, not new issues (per correction 10):
  `issue-draft-mtp-imatrix-blindspot.md` (IQ2-blocks-MTP folded in),
  `issue-draft-lora-decode-overhead.md` (+ fusion sketch),
  `issue-draft-training-kv-backprop.md`. [relisted 2026-08-04 —
  the old list named a file that doesn't exist and missed one;
  coherence-audit P3.3]
- E13 re-rounder RFC case: 0.6B −27% KLD free; needs Q3_K/Q4_K codecs +
  act-order for the full story.
- Fused-kernel design with sparse-A lever: `prior-art/arxiv-kernels.md`
  + localization audit (factors on 2–5% of channels).
- Paper outline rev 4: `paper/OUTLINE.md`; chronicle: `JOURNAL.md`.
