# 3. Results I: re-rounding at zero added bytes

## 3.1 What re-rounding changes, and what it does not

Re-rounding changes no file size, VRAM use or serving speed: it re-chooses which available code each weight gets, using one calibration pass and the model's higher-precision source weights. It improves a given K-quant file at zero bytes; it does not make Q2_K the best choice at its byte point. We report three sizes, and at each we add the stock rung nearest in size.

**Qwen3-0.6B (Q2_K with imatrix; legacy scoring against a Q8_0 reference).** [source: research/lowrank/experiments/13-rerounder/README.md]

| file (296 MB) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K, as written by llama-quantize | 43.33 | 0.766 | 58.2% |
| Q2_K, re-rounded | 35.54 | 0.556 | 64.0% |

Perplexity falls by 18%. The two rows are 15.6 unpaired standard errors apart on perplexity and about 19 on KLD [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md]. No per-chunk logs exist for this pair, and no smaller stock rung was scored against it.

**Qwen3.5-0.8B (Q2_K; BF16 reference; paired).** A second, hybrid-attention architecture [source: research/lowrank/experiments/24-yaqa-lite/README.md; experiments/22-qwen35-08b/results.txt]:

| file | MB | PPL | KLD | top-1 |
|---|---|---|---|---|
| Q2_K, as written | 436 | 33.09 | 0.4902 | 66.4% |
| Q2_K, re-rounded | 436 | 28.09 | 0.3234 | 72.7% |
| IQ3_XXS, stock | 412 | 23.95 | 0.2573 | 74.3% |
| Q3_K_M, stock | 480 | 23.28 | 0.1668 | 79.2% |

Re-rounded against the same file as written: ΔNLL −0.1637 ± 0.0036 nats/token (t = −44.9, n = 580, better on 557 chunks), ΔKLD −0.1668 ± 0.0055 (t = −30.3, n = 40, better on 40), Δtop-1 +6.26 ± 0.45 points (t = +14.1, n = 40). That is 15% lower perplexity and 34% lower KLD at identical bytes, and about half of the KLD distance to Q3_K_M (51.6%).

Re-rounded Q2_K against the stock IQ3_XXS file, which is 24 MB smaller: the stock file is better, by 0.0657 ± 0.0061 KLD (t = +10.8, n = 40, IQ3_XXS better on 39 chunks; Wilcoxon p < 1e-11), by 0.136 nats/token in NLL (t = +11.5) and by 1.6 points of top-1 (t = −3.1). The two logs come from different sessions and were scored against the same 40-chunk BF16 reference, which we verified by recovering the reference's per-chunk NLL from each log [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md]. At 0.8B, then, a user choosing a file near 430 MB should take the stock IQ3_XXS over a re-rounded Q2_K. Whether re-rounding would improve IQ3_XXS in turn is not known, because the re-rounder implements only the Q2_K codec.

**Qwen3.6-27B (Q2_K with imatrix; paired, n = 100; legacy-provenance pair).** On 100 chunks against BF16 reference logits [source: research/lowrank/experiments/27-bf16-rederivation/results-100ch-paired.txt; experiments/27-bf16-rederivation/results.txt]:

| file | GB | n | PPL | KLD | top-1 |
|---|---|---|---|---|---|
| Q2_K, as written (legacy provenance) | 10.86 | 100 | 7.735 ± 0.126 | 0.1603 ± 0.0032 | 83.48% |
| Q2_K, re-rounded (legacy provenance) | 10.86 | 100 | 7.696 ± 0.124 | 0.1390 ± 0.0029 | 84.55% |
| IQ3_XXS, stock (single step from BF16) | 11.48 | 20 | 7.562 ± 0.270 | 0.0983 ± 0.0041 | 87.29% |

Re-rounded against as written: ΔKLD −0.0213 ± 0.0030 (t = −7.02, better on 77/100 chunks; block-bootstrap 95% interval [−0.0285, −0.0138]; Wilcoxon p < 1e-10), Δtop-1 +1.07 points (t = +4.72), ΔNLL −0.005 ± 0.005 (t = −0.98). KL divergence falls by 13.3% and perplexity does not move.

Three labels travel with this pair wherever it is quoted. It is legacy provenance: both files are requantizations of a Q6_K file and not single steps from BF16, so the pair is not tabled beside the single-step rows of §4.3; the difference between the two is clean because both share that provenance. Perplexity is unchanged. And there is no held-out test of the re-rounder at 27B. In addition, the sweep covers part of the model: inside a nominal Q2_K file at 27B, llama-quantize writes ffn_down and attn_output as Q3_K and attn_qkv and attn_v as Q4_K, and only the Q2_K tensors are re-rounded.

The stock row is not at or below the pair's bytes: IQ3_XXS is 0.6 GB larger, and it was scored at 20 chunks under single-step provenance. On the 20 chunks both runs share (same text and reference, verified), IQ3_XXS is lower than re-rounded Q2_K by 0.0526 ± 0.0114 KLD (t = +4.6, 20/20 chunks). On those 20 chunks re-rounding lowers the legacy file by 0.0065 KLD. The comparison crosses provenance and is not byte-matched: it says that a stock file about 6% larger is much better than the re-rounded one, and nothing about equal bytes. No stock rung at or below 10.86 GB was scored against BF16 [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md].

