# Appendix A. Additional negative results

These results are summarized in the main text and given here in full.

## A.1 A correction on an NVFP4 base at 0.8B

NVFP4 is a 4-bit floating-point block format. A whitened low-rank correction on a frozen NVFP4 base of Qwen3.5-0.8B improves that base on 40/40 chunks (KLD 0.207 → 0.104, top-1 +6.2 points) and loses to the ladder: the 603 MB composite is behind every stock rung at or below its bytes that we scored (Q4_K_S at 519 MB, 0.047; Q5_K_S at 578 MB, 0.015). Under llama.cpp's quantizer, which has no importance weighting specific to NVFP4, the bare NVFP4 file is the worst 4-bit option on this architecture (0.207 against 0.088 for Q4_0 at equal bytes). Its residual is slightly more capturable than that of Q3_K_M (capture 0.736 against 0.721), yet the correction recovers less of the damage (50% against 70%). This is row 6 of the table in §4.7 [source: research/lowrank/experiments/34-e16-nvfp4-08b/README.md].

## A.2 The thinnest base: IQ1_S with large factors

This test takes the idea of replacing base bits with factors as far as llama.cpp's formats allow. The base is IQ1_S, the thinnest type available (nominally 1.56 bits per weight; the 7.44 GB file is 2.18 bits per weight by file bytes). With rank 512 on non-FFN tensors and rank 256 on FFN tensors (2.52 GB of Q8_0 factors), recovery of the base's KLD damage rises to 52%, and the result still loses to bare Q2_K at near-equal total bytes: perplexity 10.50 and KLD 0.431 at 9.96 GB against 7.891 and 0.153 at 10.86 GB. These are legacy 27B rows, scored against a Q6_K reference.

The exchange rate is the point. Going from 0.80 GB to 2.52 GB of factors lowered KLD from 0.527 to 0.431, which is 0.056 per GB. Base bits bought 0.216 per GB (IQ1_S to Q2_K) or 0.323 per GB (IQ1_S to IQ2_XS), four to six times as much [source: research/lowrank/experiments/15-iq1-carrier/README.md; experiments/35-final-reanalysis/RESULTS.md]. Published work on real factorization ratios reports retraining budgets above 50 billion tokens (arXiv:2310.06694, arXiv:2407.14679); a calibration-only correction does not substitute for that.

The corrected IQ1_S file with the smaller adapter (8.24 GB on disk, KLD 0.891 → 0.527, top-1 +8.5 points, 16.5 unpaired standard errors) sits where we measured no rival rung. The equal-byte rivals IQ1_M and IQ2_XXS were not built, and 8.24 GB is file size; VRAM would be about 10.3-10.5 GB on the measured adapter-overhead pattern [source: research/lowrank/review/adversarial-claims.md §F4].

## A.3 Equalization before quantization

The AWQ and SmoothQuant line of work moves importance into the weights by scaling columns so the quantizer spends its range where activations are large. We tested the full-strength version on llama.cpp's imatrix-weighted quantizer. On this architecture only diagonal scalings fold exactly, because every candidate site sits behind a normalization with a learnable gain; restricting the scales to powers of two makes the fold bit-exact (equalized BF16 against stored BF16: KLD 0.000000, top-1 100%).

Requantizing the equalized model with the same recipe at the same bytes is strongly harmful: perplexity 33.09 → 41.73, paired ΔKLD t = +28.3 (n = 40, equalized better on 0 chunks), ΔNLL t = +59.8 (n = 580). The quantizer's own weighted error rises 6-68% per folded tensor and is unchanged on the untouched control tensors [source: research/lowrank/experiments/26-lloyd-gauge/README.md].

The mechanism we can support is a format interaction: the fold puts up to 32× dynamic range into 16-element sub-blocks whose shared 4-bit sub-scales cannot span it. Whether there is a separate double-counting of importance is not tested by this run; the controls that would test it (equalization without an imatrix, and a strength sweep) have not been run. The result is scoped to full strength on one format, which is where that literature itself expects harm. This is row 4 of the table in §4.7.

## A.4 Further descent of the same objective

A natural extension of single-pass re-rounding alternates the code sweep with a per-row least-squares refit of the format's storable scales, both steps descending the same whitened objective, with the 4-bit sub-scales frozen so the file stays byte-compatible. Run to a pre-registered cap of 8 iterations at 0.8B, the alternation lowers the whitened objective 2.6% below single-pass re-rounding. End to end nothing changes: paired ΔNLL +0.0009 ± 0.0022 (t = +0.40), ΔKLD +0.0015 ± 0.0034 (t = +0.43), Δtop-1 −0.55 ± 0.42 points (t = −1.32). The same harness resolves re-rounding against the untouched file at t = −30.3 (KLD) and −44.9 (NLL) [source: research/lowrank/experiments/26-lloyd-gauge/README.md].

No tensor reached a fixed point of the codes. Of 96 tensors, 92 ran to the cap and 4 stopped earlier on a plateau rule; the objective falls for three or four iterations and then enters a cycle of about 0.1% amplitude. Single-pass re-rounding is therefore the recipe we recommend, and this is row 3 of the table in §4.7.

## A.5 Alternation: the termination argument and its amendment

The alternation of §2.4 keeps the best iterate and stops when a round is worse. The project's theory note makes three claims about it [source: research/lowrank/paper/theory-alternation-convergence.md]. First, the quantizer's reachable output set is finite and the corrector step is an exact minimum, so the best-iterate value is non-increasing over a finite set and becomes constant after finitely many rounds. Second, the first round improves exactly when subtracting the corrector steers the quantizer to a grid point whose residual is more compressible at rank r in the whitened metric; this was observed at both small sizes (whitened capture 0.37 → 0.46 → 0.51 across rounds at 0.6B). Third, and informally, per-round gains decay as the composed map approaches a fixed point.

The third claim needed an amendment. When a related alternation was pushed to its 8-iteration cap at 0.8B (B.4), no fixed point of the codes was reached; the iterates settle onto a small limit cycle. The best-iterate argument is unaffected. The quantizer's internal objective differs from the whitened one, which bounds what alternation can reach; schemes that modify the quantizer's metric address this (arXiv:2606.00494; arXiv:2507.09428). In practice we used two or three rounds, and changes after the second were below noise.
