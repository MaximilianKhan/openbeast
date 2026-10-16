# 4. Results II: where low-rank correction falls short

This section reports the correction audit. Most of its results are negative, and several of them are the best-resolved findings of the study [source: research/lowrank/review/adversarial-stats.md §F15].

## 4.1 Unweighted spectra: neither the weights nor the residual are low-rank

A singular-value census over all 197 tensors of Qwen3-0.6B finds that capturing 95% of Frobenius energy needs 50-80% of full rank on every projection type. At that rank a factorization saves nothing (best case attn_q at 1.09×; value and FFN projections cost memory) [source: research/lowrank/experiments/01-svd-spectrum/README.md]. The quantization residual is flatter: 90% of its energy needs about 62% of full rank, as the Marchenko-Pastur analysis of LQER (arXiv:2402.02446) predicts and as RILQ (arXiv:2412.01129) reports for 2-bit quantization. An unweighted rank-128 F16 correction of a Q2_K base recovers 26-44% of error energy at a cost of 5.26 bits per weight, more than Q4_K [source: research/lowrank/experiments/03-residual-rank/README.md].

This statement is scoped to the unweighted Frobenius norm on one 0.6B model. Under activation whitening the picture differs: at 27B, rank 128 captures 76% of the whitened weight matrix on a 12-tensor sample (§4.4). That concentration does not convert into quality at equal bytes, which is the subject of the rest of this section.

## 4.2 Capture against rank and width

Diagonal-whitened rank-64 capture of the Q2_K residual is 0.37 at width d = 1,024 (0.6B) and 0.07 at d = 5,120 (27B). To replace that two-point reading we recomputed whitened spectra for 1,146 tensors at 10 ranks under 3 whiteners on four models: Qwen3-0.6B and Qwen3.5-0.8B at d = 1,024, and Qwen3.6-27B and Qwen3.8-27B at d = 5,120. We fitted capture ≈ a·r^{b_r}·d^{b_d} [source: research/lowrank/experiments/33-t110-capture-width/REPORT.md, fit_summary.txt, addendum_summary.txt].

| fit | whitener | b_r | b_d |
|---|---|---|---|
| all four models as shipped or built | diagonal | 0.61 [0.60, 0.61] | −0.67 [−0.70, −0.64] |
| all four models as shipped or built | full Gram | 0.44 [0.44, 0.45] | −0.59 [−0.61, −0.57] |
| one quantization recipe on every model (primary) | diagonal | 0.68 [0.67, 0.68] | −0.76 [−0.79, −0.73] |
| one quantization recipe on every model (primary) | full Gram | 0.37 [0.36, 0.37] | −0.52 [−0.54, −0.50] |

Capture is sublinear in rank and, across a single width step, falls more slowly than r/d predicts: the linear form capture = c·(r/d) is the worst of six candidate forms under every whitener, and with the recipe fixed, holding out either 27B model predicts it to within 0.9-1.1×. Whitening by the full Gram reduces the width exponent by about a third (−0.76 to −0.52). The prefactor depends on the base recipe and not on the model: requantizing Qwen3.8-27B from BF16 with the recipe used for Qwen3.6-27B makes the two models indistinguishable (0 of 40 per-kind contrasts exclude zero), while Unsloth's shipped dynamic quantization of the same model sits on a different curve.

Scope: the width axis is one step (1,024 → 5,120) and is confounded with model size, depth and family. The two 27B models share a width. The intervals resample tensors (1,000 cluster bootstraps), not models, and the fit treats ranks and whiteners of one tensor as separate points. A "constant outlier head plus isotropic bulk" form fits a head fraction of zero, so the outlier-head explanation (arXiv:2208.07339, arXiv:2402.17762, arXiv:2411.07191) is not supported by this regression. A third width is required before any exponent is quoted as more than a fit [source: research/lowrank/RESULTS_ROLLUP.md corrections 5; review/adversarial-stats.md §F6]. What is established is the direction: at these widths a deployable rank is a small fraction of dimension, and fixed-rank capture falls as the model widens, consistent with the bounds of CALDERA (arXiv:2405.18886) and arXiv:2606.01412.

