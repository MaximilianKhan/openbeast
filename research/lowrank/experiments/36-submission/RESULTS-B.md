# E36-B results — 27B re-rounder, single-step provenance, b10865, 100 chunks per corpus

Base `h27bf16-Q2_K.gguf` (11,003,736,448 B; E27). Re-round arms differ only in Gram calibration text (fresh post-fix captures, 48 chunks): wiki.train vs mixed-calib2 (2:1 wiki:code). IQ2_M 10,296,664,480 B; IQ3_XXS 11,478,442,368 B. Rules: PLAN.md §B. Raw: results-b.txt (+ results-a.txt for the shared rows); logs in logs-a/.

| file | KLD wiki | KLD fw | KLD code |
|---|---|---|---|
| IQ2_M | 0.1710 | 0.1314 | 0.1340 |
| Q2_K, as written | 0.1581 | 0.1182 | 0.1188 |
| Q2_K re-rounded, wiki Grams | 0.1523 | 0.1359 | 0.1515 |
| Q2_K re-rounded, 2:1 Grams | 0.1671 | 0.1465 | 0.1130 |
| IQ3_XXS | 0.1077 | 0.0710 | 0.0855 |

## wiki

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: Q2Krr-wiki vs Q2_K | 100 | -0.00582 ± 0.00311 | -1.87 | 0.0646 | [-0.01239, -0.00017] | 56/100 | 0.2713 | 0.0477 | [-0.01229, +0.00021] | -1.71 (df 9, p 0.1210) | +0.13 |
| wiki: Q2Krr-mixed vs Q2_K | 100 | +0.00904 ± 0.00282 | +3.21 | 0.0018 | [+0.00345, +0.01472] | 30/100 | 7.9e-05 | 3.5e-05 | [+0.00632, +0.01146] | +6.42 (df 9, p 0.00012) | +0.03 |
| wiki: Q2Krr-mixed vs IQ2_M | 100 | -0.00390 ± 0.00394 | -0.99 | 0.3241 | [-0.01142, +0.00394] | 56/100 | 0.2713 | 0.0813 | [-0.00859, +0.00142] | -1.44 (df 9, p 0.1827) | -0.02 |
| wiki: Q2Krr-mixed vs IQ3_XXS | 100 | +0.05941 ± 0.00357 | +16.62 | 2.1e-30 | [+0.05252, +0.06646] | 2/100 | 8e-27 | 5.6e-17 | [+0.05449, +0.06451] | +22.24 (df 9, p 3.6e-09) | -0.03 |
| wiki: Q2Krr-wiki vs IQ2_M | 100 | -0.01876 ± 0.00417 | -4.50 | 1.9e-05 | [-0.02718, -0.01076] | 76/100 | 1.8e-07 | 1.9e-07 | [-0.02820, -0.00933] | -3.64 (df 9, p 0.0054) | +0.12 |

- wiki: Q2Krr-wiki vs Q2_K: ΔNLL +0.02095 ± 0.00565 (t +3.71, A better 36/100); Δtop-1 +0.298 pt (t +1.31); max |Δ ref NLL| 0.00201
- wiki: Q2Krr-mixed vs Q2_K: ΔNLL +0.04204 ± 0.00726 (t +5.79, A better 20/100); Δtop-1 -0.600 pt (t -2.56); max |Δ ref NLL| 0.00204
- wiki: Q2Krr-mixed vs IQ2_M: ΔNLL +0.00438 ± 0.00707 (t +0.62, A better 48/100); Δtop-1 +0.180 pt (t +0.64); max |Δ ref NLL| 0.00194
- wiki: Q2Krr-mixed vs IQ3_XXS: ΔNLL +0.03762 ± 0.01146 (t +3.28, A better 20/100); Δtop-1 -3.655 pt (t -12.27); max |Δ ref NLL| 0.00162
- wiki: Q2Krr-wiki vs IQ2_M: ΔNLL -0.01672 ± 0.00613 (t -2.73, A better 59/100); Δtop-1 +1.078 pt (t +4.10); max |Δ ref NLL| 0.00164

