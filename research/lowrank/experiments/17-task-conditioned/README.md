# E14-cond — task-conditioned correction (dir: 17-task-conditioned)

The functional-rank sweep's designed experiment (its "E14"): the
surviving form of pure factorization is CONDITIONAL — fix the target
distribution and functional rank collapses. Test: does conditioning the
correction's covariance to the target distribution buy on-distribution
quality at identical bytes?

## Method (0.6B, fully offline)

- Corpora from our own repos (openbeast + llama.cpp sources): 3.0 MB
  code-train / 0.4 MB code-test, disjoint.
- Same base for all cells: Q2_K-imat (wiki-quantized — only the
  CORRECTION conditions). Two fc r64 F16 adapters, identical bytes:
  wiki-Grams (gram06b) vs code-Grams (gram06b-code, 40 chunks).
- 2x2 + bare PPL matrix.

## Result

| adapter \ eval | wikitext | code |
|---|---|---|
| bare | 43.33 | 3.794 |
| wiki-cond fc r64 | **27.58** | 3.061 |
| code-cond fc r64 | 34.90 | **2.746** |

1. **Conditioning pays: +10.3% on-distribution** (2.746 vs 3.061) — the
   code-conditioned adapter recovers 43% more of the bare gap on code.
   The correction subspace is measurably distribution-specific.
2. **Specialization costs off-distribution**: code-cond on wiki = 34.90
   (vs 27.58) — this is a trade, not a free lunch.
3. Asymmetric generalization: wiki stats transfer to code far better
   than code stats to wiki (breadth of the calibration distribution
   matters — matches SliceGPT's Alpaca>>WikiText healing observation).

## Product implication (OpenBeast)

Per-workload correction adapters at zero serving-stack cost: a
code-serving deployment ships a code-conditioned adapter for ~10% extra
on-task quality at the same bytes; llama.cpp's per-request lora API
means beast-slot could select the adapter per client workload. Queued
as a beast-slot enhancement candidate.

## Paper role

The conditional-factorization section's empirical anchor: the general
dream is dead (quadruple triangulation, prior-art/arxiv-functional-rank-
healing.md), the conditional form is alive and cheap to exploit.