## 4.3 Correction against the stock types, on and off the calibration distribution

**27B: files and protocol.** Every 27B file in this subsection is one quantization step from the BF16 reference, with a 48-chunk BF16 imatrix and Gram capture on wikitext-2 train [source: research/lowrank/experiments/27-bf16-rederivation/README.md]. The corrected configuration is the mixed base (FFN tensors at Q3_K, the rest at Q2_K) plus a rank-128 full-covariance correction with Q8_0 factors on the non-FFN tensors (336.5 MB; mean whitened capture 0.42). The control is a mixed-type file with no adapter, built once at the corrected configuration's byte count (Q3_K_S with attn_k, attn_v and attn_output promoted to Q4_K and a partial attn_qkv promotion; −0.016% bytes), its bytes predicted in advance from measured per-kind sensitivities. All rows below were scored on llama.cpp b10865 against BF16 reference logits generated on that build, on 100 chunks of three corpora: wikitext-2 test (the calibration distribution), FineWeb-Edu and a code corpus disjoint from the code used anywhere in calibration (§2.5). The comparison and its decision rules were registered before the run [source: research/lowrank/experiments/36-submission/PLAN.md §A; experiments/36-submission/RESULTS-A.md].

| file | GB | KLD, wikitext | KLD, FineWeb-Edu | KLD, code |
|---|---|---|---|---|
| mixed base, no adapter | 12.16 | 0.0971 ± 0.0025 | 0.0731 ± 0.0014 | 0.0830 ± 0.0021 |
| IQ3_XS | 12.26 | 0.0766 ± 0.0022 | 0.0517 ± 0.0010 | 0.0632 ± 0.0016 |
| Q3_K_S | 12.37 | 0.0910 ± 0.0022 | 0.0638 ± 0.0012 | 0.0746 ± 0.0020 |
| corrected configuration | 12.50 | 0.0863 ± 0.0023 | 0.0686 ± 0.0014 | 0.0779 ± 0.0020 |
| mixed-type control | 12.50 | 0.0813 ± 0.0022 | 0.0577 ± 0.0011 | 0.0666 ± 0.0018 |
| Q3_K_M | 13.59 | 0.0605 ± 0.0016 | 0.0410 ± 0.0008 | 0.0500 ± 0.0015 |

Paired per-chunk KLD differences, n = 100 per corpus (t; A ahead on how many chunks):

| pair (A − B) | wikitext | FineWeb-Edu | code |
|---|---|---|---|
| corrected vs Q3_K_S | −0.0047, t = −2.62 (71/100) | +0.0048, t = +3.26 (40/100) | +0.0032, t = +1.86 (39/100) |
| corrected vs control | +0.0050, t = +2.37 (40/100) | +0.0108, t = +6.48 (10/100) | +0.0113, t = +7.11 (20/100) |
| control vs Q3_K_S | −0.0097, t = −8.64 (91/100) | −0.0060, t = −13.37 (98/100) | −0.0080, t = −6.90 (81/100) |
| corrected vs mixed base | −0.0108, t = −10.41 (96/100) | −0.0045, t = −8.63 (91/100) | −0.0051, t = −6.48 (81/100) |
| mixed base vs Q3_K_S | +0.0061, t = +2.93 (31/100) | +0.0093, t = +5.70 (23/100) | +0.0083, t = +4.32 (30/100) |
| IQ3_XS vs corrected | −0.0097, t = −3.58 (83/100) | −0.0169, t = −8.55 (97/100) | −0.0147, t = −7.06 (81/100) |

