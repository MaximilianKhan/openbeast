# E36-A results — 27B ladder, b10865, three corpora (100 chunks each)

Generated 2026-10-15 from logs-a/ by e36_stats.py. Raw rows: results-a.txt. Rules: PLAN.md §A.

## wiki

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: MIXEDfc vs Q3_K_S | 100 | -0.00468 ± 0.00178 | -2.62 | 0.0101 | [-0.00799, -0.00109] | 71/100 | 3.2e-05 | 2.2e-05 | [-0.00814, -0.00087] | -2.39 (df 9, p 0.0404) | +0.02 |
| wiki: MIXEDfc vs CONTROL | 100 | +0.00502 ± 0.00211 | +2.37 | 0.0195 | [+0.00114, +0.00935] | 40/100 | 0.0569 | 0.0014 | [+0.00171, +0.00906] | +2.49 (df 9, p 0.0345) | -0.04 |
| wiki: MIXEDfc vs MIXEDbare | 100 | -0.01078 ± 0.00104 | -10.41 | 1.4e-17 | [-0.01287, -0.00885] | 96/100 | 6.4e-24 | 1.8e-15 | [-0.01289, -0.00887] | -9.97 (df 9, p 3.7e-06) | -0.05 |
| wiki: CONTROL vs Q3_K_S | 100 | -0.00970 ± 0.00112 | -8.64 | 1e-13 | [-0.01189, -0.00750] | 91/100 | 3.3e-18 | 1.9e-14 | [-0.01173, -0.00745] | -8.37 (df 9, p 1.5e-05) | +0.06 |
| wiki: MIXEDbare vs Q3_K_S | 100 | +0.00610 ± 0.00209 | +2.93 | 0.0043 | [+0.00225, +0.01037] | 31/100 | 0.00018 | 9.7e-05 | [+0.00184, +0.01035] | +2.62 (df 9, p 0.0280) | -0.01 |
| wiki: IQ3_XS vs MIXEDfc | 100 | -0.00971 ± 0.00271 | -3.58 | 0.00053 | [-0.01516, -0.00454] | 83/100 | 1.3e-11 | 5.7e-09 | [-0.01569, -0.00376] | -2.99 (df 9, p 0.0151) | -0.01 |
| wiki: IQ3_XS vs Q3_K_S | 100 | -0.01439 ± 0.00226 | -6.36 | 6.3e-09 | [-0.01855, -0.00979] | 88/100 | 1.9e-15 | 1.6e-12 | [-0.01972, -0.00848] | -4.69 (df 9, p 0.0011) | -0.00 |
| wiki: Q2Krr-legacy vs Q2_K | 100 | -0.01961 ± 0.00324 | -6.05 | 2.6e-08 | [-0.02650, -0.01377] | 78/100 | 1.6e-08 | 1.8e-10 | [-0.02430, -0.01457] | -7.51 (df 9, p 3.6e-05) | -0.08 |
| wiki: IQ3_XXS vs Q2_K | 100 | -0.05037 ± 0.00311 | -16.17 | 1.5e-29 | [-0.05668, -0.04451] | 98/100 | 8e-27 | 6.7e-18 | [-0.05386, -0.04683] | -26.99 (df 9, p 6.4e-10) | -0.10 |