## fw

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fw: Q2Krr-wiki vs Q2_K | 100 | +0.01766 ± 0.00227 | +7.79 | 6.8e-12 | [+0.01323, +0.02213] | 15/100 | 4.8e-13 | 5.7e-13 | [+0.01370, +0.02141] | +8.41 (df 9, p 1.5e-05) | +0.07 |
| fw: Q2Krr-mixed vs Q2_K | 100 | +0.02828 ± 0.00213 | +13.25 | 1.2e-23 | [+0.02435, +0.03264] | 3/100 | 2.6e-25 | 6.1e-18 | [+0.02473, +0.03203] | +14.45 (df 9, p 1.6e-07) | +0.09 |
| fw: Q2Krr-mixed vs IQ2_M | 100 | +0.01506 ± 0.00266 | +5.66 | 1.5e-07 | [+0.00993, +0.02033] | 21/100 | 4.3e-09 | 2e-09 | [+0.00896, +0.02125] | +4.63 (df 9, p 0.0012) | +0.15 |
| fw: Q2Krr-mixed vs IQ3_XXS | 100 | +0.07554 ± 0.00415 | +18.20 | 2.3e-33 | [+0.06783, +0.08401] | 0/100 | 1.6e-30 | 3.9e-18 | [+0.06884, +0.08255] | +20.26 (df 9, p 8.1e-09) | +0.23 |
| fw: Q2Krr-wiki vs IQ2_M | 100 | +0.00444 ± 0.00327 | +1.36 | 0.1775 | [-0.00234, +0.01037] | 43/100 | 0.1933 | 0.0658 | [-0.00390, +0.01106] | +1.11 (df 9, p 0.2957) | +0.18 |

- fw: Q2Krr-wiki vs Q2_K: ΔNLL +0.02162 ± 0.00377 (t +5.73, A better 31/100); Δtop-1 -1.243 pt (t -6.16); max |Δ ref NLL| 0.00142
- fw: Q2Krr-mixed vs Q2_K: ΔNLL +0.02967 ± 0.00382 (t +7.77, A better 22/100); Δtop-1 -1.698 pt (t -8.16); max |Δ ref NLL| 0.00176
- fw: Q2Krr-mixed vs IQ2_M: ΔNLL +0.01711 ± 0.00478 (t +3.58, A better 39/100); Δtop-1 -0.529 pt (t -2.14); max |Δ ref NLL| 0.00155
- fw: Q2Krr-mixed vs IQ3_XXS: ΔNLL +0.07090 ± 0.00576 (t +12.30, A better 6/100); Δtop-1 -5.368 pt (t -19.22); max |Δ ref NLL| 0.00202
- fw: Q2Krr-wiki vs IQ2_M: ΔNLL +0.00906 ± 0.00503 (t +1.80, A better 46/100); Δtop-1 -0.074 pt (t -0.31); max |Δ ref NLL| 0.00202

## code

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| code: Q2Krr-wiki vs Q2_K | 100 | +0.03279 ± 0.00307 | +10.69 | 3.5e-18 | [+0.02699, +0.03903] | 6/100 | 2e-21 | 4.9e-17 | [+0.02605, +0.03882] | +9.55 (df 9, p 5.3e-06) | -0.04 |
| code: Q2Krr-mixed vs Q2_K | 100 | -0.00573 ± 0.00302 | -1.90 | 0.0604 | [-0.01135, +0.00046] | 62/100 | 0.0210 | 0.0033 | [-0.01139, +0.00086] | -1.72 (df 9, p 0.1188) | +0.02 |
| code: Q2Krr-mixed vs IQ2_M | 100 | -0.02094 ± 0.00325 | -6.44 | 4.3e-09 | [-0.02719, -0.01434] | 77/100 | 5.5e-08 | 1.9e-09 | [-0.02706, -0.01460] | -6.16 (df 9, p 0.00017) | +0.17 |
| code: Q2Krr-mixed vs IQ3_XXS | 100 | +0.02754 ± 0.00341 | +8.09 | 1.6e-12 | [+0.02130, +0.03460] | 14/100 | 8.3e-14 | 2.6e-14 | [+0.01999, +0.03555] | +6.57 (df 9, p 0.0001) | +0.20 |
| code: Q2Krr-wiki vs IQ2_M | 100 | +0.01758 ± 0.00314 | +5.59 | 2e-07 | [+0.01166, +0.02410] | 29/100 | 3.2e-05 | 2.5e-07 | [+0.01049, +0.02479] | +4.49 (df 9, p 0.0015) | +0.18 |

- code: Q2Krr-wiki vs Q2_K: ΔNLL +0.02868 ± 0.00375 (t +7.65, A better 24/100); Δtop-1 -0.973 pt (t -6.23); max |Δ ref NLL| 0.00496
- code: Q2Krr-mixed vs Q2_K: ΔNLL -0.00290 ± 0.00329 (t -0.88, A better 57/100); Δtop-1 +0.231 pt (t +1.72); max |Δ ref NLL| 0.00683
- code: Q2Krr-mixed vs IQ2_M: ΔNLL -0.01633 ± 0.00474 (t -3.45, A better 66/100); Δtop-1 +0.702 pt (t +4.32); max |Δ ref NLL| 0.00578
- code: Q2Krr-mixed vs IQ3_XXS: ΔNLL +0.02339 ± 0.00463 (t +5.06, A better 26/100); Δtop-1 -1.012 pt (t -6.42); max |Δ ref NLL| 0.00870
- code: Q2Krr-wiki vs IQ2_M: ΔNLL +0.01525 ± 0.00423 (t +3.61, A better 36/100); Δtop-1 -0.502 pt (t -3.27); max |Δ ref NLL| 0.00427