**On the calibration distribution** the corrected configuration is ahead of Q3_K_S by 0.0047 KLD (5%; t = −2.62; Wilcoxon p = 2e-5; block bootstrap over contiguous blocks of 10 chunks [−0.0081, −0.0009]) and behind the same-byte control by 0.0050 (t = +2.37; Wilcoxon p = 0.0014; block bootstrap [+0.0017, +0.0091]). In NLL it is ahead of Q3_K_S (t = −5.79) and level with the control (t = +0.21). This is a reproduction and not an independent sample: the same files and the same 100 chunks were first scored on a build that predates llama.cpp's Gated-DeltaNet normalization fix (b10829), where the two margins were −0.0048 (t = −2.85) and +0.0063 (t = +2.62); the per-chunk differences of the two runs correlate at 0.87. The margin over Q3_K_S is not uniform across the test set on either build: on chunks 1-40 it is +0.0004 (t = +0.13) and on chunks 41-100 −0.0081 (t = −3.99). The correction's increment over its base is the same in both blocks (−0.0106 and −0.0109); what changes is the base, which is behind Q3_K_S by 0.0110 on chunks 1-40 (t = +3.38) and by 0.0028 on chunks 41-100 (t = +1.07). That pre-fix 100-chunk sample was reached through looks at 20 and 40 chunks (each a prefix of the next) with a registered rule for parity and none for a win [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md].

**Off the calibration distribution** the margin over Q3_K_S does not hold. On FineWeb-Edu it reverses: Q3_K_S is ahead of the corrected configuration by 0.0048 (t = +3.26, block bootstrap [+0.0024, +0.0073]). On code it is unresolved and leans the same way (+0.0032, t = +1.86). The control's lead over the corrected configuration roughly doubles off-distribution (t = +6.48 and +7.11; ahead on 90/100 and 80/100 chunks), and IQ3_XS, which is smaller and needs no adapter, is ahead of every other file in the group on all three corpora. The reversal decomposes into its two parts. The mixed base is further behind Q3_K_S off-distribution (+0.0093 and +0.0083, against +0.0061), and the correction, whitened by wikitext Grams, recovers less than half as much there (−0.0045 and −0.0051, against −0.0108). It still improves its base on every corpus.

**Reading.** The narrow 27B advantage of the corrected configuration over the adjacent stock type is a property of the calibration distribution. A stock mixed-type file at the same bytes is better on every corpus we measured, and IQ3_XS is better at fewer bytes. What survives off-distribution is the correction's increment over its own base, at about half its on-distribution size; §4.5 measures the same halving along a recovery curve.

A task-level check was run before the held-out pass and does not resolve the contested pair either. On the HellaSwag validation set (10,042 items, identical per file) the corrected configuration, Q3_K_S and Q3_K_M score 82.25%, 82.39% and 82.85%; no gap reaches the pre-registered 1.4-point threshold. A post-hoc paired McNemar test finds the corrected configuration and Q3_K_S tied (z = −0.81, with the sign opposite to the wikitext KLD difference) and Q3_K_M ahead of both (z = +3.48 and +2.82) at a KLD difference of about 0.03 [source: research/lowrank/JOURNAL.md, 2026-08-11 T1.8].

![Figure 1. Qwen3.6-27B near 12.5 GB on three corpora, 100 chunks each, every file one quantization step from BF16, scored on llama.cpp b10865; error bars are standard errors. On wikitext, the calibration distribution, the correction (arrow) moves the mixed base past the stock type Q3_K_S; on FineWeb-Edu and code it does not. A mixed-type control with no adapter at the same bytes is lower on every corpus, and IQ3_XS is the lowest of the group at fewer bytes.](figures/fig-ladder-27b.svg)

