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

## 4.3 Correction against the stock types at three dense sizes

**27B.** The 27B comparison was rebuilt under clean provenance: every base one quantization step from the BF16 reference, a fresh 48-chunk BF16 imatrix and Gram capture, BF16 reference logits, and paired statistics [source: research/lowrank/experiments/27-bf16-rederivation/README.md]. The corrected configuration is the mixed base (FFN tensors at Q3_K, the rest at Q2_K) plus a rank-128 full-covariance correction with Q8_0 factors on the non-FFN tensors (mean whitened capture 0.42). The control is a mixed-type file with no adapter, built once at the corrected configuration's byte count (Q3_K_S with attn_k, attn_v and attn_output promoted to Q4_K and a partial attn_qkv promotion; −0.016% bytes), its bytes predicted in advance from measured per-kind sensitivities. Results on 100 chunks of wikitext-2 test:

| file | GB | PPL | KLD | top-1 % |
|---|---|---|---|---|
| mixed base, no adapter | 12.16 | 7.235 ± 0.114 | 0.0967 ± 0.0024 | 87.2 |
| IQ3_XS | 12.26 | 7.303 ± 0.114 | 0.0767 ± 0.0021 | 88.8 |
| Q3_K_S | 12.37 | 7.378 ± 0.118 | 0.0924 ± 0.0024 | 87.5 |
| corrected configuration | 12.50 | 7.204 ± 0.114 | 0.0876 ± 0.0024 | 87.9 |
| mixed-type control | 12.50 | 7.194 ± 0.114 | 0.0814 ± 0.0021 | 88.3 |
| Q3_K_M | 13.59 | 7.128 ± 0.113 | 0.0609 ± 0.0016 | 89.9 |

Paired per-chunk differences, n = 100 [source: research/lowrank/experiments/27-bf16-rederivation/results-100ch-paired.txt; experiments/35-final-reanalysis/RESULTS.md]:

| pair (A − B) | ΔKLD | t | A better on | Wilcoxon p | block-bootstrap 95% interval | ΔNLL t | Δtop-1 t |
|---|---|---|---|---|---|---|---|
| corrected vs Q3_K_S | −0.0048 ± 0.0017 | −2.85 | 71/100 | 8.5e-5 | [−0.0073, −0.0019] | −5.75 | +2.04 |
| corrected vs control | +0.0063 ± 0.0024 | +2.62 | 38/100 | 0.0023 | [+0.0032, +0.0100] | +0.41 | −2.14 |
| control vs Q3_K_S | −0.0110 ± 0.0015 | −7.23 | 91/100 | 1e-14 | [−0.0133, −0.0081] | −7.35 | +5.55 |
| corrected vs mixed base | −0.0090 ± 0.0011 | −8.52 | 92/100 | 5e-13 | [−0.0109, −0.0068] | −1.87 | +5.20 |
| mixed base vs Q3_K_S | +0.0043 ± 0.0018 | +2.38 | 32/100 | 0.0012 | [+0.0006, +0.0077] | −3.90 | −1.61 |

At 100 chunks the corrected configuration is ahead of Q3_K_S by 0.0048 KLD (5%, t = −2.85, 71/100 chunks) and behind the same-byte mixed-type control by 0.0063 (t = +2.62, 62/100; no difference in NLL). Both margins are small, on-distribution, and unreplicated. The correction's gain over its own base is larger and well resolved (−0.0090, 92/100 chunks). At this byte count the measured order is the mixed-type control, then the corrected configuration, then Q3_K_S, with IQ3_XS below all three at fewer bytes and no adapter.

**How the sample was reached.** The same files were scored at 20, 40 and 100 chunks, and each sample is a prefix of the next: the per-chunk values of the 40-chunk run equal the first 40 of the 100-chunk run to printed precision. At 20 chunks corrected-versus-Q3_K_S was a tie (t = +0.57) and the control led at t = +1.87. At 40 chunks the tie was exact (KLD 0.0831 against 0.0832, t = −0.04) and the control's lead was t = +2.26. The 100-chunk run was pre-registered as the final sample, with the rule that |t| < 2 would certify parity. No rule for a win was registered, and no adjustment for three looks. The t = −2.85 is therefore the third look at accumulating data (p = 0.005 two-sided; 0.016 after a factor of three).

