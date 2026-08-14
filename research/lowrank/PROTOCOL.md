# beast-rank methodology v2 — the ground-up rerun protocol
# (post-adversarial-review, 2026-08-04; applies to ALL new results)

Ground-up rebuild order: **Qwen3.5-0.8B (BF16 reference) first**, then
upward (27B re-derived from its BF16, then MoE 35B, then the target
class). Every rerun follows these standards or is not a result.

## Compression reporting standard (Max directive 2026-08-04d)
0. The compression score is ALWAYS stated against the specifically
   TARGETED weights: C = bytes(target reference) / bytes(our artifact,
   base+adapter), file and VRAM both. BF16 multiples are context only
   and never headline. Every score is immediately paired with its
   EQUIVALENT-SIZE quantization comparators: the nearest ladder rungs
   above and below, plus the interpolation control — each with quality.
   A compression score without its same-size quant comparator is not a
   result.

## Reference & provenance
1. Reference = the BF16 artifact; every base/adapter derives from it in
   ONE quantization step. No requant-of-requant. Mixed-provenance rows
   never share a table without a provenance column.
2. Cache dirs (spectra, codes, grams) carry a provenance fingerprint
   (hash of ref+base+gram identity); consumers refuse mismatches.

## Metrics (report ALL, always with uncertainty)
3. KLD + top-1 vs reference logits (>=40 chunks) with printed stderr;
   PPL (full corpus) secondary; near-baseline comparisons carry the
   KLD-silent-zone caveat and require a task eval before "parity+"
   language.
4. **Paired per-chunk statistics for every comparison within 2σ
   unpaired** — report mean±sem of the per-chunk DIFFERENCE.
5. Speed: same-session paired A/B only; boost drift documented; stock
   vs fused-kernel numbers labeled; serving tax ALWAYS priced next to
   any byte-fair quality claim.

## Comparisons
6. Byte-fair = total bytes (base+adapter) AND equal-VRAM stated; the
   ladder gets its full lineup (K + IQ + interpolation CONTROL — a
   mixed-quant build at the matched byte point) at every scale.
7. Calibration on wikitext-2-train; eval on wikitext-2-test AND one
   held-out corpus (code-test or other); generality claims need both.
8. Selection discipline: sweeps pre-register their grid; post-hoc best
   points are labeled exploratory until re-measured. **Amendment
   2026-08-04 (round-2 experiments review F12): pre-registration must
   be a TIMESTAMPED entry (one line each: config, cap, damping, grid)
   written to the append-only JOURNAL BEFORE the run** — self-attested
   "pre-registered" in a post-run README is unverifiable and drifted
   twice (M4/M5 pre-specified 0.6B, ran 0.8B, no deviation note).
   Deviations from the registered spec get their own line.

## Claims
9. Novelty claims require a tracker/GitHub sweep (not just arXiv).
10. "Law" requires >=3 scale points + stated functional form + fit
    quality; otherwise "observation".
11. Every experiment README: hypothesis -> method -> result -> what
    would falsify it; raw output files retained next to the README.

## The rerun matrix (0.8B, BF16-pure, review-compliant)
## (statuses updated 2026-08-04 per coherence-audit P2.8)
R1 ladder + interpolation control + vendor row [partially done]
R2 corrections: diag/fullcov/two-sided x {r64,r128q8} [partially done]
R3 alternation x2 + paired stats [round 1 done, unpaired]
R4 re-round: input-only vs YAQA-lite [DONE — E24, the §3 flagship
   measurement, paired throughout]
R5 rank sweep 32..256 (repeat of E21 on 0.8B, BF16 refs)
R6 serving: fused/unfused tok/s + VRAM per config, same-session
R7 held-out corpus + one task eval [first held-out pass DONE for the
   re-rounder — anti-generalization finding + confounded mitigation,
   see DEPLOYABLE-WINS; task eval still OPEN]
R8 locked-clock serving columns: nvidia-smi -lgc, interleaved A/B,
   N=10, median±IQR, quiet host [defined 2026-08-04 — MASTER-TABLE
   cited a dangling "R8"; coherence-audit P3.2]
Then: 27B-from-BF16 re-derivation of the frontier configs only
   [DONE — E27, 2026-08-04 evening].