**0.8B: the control at small scale.** At 0.8B the paper's earlier comparison chose its rung from below and built no control. We built one by a rule fixed in advance: start from Q3_K_M and promote whole tensor kinds to Q4_K in the order of the 27B sensitivities until the file reaches the corrected configuration's bytes from below. At 0.8B, Q3_K_M already holds four of the listed kinds at Q4_K or wider, so the list ran out at 517.5 MB, 1.1% below the 523.3 MB corrected configuration (Q2_K with one alternation round and a rank-128 full-covariance correction with Q8_0 factors). A second control matched to the byte (523.0 MB, a further pass at Q5_K) was designed after the registration and is reported as such. Both were built once and scored on 40 chunks of the three corpora against BF16 references on the current build [source: research/lowrank/experiments/36-submission/RESULTS-D.md]:

| file (Qwen3.5-0.8B) | MB | KLD, wikitext | KLD, FineWeb-Edu | KLD, code |
|---|---|---|---|---|
| Q2_K | 436 | 0.4900 | 0.5347 | 0.3926 |
| IQ3_XXS | 412 | 0.2577 | 0.2577 | 0.2225 |
| Q3_K_M | 480 | 0.1677 | 0.1678 | 0.1209 |
| corrected configuration | 523 | 0.1474 | 0.1966 | 0.1805 |
| control (registered rule) | 517 | 0.0489 | 0.0470 | 0.0338 |
| control (byte-matched, post-registration) | 523 | 0.0432 | 0.0426 | 0.0285 |
| Q4_K_M | 543 | 0.0397 | 0.0380 | 0.0279 |

The registered control is ahead of the corrected configuration by 0.099 KLD on wikitext (t = −26.7), 0.150 on FineWeb-Edu (t = −32.0) and 0.147 on code (t = −21.0), on every chunk of every corpus, with fewer bytes; its KLD is a third of the corrected configuration's. The corrected configuration is ahead of Q3_K_M on wikitext (t = −5.75) and behind it on FineWeb-Edu (t = +7.77) and code (t = +14.48). At this size the corrected file is far below the line that stock types trace when they are mixed at its bytes. At 0.6B (legacy rows, scored against Q8_0) the corrected configuration was compared only with Q3_K_M below it, and linear interpolation between the neighbouring rungs was already better; no control was built there [source: research/lowrank/RESULTS_ROLLUP.md; review/adversarial-stats.md §F7].

A correction on an NVFP4 base at 0.8B improves its base and loses to every stock rung at or below its bytes; the details are in Appendix A.1.

**A qualification on all of the above.** Every corrected configuration pays a decode cost of 20-40% on stock llama.cpp that the bare files do not (§6).

## 4.4 Three placements of the low-rank bytes, and a bound on their difference

A set of pre-registered paired experiments at 27B asked whether it matters where the low-rank bytes act. Three placements were built at matched bytes on the mixed base. The first is the post-hoc correction of §2.2. The second is a pre-quantization carve in the preserve-then-quantize order (arXiv:2602.02001): take a whitened rank-128 component C = B·A off the BF16 weights, quantize the remainder with the same mixed recipe, and serve C as the adapter. The third shares one A factor across tensors that read the same input (the arrangement of arXiv:2603.25385), in two variants: at byte parity with the first placement, and at rank 128.

Their surrogate scores differ. The post-hoc correction captures 0.42 of the whitened residual. The carve captures 76% of the whitened weight matrix at rank 128 on a 12-tensor sample. The shared basis at byte parity captures 12.2% more whitened energy than separate bases on the attention groups and 6.9% more on the GDN groups; at equal rank it keeps 99.55% and 99.75% of the separate capture [source: research/lowrank/experiments/28-glowq-shared-a/README.md; experiments/29-srr-split/README.md].

Their measured quality does not. Each alternative placement minus the post-hoc correction, on 100 paired chunks [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md]:

| placement vs post-hoc correction | ΔKLD | t | 90% interval | largest bound as a share of the correction's increment (0.0090) |
|---|---|---|---|---|
| pre-quantization carve | +0.0017 ± 0.0017 | +0.99 | [−0.0011, +0.0045] | 49% |
| shared basis, byte parity | −0.0005 ± 0.0006 | −0.91 | [−0.0015, +0.0004] | 17% |
| shared basis, rank 128 | −0.0008 ± 0.0006 | −1.48 | [−0.0018, +0.0001] | 20% |

