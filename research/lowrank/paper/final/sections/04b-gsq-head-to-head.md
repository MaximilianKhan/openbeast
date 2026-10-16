# 5. A head-to-head with released trained checkpoints

## 5.1 Setup, bytes and cost

GSQ-RCO (arXiv:2604.18556, arXiv:2605.00649) is released as refined GGUF checkpoints of Qwen3.8-27B. By its model card, every tensor is quantized with GSQ (Gumbel-Softmax re-selection of codes) at every candidate GGUF type, RCO selects one type per tensor under an exact byte budget, and the selections are assembled into a standard GGUF. The public GSQ repository contains no GGUF path, so we compare the released files and make no statement about the mechanism that produced them. The comparison was pre-registered on the Unsloth Dynamic baselines the card compares against, scored on 40 chunks of wikitext-2 test against BF16 reference logits, with a fresh 48-chunk BF16 imatrix and Gram capture [source: research/lowrank/experiments/32-t117-gsq-head-to-head/README.md, results-40ch.txt].

The three arms are not byte-matched, and every statement below should be read against this table:

| arm | IQ2 class | IQ3 class |
|---|---|---|
| GSQ-RCO, trained | IQ2_XS, 8.42 GB | IQ3_S, 11.77 GB |
| Unsloth baseline, untrained | UD-IQ2_S, 8.37 GB | UD-IQ3_S, 12.04 GB |
| baseline plus our correction | 9.27 GB (8.37 + 0.90 adapter) | 12.94 GB (12.04 + 0.90 adapter) |
| stock file near the corrected arm's bytes | UD-Q2_K_XL, 9.83 GB | none measured |

Our arm is a rank-128 full-covariance correction with Q8_0 factors on the Unsloth baseline. It is larger than the GSQ-RCO file by 0.85 GB in the IQ2 class and by 1.17 GB in the IQ3 class. The re-rounder was not used, because its codec does not cover the I-quant formats.

The cost of our arm, end to end: the extraction takes about 12 CPU-minutes per model (724, 727 and 744 seconds). That figure excludes obtaining the BF16 weights (54.7 GB) and the 48-chunk Gram capture, which needs our patched build and which the capture log estimated at about 22 minutes. The released checkpoints come from a GPU training run whose cost we did not measure [source: research/lowrank/experiments/32-t117-gsq-head-to-head/capture-gram38.log, extract-UD-IQ2_S.log].

## 5.2 512-token KL divergence on wikitext

Paired per-chunk differences, wikitext-2 test, n = 40 [source: research/lowrank/experiments/32-t117-gsq-head-to-head/paired-wikitext-40ch.txt; experiments/35-final-reanalysis/RESULTS.md]:

| pair (A − B) | ΔKLD | t | A better on | Wilcoxon p |
|---|---|---|---|---|
| GSQ-RCO IQ2_XS vs UD-IQ2_S (8.42 vs 8.37 GB) | +0.0715 ± 0.0061 | +11.68 | 0/40 | 2e-12 |
| GSQ-RCO IQ3_S vs UD-IQ3_S (11.77 vs 12.04 GB) | +0.0152 ± 0.0018 | +8.56 | 1/40 | 6e-11 |
| corrected vs own base, IQ2_S (+0.90 GB) | −0.0122 ± 0.0019 | −6.45 | 39/40 | 9e-12 |
| corrected vs own base, Q2_K_XL (+0.90 GB) | −0.0055 ± 0.0008 | −6.98 | 34/40 | 2e-8 |
| corrected vs own base, IQ3_S (+0.90 GB) | −0.0021 ± 0.0004 | −5.89 | 36/40 | 1e-6 |
| corrected IQ2_S vs GSQ-RCO IQ2_XS (9.27 vs 8.42 GB) | −0.0837 ± 0.0073 | −11.49 | 40/40 | 2e-12 |
| corrected IQ3_S vs GSQ-RCO IQ3_S (12.94 vs 11.77 GB) | −0.0173 ± 0.0018 | −9.48 | 38/40 | 9e-12 |
| corrected IQ2_S vs UD-Q2_K_XL (9.27 vs 9.83 GB) | +0.0337 ± 0.0029 | +11.59 | 0/40 | 2e-12 |