- wiki: MIXEDfc vs Q3_K_S: ΔNLL -0.02377 ± 0.00410 (t -5.79, A better 77/100); Δtop-1 +0.467 pt (t +2.50); max |Δ ref NLL| 0.00133
- wiki: MIXEDfc vs CONTROL: ΔNLL +0.00071 ± 0.00343 (t +0.21, A better 53/100); Δtop-1 -0.345 pt (t -1.73); max |Δ ref NLL| 0.00192
- wiki: MIXEDfc vs MIXEDbare: ΔNLL -0.00524 ± 0.00220 (t -2.38, A better 64/100); Δtop-1 +0.800 pt (t +5.21); max |Δ ref NLL| 0.00153
- wiki: CONTROL vs Q3_K_S: ΔNLL -0.02448 ± 0.00293 (t -8.36, A better 91/100); Δtop-1 +0.812 pt (t +5.79); max |Δ ref NLL| 0.00231
- wiki: MIXEDbare vs Q3_K_S: ΔNLL -0.01854 ± 0.00476 (t -3.89, A better 69/100); Δtop-1 -0.333 pt (t -1.80); max |Δ ref NLL| 0.00280
- wiki: IQ3_XS vs MIXEDfc: ΔNLL +0.01389 ± 0.00575 (t +2.42, A better 42/100); Δtop-1 +0.788 pt (t +3.51); max |Δ ref NLL| 0.00224
- wiki: IQ3_XS vs Q3_K_S: ΔNLL -0.00988 ± 0.00588 (t -1.68, A better 61/100); Δtop-1 +1.255 pt (t +5.72); max |Δ ref NLL| 0.00159
- wiki: Q2Krr-legacy vs Q2_K: ΔNLL +0.02509 ± 0.00782 (t +3.21, A better 41/100); Δtop-1 +1.024 pt (t +4.35); max |Δ ref NLL| 0.00142
- wiki: IQ3_XXS vs Q2_K: ΔNLL +0.00443 ± 0.01201 (t +0.37, A better 62/100); Δtop-1 +3.055 pt (t +9.82); max |Δ ref NLL| 0.00131

## fw

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fw: MIXEDfc vs Q3_K_S | 100 | +0.00479 ± 0.00147 | +3.26 | 0.0015 | [+0.00211, +0.00781] | 40/100 | 0.0569 | 0.0221 | [+0.00239, +0.00726] | +3.65 (df 9, p 0.0053) | +0.06 |
| fw: MIXEDfc vs CONTROL | 100 | +0.01083 ± 0.00167 | +6.48 | 3.5e-09 | [+0.00779, +0.01434] | 10/100 | 3.1e-17 | 8.4e-15 | [+0.00828, +0.01357] | +7.61 (df 9, p 3.3e-05) | +0.05 |
| fw: MIXEDfc vs MIXEDbare | 100 | -0.00452 ± 0.00052 | -8.63 | 1e-13 | [-0.00554, -0.00349] | 91/100 | 3.3e-18 | 7.8e-15 | [-0.00540, -0.00371] | -10.04 (df 9, p 3.5e-06) | +0.04 |
| fw: CONTROL vs Q3_K_S | 100 | -0.00604 ± 0.00045 | -13.37 | 6.5e-24 | [-0.00692, -0.00520] | 98/100 | 8e-27 | 2.3e-17 | [-0.00705, -0.00499] | -10.76 (df 9, p 1.9e-06) | +0.08 |
| fw: MIXEDbare vs Q3_K_S | 100 | +0.00931 ± 0.00163 | +5.70 | 1.2e-07 | [+0.00635, +0.01266] | 23/100 | 5.5e-08 | 3.6e-10 | [+0.00653, +0.01207] | +6.22 (df 9, p 0.00015) | +0.05 |
| fw: IQ3_XS vs MIXEDfc | 100 | -0.01692 ± 0.00198 | -8.55 | 1.5e-13 | [-0.02090, -0.01330] | 97/100 | 2.6e-25 | 9.8e-17 | [-0.02044, -0.01371] | -9.42 (df 9, p 5.9e-06) | +0.12 |
| fw: IQ3_XS vs Q3_K_S | 100 | -0.01213 ± 0.00135 | -8.97 | 1.9e-14 | [-0.01467, -0.00947] | 94/100 | 2e-21 | 2.2e-15 | [-0.01412, -0.00947] | -9.82 (df 9, p 4.2e-06) | +0.17 |
| fw: Q2Krr-legacy vs Q2_K | 100 | +0.01623 ± 0.00205 | +7.93 | 3.4e-12 | [+0.01239, +0.02046] | 13/100 | 1.3e-14 | 2.6e-13 | [+0.01288, +0.01979] | +8.40 (df 9, p 1.5e-05) | +0.08 |
| fw: IQ3_XXS vs Q2_K | 100 | -0.04726 ± 0.00301 | -15.72 | 1.2e-28 | [-0.05346, -0.04177] | 99/100 | 1.6e-28 | 4.3e-18 | [-0.05319, -0.04158] | -15.22 (df 9, p 9.9e-08) | +0.16 |