Three low-rank placements differ by less than 0.0045 KLD (90% interval), under 50% of the correction's own increment (0.0090); for the two shared-basis variants the bound is 0.0018, about 20%. We could not distinguish them. This is a bound and not a demonstration that the placements are the same: the interval for the carve admits a deficit of half an increment, its top-1 difference leans negative (t = −1.68), and it trails the control by more than the others do. One mixed-type control at the same bytes is better than all four files (t = +2.62, +5.15, +2.35 and +2.39 for the post-hoc correction, the carve, and the two shared-basis variants).

Scope: one model, one byte count, one rank, one corpus, with three arms that share a base recipe and a Gram. The statement that type allocation does better rests on one designed control. Per-tensor type allocation is the axis that RCO (arXiv:2605.00649) optimizes under an exact byte budget; that paper does not compare placements, and this one does not optimize allocation.

A related family uses seeded, unlearned bases (AWSRC, arXiv:2608.23144). A pre-registered capture comparison on our cached whitened spectra (18 tensors, 3 seeds) finds that such bases capture 0.035 of the dominant whitened energy at byte parity against 0.983 for learned factors, which is the random-subspace expectation [source: research/lowrank/experiments/31-seeded-basis-duel/PREREG.md]. We did not run a quality comparison for that family.

One by-product: the shared basis at rank 128 is within the bound above of the post-hoc correction while storing fewer distinct factor bytes. If a loader aliased the shared factor, the adapter would occupy 280.8 MB against 336.5 MB (−16.6%). That figure is a computed size under a loader patch we did not write; the file that was evaluated stores the duplicates and is 336.5 MB [source: research/lowrank/experiments/28-glowq-shared-a/e28c.log].

## 4.5 Recovery grows as the base gets thinner, and factors do not replace base bits

We held the correction recipe fixed and varied only the base: the same rank-128 full-covariance correction with Q8_0 factors on the non-FFN tensors, whitened by one wikitext Gram capture, extracted on three 27B bases each one quantization step from BF16, and scored on 100 chunks of wikitext and of FineWeb-Edu on the current build. Recovery is the fraction of the base's KLD that the correction removes, with a 95% paired bootstrap interval [source: research/lowrank/experiments/36-submission/RESULTS-D.md §T1.9]:

| base | GB | KLD wikitext, base → corrected | recovery, wikitext | KLD FineWeb-Edu, base → corrected | recovery, FineWeb-Edu |
|---|---|---|---|---|---|
| Q2_K | 11.00 | 0.1581 → 0.1470 | 7.0% [5.2, 8.8] | 0.1182 → 0.1143 | 3.3% [2.5, 4.1] |
| IQ2_XS | 9.38 | 0.2507 → 0.2201 | 12.2% [10.2, 14.4] | 0.1875 → 0.1732 | 7.6% [6.8, 8.5] |
| IQ1_S | 7.44 | 0.9348 → 0.6356 | 32.0% [30.1, 33.9] | 0.6588 → 0.5513 | 16.3% [15.3, 17.2] |

Every increment is resolved (t from −6.3 to −29.1; the corrected file is ahead on at least 86 of 100 chunks). Recovery rises monotonically as the base gets thinner, which agrees with the published claim that correction pays most at the lowest bit widths (arXiv:2604.07955) and with EoRA's results. Off the calibration distribution it is about half as large at every base, the same halving as in §4.3. This curve replaces an earlier one (20%, 28%, 41%, legacy rows) whose points differed in adapter budget and rank mix as well as base.

Recovery is not the same as competitiveness. The corrected IQ1_S file (7.78 GB with its 0.34 GB adapter) has KLD 0.636 on wikitext, against 0.251 for the bare IQ2_XS file at 9.38 GB. Pushing the idea further, IQ1_S with 2.52 GB of factors still loses to bare Q2_K at near-equal total bytes by about a factor of three in KLD; per gigabyte, added factor bytes bought about 0.06 KLD where base bits bought 0.22 to 0.32 (legacy rows, Appendix A.2).