Three readings, in order of byte fairness. First, at matched bytes the untrained vendor baseline already has lower KLD than the trained checkpoint at both classes (rows 1-2). Second, our correction lowers its own base at all three rungs, at a cost of 0.90 GB (rows 3-5); top-1 agreement rises at the two 2-bit rungs (+0.7 points, t about +3.8) and not at IQ3_S (t = +0.5). Third, the gap between the corrected arm and GSQ-RCO (rows 6-7) is mostly the first effect: at the IQ2 class, 0.0715 of the 0.0837 (85%) is the baseline's lead and 0.0122 (15%) is the correction; at the IQ3 class, 0.0152 of 0.0173 (88%) and 0.0021 (12%).

Against the ladder the result is that of §4.3. The stock UD-Q2_K_XL file at 9.83 GB is better than the corrected IQ2_S arm at 9.27 GB by 0.0337 KLD (row 8). The two are 0.56 GB apart, so this is a near-equal-byte comparison and not an equal-byte one.

![Figure 2. Released GSQ-RCO checkpoints of Qwen3.8-27B against their untrained Unsloth baselines and against the baseline plus a one-shot whitened correction, on two corpora (40 chunks each). The corrected arm carries a 0.9 GB adapter and is not byte-matched to the other two.](figures/fig-gsq-kld.svg)

## 5.3 A second corpus, and both halves of a registered prediction

The wikitext result admits a calibration-mismatch reading: the released files may be tuned to text unlike wikitext. The calibration corpus of the released files is not documented. An earlier entry in our experiment log said they were trained on FineWeb-Edu; a check of the card and the released code the next day found no support for that, and the control was registered with the corrected wording: FineWeb-Edu is used because it is the one non-wikitext corpus the card reports on [source: research/lowrank/JOURNAL.md, 2026-09-11 11:00, 11:10].

The registration made a two-part prediction for the mismatch reading: the GSQ-RCO deficit against its baseline would shrink materially or change sign on FineWeb-Edu, and our own increments, whitened by wikitext Grams, would shrink too. The alternative reading predicted a GSQ-RCO deficit of t ≳ +4. The same eight contrasts on 40 chunks of FineWeb-Edu, against a BF16 reference on that text [source: research/lowrank/experiments/32-t117-gsq-head-to-head/paired-fineweb-40ch.txt; experiments/35-final-reanalysis/RESULTS.md]:

| pair (A − B), FineWeb-Edu, n = 40 | ΔKLD | t | A better on | Wilcoxon p |
|---|---|---|---|---|
| GSQ-RCO IQ2_XS vs UD-IQ2_S | +0.0073 ± 0.0044 | +1.66 | 13/40 | 0.022 |
| GSQ-RCO IQ3_S vs UD-IQ3_S | +0.0033 ± 0.0016 | +2.07 | 13/40 | 0.029 |
| corrected vs own base, IQ2_S | −0.0103 ± 0.0011 | −9.40 | 38/40 | 1e-11 |
| corrected vs own base, Q2_K_XL | −0.0052 ± 0.0006 | −9.20 | 38/40 | 5e-8 |
| corrected vs own base, IQ3_S | −0.0022 ± 0.0003 | −8.65 | 36/40 | 1e-7 |
| corrected IQ2_S vs GSQ-RCO IQ2_XS | −0.0175 ± 0.0045 | −3.94 | 35/40 | 1e-4 |
| corrected IQ3_S vs GSQ-RCO IQ3_S | −0.0055 ± 0.0016 | −3.39 | 31/40 | 3e-4 |
| corrected IQ2_S vs UD-Q2_K_XL | +0.0471 ± 0.0044 | +10.72 | 1/40 | 4e-12 |

The first half of the prediction held. The GSQ-RCO deficit falls about tenfold at the IQ2 class (+0.0715 to +0.0073) and fivefold at the IQ3 class (+0.0152 to +0.0033), to a difference that is unresolved by t at IQ2 and marginal at IQ3. On FineWeb-Edu the matched-byte comparison of trained checkpoint and untrained baseline is close to a tie.

The second half mostly did not hold. Our increments were predicted to shrink; the IQ2_S increment is 16% smaller (−0.0122 to −0.0103) and the other two are unchanged (−0.0055 to −0.0052; −0.0021 to −0.0022). The registered mismatch reading is therefore supported for the competitor's arm and not for ours, and we report it as a prediction that was half wrong. The useful consequence is a held-out measurement of the correction increment at 27B: a correction whitened on wikitext lowers its base on FineWeb-Edu by nearly the same amount.