## Verdicts (registered rules)

- **B1 FAILS.** rr-wiki − Q2_K on wiki = −0.0058 (−3.7%), t = −1.87, 56/100: the legacy-provenance −13.3% (t = −7.0) does not replicate under single-step provenance on the current build.
- **B2: wiki-only calibration harms both held-out corpora** (fw +0.0177, t = +7.79; code +0.0328, t = +10.69).
- **B3 FAILS.** rr-mixed − Q2_K: wiki +0.0090 (t = +3.21), fw +0.0283 (t = +13.25), code −0.0057 (t = −1.90). The fixed deployable recipe is worse than the file as written on two of three corpora.
- **B4.** rr-mixed vs IQ2_M (0.71 GB smaller): wiki tie (t = −0.99), fw worse (+5.66), code better (−6.44); vs IQ3_XXS (0.47 GB larger): worse on all three (t = +16.6, +18.2, +8.1).

## Diagnostics B5 and B6 (PLAN.md amendments 20:40, 20:55)

| wiki: Q2K-oracleQ8swept vs Q2_K | 100 | -0.12373 ± 0.00574 | -21.57 | 3.4e-39 | [-0.13559, -0.11345] | 100/100 | 1.6e-30 | 3.9e-18 | [-0.13587, -0.11285] | -19.86 (df 9, p 9.7e-09) | +0.19 |
| wiki: Q2Krr-oldgram vs Q2_K | 100 | -0.00804 ± 0.00280 | -2.87 | 0.0050 | [-0.01380, -0.00281] | 66/100 | 0.0018 | 0.0017 | [-0.01164, -0.00481] | -4.46 (df 9, p 0.0016) | -0.01 |
| wiki: Q2Krr-oldgram vs Q2Krr-wiki | 100 | -0.00222 ± 0.00200 | -1.11 | 0.2705 | [-0.00582, +0.00206] | 58/100 | 0.1332 | 0.0151 | [-0.00616, +0.00218] | -0.99 (df 9, p 0.3473) | +0.06 |
| fw: Q2K-oracleQ8swept vs Q2_K | 100 | -0.09487 ± 0.00675 | -14.05 | 2.7e-25 | [-0.10976, -0.08347] | 100/100 | 1.6e-30 | 3.9e-18 | [-0.10687, -0.08380] | -15.22 (df 9, p 9.9e-08) | +0.15 |
| fw: Q2Krr-oldgram vs Q2_K | 100 | +0.02015 ± 0.00228 | +8.83 | 3.8e-14 | [+0.01584, +0.02484] | 11/100 | 2.5e-16 | 2.2e-15 | [+0.01605, +0.02419] | +9.11 (df 9, p 7.7e-06) | +0.13 |
| fw: Q2Krr-oldgram vs Q2Krr-wiki | 100 | +0.00249 ± 0.00114 | +2.18 | 0.0318 | [+0.00034, +0.00479] | 39/100 | 0.0352 | 0.0360 | [+0.00055, +0.00446] | +2.37 (df 9, p 0.0422) | -0.07 |
| code: Q2K-oracleQ8swept vs Q2_K | 100 | -0.08978 ± 0.00591 | -15.18 | 1.3e-27 | [-0.10183, -0.07841] | 100/100 | 1.6e-30 | 3.9e-18 | [-0.10487, -0.07318] | -10.49 (df 9, p 2.4e-06) | +0.37 |
| code: Q2Krr-oldgram vs Q2_K | 100 | +0.03545 ± 0.00344 | +10.29 | 2.5e-17 | [+0.02904, +0.04268] | 8/100 | 3.2e-19 | 4.7e-16 | [+0.02698, +0.04298] | +8.19 (df 9, p 1.8e-05) | +0.01 |
| code: Q2Krr-oldgram vs Q2Krr-wiki | 100 | +0.00266 ± 0.00221 | +1.20 | 0.2326 | [-0.00159, +0.00700] | 46/100 | 0.4841 | 0.2727 | [-0.00075, +0.00671] | +1.33 (df 9, p 0.2166) | +0.04 |

- **B5 headroom.** With every kind the sweep covers at Q8_0 (22.4 GB, bytes not comparable), KLD falls from 0.158/0.118/0.119 to 0.034/0.023/0.029: the swept tensors carry 78%/80%/76% of the bare file's KLD. The wiki-calibrated re-round removed 0.0058 of that 0.124 on wiki (5%) while halving the swept tensors' whitened error (checks/rr27b-independent-dequant.txt).
- **B6.** Pre-fix E27 Grams on the same single-step base: wiki −0.0080 (t = −2.87) vs bare, but not resolved against the post-fix-Gram arm (−0.0022, t = −1.11) → registered reading: the capture graph does not explain the gap to the legacy result; provenance does (legacy source and Grams were Q6_K-derived). Off-distribution it harms as the other arms do (fw +8.83, code +10.29).