- fw: MIXEDfc vs Q3_K_S: ΔNLL -0.00090 ± 0.00241 (t -0.37, A better 54/100); Δtop-1 -0.176 pt (t -0.93); max |Δ ref NLL| 0.00171
- fw: MIXEDfc vs CONTROL: ΔNLL +0.00658 ± 0.00227 (t +2.89, A better 39/100); Δtop-1 -0.800 pt (t -4.20); max |Δ ref NLL| 0.00147
- fw: MIXEDfc vs MIXEDbare: ΔNLL -0.00414 ± 0.00098 (t -4.24, A better 68/100); Δtop-1 +0.216 pt (t +1.75); max |Δ ref NLL| 0.00225
- fw: CONTROL vs Q3_K_S: ΔNLL -0.00748 ± 0.00110 (t -6.81, A better 79/100); Δtop-1 +0.624 pt (t +4.92); max |Δ ref NLL| 0.00152
- fw: MIXEDbare vs Q3_K_S: ΔNLL +0.00325 ± 0.00242 (t +1.34, A better 43/100); Δtop-1 -0.392 pt (t -2.04); max |Δ ref NLL| 0.00210
- fw: IQ3_XS vs MIXEDfc: ΔNLL -0.02165 ± 0.00402 (t -5.38, A better 68/100); Δtop-1 +1.925 pt (t +8.80); max |Δ ref NLL| 0.00133
- fw: IQ3_XS vs Q3_K_S: ΔNLL -0.02255 ± 0.00371 (t -6.08, A better 67/100); Δtop-1 +1.749 pt (t +7.30); max |Δ ref NLL| 0.00141
- fw: Q2Krr-legacy vs Q2_K: ΔNLL +0.01952 ± 0.00347 (t +5.63, A better 28/100); Δtop-1 -1.204 pt (t -6.21); max |Δ ref NLL| 0.00157
- fw: IQ3_XXS vs Q2_K: ΔNLL -0.04122 ± 0.00433 (t -9.52, A better 83/100); Δtop-1 +3.670 pt (t +14.08); max |Δ ref NLL| 0.00156

## code

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| code: MIXEDfc vs Q3_K_S | 100 | +0.00324 ± 0.00174 | +1.86 | 0.0662 | [-0.00015, +0.00670] | 39/100 | 0.0352 | 0.0360 | [-0.00203, +0.00791] | +1.20 (df 9, p 0.2607) | +0.16 |
| code: MIXEDfc vs CONTROL | 100 | +0.01126 ± 0.00158 | +7.11 | 1.8e-10 | [+0.00825, +0.01446] | 20/100 | 1.1e-09 | 1.6e-10 | [+0.00740, +0.01502] | +5.54 (df 9, p 0.00036) | +0.18 |
| code: MIXEDfc vs MIXEDbare | 100 | -0.00509 ± 0.00078 | -6.48 | 3.5e-09 | [-0.00668, -0.00365] | 81/100 | 2.7e-10 | 1.3e-10 | [-0.00647, -0.00389] | -7.24 (df 9, p 4.8e-05) | +0.03 |
| code: CONTROL vs Q3_K_S | 100 | -0.00802 ± 0.00116 | -6.90 | 5e-10 | [-0.01046, -0.00584] | 81/100 | 2.7e-10 | 3.1e-11 | [-0.01174, -0.00505] | -4.40 (df 9, p 0.0017) | +0.14 |
| code: MIXEDbare vs Q3_K_S | 100 | +0.00833 ± 0.00193 | +4.32 | 3.7e-05 | [+0.00463, +0.01213] | 30/100 | 7.9e-05 | 8.6e-06 | [+0.00313, +0.01342] | +3.02 (df 9, p 0.0145) | +0.15 |
| code: IQ3_XS vs MIXEDfc | 100 | -0.01469 ± 0.00208 | -7.06 | 2.3e-10 | [-0.01889, -0.01076] | 81/100 | 2.7e-10 | 3.6e-11 | [-0.01960, -0.00934] | -5.28 (df 9, p 0.00051) | +0.15 |
| code: IQ3_XS vs Q3_K_S | 100 | -0.01145 ± 0.00240 | -4.77 | 6.3e-06 | [-0.01616, -0.00684] | 71/100 | 3.2e-05 | 2.4e-07 | [-0.01852, -0.00465] | -3.07 (df 9, p 0.0133) | +0.21 |
| code: Q2Krr-legacy vs Q2_K | 100 | +0.03768 ± 0.00301 | +12.53 | 3.8e-22 | [+0.03191, +0.04365] | 6/100 | 2e-21 | 3e-17 | [+0.03031, +0.04367] | +10.40 (df 9, p 2.6e-06) | +0.00 |
| code: IQ3_XXS vs Q2_K | 100 | -0.03327 ± 0.00303 | -11.00 | 7.4e-19 | [-0.03940, -0.02746] | 88/100 | 1.9e-15 | 4.5e-16 | [-0.03964, -0.02660] | -9.45 (df 9, p 5.7e-06) | +0.14 |

