# 3. Results I: re-rounding at zero added bytes

## 3.1 What re-rounding changes, and what it does not

Re-rounding changes no file size, VRAM use or serving speed: it re-chooses which available code each weight gets, using one calibration pass and the model's higher-precision source weights. We report it at four sizes and on two families. It improves the file at 0.6B and 0.8B; at 8B and 27B it does not, and at every size where we scored held-out text (0.8B, 8B, 27B) it damages text its calibration does not cover.

**Qwen3-0.6B (Q2_K with imatrix; legacy scoring against a Q8_0 reference).** [source: research/lowrank/experiments/13-rerounder/README.md]

| file (296 MB) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K, as written by llama-quantize | 43.33 | 0.766 | 58.2% |
| Q2_K, re-rounded | 35.54 | 0.556 | 64.0% |

Perplexity falls by 18%. The two rows are 15.6 unpaired standard errors apart on perplexity and about 19 on KLD [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md]. No per-chunk logs exist for this pair, and no smaller stock rung was scored against it.

**Qwen3.5-0.8B (Q2_K; BF16 reference; three corpora; current build).** A second, hybrid-attention architecture. Every file is one quantization step from BF16 with a 40-chunk wikitext-2-train imatrix and Gram capture; KLD is on 40 chunks of each corpus against BF16 logits generated on llama.cpp b10865 [source: research/lowrank/experiments/36-submission/RESULTS-D.md]:

| file | MB | KLD, wikitext | KLD, FineWeb-Edu | KLD, code |
|---|---|---|---|---|
| IQ2_M, stock | 392 | 0.5477 | 0.5391 | 0.4881 |
| IQ3_XXS, stock | 412 | 0.2577 | 0.2577 | 0.2225 |
| Q2_K, as written | 436 | 0.4900 | 0.5347 | 0.3926 |
| Q2_K, re-rounded, wikitext calibration | 436 | 0.3268 | 0.4255 | 0.5433 |
| Q2_K, re-rounded, 2:1 wikitext:code calibration | 436 | 0.3690 | 0.4660 | 0.2591 |
| Q3_K_M, stock | 480 | 0.1677 | 0.1678 | 0.1209 |

Re-rounded with wikitext calibration against the same file as written: ΔKLD −0.1632 ± 0.0058 on wikitext (t = −28.4, better on 40/40 chunks), 33% lower at identical bytes; the paper's earlier measurement on a pre-fix build was −34% (t = −30.3). On FineWeb-Edu the gain is smaller (−0.1092, t = −12.6). On code the file is worse than the untouched one (+0.1507, t = +13.6).

Against stock rungs: the re-rounded file is ahead of IQ2_M, 44 MB smaller, on wikitext and FineWeb-Edu (t = −25.9 and −15.7) and behind it on code (t = +4.1). The stock IQ3_XXS file, 24 MB smaller, is better than the re-rounded Q2_K on all three corpora (t = +11.7, +22.2, +16.2). A user choosing a file near 430 MB at this size should take IQ3_XXS. Whether re-rounding would improve IQ3_XXS in turn is not known, because the re-rounder implements only the Q2_K codec.

**Calibration decides where the gain lands.** We re-rounded the same Q2_K file under five calibration texts, every one disjoint from every evaluation text: wikitext-2 train, a code corpus, a FineWeb-Edu slice taken from documents outside the evaluation slice, a 2:1 wikitext:code interleave and a 1:1:1 interleave of all three. Paired ΔKLD against the file as written, 40 chunks per cell (t in parentheses) [source: research/lowrank/experiments/36-submission/RESULTS-D.md; experiments/36-submission/paired-d.txt]:

| calibration | wikitext | FineWeb-Edu | code |
|---|---|---|---|
| wikitext | −0.163 (−28.4) | −0.109 (−12.6) | **+0.151 (+13.6)** |
| FineWeb-Edu | −0.115 (−16.3) | −0.151 (−16.8) | **+0.088 (+8.9)** |
| code | **+0.247 (+20.3)** | **+0.275 (+21.1)** | −0.132 (−14.0) |
| 2:1 wikitext:code | −0.121 (−21.4) | −0.069 (−8.2) | −0.133 (−14.6) |
| 1:1:1 wikitext:code:FineWeb-Edu | −0.116 (−20.6) | −0.087 (−10.0) | −0.134 (−14.8) |

The pattern is consistent across the grid. Re-rounding transfers between the two prose corpora and does not transfer between prose and code in either direction: every calibration that lacks one of the two kinds of text damages it, by up to half the file's own KLD. Both calibrations that contain both kinds improve all three corpora, at a cost on each corpus against calibrating on that corpus alone (for wikitext, −0.121 against −0.163). The 2:1 interleave was fixed as the deployable recipe before this grid was run. Re-rounding fits the calibration covariance with nothing pulling the codes toward their original values in directions the calibration does not excite, which is a plausible reading of why uncovered text is damaged; we did not test it directly.

