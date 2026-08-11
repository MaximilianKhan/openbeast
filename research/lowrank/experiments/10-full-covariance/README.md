> ⚠ see review/ corrections 2026-08-04

# E10 — full-covariance whitening: the paradigm's second act

E04c/E07/E09 established the boundary: DIAGONAL-whitened correction
plateaus (27B KLD ~0.14 vs Q3_K_M 0.0555) with rising-but-insufficient
recovery toward the cliff (7.5%→18%). QERA's ablations + the physics
sweep's information-geometry lane both point the same way: the metric is
the bottleneck, not the rank. Full covariance S = E[xxᵀ] sees
cross-channel structure that diag(E[x²]) cannot.

## Instrumentation (the campaign's first llama.cpp C++ modification)

Local research patch to `tools/imatrix/imatrix.cpp` (env-gated, NOT
upstream-shaped yet; AGENTS.md honored — private-checkout work, any
upstream version goes through Max with an RFC first):

- `LLAMA_IMATRIX_GRAM_DIR=<dir>` enables accumulation of activation
  Grams G = Σ x·xᵀ alongside the normal imatrix.
- One Gram per DISTINCT layer input (attn_k covers q/k/v; ffn_up covers
  gate/up; attn_output and ffn_down their own) — 4 per layer.
- `n > LLAMA_IMATRIX_GRAM_MAXDIM` (default 8192) → block-diagonal, 8
  blocks (ffn_down's 25600-dim input → 8×3200; the "nobody has
  published block-diagonal activation-Gram approximation" open lane).
- fp32 accumulation, hand-rolled threaded rank-k update (no BLAS dep),
  written at each imatrix save: raw fp32 + `grams.json` manifest.

**Validation (0.6B, 40 chunks):** diag(G)/count matches the standard
imatrix to median rel err 2e-8; exact symmetry; PSD (no negative
eigenvalues). Extractor side: damped Cholesky (λ = 0.01·mean diag —
CLoQ/GPTQ convention), whiten Rd[:, block] = R[:, block] @ L_b, then the
usual projection form (B = U_r of Rd, A = BᵀR — optimality proof holds
for any invertible whitener, D or L).

## Checkpoint result — 0.6B, Q2_K-imat base, r64, identical 80.8 MB

| whitening | PPL | mean KLD | top-1 | capture |
|---|---|---|---|---|
| diagonal (imatrix) | 31.29 | 0.420 | 68.5% | 0.37 |
| **full covariance** | **27.58** | **0.318** | **72.9%** | **0.60** |

Full covariance at rank 64 beats DOUBLE-rank diagonal adapters
(r128-Q8: 28.46; r128-F16: 28.48) at half their rank. The metric was
the bottleneck, exactly as prescribed. Novelty, corrected (review F13):
full-covariance-weighted objectives at 2-bit on 7B+ ARE published
(CALDERA; LQER is the diagonal counterpart) — what the 27B run below is
first at is a *same-pipeline* full-vs-diagonal ablation on llama.cpp
K-quants, nothing wider.

## 27B main event

Grams capturing (32 chunks). Then: full-cov r64(+Q8) adapters for BOTH
bases (Q2_K, IQ2_XS), byte-fair against Q3_K_M and the rung above each.
Targets to beat: diagonal's 0.1415 (Q2_K base) and 0.2175 (IQ2_XS base);
rung-jump thresholds: 0.153 (Q2_K bare, for IQ2_XS+adapter at fewer
bytes) and 0.0555 (Q3_K_M, for Q2_K+adapter at fewer bytes).

## Result — 27B round 1 (partial coverage, 16k tokens): incremental

fc-Q2K+r64: KLD 0.1387/85.1% (best 27B yet; diag-alloc was 0.1415);
fc-IQ2+r64: 0.2047/81.3% (recovery 22.5%, up from 18%). No rung jump.
Diagnosis: hybrid architecture (48/64 layers are linear-attention;
their attn_qkv/attn_gate/ssm_out inputs weren't captured → fell back to
diagonal) + thin calibration. v2 recapture with full coverage + 64
chunks in flight.

## Result — 0.6B composition ladder (the frontier bends)

Composed stack = full-covariance x alternation x Q8 factors, vs Q3_K_M
(347.1 MB, PPL 26.06, KLD 0.2378, top-1 76.2%):

| config | total MB | PPL | mean KLD | top-1 |
|---|---|---|---|---|
| fcalt2 + r64-Q8 | 339.2 (-2.3%) | 26.58 | — | — |
| **fcalt2 + r96-Q8** | **360.6 (+3.9%)** | **25.87** | **0.2386** | **76.1%** |
| fcalt2 + r128-Q8 | 382.1 (+10%) | **25.27** | **0.2086** | **77.9%** |
| fc + r128-Q8 (no alt) | 382.1 (+10%) | 25.99 | 0.2464 | 75.7% |

**First configurations non-dominated by the DISCRETE ladder — a
win-from-below** (review 2026-08-04: no interpolation control was built;
the ladder's own interpolation at 382 MB ≈ PPL 24.0 / KLD ≈ 0.118 beats
these points on both metrics, and Q4_K_M at +3.9% bytes over the crown
is 3.2× better on KLD). At +4% bytes: PPL point-estimate win (0.58σ,
post-hoc r96), KLD dead heat. At +10%: win on both metrics vs the rung
BELOW. ~85% of the Q2->Q8 PPL gap recovered. Each lever contributes
measurably (alternation alone: 0.246 -> 0.209 KLD at fixed rank/bytes).
Serving tax, unpriced above: on stock llama.cpp the adapter path costs
−33..−41% decode that the bare rungs don't pay (near-tax-free only on
our unmerged fused kernels). The 0.6B question, restated honestly: the
correction paradigm creates points above the rung below it, inside the
347–397 MB gap — with the right metric, co-design, and factor density.
The 27B verdict now rests on v2 coverage + calibration.