- code: MIXEDfc vs Q3_K_S: ΔNLL -0.00331 ± 0.00255 (t -1.29, A better 51/100); Δtop-1 +0.075 pt (t +0.61); max |Δ ref NLL| 0.00765
- code: MIXEDfc vs CONTROL: ΔNLL +0.00388 ± 0.00243 (t +1.60, A better 41/100); Δtop-1 -0.278 pt (t -2.30); max |Δ ref NLL| 0.00586
- code: MIXEDfc vs MIXEDbare: ΔNLL -0.00399 ± 0.00104 (t -3.83, A better 68/100); Δtop-1 +0.220 pt (t +3.45); max |Δ ref NLL| 0.00582
- code: CONTROL vs Q3_K_S: ΔNLL -0.00719 ± 0.00152 (t -4.72, A better 64/100); Δtop-1 +0.353 pt (t +4.00); max |Δ ref NLL| 0.00666
- code: MIXEDbare vs Q3_K_S: ΔNLL +0.00068 ± 0.00260 (t +0.26, A better 48/100); Δtop-1 -0.145 pt (t -1.24); max |Δ ref NLL| 0.00713
- code: IQ3_XS vs MIXEDfc: ΔNLL -0.01163 ± 0.00286 (t -4.06, A better 63/100); Δtop-1 +0.353 pt (t +2.15); max |Δ ref NLL| 0.00799
- code: IQ3_XS vs Q3_K_S: ΔNLL -0.01493 ± 0.00333 (t -4.49, A better 67/100); Δtop-1 +0.428 pt (t +2.72); max |Δ ref NLL| 0.00596
- code: Q2Krr-legacy vs Q2_K: ΔNLL +0.02815 ± 0.00347 (t +8.10, A better 19/100); Δtop-1 -0.922 pt (t -5.78); max |Δ ref NLL| 0.00753
- code: IQ3_XXS vs Q2_K: ΔNLL -0.02629 ± 0.00444 (t -5.93, A better 70/100); Δtop-1 +1.243 pt (t +7.38); max |Δ ref NLL| 0.00767


## Whole test set (amendment A-full, run 2026-10-16 12:00–12:36)

Registered in PLAN.md ("Amendment A-full") before the run. Same nine files,
ctx 512, fresh BF16 reference over all 580 chunks of wiki.test.raw (73.5 GB,
PPL 7.1964). Chunks 1–100 reproduce the n = 100 logs bit for bit (reference
NLL and per-chunk KLD max |Δ| = 0 for Q3_K_S, MIXEDfc, CONTROL). Inserted at
an F row boundary (after e36f-GSQ-IQ3S-r1); F resumed 12:36.

