# 3. Results I: re-rounding at zero added bytes

## 3.1 What re-rounding changes, and what it does not

Re-rounding changes no file size, VRAM use or serving speed: it re-chooses which available code each weight gets, using one calibration pass and the model's higher-precision source weights. It improves a given K-quant file at zero bytes; it does not make Q2_K the best choice at its byte point. We report three sizes, and at each we add the stock rung nearest in size.

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

**Qwen3.6-27B (Q2_K; BF16 reference; three corpora; current build).** [PENDING:B — single-step re-round pair (wikitext and 2:1 calibration), IQ2_M and IQ3_XXS brackets, scored on wikitext / FineWeb-Edu / code; RESULTS-B.md.]

The paper's earlier 27B pair was legacy provenance (both files requantized from a Q6_K file) and wikitext-calibrated: on 100 chunks it lowered KLD by 13.3% (t = −7.02) with perplexity unchanged. On the current build and the new references that re-rounded file still has lower KLD than the single-step Q2_K on wikitext (0.1385 against 0.1581, with 0.14 GB fewer bytes) and higher on FineWeb-Edu (0.1345 against 0.1182) and code (0.1564 against 0.1188), the cross-domain pattern of the 0.8B grid; the two files differ in provenance as well as codes, so this is not a paired test of re-rounding [source: research/lowrank/experiments/36-submission/RESULTS-A.md].

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

Re-rounding helps a bare file and hurts under a corrector. At 0.8B, the bare base with a freshly extracted full-covariance correction beats the re-rounded base with one (ΔNLL t = +17.6, n = 580; the re-rounded stack is better on 133 chunks). The same ordering was seen at 0.6B, unpaired: re-round then correct 26.80 perplexity, a one-shot re-round under a ProjQ-style modified metric (arXiv:2606.00494) 25.94, alternation then correct 25.27 [source: research/lowrank/experiments/24-yaqa-lite/README.md; experiments/13-rerounder/README.md]. Re-rounding minimizes the standalone whitened error and spends grid resolution on directions the corrector would have absorbed. The guidance: re-round when shipping a bare file, alternate (§2.4) when a corrector will be attached, and do not stack re-rounding under a corrector.

## 3.4 Freezing the grid against refitting it

A referee asked whether keeping llama-quantize's grid is a constraint worth its cost, against GPTQ allowed to choose the grid as well. We built that baseline at identical format and bytes: the same sweep, Grams, damping and tensor coverage as §2.3, except that when the sweep enters each 256-column superblock it refits that superblock's grid from the error-updated weights with llama.cpp's own Q2_K quantizer and the same imatrix. With error feedback switched off the tool reproduces llama-quantize's file byte for byte (96/96 tensors at 0.8B, 256/256 at 27B), so the only difference between the two arms is whether the grid is frozen. On the whitened objective the refit grid is lower than the frozen one on all 96 tensors at 0.8B (by 12% in total) [source: research/lowrank/experiments/36-submission/freegrid/README.md].

[PENDING:E — free-grid vs frozen-grid, paired ΔKLD on wikitext / FineWeb-Edu / code at 0.8B (wikitext and 2:1 Grams) and 27B (2:1 Grams); and at Llama-3.1-8B (§G).]

This baseline does not cover learned rounding (AdaRound and its successors), which needs a block-reconstruction training loop, or vector and lattice quantizers at 2 bits, which are outside the GGUF formats; §7.1 lists both.