## Result — 27B endgame (v2 grams: full hybrid coverage, 64 chunks)

All KLD/top-1 vs the Q6 production reference (20-chunk logits); serving
at -c 8192 on the RTX 5090:

| config | total GB | PPL20 | KLD | top-1 | notes |
|---|---|---|---|---|---|
| fcv2-Q2K+r64 | 11.73 | 7.817 | 0.1282 | 85.4% | vs diag 0.1460 |
| fcv2-Q2K+r128q8 | 11.79 | 7.789 | 0.1217 | 86.1% | |
| + alternation round | 11.79 | 7.707 | 0.1142 | 86.1% | -6%/round |
| fcv2-IQ2+r128q8 | 10.31 | 8.349 | 0.1905 | 82.8% | recovery 28% |
| IQ3_XXS (rival) | 11.48 | 7.512 | 0.0962 | 87.9% | |
| **MIXED+fc (geometry)** | **12.50** | **7.313** | **0.0812** | **88.0%** | 15.0 GB VRAM, 86.3 tok/s |
| Q3_K_S (rival) | 12.37 | 7.486 | 0.0872 | 88.0% | ~1σ, see note |
| IQ3_XS (rival) | 12.26 | 7.275 | 0.0656 | 89.4% | still ahead |
| MIXED2+fc (IQ-ffn) | 11.70 | 7.439 | 0.0970 | 87.8% | ≈ IQ3_XXS at +2% bytes |

(Review 2026-08-04, table notes: the 86.3 tok/s cell is boost drift —
the same-session A/B measured 82.3–82.4, so quote 82.4 or remeasure
locked-clock. The Q3_K_S margin is KLD Δ = 1.0σ / PPL 0.46σ / top-1
exactly tied — parity, not a win. MIXED2 vs IQ3_XXS is more bytes at
slightly worse KLD — matches-within-noise, not a tie in our favor. "fc"
on 27B rows means full covariance at ≤8192-dim inputs and
block-diagonal-8 for ffn_down's 25600-dim input.)

MIXED = geometry-aware base (FFN at Q3_K where r/d dooms correction,
Q2_K elsewhere) + fc r128-Q8 correction on non-FFN only (336 MB, capture
0.42 — the campaign's highest at 27B).

**27B verdict, complete and honest:**
1. Full covariance + coverage + calibration ground the Q2_K-base KLD
   from 0.1460 (diagonal day-1) to 0.1142 (fc+alternation) — a 22%
   improvement arc with every step measured.
2. **The geometry-aware composition reaches ~1σ parity with Q3_K_S at
   27B** (MIXED+fc: KLD Δ = 1.0σ, PPL 0.46σ, top-1 exactly tied, at
   +1% MORE file bytes) — statistically indistinguishable, not a
   victory (review 2026-08-04, replacing "first 27B victory"). VRAM
   caveat: the win is claimed on file bytes, but the mission metric is
   VRAM, where the adapter path inflates overhead — MIXED+fc serves at
   15.0 GB VRAM while Q3_K_M at 15.3 GB VRAM crushes it on quality
   (0.0555 vs 0.0812), and Q3_K_S is itself dominated by IQ3_XS inside
   this very table. Parity with the ladder from a corrected 2-bit base
   is still the finding.
3. **The I-quant family remains the 27B frontier by a hair**: IQ3_XS
   wins at fewer bytes; MIXED2 (I-quant FFN) + correction ties IQ3_XXS,
   showing corrected-Q2_K ≈ IQ3-codebook efficiency on equal tensors —
   parity, not superiority, against llama.cpp's best.
4. The r/d law ruled every outcome at both scales, in both directions:
   correction wins where rank is a large fraction of dimension (0.6B
   globally; 27B k/v and hybrid tensors), and cannot beat dense
   codebooks where it is not (27B FFN). "Correct where geometry favors
   correction, spend base bits where it doesn't" is the design rule the
   whole campaign distilled — and the paper's central prescription.