With the baseline and the trained checkpoint close on this corpus, the corrected arm's lead over GSQ-RCO (−0.0175 and −0.0055) is about the size of the correction increment, which is bought with 0.9 GB. The stock UD-Q2_K_XL file again beats the corrected IQ2_S arm at near-equal bytes.

## 5.4 Context length against metric

The card's wikitext perplexities order GSQ-RCO ahead of the Unsloth baseline at both classes. At our 512-token context the paired NLL difference is unresolved: GSQ-RCO minus baseline +0.017 ± 0.013 (t = +1.33) at the IQ2 class and +0.003 ± 0.006 (t = +0.58) at the IQ3 class, n = 40. At a 2048-token context over the whole test set (145 chunks) the card's ordering reproduces: −0.041 ± 0.012 (t = −3.33) and −0.018 ± 0.004 (t = −4.15) [source: research/lowrank/experiments/32-t117-gsq-head-to-head/results-ppl2048.txt; experiments/35-final-reanalysis/RESULTS.md].

That left two explanations for the disagreement between 512-token KLD and the card: context length, or the metric itself. A registered run separates them. We generated BF16 reference logits at a 2048-token context (40 windows; llama-perplexity scores the second half of each window, so every scored token has at least 1,024 tokens of context) and scored all eight files on them, recording KLD, NLL and top-1 agreement from the same run on the same tokens [source: research/lowrank/experiments/36-submission/PLAN.md §C; experiments/36-submission/RESULTS-C.md]:

| GSQ-RCO − Unsloth baseline, 2048-token context, n = 40 | ΔKLD to BF16 | Δtop-1 agreement | ΔNLL of the text |
|---|---|---|---|
| IQ2 class (8.42 vs 8.37 GB) | +0.0569 ± 0.0071 (t = +8.03, 0/40) | −2.37 pt (t = −8.88) | −0.025 ± 0.015 (t = −1.64) |
| IQ3 class (11.77 vs 12.04 GB) | +0.0178 ± 0.0035 (t = +5.02, 1/40) | −1.53 pt (t = −9.51) | −0.018 ± 0.007 (t = −2.49) |

With long context the trained checkpoints are still further from the BF16 distribution than their untrained baselines, by about as much as at 512 tokens, and they agree less often with its top token; on the same tokens they assign the text higher likelihood (resolved at the IQ3 class, leaning the same way at IQ2). The registered rule reads this as a metric effect and not a context effect. KL divergence to the reference measures fidelity to the BF16 model. A file trained against data, as these are, can trade that fidelity for likelihood of the text, and KL divergence then ranks it below a file that is closer to the reference and worse on the text. Our correction, which only reduces distance to the reference, keeps its increment at 2048 tokens on all three bases (−0.0114, −0.0026 and −0.0081; t = −6.7, −6.8, −5.3).

## 5.5 Capability

The card's claims are task-level. Before this revision we had one run per arm of the 112-unit fast subset of an agentic coding suite (multi-language tasks solved by an agent loop with a compiler and tests; single-slot serving, stock llama.cpp b10865, reasoning budget 20,480 tokens) on the two IQ3-class files: GSQ-RCO 88 of 112, the Unsloth baseline 76, a paired net of +12 (exact McNemar p = 0.012), against a four-run band of 82-91 for one unchanged file of a different finetune [source: research/lowrank/experiments/32-t117-gsq-head-to-head/capability-verdict-iq3-final.txt]. A 9-unit spread for an unchanged file means a +12 difference between two single runs has to be read as one draw. The suite samples at temperature 0.6 without a fixed seed, so repeated runs are independent draws.

[PENDING:F — three runs per arm in the current eval era: GSQ-RCO IQ3_S, UD-IQ3_S, and UD-IQ3_S + our correction; per-unit pass rate paired over 112 units (Wilcoxon, sign test), run-level mean ± SD; registered rule in PLAN.md §F.]

## 5.6 What the head-to-head shows

[PENDING:F — final paragraph once the capability replication is in.] Against released GSQ-RCO checkpoints, the untrained vendor baseline at matched bytes is closer to the BF16 distribution on wikitext at both 512 and 2,048 tokens and close to tied on FineWeb-Edu; the trained checkpoints assign the text higher likelihood at 2,048 tokens. Our correction (+0.9 GB, not byte-matched) moves its base closer to the reference on both corpora at 512 tokens and on wikitext at 2,048 tokens, and a stock file at near-equal total bytes beats the corrected arm. KL divergence to a reference is therefore the right instrument for a method that only approximates the reference, such as ours, and the wrong one for ranking a trained file against an untrained one.
