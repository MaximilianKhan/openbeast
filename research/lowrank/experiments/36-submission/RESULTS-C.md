# E36-C results — KLD at a 2048-token context, Qwen3.8-27B, wikitext-2 test, 40 chunks x 2048

Generated 2026-10-15 by e36_stats.py from logs-b/ctx2048-*.log. Raw rows: results-c.txt. Rule: PLAN.md §C. Every scored token has >= 1024 tokens of context.

| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of 10) | t on block means | lag-1 r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ctx2048: gsq-rco-iq2xs vs unsloth-ud-iq2s | 40 | +0.05694 ± 0.00709 | +8.03 | 8.6e-10 | [+0.04450, +0.07201] | 0/40 | 1.8e-12 | 1.8e-12 | [+0.04451, +0.07168] | +7.42 (df 3, p 0.0051) | -0.01 |
| ctx2048: gsq-rco-iq3s vs unsloth-ud-iq3s | 40 | +0.01780 ± 0.00354 | +5.02 | 1.2e-05 | [+0.01222, +0.02567] | 1/40 | 7.5e-11 | 3.6e-12 | [+0.01432, +0.02251] | +7.11 (df 3, p 0.0057) | -0.08 |
| ctx2048: ours-iq2s-fc128 vs unsloth-ud-iq2s | 40 | -0.01138 ± 0.00171 | -6.67 | 6.1e-08 | [-0.01515, -0.00836] | 37/40 | 1.9e-08 | 5.6e-10 | [-0.01501, -0.00917] | -6.30 (df 3, p 0.0081) | -0.32 |
| ctx2048: ours-iq3s-fc128 vs unsloth-ud-iq3s | 40 | -0.00263 ± 0.00039 | -6.83 | 3.7e-08 | [-0.00343, -0.00193] | 37/40 | 1.9e-08 | 2.7e-09 | [-0.00281, -0.00245] | -22.64 (df 3, p 0.00019) | +0.09 |
| ctx2048: ours-q2kxl-fc128 vs unsloth-ud-q2kxl | 40 | -0.00808 ± 0.00154 | -5.26 | 5.6e-06 | [-0.01123, -0.00528] | 39/40 | 7.5e-11 | 5e-07 | [-0.00943, -0.00647] | -8.79 (df 3, p 0.0031) | -0.01 |
| ctx2048: ours-iq2s-fc128 vs gsq-rco-iq2xs | 40 | -0.06832 ± 0.00768 | -8.89 | 6.3e-11 | [-0.08451, -0.05502] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.08125, -0.05628] | -8.79 (df 3, p 0.0031) | -0.05 |
| ctx2048: ours-iq3s-fc128 vs gsq-rco-iq3s | 40 | -0.02043 ± 0.00361 | -5.65 | 1.6e-06 | [-0.02842, -0.01461] | 40/40 | 1.8e-12 | 1.8e-12 | [-0.02537, -0.01673] | -7.85 (df 3, p 0.0043) | -0.08 |
| ctx2048: ours-iq2s-fc128 vs unsloth-ud-q2kxl | 40 | +0.03541 ± 0.00387 | +9.14 | 3.1e-11 | [+0.02877, +0.04358] | 2/40 | 1.5e-09 | 1.3e-11 | [+0.02856, +0.04334] | +7.71 (df 3, p 0.0045) | +0.13 |

- ctx2048: gsq-rco-iq2xs vs unsloth-ud-iq2s: ΔNLL -0.02454 ± 0.01492 (t -1.64, A better 19/40); Δtop-1 -2.366 pt (t -8.88); max |Δ ref NLL| 0.00077
- ctx2048: gsq-rco-iq3s vs unsloth-ud-iq3s: ΔNLL -0.01791 ± 0.00718 (t -2.49, A better 26/40); Δtop-1 -1.527 pt (t -9.51); max |Δ ref NLL| 0.00078
- ctx2048: ours-iq2s-fc128 vs unsloth-ud-iq2s: ΔNLL -0.01898 ± 0.00397 (t -4.78, A better 37/40); Δtop-1 +0.711 pt (t +6.37); max |Δ ref NLL| 0.00067
- ctx2048: ours-iq3s-fc128 vs unsloth-ud-iq3s: ΔNLL -0.00025 ± 0.00115 (t -0.22, A better 20/40); Δtop-1 +0.252 pt (t +3.47); max |Δ ref NLL| 0.00062
- ctx2048: ours-q2kxl-fc128 vs unsloth-ud-q2kxl: ΔNLL -0.00770 ± 0.00292 (t -2.63, A better 34/40); Δtop-1 +0.594 pt (t +6.04); max |Δ ref NLL| 0.00071
- ctx2048: ours-iq2s-fc128 vs gsq-rco-iq2xs: ΔNLL +0.00556 ± 0.01177 (t +0.47, A better 27/40); Δtop-1 +3.077 pt (t +12.04); max |Δ ref NLL| 0.00049
- ctx2048: ours-iq3s-fc128 vs gsq-rco-iq3s: ΔNLL +0.01766 ± 0.00726 (t +2.43, A better 15/40); Δtop-1 +1.779 pt (t +10.94); max |Δ ref NLL| 0.00038
- ctx2048: ours-iq2s-fc128 vs unsloth-ud-q2kxl: ΔNLL +0.03357 ± 0.00770 (t +4.36, A better 6/40); Δtop-1 -1.777 pt (t -10.16); max |Δ ref NLL| 0.00090

## Verdict (registered rule)

GSQ-RCO − UD: t = +8.03 (IQ2), +5.02 (IQ3) ≥ +2 → KLD still disagrees with PPL@2048 and the suite at a 2048-token context: the disagreement is the metric, not context length. In the same run on the same tokens, GSQ-RCO's NLL is lower (t = −1.64, −2.49) while its KLD to BF16 is higher and its top-1 agreement lower (t ≈ −9): the trained files depart from the reference distribution while fitting the observed text better.
