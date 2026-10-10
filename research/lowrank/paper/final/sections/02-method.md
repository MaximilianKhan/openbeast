# 2. Method

The pipeline has four stages: capture second-moment statistics on calibration text (§2.1); fit whitened corrections in closed form (§2.2) or re-round the base's codes on their frozen grids (§2.3); optionally alternate quantizer and corrector (§2.4); serve through llama.cpp's adapter path or our fused kernels (§6). Quality claims follow the measurement protocol of §2.5.

**Terms.** The *imatrix* is llama.cpp's importance statistic: for each weight column, the mean squared input activation on calibration text, which llama-quantize uses to weight its rounding error. *K-quants* (Q2_K, Q3_K_S, Q3_K_M, Q4_K_M, Q6_K) are llama.cpp's block-wise scalar quantizers; the digit is the nominal bit width of the main tensor type and the suffix (S, M) says how many tensors are promoted to a wider type. *I-quants* (IQ1_S, IQ2_XS, IQ3_XXS, IQ3_XS) are its codebook-based types at similar or smaller sizes. We call the set of stock types ordered by file size *the ladder* and one stock type *a rung*. *UD* marks Unsloth Dynamic files, vendor quantizations that choose a type per tensor. *MTP* is the multi-token-prediction head some of these checkpoints carry; we pin its tensors to a fixed type. *GDN* is Gated DeltaNet, the linear-attention layer of the hybrid architecture (llama.cpp name `qwen35`) used by Qwen3.5-0.8B and Qwen3.8-27B. The sizes of the types used at 27B, each one quantization step from BF16 [source: research/lowrank/experiments/27-bf16-rederivation/README.md]:

| type | file GB (Qwen3.6-27B) | bits per weight (file bytes × 8 / parameters) |
|---|---|---|
| Q2_K | 11.00 | 3.22 |
| IQ3_XXS | 11.48 | 3.36 |
| IQ3_XS | 12.26 | 3.59 |
| Q3_K_S | 12.37 | 3.62 |
| Q3_K_M | 13.59 | 3.98 |
| Q4_K_M | 16.84 | 4.93 |

A nominal "Q2_K" file is above 3 bits per weight because llama-quantize promotes some tensors to wider types. Bits per weight in this paper are file bytes over parameter count unless marked nominal.

**Configurations.** We use four names throughout. *Re-rounding* is the code re-selection of §2.3. *Full-covariance correction* is the low-rank adapter of §2.2 whitened by the full input Gram, as opposed to the imatrix diagonal. *The mixed base* is the 27B base with FFN tensors at Q3_K and the remaining tensors at Q2_K. *The corrected configuration* is a base plus its correction; at 27B it means the mixed base plus a rank-128 full-covariance correction with Q8_0 factors on the non-FFN tensors. *The control* is a file with no adapter, built from stock tensor types at the corrected configuration's byte count.

## 2.1 Statistics capture

**Input-side Grams.** llama-imatrix accumulates diag(E[x²]) per weight column, which is the diagonal scaling used by LQER (arXiv:2402.02446) and QERA-approx (arXiv:2410.06040). We extended it (a local, environment-gated C++ patch of about 140 lines) to accumulate the full Gram G = Σ x xᵀ per distinct layer input: one for the attention q/k/v input, one for the FFN gate/up input, one each for attn_output and ffn_down. Accumulation is fp32. The diagonal of the captured Gram matches the stock imatrix to a median relative error of 2e-8, and the matrices are symmetric and positive semidefinite [source: research/lowrank/experiments/10-full-covariance/README.md]. Inputs wider than 8,192 dimensions are captured block-diagonally in 8 blocks, an approximation forced by memory whose consequence is stated in §2.2.

**Mixture-of-experts extension.** The expert-routed branch needs its own accumulator, because rows from different experts are mixed and must be unpacked per (token, slot) pair. We capture one pooled Gram per expert stack, the union of routed inputs, and not a Gram per expert: at 16 calibration chunks there are about 256 samples per expert for a 2,048-dimensional Gram, which is rank-deficient, while the pooled Gram is full-rank. The validation gates pass on Qwen3.6-35B-A3B [source: research/lowrank/experiments/23-moe/REPORT-capture.md].