**Verdicts (registered rules):** C1 MIXEDfc vs Q3_K_S **HOLDS** (t = −4.32,
Bonferroni ×6 p = 1.1e-4); C2 CONTROL better than MIXEDfc **HOLDS**
(t = −5.50, adjusted p = 3.4e-7). Out-of-prefix replication (chunks 101–580):
both hold (t = −3.68, −4.96). Full-set and n = 100 verdicts agree.
Not registered, reported: the legacy re-rounded file's wiki lead over
single-step Q2_K is −0.0043 (t = −3.10) on 580 chunks and unresolved on
101–580 (t = −0.73); its NLL is worse (t = +11.19).

Rows (`results-afull.txt`):

```
wiki MIXEDfc bytes=12162412928 Mean PPL(Q) : 7.418640 ± 0.049554 Mean KLD: 0.090573 ± 0.000979 Same top p: 87.767 ± 0.085 %
wiki MIXEDbare bytes=12162412928 Mean PPL(Q) : 7.477301 ± 0.050070 Mean KLD: 0.101373 ± 0.001057 Same top p: 87.135 ± 0.087 %
wiki Q3_K_S bytes=12366025088 Mean PPL(Q) : 7.569533 ± 0.050944 Mean KLD: 0.094792 ± 0.001041 Same top p: 87.538 ± 0.086 %
wiki CONTROL bytes=12496933248 Mean PPL(Q) : 7.388198 ± 0.049256 Mean KLD: 0.085452 ± 0.000990 Same top p: 88.165 ± 0.084 %
wiki IQ3_XS bytes=12259201408 Mean PPL(Q) : 7.521864 ± 0.049664 Mean KLD: 0.079942 ± 0.000916 Same top p: 88.819 ± 0.082 %
wiki IQ3_XXS bytes=11478442368 Mean PPL(Q) : 7.824701 ± 0.053173 Mean KLD: 0.112577 ± 0.001195 Same top p: 86.701 ± 0.088 %
wiki Q3_K_M bytes=13593514368 Mean PPL(Q) : 7.321774 ± 0.048846 Mean KLD: 0.064954 ± 0.000882 Same top p: 89.757 ± 0.079 %
wiki Q2_K bytes=11003736448 Mean PPL(Q) : 7.762491 ± 0.052526 Mean KLD: 0.162657 ± 0.001339 Same top p: 83.574 ± 0.096 %
wiki Q2Krr-legacy bytes=10864595360 Mean PPL(Q) : 8.032175 ± 0.054954 Mean KLD: 0.158384 ± 0.001321 Same top p: 83.807 ± 0.096 %
```

### All 580 chunks

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: MIXEDfc vs Q3_K_S | 580 | -0.00422 ± 0.00098 | -4.32 | 1.8e-05 | [-0.00615, -0.00236] | 363/580 | 1.4e-09 | 5.4e-10 | [-0.00618, -0.00217] | -4.09 (df 57, p 0.00014) | +0.12 |
| wiki: CONTROL vs MIXEDfc | 580 | -0.00512 ± 0.00093 | -5.50 | 5.6e-08 | [-0.00689, -0.00330] | 371/580 | 1.7e-11 | 2e-17 | [-0.00693, -0.00327] | -5.46 (df 57, p 1.1e-06) | +0.07 |
| wiki: CONTROL vs Q3_K_S | 580 | -0.00934 ± 0.00062 | -14.98 | 4.2e-43 | [-0.01059, -0.00812] | 513/580 | 4.3e-86 | 1.4e-67 | [-0.01045, -0.00813] | -15.66 (df 57, p 2.6e-22) | +0.02 |
| wiki: IQ3_XS vs MIXEDfc | 580 | -0.01063 ± 0.00117 | -9.06 | 2.1e-18 | [-0.01295, -0.00835] | 447/580 | 1.2e-40 | 3.2e-39 | [-0.01283, -0.00844] | -9.48 (df 57, p 2.6e-13) | +0.02 |
| wiki: MIXEDfc vs MIXEDbare | 580 | -0.01080 ± 0.00054 | -19.84 | 3.1e-67 | [-0.01190, -0.00976] | 541/580 | 4.3e-114 | 1.2e-82 | [-0.01186, -0.00984] | -20.55 (df 57, p 5e-28) | -0.01 |
| wiki: MIXEDbare vs Q3_K_S | 580 | +0.00658 ± 0.00093 | +7.08 | 4.1e-12 | [+0.00479, +0.00832] | 171/580 | 1.8e-23 | 1.8e-22 | [+0.00476, +0.00845] | +6.97 (df 57, p 3.6e-09) | +0.13 |
| wiki: Q2Krr-legacy vs Q2_K | 580 | -0.00428 ± 0.00138 | -3.10 | 0.0020 | [-0.00699, -0.00154] | 350/580 | 7.1e-07 | 9.2e-07 | [-0.00812, -0.00035] | -2.17 (df 57, p 0.0341) | +0.14 |

