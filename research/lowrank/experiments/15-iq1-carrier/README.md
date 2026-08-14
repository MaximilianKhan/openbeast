> ⚠ see review/ corrections 2026-08-04

# E15 — the IQ1_S carrier: Max's dream in servable form

The founding vision was pure factorization: thin factors that multiply
back into the matrices. E01 measured that dead in pure form, so this is
the closest stock-servable approximation: a nearly-free carrier (IQ1_S,
1.56 bpw — the thinnest quant llama.cpp has) with correction doing the
maximum share of the representational work.

## Setup

- Carrier: IQ1_S requant (imatrix, MTP layer pinned q5_k) = 7.44 GB.
- Correction: fc mixed-rank (non-FFN r192, FFN r64), Q8 factors, full
  covariance (gram27b-v2) = 799 MB. Total **8.24 GB**.

## Result (KLD/top-1 vs Q6 reference)

| config | GB | PPL20 | KLD | top-1 |
|---|---|---|---|---|
| IQ1_S bare | 7.44 | 16.42 | 0.891 | 61.9% |
| **IQ1_S + fc-mix** | **8.24** | **11.55** | **0.527** | **70.4%** |
| (next usable config up: IQ2_XS+fc) | 10.25 | 8.40 | 0.218 | 80.6% |

**Two findings:**
1. **The cliff gradient's most dramatic point: 41% of the damage
   recovered** (0.891 -> 0.527). Raw curve vs base bitwidth: 7.5%
   (Q2_K, DIAGONAL alloc-Q8) -> 18% (IQ2_XS, DIAGONAL) -> 41% (IQ1_S,
   full-cov mixed-rank) — but those points mix whitening methods
   (review 2026-08-04): held to one estimator (fc), the curve is 20%
   (Q2_K) -> 28% (IQ2_XS) -> 41% (IQ1_S) — still monotone toward the
   cliff, ~3× shallower at the left end than the mixed-method version.
   Paper figure F7 must plot the method-consistent series (both,
   labeled by whitening, if the diagonal points stay).
2. **8.24 GB (file) is a frontier point among the rungs we built** —
   no MEASURED rung below IQ2_XS produces usable output at 27B, but
   the equal-byte rivals IQ1_M (~8.1 GB) and IQ2_XXS (~8.9 GB) were
   never run, and 8.24 GB is file bytes, not VRAM: by the measured
   adapter overhead pattern this build serves at roughly 10.3–10.5 GB
   VRAM (unmeasured) — NOT inside a 10 GB card (review 2026-08-04).
   Quality is rough (70% top-1); servable by stock llama.cpp today,
   at the stock adapter path's decode tax.

## The asymptote test — ANSWERED (block 5, BF16-pure residuals)

Rank scaled to the freed byte budget (non-FFN r512, FFN r256, Q8):

| config | GB | PPL20 | KLD | top-1 | recovery |
|---|---|---|---|---|---|
| IQ1_S bare | 7.44 | 16.42 | 0.891 | 61.9% | — |
| + fc-mix (0.80 GB) | 8.24 | 11.55 | 0.527 | 70.4% | 41% |
| + fc-BIG (2.52 GB, r512) | 9.96 | 10.50 | 0.431 | 73.3% | 52% |
| Q2_K bare (the rival) | 10.86 | 7.891 | 0.153 | 83.6% | — |

**Factors cannot substitute for carrier bits — measured to the end.**
The recovery fraction keeps climbing (41% -> 52%) but the exchange rate
is brutal and worsening: the adapter's second+third GB bought 0.10 KLD
where a GB of carrier bits buys ~0.28. At near-equal total bytes the
cheap-carrier route loses to plain Q2_K by ~3x KLD, with every lever we
own engaged (full covariance, BF16-pure residuals, Q8 factors, r512 —
whitened capture still only 0.56 at rank 512). Paper figure F7 is
complete: the founding pure-factorization dream has its epitaph, and
the geometry rule (spend bytes as carrier where r/d is unfavorable)
stands as the campaign's constructive answer.
