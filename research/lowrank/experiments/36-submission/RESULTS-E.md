# E36-E results — free-grid GPTQ vs frozen-grid re-rounding (identical format and bytes)

Rule: PLAN.md §E (no direction predicted). Raw: results-d.txt (0.8B, 40 chunks), results-b.txt (27B, 100 chunks).

## wiki

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: Q2K-freegrid-wiki vs Q2K-rr-wiki | 40 | -0.01694 ± 0.00374 | -4.53 | 5.4e-05 | [-0.02432, -0.00962] | 28/40 | 0.0166 | 8.8e-05 | [-0.02124, -0.01192] | -5.78 (df 3, p 0.0103) | -0.12 |
| wiki: Q2K-freegrid-mix2 vs Q2K-rr-mix2 | 40 | -0.01672 ± 0.00460 | -3.63 | 0.00081 | [-0.02533, -0.00759] | 26/40 | 0.0807 | 0.0008 | [-0.02450, -0.01010] | -4.05 (df 3, p 0.0272) | +0.20 |
| wiki: Q2K-freegrid-wiki vs Q2_K | 40 | -0.18015 ± 0.00729 | -24.72 | 1.9e-25 | [-0.19444, -0.16616] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.19575, -0.16041] | -18.10 (df 3, p 0.00037) | +0.18 |
| wiki: Q2K-freegrid-mix2 vs Q2_K | 40 | -0.13775 ± 0.00599 | -23.01 | 2.7e-24 | [-0.14925, -0.12600] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.15116, -0.12434] | -17.34 (df 3, p 0.00042) | +0.20 |

- wiki: Q2K-freegrid-wiki vs Q2K-rr-wiki: ΔNLL -0.00770 ± 0.00977 (t -0.79, A better 18/40); Δtop-1 +0.255 pt (t +0.66); max |Δ ref NLL| 0.00045
- wiki: Q2K-freegrid-mix2 vs Q2K-rr-mix2: ΔNLL -0.02230 ± 0.01095 (t -2.04, A better 22/40); Δtop-1 +0.980 pt (t +2.63); max |Δ ref NLL| 0.00040
- wiki: Q2K-freegrid-wiki vs Q2_K: ΔNLL -0.17474 ± 0.01370 (t -12.76, A better 39/40); Δtop-1 +6.726 pt (t +14.65); max |Δ ref NLL| 0.00038
- wiki: Q2K-freegrid-mix2 vs Q2_K: ΔNLL -0.14048 ± 0.01223 (t -11.48, A better 39/40); Δtop-1 +5.853 pt (t +14.63); max |Δ ref NLL| 0.00050

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| wiki: Q2Kfreegrid-mixed vs Q2Krr-mixed | 100 | -0.00947 ± 0.00294 | -3.22 | 0.0017 | [-0.01488, -0.00351] | 73/100 | 4.7e-06 | 1.3e-05 | [-0.01309, -0.00593] | -4.91 (df 9, p 0.00084) | -0.05 |
| wiki: Q2Kfreegrid-mixed vs Q2_K | 100 | -0.00043 ± 0.00232 | -0.19 | 0.8532 | [-0.00490, +0.00412] | 48/100 | 0.7644 | 0.9342 | [-0.00491, +0.00401] | -0.18 (df 9, p 0.8627) | -0.03 |

- wiki: Q2Kfreegrid-mixed vs Q2Krr-mixed: ΔNLL -0.00060 ± 0.00716 (t -0.08, A better 61/100); Δtop-1 +0.777 pt (t +3.49); max |Δ ref NLL| 0.00140
- wiki: Q2Kfreegrid-mixed vs Q2_K: ΔNLL +0.04144 ± 0.00916 (t +4.52, A better 25/100); Δtop-1 +0.177 pt (t +0.73); max |Δ ref NLL| 0.00174

## fw

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fw: Q2K-freegrid-wiki vs Q2K-rr-wiki | 40 | -0.02789 ± 0.00508 | -5.49 | 2.6e-06 | [-0.03772, -0.01809] | 32/40 | 0.00018 | 2.7e-06 | [-0.03538, -0.02040] | -6.18 (df 3, p 0.0085) | -0.16 |
| fw: Q2K-freegrid-mix2 vs Q2K-rr-mix2 | 40 | -0.02111 ± 0.00582 | -3.62 | 0.00083 | [-0.03241, -0.00997] | 24/40 | 0.2682 | 0.0037 | [-0.02893, -0.01006] | -3.54 (df 3, p 0.0383) | +0.01 |
| fw: Q2K-freegrid-wiki vs Q2_K | 40 | -0.13710 ± 0.00873 | -15.71 | 1.9e-18 | [-0.15425, -0.12067] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.15122, -0.12299] | -15.67 (df 3, p 0.00057) | +0.28 |
| fw: Q2K-freegrid-mix2 vs Q2_K | 40 | -0.08978 ± 0.00877 | -10.23 | 1.3e-12 | [-0.10716, -0.07338] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.10569, -0.07145] | -8.43 (df 3, p 0.0035) | +0.22 |