**The calibration sample is a small part of the variance.** Re-rounding on three disjoint thirds of wikitext-2 train, the same budget each, moves ΔKLD by at most 0.004 on wikitext, 0.018 on FineWeb-Edu and 0.007 on code (wikitext: −0.163, −0.164, −0.167). This bounds calibration-sampling variance alone at this size, and it is one to two orders of magnitude smaller than the effect of which kind of text is in the calibration set.

**Qwen3.6-27B and Llama-3.1-8B (Q2_K; BF16 reference; three corpora; current build).** The same sweep at two larger sizes, one of them outside the Qwen family. Every base is one quantization step from BF16 with a 48-chunk wikitext imatrix; the re-round arms differ only in the calibration text of their Gram capture; 100 chunks per corpus; registered rules [source: research/lowrank/experiments/36-submission/RESULTS-B.md; experiments/36-submission/RESULTS-G.md]:

| file | Qwen3.6-27B: KLD wikitext / FineWeb-Edu / code | Llama-3.1-8B: KLD wikitext / FineWeb-Edu / code |
|---|---|---|
| IQ2_M, stock | 0.1710 / 0.1314 / 0.1340 (10.30 GB) | 0.2891 / 0.2539 / 0.1130 (2.95 GB) |
| Q2_K, as written | 0.1581 / 0.1182 / 0.1188 (11.00 GB) | 0.3064 / 0.2701 / 0.1124 (3.18 GB) |
| Q2_K, re-rounded, wikitext calibration | 0.1523 / 0.1359 / 0.1515 | 0.3011 / 0.3080 / 0.1496 |
| Q2_K, re-rounded, 2:1 wikitext:code calibration | 0.1671 / 0.1465 / 0.1130 | 0.3340 / 0.3299 / 0.1111 |
| IQ3_XXS, stock | 0.1077 / 0.0710 / 0.0855 (11.48 GB) | 0.1658 / 0.1512 / 0.0620 (3.27 GB) |

Paired against the file as written (t):

| arm | Qwen3.6-27B: wikitext / FineWeb-Edu / code | Llama-3.1-8B: wikitext / FineWeb-Edu / code |
|---|---|---|
| wikitext calibration | −1.87 / **+7.79** / **+10.69** | −1.97 / **+10.22** / **+12.57** |
| 2:1 calibration | **+3.21** / **+13.25** / −1.90 | **+8.70** / **+14.17** / −0.66 |

At both sizes the sweep does not resolve a gain on the text it was calibrated on (−3.7% and −1.7% in KLD, |t| < 2), damages the held-out corpora, and with the mixed calibration that rescued the 0.8B file is worse than the untouched file on wikitext and FineWeb-Edu and level on code. The paper's earlier 27B result (−13.3%, t = −7.0) was a legacy pair whose source weights and Grams came from a Q6_K requantization; the same sweep on a single-step base does not reproduce it, and neither do the pre-fix BF16 Grams of that earlier capture (−0.0080 against the file as written, but level with the current-Gram arm, t = −1.11).

**Why it fails at size: the sweep optimizes its proxy and not the outcome.** We dequantized the 27B files with gguf-py's own dequantizer and recomputed the error of three re-rounded tensors independently of the sweep's code. Their whitened error, under the calibration Gram, is 0.49, 0.57 and 0.63 of the untouched file's, as the sweep reports; their unweighted Frobenius error is 16-25% larger; 9-11% of their codes changed [source: research/lowrank/experiments/36-submission/checks/rr27b-independent-dequant.txt]. The sweep moves error out of the directions the calibration activations occupy and into the others, and text whose activations occupy other directions sees more error than before. The headroom was there: a file that stores every tensor the sweep covers at Q8_0 instead of Q2_K has KLD 0.034 / 0.023 / 0.029, so those tensors carry 76-80% of the Q2_K file's divergence, and halving their whitened error removed about 5% of it on wikitext. This is the largest surrogate-against-outcome gap in the paper (§4.7).

What separates 0.8B from 8B and 27B is not established by this design. Candidates we did not test: the share of the model the Q2_K codec reaches (the larger files promote more of their most sensitive tensors to wider types), the conditioning of the Gram at larger width, and the larger KLD of the 0.8B Q2_K file, which leaves more to remove.

**What the tool requires.** The re-rounder is not a transformation of a GGUF alone. It needs the higher-precision source weights, a Gram capture made with our patched llama-imatrix, calibration text that covers every kind of text the file will serve, and a held-out check. Its output is a standard GGUF.

## 3.2 A richer metric made the result worse

The natural extension of input-only re-rounding is the two-sided Kronecker metric tr(T ΔW S ΔWᵀ), with S the input Gram and T the output-gradient Gram: the Kronecker-factored Hessian of YAQA (arXiv:2505.22988) with our captured factors. The nested sweep of §2.3 reduces that surrogate (about 35% on correlated synthetic problems) and reproduces the input-only sweep exactly when T = I. End to end [source: research/lowrank/experiments/24-yaqa-lite/README.md]:

| comparison (0.8B, paired) | ΔNLL (n = 580) | ΔKLD (n = 40) | Δtop-1 (n = 40) |
|---|---|---|---|
| two-sided vs as written | −0.1154 ± 0.0042 (t = −27.7) | −0.1193 ± 0.0081 | +5.58 ± 0.50 |
| two-sided vs input-only | +0.0483 ± 0.0031 (t = +15.5) | +0.0475 ± 0.0058 (t = +8.2) | −0.69 ± 0.42 |

The two-sided sweep with our measured T gives back 29% of the input-only gain (t = +15.5 on NLL). The solver does what the mathematics says; the estimate of T is the weak part. It comes from a single 16,384-token pass, carries the gradient cut of §2.1 on this architecture, and its late-layer eigenstructure is at the fp32 noise floor by the capture tool's own checks.

A pre-registered run on Qwen3-0.6B, which has no gradient cut, asked whether the cut was the cause. There the two-sided sweep is worse than the input-only sweep and worse than the untouched file: perplexity 50.53, KLD 0.866, top-1 58.0%, against 35.54 / 0.556 / 64.0% input-only and 43.33 / 0.766 / 58.2% as written. The model changed along with the capture, so magnitudes are not comparable, but the direction rules out capture coverage as the explanation. These 0.6B numbers are recorded in the experiment log only; the evaluation log was not kept [source: research/lowrank/JOURNAL.md, 2026-08-04 T1.12].

A second surrogate result points the same way: alternating the code sweep with a refit of the storable scales lowers the whitened objective a further 2.6% and changes no end-to-end metric (Appendix A.4). §4.7 collects every such case.

## 3.3 Ordering: re-round a bare file, alternate when a corrector will be attached

Re-rounding helps a bare file and hurts under a corrector. At 0.8B, the bare base with a freshly extracted full-covariance correction beats the re-rounded base with one (ΔNLL t = +17.6, n = 580; the re-rounded stack is better on 133 chunks). The same ordering was seen at 0.6B, unpaired: re-round then correct 26.80 perplexity, a one-shot re-round under a ProjQ-style modified metric (arXiv:2606.00494) 25.94, alternation then correct 25.27 [source: research/lowrank/experiments/24-yaqa-lite/README.md; experiments/13-rerounder/README.md]. Re-rounding minimizes the standalone whitened error and spends grid resolution on directions the corrector would have absorbed. The guidance, for the small models where re-rounding helps at all (§3.1): re-round a bare file only with a calibration that covers its intended text, alternate (§2.4) when a corrector will be attached, and do not stack re-rounding under a corrector.

## 3.4 Freezing the grid against refitting it

A referee asked whether keeping llama-quantize's grid is a constraint worth its cost, against GPTQ allowed to choose the grid as well. We built that baseline at identical format and bytes: the same sweep, Grams, damping and tensor coverage as §2.3, except that when the sweep enters each 256-column superblock it refits that superblock's grid from the error-updated weights with llama.cpp's own Q2_K quantizer and the same imatrix. With error feedback switched off the tool reproduces llama-quantize's file byte for byte (96/96 tensors at 0.8B, 256/256 at 27B), so the only difference between the two arms is whether the grid is frozen. On the whitened objective the refit grid is lower than the frozen one on all 96 tensors at 0.8B (by 12% in total) [source: research/lowrank/experiments/36-submission/freegrid/README.md].

Paired ΔKLD, free-grid minus frozen-grid at identical bytes, same Grams (t) [source: research/lowrank/experiments/36-submission/RESULTS-E.md; experiments/36-submission/RESULTS-G.md]:

| model, calibration | wikitext | FineWeb-Edu | code |
|---|---|---|---|
| Qwen3.5-0.8B, wikitext | −0.017 (−4.53) | −0.028 (−5.49) | −0.058 (−7.33) |
| Qwen3.5-0.8B, 2:1 | −0.017 (−3.63) | −0.021 (−3.62) | −0.023 (−7.47) |
| Llama-3.1-8B, 2:1 | −0.018 (−6.43) | −0.010 (−3.08) | −0.007 (−3.62) |
| Qwen3.6-27B, 2:1 | −0.0095 (−3.22) | −0.0059 (−2.64) | −0.0046 (−1.83) |

Refitting the grid is better than freezing it in eleven of twelve cells and level in the twelfth. Freezing llama-quantize's grid is therefore a restriction that costs quality and not a feature of the method, and the method is better described as GPTQ inside the GGUF formats, with the frozen grid as its zero-search special case. The refit does not change the verdict at size: at 8B and 27B the free-grid file is still worse than the untouched file on FineWeb-Edu (t = +8.29, +8.16) and not better on wikitext (+2.87, −0.19); it is better on code under the code-containing calibration (−3.85, −4.08).

This baseline does not cover learned rounding (AdaRound and its successors), which needs a block-reconstruction training loop, or vector and lattice quantizers at 2 bits, which are outside the GGUF formats; §7.1 lists both.