**Checks that do not assume independent chunks.** For corrected-versus-Q3_K_S the exact sign test gives p = 3e-5, the Wilcoxon test p = 8.5e-5, a bootstrap over contiguous blocks of 10 chunks gives [−0.0073, −0.0019], a t-test on the ten block means gives t = −3.19 (p = 0.011), and the lag-1 autocorrelation of the differences is 0.00. For corrected-versus-control the corresponding values are p = 0.021, p = 0.0023, [+0.0032, +0.0100], t = +3.37 (p = 0.008) and −0.06. The control's margin does not survive a Bonferroni factor of six on its t-test (p = 0.061) and has no counterpart in NLL (t = +0.41, with the corrected configuration ahead on 58/100 chunks).

**The margin over Q3_K_S is not uniform across the corpus.** On chunks 1-40 the difference is −0.0001 (t = −0.04); on chunks 41-100 it is −0.0079 (t = −3.32); the two blocks differ at Welch t = 2.51 (p = 0.014). The correction's increment over its base is the same in both blocks (−0.0093 and −0.0089). What changes is the base: the mixed base is behind Q3_K_S by 0.0092 on chunks 1-40 (t = +4.3) and level with it on chunks 41-100 (+0.0010, t = +0.4). The corrected configuration is thus ahead of Q3_K_S on the part of the test set where its base is level with Q3_K_S, and level where its base is behind. The control's lead is larger in the first block (+0.0092, t = +2.26) than in the second (+0.0043, t = +1.47). Before the rebuild, the legacy tables had shown an unpaired margin of one standard error for the corrected configuration whose sign reversed under clean provenance [source: research/lowrank/review/adversarial-stats.md §F1, §F10].

**Further facts.** IQ3_XS has the lowest KL divergence near this byte count (0.0767 at 12.26 GB) and needs no adapter. Every rebuilt rung scores 0.002-0.003 KLD better than its legacy requantized sibling; the rebuild changed the imatrix along with the provenance, and the one rung that was already single-step (Q4_K_M) improved by the same amount, so the improvement cannot be attributed to provenance alone.

A task-level check does not resolve the contested pair. On the HellaSwag validation set (10,042 items, identical per file) the corrected configuration, Q3_K_S and Q3_K_M score 82.25%, 82.39% and 82.85%; no gap reaches the pre-registered 1.4-point threshold. A post-hoc paired McNemar test finds the corrected configuration and Q3_K_S tied (z = −0.81, with the sign opposite to the KLD difference) and Q3_K_M ahead of both (z = +3.48 and +2.82) at a KLD difference of about 0.03. The task resolves the larger KLD difference and not the smaller one; it does not validate KLD at the size of the contested margins [source: research/lowrank/JOURNAL.md, 2026-08-11 T1.8].

![Figure 1. Qwen3.6-27B near 12.5 GB, 100 chunks, every file one quantization step from BF16; error bars are standard errors. The correction (arrow) moves the mixed base past the stock type Q3_K_S; a mixed-type control with no adapter at the same bytes is lower, and the I-quant type IQ3_XS is the lowest of the group.](figures/fig-ladder-27b.svg)

**0.6B and 0.8B.** At 0.6B the corrected configuration (two alternation rounds, full-covariance correction, Q8_0 factors; 382 MB) reaches perplexity 25.27 and KLD 0.209 against Q3_K_M at 347 MB (26.06 and 0.238); the KLD difference is about 5 unpaired standard errors, on-distribution [source: research/lowrank/RESULTS_ROLLUP.md; review/adversarial-stats.md §F7]. At 0.8B, scored against BF16, a rank-128 full-covariance correction of Q2_K ties Q3_K_M, and one alternation round is ahead of it at 9% more bytes (KLD 0.147 against 0.167; no error bars were logged for this row) [source: research/lowrank/experiments/22-qwen35-08b/README.md].