- fw: Q2K-freegrid-wiki vs Q2K-rr-wiki: ΔNLL -0.04355 ± 0.00927 (t -4.70, A better 33/40); Δtop-1 +0.549 pt (t +1.74); max |Δ ref NLL| 0.00036
- fw: Q2K-freegrid-mix2 vs Q2K-rr-mix2: ΔNLL -0.02208 ± 0.01057 (t -2.09, A better 26/40); Δtop-1 +1.019 pt (t +2.05); max |Δ ref NLL| 0.00049
- fw: Q2K-freegrid-wiki vs Q2_K: ΔNLL -0.15801 ± 0.01457 (t -10.85, A better 39/40); Δtop-1 +3.784 pt (t +9.44); max |Δ ref NLL| 0.00039
- fw: Q2K-freegrid-mix2 vs Q2_K: ΔNLL -0.09728 ± 0.01247 (t -7.80, A better 38/40); Δtop-1 +2.078 pt (t +4.46); max |Δ ref NLL| 0.00037

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fw: Q2Kfreegrid-mixed vs Q2Krr-mixed | 100 | -0.00588 ± 0.00223 | -2.64 | 0.0097 | [-0.01025, -0.00159] | 63/100 | 0.0120 | 0.00023 | [-0.01159, -0.00109] | -2.09 (df 9, p 0.0666) | +0.08 |
| fw: Q2Kfreegrid-mixed vs Q2_K | 100 | +0.02240 ± 0.00274 | +8.16 | 1.1e-12 | [+0.01726, +0.02798] | 8/100 | 3.2e-19 | 1.6e-14 | [+0.01655, +0.02755] | +7.55 (df 9, p 3.5e-05) | +0.16 |

- fw: Q2Kfreegrid-mixed vs Q2Krr-mixed: ΔNLL -0.00472 ± 0.00375 (t -1.26, A better 55/100); Δtop-1 +0.078 pt (t +0.37); max |Δ ref NLL| 0.00216
- fw: Q2Kfreegrid-mixed vs Q2_K: ΔNLL +0.02495 ± 0.00340 (t +7.34, A better 24/100); Δtop-1 -1.620 pt (t -7.37); max |Δ ref NLL| 0.00164

## code

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| code: Q2K-freegrid-wiki vs Q2K-rr-wiki | 40 | -0.05841 ± 0.00796 | -7.33 | 7.5e-09 | [-0.07368, -0.04368] | 36/40 | 1.9e-07 | 5e-09 | [-0.08045, -0.03617] | -4.25 (df 3, p 0.0238) | -0.12 |
| code: Q2K-freegrid-mix2 vs Q2K-rr-mix2 | 40 | -0.02316 ± 0.00310 | -7.47 | 4.9e-09 | [-0.02921, -0.01748] | 36/40 | 1.9e-07 | 1.6e-07 | [-0.02805, -0.01827] | -8.01 (df 3, p 0.0041) | +0.01 |
| code: Q2K-freegrid-wiki vs Q2_K | 40 | +0.09233 ± 0.00968 | +9.54 | 9.6e-12 | [+0.07439, +0.11190] | 0/40 | 1.8e-12 | 1.8e-12 | [+0.08068, +0.10926] | +10.42 (df 3, p 0.0019) | +0.20 |
| code: Q2K-freegrid-mix2 vs Q2_K | 40 | -0.15665 ± 0.00997 | -15.71 | 1.8e-18 | [-0.17598, -0.13790] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.18342, -0.13179] | -9.70 (df 3, p 0.0023) | +0.22 |

- code: Q2K-freegrid-wiki vs Q2K-rr-wiki: ΔNLL -0.07107 ± 0.01303 (t -5.46, A better 31/40); Δtop-1 +1.314 pt (t +3.39); max |Δ ref NLL| 0.00110
- code: Q2K-freegrid-mix2 vs Q2K-rr-mix2: ΔNLL -0.01387 ± 0.00763 (t -1.82, A better 24/40); Δtop-1 +0.362 pt (t +0.95); max |Δ ref NLL| 0.00135
- code: Q2K-freegrid-wiki vs Q2_K: ΔNLL +0.06445 ± 0.01379 (t +4.67, A better 5/40); Δtop-1 -1.578 pt (t -3.32); max |Δ ref NLL| 0.00069
- code: Q2K-freegrid-mix2 vs Q2_K: ΔNLL -0.16540 ± 0.01345 (t -12.29, A better 40/40); Δtop-1 +4.549 pt (t +9.22); max |Δ ref NLL| 0.00079

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| code: Q2Kfreegrid-mixed vs Q2Krr-mixed | 100 | -0.00456 ± 0.00249 | -1.83 | 0.0697 | [-0.00951, +0.00021] | 57/100 | 0.1933 | 0.0954 | [-0.00839, -0.00039] | -2.11 (df 9, p 0.0640) | +0.05 |
| code: Q2Kfreegrid-mixed vs Q2_K | 100 | -0.01029 ± 0.00252 | -4.08 | 9.2e-05 | [-0.01526, -0.00528] | 73/100 | 4.7e-06 | 1.2e-05 | [-0.01695, -0.00370] | -2.84 (df 9, p 0.0193) | +0.07 |

- code: Q2Kfreegrid-mixed vs Q2Krr-mixed: ΔNLL -0.00711 ± 0.00367 (t -1.94, A better 63/100); Δtop-1 +0.287 pt (t +2.12); max |Δ ref NLL| 0.00590
- code: Q2Kfreegrid-mixed vs Q2_K: ΔNLL -0.01001 ± 0.00371 (t -2.70, A better 59/100); Δtop-1 +0.518 pt (t +3.15); max |Δ ref NLL| 0.00779

## Verdict

Free-grid − frozen-grid: 0.8B wiki Grams −0.017/−0.028/−0.058 (t = −4.5/−5.5/−7.3), 2:1 Grams −0.017/−0.021/−0.023 (t = −3.6/−3.6/−7.5); 27B 2:1 Grams −0.0095/−0.0059/−0.0046 (t = −3.22/−2.64/−1.83). Refitting the grid beats freezing it everywhere it resolves. Per the registration, the method is reframed as GPTQ on GGUF formats with the frozen grid as its zero-search special case. At 27B the free-grid file is still level with the file as written on wiki (t = −0.19), worse on fw (+8.16) and better on code (−4.08).
