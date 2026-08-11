> ⚠ see review/ corrections 2026-08-04

# E21 — incremental rank-response sweep (Max directive 2026-08-04c)

Question: does the system's geometry hide non-monotonic "landing spots"
in rank? Config: the 0.6B crown recipe (fcalt2 base + fc Q8 factors,
full covariance), r = 32..256 in steps of 32, full wikitext-2 PPL.

| r | adapter MB | total MB | PPL | marginal ΔPPL/32r |
|---|---|---|---|---|
| 32 | 21 | 317 | 31.06 | — |
| 64 | 42 | 339 | 26.58 | −4.49 |
| 96 | 64 | 360 | 25.87 | −0.71 |
| 128 | 85 | 382 | 25.27 | −0.60 |
| 160 | 107 | 403 | 24.85 | −0.42 |
| 192 | 128 | 424 | 24.51 | −0.34 |
| 224 | 150 | 446 | 24.24 | −0.27 |
| 256 | 171 | 467 | 23.95 | −0.29 |

**Answer: the geometry is SMOOTH** — at this measurement's resolution
(review 2026-08-04: single runs, σ ≈ 0.22–0.27; adjacent deltas from
r96 on are 1–2× the unpaired joint σ, so the fitted concave monotone
trend is credible but "no landing spots" below ~σ scale is unresolved).
No packing/occupancy artifacts in quality (serving is rank-flat after
kernel Phase 3 — on our UNMERGED kernels; the stock adapter path pays a
20–40% decode tax — so bytes are the only cost axis on this rig). Marginal gains track
the whitened spectrum tail — the r/d rule's local form. The only
structure is EXTERNAL: where the curve crosses quant-ladder rungs
(the Q3_K_M window 340-390 MB is ours vs the DISCRETE rungs only — the
ladder's own interpolation line still wins inside it, review F1;
Q4_K_M at 397 MB / PPL 23.14 reasserts above ~400 MB — r160+ does not
beat it byte-fair).

Implication for the method: rank selection is a smooth budget dial, not
a search problem — pick rank from the byte budget via the spectrum
(cache-based emit makes this free); no sweep needed in deployment.
Paper: this table IS figure F5's quality panel; "smoothness" is the
finding. 27B replication from spectra caches queued (expect the same by
the law).