At both sizes the comparison rung was chosen from below. Linear interpolation between the neighbouring rungs at the corrected configuration's byte count (about 0.118 at 382 MB and 0.080 at 523 MB) is better than the corrected configuration, and Q4_K_M at 4% more bytes is three to four times lower in KLD. A stock rung scored later against the same 0.8B reference, Q4_K_S at 519 MB, has KLD 0.047 against the 523 MB corrected configuration's 0.147 (unpaired) [source: research/lowrank/experiments/34-e16-nvfp4-08b/results-e16-08b.txt]. No mixed-type control was built at these sizes. What the small-scale runs show is that the method transfers to a second architecture and lands between two discrete rungs; they do not show a result against the ladder.

A correction on an NVFP4 base at 0.8B improves its base and loses to every stock rung at or below its bytes; the details are in Appendix A.1.

**Two qualifications on all of the above.** Every corrected configuration pays a decode cost of 20-40% on stock llama.cpp that the bare rungs do not (§6). And the tables in this subsection are on-distribution: calibration and evaluation share wikitext-2 (train and test splits), and §4.8 shows the method is sensitive to distribution. The held-out evidence for the correction increment at 27B is the FineWeb-Edu control of §5.3; the ladder comparisons themselves have not been repeated on a held-out corpus.

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

With the estimator held to full covariance, the fraction of the base's KLD damage that correction recovers rises as the base gets thinner: 20% at Q2_K, 28% at IQ2_XS, 41% at IQ1_S (legacy 27B rows) [source: research/lowrank/RESULTS_ROLLUP.md corrections 6; review/adversarial-stats.md §F5]. The three points do not hold the recipe fixed: adapter budgets are 0.93, 0.93 and 0.80 GB and the rank mix varies, so the curve is confounded. Its direction agrees with the published claim that correction pays most at the lowest bit widths (arXiv:2604.07955) and with EoRA's results.

Pushing this to the thinnest base llama.cpp offers, IQ1_S with 2.52 GB of factors, still loses to bare Q2_K at near-equal total bytes by about a factor of three in KLD. Per gigabyte, added factor bytes bought about 0.06 KLD where base bits bought 0.22 to 0.32. The details are in Appendix A.2.

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
| 4 | importance moved into column scales (equalization) | perplexity 33.09 → 41.73 (t = +28 on KLD, +60 on NLL) | Appendix A.3 |
| 5 | whitened capture at equal bytes (shared basis, +12% on attention groups) | KLD within the bound of §4.4 (t = −0.91) | §4.4 |
| 6 | residual capture of the base (NVFP4 0.736 vs Q3_K_M 0.721) | smaller gain (50% vs 70% recovery) | Appendix A.1 |
| 7 | 512-token KL divergence, across refinement families | opposite ordering on a coding suite; one pair, one run per arm, unreplicated | §5.5 |

Rows 1-6 are surrogates for KL divergence that did not predict KL divergence. Row 7 is of a different kind and weaker: KL divergence against a task outcome, in a single pair. We do not have evidence that KL divergence predicts task outcomes at the size of the contested margins within a family either: the one task evaluation we ran there (HellaSwag, §4.3) tied the contested pair.

## 4.8 Conditioning the correction on a task distribution

Conditioning the correction's covariance on a code corpus gives 10.3% lower perplexity on code at identical bytes (2.746 against 3.061 for the wikitext-conditioned correction; about 8 unpaired standard errors) and costs 27% on wikitext (34.90 against 27.58). Broad calibration transfers to a narrow distribution better than the reverse [source: research/lowrank/experiments/17-task-conditioned/README.md]. Per-workload adapters are possible through llama.cpp's per-request adapter API. The same measurement is a caution: the method's sensitivity to distribution (10-27%) is larger than every contested margin in §4.3, which is why §3.1 and §5.3 report held-out corpora and why the comparisons of §4.3 are labeled on-distribution.

## 4.9 Ordering of operations

From §3.3 and the alternation runs: shaping the base and the corrector jointly beats composing them greedily. At 0.6B, two alternation rounds (perplexity 25.27) are better than a one-shot re-round under a ProjQ-style metric (25.94), which is better than re-round-then-correct (26.80); these are unpaired, and the ordering is paired-resolved at 0.8B (§3.3) [source: research/lowrank/experiments/13-rerounder/README.md; review/adversarial-claims.md §F14]. The alternation is LoftQ's; what is specific here is the black-box llama-quantize grid and the whitened metric. A second rule comes from the mixed base and from §4.5 and §4.6: put the correction where rank is a large fraction of width, and spend base bits elsewhere.