## 4.6 Mixture-of-experts: correction loses to a one-line promotion (n = 20, Q4-referenced)

On Qwen3.6-35B-A3B (256 experts per stack, 8 routed; expert stacks are 91% of parameters) both correction variants improve their base and both lose to the ladder, with a control built from the start [source: research/lowrank/experiments/23-moe/README.md]. Two limits apply to every number in this subsection: n = 20 chunks, and the reference is not BF16. We had no BF16 of this model, so every file is one quantization step from the vendor UD-Q4_K_M file (22.1 GB) and KLD is measured against that file's logits.

Per-expert whitened correction (rank 32, Q8_0 factors, pooled-Gram whitening; served on stock llama.cpp, which handles three-dimensional adapter pairs) improves the Q2_K base (t = −4.55, 18/20 chunks); a shared-basis variant improves it weakly (t = −2.69, 13/20). At its byte count (14.5 GB) the per-expert correction loses to a control that promotes ffn_down_exps to Q4_K, by +0.0257 ± 0.0038 KLD (t = +6.85, 0/20 chunks), and the shared-basis variant loses to the same control at t = +9.33. Q3_K_S, 0.7 GB larger than the corrected file, is lower again (KLD 0.1150 against the control's 0.1666; control versus Q3_K_S, t = +8.15). Per byte added to the base, the promotion control removes 2.6 times as much KLD as the rank-32 factors, and Q3_K_S 4.2 times as much [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md].

Because the control promotes a tensor group toward the reference's own bit width, we checked whether the reference favours it by construction. In the vendor reference, ffn_down_exps is Q5_K in 37 layers and Q6_K in 3 (from the quantize log and from the GGUF header); the up and gate stacks are Q4_K. The control's Q4_K is therefore a real quantization step below the reference and not a copy of it. The comparison still measures distance to a quantized model and should be repeated against BF16 when one is available [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md].

The structural result is about sharing. One output basis per stack, shared by 256 experts, captures 0.086-0.090 of whitened residual energy on the up and gate stacks against 0.0625 for a random orthonormal basis, and 0.022 against 0.0156 on the down stacks. The pooled whitened residual is nearly isotropic, so there is no common subspace to amortize at rank 32. Each expert is a 512 × 2,048 matrix whose residual is weakly structured (per-expert capture 0.19-0.36, measured under the pooled whitener, which approximates each expert's own input covariance), and the factor overhead is paid 256 times. Correcting the dense tensors of a mixture-of-experts model, or expert bases below 2 bits where no cheap promotion exists, were not tried.

## 4.7 When a better surrogate did not give a better outcome

Metric quality mattered more than rank. Going from diagonal to full-covariance whitening at 0.6B was worth more than doubling rank: full covariance at rank 64 gives perplexity 27.58 and KLD 0.318, against 31.29 and 0.420 for the diagonal at rank 64 and 28.46 for the diagonal at rank 128 (about 10 and 2.6 unpaired standard errors). The same comparison at 27B was about 2 unpaired standard errors (0.1460 → 0.1282 at rank 64) [source: research/lowrank/experiments/10-full-covariance/README.md].

In the other direction, improving a surrogate repeatedly failed to improve the outcome.

Rank allocation by greedy energy water-filling at 0.6B is worse than uniform rank at equal bytes (perplexity 34.17 against 31.29); it assigns no rank to ffn_down. Measured per-kind KL sensitivities give a different ordering (attn_k highest at 0.0128 ΔKLD per MB; isolated sensitivities sum to 0.352 against a joint 0.346). The end-to-end margin of allocated over uniform rank at 27B is unresolved (0.5 unpaired standard errors), and the 27B weights were priors carried over from 0.6B [source: research/lowrank/JOURNAL.md; review/adversarial-code.md §B6].

Sensitivity estimated from one side anti-correlates with measured sensitivity (Spearman −0.29 for the gradient side, as for the input side); the two-sided product correlates positively (+0.39) [source: research/lowrank/experiments/20-gradgram/REPORT.md]. Measured two-sided full-covariance correction at 0.6B gives 27.27 against 27.58 one-sided at equal bytes.

The full list, with the cases reported in other sections:

| # | what improved on the surrogate | what the outcome did | where |
|---|---|---|---|
| 1 | energy captured by greedy rank allocation | perplexity worse than uniform (34.17 vs 31.29) | this section |
| 2 | exact descent of a two-sided Kronecker metric with measured T | +0.048 nats vs input-only (t = +15.5); worse than the untouched file with a cleaner T | §3.2 |
| 3 | a further 2.6% descent of the same whitened objective | no change (\|t\| ≤ 1.32) | Appendix A.4 |
| 4 | importance moved into column scales (equalization) | KLD worse at every strength tested (α = 0.25 to 1; t = +5 to +29), with or without an imatrix | Appendix A.3 |
| 5 | whitened capture at equal bytes (shared basis, +12% on attention groups) | KLD within the bound of §4.4 (t = −0.91) | §4.4 |
| 6 | residual capture of the base (NVFP4 0.736 vs Q3_K_M 0.721) | smaller gain (50% vs 70% recovery) | Appendix A.1 |
| 7 | KL divergence to the reference, across refinement families | opposite ordering to NLL in the same run at 2,048 tokens, and to a coding suite | §5.4, §5.5 |

Rows 1-6 are surrogates for KL divergence that did not predict KL divergence. Row 7 is of a different kind: KL divergence measures distance to the reference model, and a file trained against data can move away from the reference while assigning the text higher likelihood (§5.4). Within one refinement family we have no evidence that KL divergence predicts task outcomes at the size of the contested margins either: the one task evaluation we ran there (HellaSwag, §4.3) tied the contested pair.

## 4.8 Conditioning the correction on a task distribution

Conditioning the correction's covariance on a code corpus gives 10.3% lower perplexity on code at identical bytes (2.746 against 3.061 for the wikitext-conditioned correction; about 8 unpaired standard errors) and costs 27% on wikitext (34.90 against 27.58). Broad calibration transfers to a narrow distribution better than the reverse [source: research/lowrank/experiments/17-task-conditioned/README.md]. Per-workload adapters are possible through llama.cpp's per-request adapter API.

The calibration grid of §3.1 measures the same question at 0.8B on KL divergence: whichever of five calibration texts the correction is whitened with (wikitext, code, FineWeb-Edu, or a 2:1 or 1:1:1 interleave), it lowers its base on all three evaluation corpora (|t| ≥ 19 in every cell), with the largest gain on the calibrated distribution. Unlike re-rounding (§3.1), the correction does not damage text it was not calibrated on; it helps it less. The measurement is also a caution: the method's sensitivity to distribution (10-27% here; a halving of the increment at 27B, §4.3 and §4.5) is larger than every contested margin, which is why every comparison in §3-§5 now carries held-out corpora.

## 4.9 Ordering of operations

From §3.3 and the alternation runs: shaping the base and the corrector jointly beats composing them greedily. At 0.6B, two alternation rounds (perplexity 25.27) are better than a one-shot re-round under a ProjQ-style metric (25.94), which is better than re-round-then-correct (26.80); these are unpaired, and the ordering is paired-resolved at 0.8B (§3.3) [source: research/lowrank/experiments/13-rerounder/README.md; review/adversarial-claims.md §F14]. The alternation is LoftQ's; what is specific here is the black-box llama-quantize grid and the whitened metric. A second rule comes from the mixed base and from §4.5 and §4.6: put the correction where rank is a large fraction of width, and spend base bits elsewhere.