**Output-gradient Grams.** For two-sided metrics we built llama-gradmatrix, which runs llama.cpp's training loop forward and backward with learning disabled and captures T = Σ g gᵀ per tensor, where g is the loss gradient at the output row. Two repairs were needed. llama.cpp's trainer does not backpropagate through the KV cache (upstream issue #21037 reports the same gap), which we bypass by feeding k/v directly to attention when the window is one micro-batch. Several operations of the GDN layers have no backward pass, which we handle with an explicit gradient cut that stops flow through those layers' token mixing and keeps the residual path. At 0.8B the cut biases the 18 linear-attention layers, which matters in §3.2 [source: research/lowrank/experiments/20-gradgram/REPORT.md].

## 2.2 Whitened low-rank correction

Given the residual R = W_ref − dequant(Q) and a whitener L (the damped Cholesky factor of G/count; damping 0.01 × mean diagonal, the convention of arXiv:2210.17323 and arXiv:2501.18475; the route is numerically viable at this size by arXiv:2403.07378), the rank-r corrector minimizes the activation-weighted error ‖(R − Δ) L‖_F². The two-sided weighted problem is NP-hard (arXiv:1012.0197); the one-sided problem has a closed form: whiten, truncate, unwhiten. It is the L_g = I case of the two-sided objective of OBD-LLM (arXiv:2604.00821). We stay one-sided for three reasons: a tool that starts from a shipped file has no gradients; a diagonal output weighting provably cannot change a frozen-grid rounding decision (§2.3); and the published two-sided gain is on weight decomposition at 8B, not on residual correction of K-quant grids [source: research/lowrank/paper/theory-L6-family-subsumption.md, Claim 4].

We use the projection form: B = U_r, the top-r left singular vectors of R L, and A = Bᵀ R. For any invertible right whitener the unwhitening factor cancels, so this is the exact optimum; our code check agrees with the explicit argmin to 7e-15 [source: research/lowrank/review/adversarial-code.md §A3]. Factors are computed by randomized SVD (arXiv:0909.4061; oversampling 32, 4 power iterations).

**The blocked-whitening caveat.** For inputs above 8,192 dimensions the captured Gram is block-diagonal. At 27B this applies to ffn_down, whose input has 17,408 dimensions (8 blocks of 2,176). The projection form is then optimal for the block-diagonal surrogate and not for the true E[xxᵀ]; a synthetic test with cross-block correlation measured a 25% excess of true-metric loss for the blocked optimum [source: research/lowrank/review/adversarial-code.md §B1]. Every "full-covariance" 27B row therefore means full covariance for inputs up to 8,192 dimensions and 8-block-diagonal for ffn_down. End-to-end measurements score the artifact and are unaffected; the mechanism claim that the metric was the bottleneck is untested against a truly full metric at that width.

**Two-sided variant.** With both S and T captured, B = L_T^{-ᵀ} U_r and A is solved against L_S, with a factor-balancing step that leaves the product unchanged and prevents half-precision overflow [source: research/lowrank/review/adversarial-code.md §A4].

**Factor density.** Factors are stored as Q8_0 (llama.cpp's 8-bit block format), which halves their bytes at measured quality parity (rank 64 with Q8_0 factors: perplexity 31.26 against 31.29 with F16 factors at 0.6B), the observation of arXiv:2406.08903 and arXiv:2303.08302 confirmed in this pipeline [source: research/lowrank/JOURNAL.md, hour 2-3].

**Serving and what is not stock.** The corrected model is a quantized base GGUF plus a LoRA-form adapter GGUF, and y = Q·x + s·B·(A·x) is llama.cpp's stock adapter path. The artifact therefore serves on an unmodified llama-server, at the decode cost §6 measures. Capture is not stock: every full-covariance number depends on our Gram patch to llama-imatrix, and gradient Grams on llama-gradmatrix.

## 2.3 Re-rounding on frozen grids

A K-quant tensor stores, per 16-element sub-block, a scale and offset (the grid) and a low-bit code per element. llama-quantize chooses both and rounds each element independently. We keep the grid bytes exactly as written and re-choose only the codes to minimize the whitened objective of §2.2 with Δ = 0, which is a closest-vector problem on the frozen grid. The solver is the GPTQ error-feedback sweep: process columns in order, round the current column on its grid, and propagate the scaled error to the unprocessed columns through the upper-triangular Cholesky factor of the inverse Gram. With frozen grids the objective does not increase from sweep to sweep (arXiv:2606.01412).

The objective needs W_ref, the higher-precision weights, and the Gram, which is captured on the patched binary. The method therefore starts from a model's source weights plus a calibration pass, and writes a file that is byte-compatible with the stock one: same size, same type tags, loadable by any llama.cpp build.

Two related methods work the same quadratic. ReQuant (arXiv:2608.07019) performs cyclic coordinate descent on it outside the GGUF format, and SchurQuant (arXiv:2608.15567) also refits the scales [source: research/lowrank/paper/theory-L6-family-subsumption.md, Claim 1, Claim 3].

Implementation points that affect the numbers: the Q2_K codec was mirrored from gguf-py and round-trips real tensors byte-identically; the triangular convention matters (a lower factor disables the feedback and reproduces llama-quantize's codes exactly); and Cholesky failures escalate damping [source: research/lowrank/experiments/13-rerounder/README.md]. Only the Q2_K codec is implemented. A two-sided (Kronecker) generalization of the sweep is derived and tested in the experiment record, together with a small negative result: a diagonal output-side weighting cannot change any code, because each row's argmin is scale-invariant [source: research/lowrank/experiments/24-yaqa-lite/README.md].

## 2.4 Alternation

Correction quality depends on the base, and the base's grid choices depend on what the corrector will absorb. We alternate Q_{t+1} = quant(W − C_t) and C_{t+1} = corr(W − Q_{t+1}), which is LoftQ's template (arXiv:2310.08659) with llama-quantize as a black-box quantizer and the whitened closed form as the corrector, and we keep the best iterate and stop when a round is worse. Because the quantizer's output set is finite and the corrector step is an exact minimum, the best-iterate value is non-increasing and becomes constant after finitely many rounds. In practice two or three rounds are used and later changes are below noise. The argument, its measured amendment (iterates settle onto a small limit cycle and not a fixed point), and the pointers to related metric-modification schemes are in Appendix A.5 [source: research/lowrank/paper/theory-alternation-convergence.md].

## 2.5 Measurement protocol

Results in §3-§6 follow these rules or are labeled legacy [source: research/lowrank/PROTOCOL.md].

- **Reference and provenance.** The reference is BF16 where it exists. For the mixture-of-experts model of §4.6 we had no BF16 and used the vendor UD-Q4_K_M file, stated at the table. Every base and adapter derives from the reference in one quantization step; rows of other provenance are labeled legacy and are not tabled beside single-step rows without a provenance label.
- **Metrics.** Mean KL divergence (KLD) and top-1 agreement against reference logits at a 512-token context, on at least 40 chunks (100 for the 27B comparison of §4.3; 20 for the mixture-of-experts table and some legacy rows), with standard errors. KLD is used because it tracks answer flips (arXiv:2407.09141) and is the measure llama.cpp's maintainers use (discussion #4110). Full-corpus perplexity is secondary (arXiv:2606.19558).
- **Paired statistics.** Compared files share evaluation tokens and reference logits. A comparison is decided by the per-chunk difference: its mean, standard error and t. We write t for a paired statistic with n − 1 degrees of freedom, where n is the chunk count given with it, and we reserve "standard errors" for unpaired distances, labeled as such.
- **What the t statistics are conditional on.** All intervals are conditional on one calibration sample and one evaluation corpus; they quantify evaluation-chunk variance only. An accidental control put calibration-sampling variance at 0.55 perplexity points (about 2%) at 0.8B, which is larger than several small-scale margins (§3.1).
- **Multiplicity.** No family-wise correction was applied across the paper's contrasts, so margins near |t| = 2-3 are suggestive and not confirmatory.
- **Dependence between chunks.** Adjacent 512-token chunks of one corpus are not independent. For the load-bearing pairs we report, in addition to t, an exact sign test, a Wilcoxon signed-rank test, and a bootstrap over contiguous blocks of 10 chunks [source: research/lowrank/experiments/35-final-reanalysis/RESULTS.md].
- **Byte-fair comparison.** Total bytes are base plus adapter. A comparison against the ladder includes K-quants, I-quants, and where built a mixed-type control at the matched byte count.
- **Speed.** Same-session A/B pairs only. Between-session drift on identical configurations reached 9%.
- **Pre-registration.** From the second internal review onward, each experiment's configuration, pairs and decision rules were written to the project journal with a timestamp before the run. The journal is the project's own record and has not been audited externally.
- **Claims.** We call a relation a law only with at least three scale points and a stated functional form; otherwise it is an observation.