- wiki: MIXEDfc vs Q3_K_S: ΔNLL -0.02014 ± 0.00184 (t -10.97, A better 406/580); Δtop-1 +0.229 pt (t +2.74); max |Δ ref NLL| 0.01544
- wiki: CONTROL vs MIXEDfc: ΔNLL -0.00411 ± 0.00182 (t -2.26, A better 295/580); Δtop-1 +0.398 pt (t +4.92); max |Δ ref NLL| 0.01467
- wiki: CONTROL vs Q3_K_S: ΔNLL -0.02424 ± 0.00124 (t -19.58, A better 505/580); Δtop-1 +0.627 pt (t +10.95); max |Δ ref NLL| 0.01447
- wiki: IQ3_XS vs MIXEDfc: ΔNLL +0.01383 ± 0.00249 (t +5.54, A better 246/580); Δtop-1 +1.052 pt (t +10.36); max |Δ ref NLL| 0.01369
- wiki: MIXEDfc vs MIXEDbare: ΔNLL -0.00788 ± 0.00094 (t -8.39, A better 413/580); Δtop-1 +0.632 pt (t +10.37); max |Δ ref NLL| 0.01210
- wiki: MIXEDbare vs Q3_K_S: ΔNLL -0.01226 ± 0.00187 (t -6.55, A better 336/580); Δtop-1 -0.403 pt (t -4.75); max |Δ ref NLL| 0.01048
- wiki: Q2Krr-legacy vs Q2_K: ΔNLL +0.03415 ± 0.00305 (t +11.19, A better 180/580); Δtop-1 +0.233 pt (t +2.35); max |Δ ref NLL| 0.01194

### Chunks 101–580 (`E36_SLICE=100:`)

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: MIXEDfc vs Q3_K_S | 480 | -0.00412 ± 0.00112 | -3.68 | 0.00026 | [-0.00630, -0.00198] | 292/480 | 2.4e-06 | 8.3e-07 | [-0.00639, -0.00178] | -3.48 (df 47, p 0.0011) | +0.13 |
| wiki: CONTROL vs MIXEDfc | 480 | -0.00514 ± 0.00104 | -4.96 | 9.6e-07 | [-0.00713, -0.00308] | 311/480 | 9e-11 | 4.4e-15 | [-0.00710, -0.00305] | -4.84 (df 47, p 1.4e-05) | +0.09 |
| wiki: CONTROL vs Q3_K_S | 480 | -0.00927 ± 0.00072 | -12.93 | 5.3e-33 | [-0.01067, -0.00789] | 422/480 | 2.8e-69 | 8.3e-55 | [-0.01051, -0.00788] | -13.55 (df 47, p 7.4e-18) | +0.02 |
| wiki: IQ3_XS vs MIXEDfc | 480 | -0.01082 ± 0.00130 | -8.31 | 9.9e-16 | [-0.01336, -0.00830] | 364/480 | 7.5e-31 | 7.6e-32 | [-0.01311, -0.00856] | -9.08 (df 47, p 6.6e-12) | +0.02 |
| wiki: MIXEDfc vs MIXEDbare | 480 | -0.01080 ± 0.00062 | -17.38 | 8.2e-53 | [-0.01204, -0.00961] | 445/480 | 1.3e-91 | 7.7e-69 | [-0.01200, -0.00971] | -18.05 (df 47, p 9e-23) | -0.01 |
| wiki: MIXEDbare vs Q3_K_S | 480 | +0.00668 ± 0.00104 | +6.45 | 2.8e-10 | [+0.00465, +0.00869] | 140/480 | 3e-20 | 4e-19 | [+0.00471, +0.00876] | +6.40 (df 47, p 6.6e-08) | +0.15 |
| wiki: Q2Krr-legacy vs Q2_K | 480 | -0.00109 ± 0.00148 | -0.73 | 0.4645 | [-0.00398, +0.00183] | 272/480 | 0.0040 | 0.0196 | [-0.00504, +0.00283] | -0.53 (df 47, p 0.5970) | +0.14 |