**Calibration dependence.** The wikitext-calibrated 0.8B file was evaluated on a held-out code corpus and is worse than the untouched file: perplexity 3.470 against 3.083. The full-covariance correction, by contrast, improves on it (2.649). Re-rounding fits the calibration covariance with nothing pulling the solution toward the original codes off-distribution. A mixed 2:1 wikitext:code calibration improves both corpora (wikitext 33.09 → 29.84; code 3.083 → 2.785), at some cost on wikitext against wikitext-only calibration (−10% against −15%) [source: research/lowrank/paper/DEPLOYABLE-WINS.md; experiments/22-qwen35-08b/HELDOUT-METHOD.md].

Two pre-registered follow-ups extend this. On a third corpus in neither calibration set (technical prose), both re-rounded files improve on the untouched one: 57.56 as written, 54.40 wikitext-calibrated, 55.32 mixed-calibrated. And the mixed-calibrated file improves KLD on both covered corpora (wikitext 0.490 → 0.372; code 0.318 → 0.219). Our reading is that re-rounding transfers to text near its calibration distribution and can damage text far from it unless that text is covered.

Three caveats. The mixed-against-narrow comparison changed corpus composition, calibration budget (+20%) and chunk sampling together. An accidental control bounds calibration-sampling variance alone at 0.55 perplexity points (about 2%), which is larger than several 0.8B margins in this paper. And the third corpus was assembled from the project's own literature notes, which are machine-drafted prose; it is one sample of unseen text and not a benchmark. The mix-ratio grid has not been run.

**What the tool requires.** The re-rounder is not a transformation of a GGUF alone. It needs the higher-precision source weights (BF16 here; a Q6_K file for the 27B legacy pair), a Gram capture made with our patched llama-imatrix, broad calibration text, and a held-out check. Its output is a standard GGUF.

## 3.2 A richer metric made the result worse

The natural extension of input-only re-rounding is the two-sided Kronecker metric tr(T ΔW S ΔWᵀ), with S the input Gram and T the output-gradient Gram: the Kronecker-factored Hessian of YAQA (arXiv:2505.22988) with our captured factors. The nested sweep of §2.3 reduces that surrogate (about 35% on correlated synthetic problems) and reproduces the input-only sweep exactly when T = I. End to end [source: research/lowrank/experiments/24-yaqa-lite/README.md]:

| comparison (0.8B, paired) | ΔNLL (n = 580) | ΔKLD (n = 40) | Δtop-1 (n = 40) |
|---|---|---|---|
| two-sided vs as written | −0.1154 ± 0.0042 (t = −27.7) | −0.1193 ± 0.0081 | +5.58 ± 0.50 |
| two-sided vs input-only | +0.0483 ± 0.0031 (t = +15.5) | +0.0475 ± 0.0058 (t = +8.2) | −0.69 ± 0.42 |

The two-sided sweep with our measured T gives back 29% of the input-only gain (t = +15.5 on NLL). The solver does what the mathematics says; the estimate of T is the weak part. It comes from a single 16,384-token pass, carries the gradient cut of §2.1 on this architecture, and its late-layer eigenstructure is at the fp32 noise floor by the capture tool's own checks.

A pre-registered run on Qwen3-0.6B, which has no gradient cut, asked whether the cut was the cause. There the two-sided sweep is worse than the input-only sweep and worse than the untouched file: perplexity 50.53, KLD 0.866, top-1 58.0%, against 35.54 / 0.556 / 64.0% input-only and 43.33 / 0.766 / 58.2% as written. The model changed along with the capture, so magnitudes are not comparable, but the direction rules out capture coverage as the explanation. These 0.6B numbers are recorded in the project journal only; the evaluation log was not kept [source: research/lowrank/JOURNAL.md, 2026-08-04 T1.12].

A second surrogate result points the same way: alternating the code sweep with a refit of the storable scales lowers the whitened objective a further 2.6% and changes no end-to-end metric (Appendix A.4). §4.7 collects every such case.

## 3.3 Ordering: re-round a bare file, alternate when a corrector will be attached

Re-rounding helps a bare file and hurts under a corrector. At 0.8B, the bare base with a freshly extracted full-covariance correction beats the re-rounded base with one (ΔNLL t = +17.6, n = 580; the re-rounded stack is better on 133 chunks). The same ordering was seen at 0.6B, unpaired: re-round then correct 26.80 perplexity, a one-shot re-round under a ProjQ-style modified metric (arXiv:2606.00494) 25.94, alternation then correct 25.27 [source: research/lowrank/experiments/24-yaqa-lite/README.md; experiments/13-rerounder/README.md]. Re-rounding minimizes the standalone whitened error and spends grid resolution on directions the corrector would have absorbed. The guidance: re-round when shipping a bare file, alternate (§2.4) when a corrector will be attached, and do not stack re-rounding under a corrector.