- wiki: MIXEDfc vs Q3_K_S: ΔNLL -0.01938 ± 0.00205 (t -9.47, A better 329/480); Δtop-1 +0.179 pt (t +1.92); max |Δ ref NLL| 0.01544
- wiki: CONTROL vs MIXEDfc: ΔNLL -0.00481 ± 0.00208 (t -2.32, A better 248/480); Δtop-1 +0.409 pt (t +4.62); max |Δ ref NLL| 0.01467
- wiki: CONTROL vs Q3_K_S: ΔNLL -0.02419 ± 0.00137 (t -17.70, A better 414/480); Δtop-1 +0.588 pt (t +9.40); max |Δ ref NLL| 0.01447
- wiki: IQ3_XS vs MIXEDfc: ΔNLL +0.01382 ± 0.00277 (t +4.99, A better 204/480); Δtop-1 +1.107 pt (t +9.76); max |Δ ref NLL| 0.01369
- wiki: MIXEDfc vs MIXEDbare: ΔNLL -0.00843 ± 0.00104 (t -8.13, A better 349/480); Δtop-1 +0.597 pt (t +9.00); max |Δ ref NLL| 0.01210
- wiki: MIXEDbare vs Q3_K_S: ΔNLL -0.01095 ± 0.00203 (t -5.40, A better 267/480); Δtop-1 -0.418 pt (t -4.39); max |Δ ref NLL| 0.01048
- wiki: Q2Krr-legacy vs Q2_K: ΔNLL +0.03604 ± 0.00331 (t +10.90, A better 139/480); Δtop-1 +0.068 pt (t +0.63); max |Δ ref NLL| 0.01194

## Chunk splits and cross-build agreement (computed 2026-10-22; quoted in paper §4.3)

Not a new run: the 100-chunk wiki logs of `logs-a/`, sliced. Commands:
`E36_SLICE=0:40 python3 e36_stats.py logs-a wiki MIXEDfc:Q3_K_S MIXEDfc:MIXEDbare MIXEDbare:Q3_K_S` and the same with `E36_SLICE=40:100`.

Chunks 1-40:

```
 wiki: MIXEDfc vs Q3_K_S | 40 | +0.00042 ± 0.00313 | +0.13 
 wiki: MIXEDfc vs MIXEDbare | 40 | -0.01057 ± 0.00134 | -7.91 
 wiki: MIXEDbare vs Q3_K_S | 40 | +0.01099 ± 0.00325 | +3.38 
```

Chunks 41-100:

```
 wiki: MIXEDfc vs Q3_K_S | 60 | -0.00808 ± 0.00202 | -3.99 
 wiki: MIXEDfc vs MIXEDbare | 60 | -0.01092 ± 0.00149 | -7.34 
 wiki: MIXEDbare vs Q3_K_S | 60 | +0.00284 ± 0.00266 | +1.07 
```

Per-chunk agreement of the pre-fix build (`../27-bf16-rederivation/kld100-*.log`) with b10865 (`logs-a/wiki-*.log`), first 100 chunks:

```
MIXEDfc - Q3_K_S: per-chunk ΔKLD, pre-fix (E27 logs) vs b10865 (logs-a), n=100: r = 0.870
MIXEDfc - CONTROL: per-chunk ΔKLD, pre-fix (E27 logs) vs b10865 (logs-a), n=100: r = 0.945
```
