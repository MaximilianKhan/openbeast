# R7: VQ / lattice / trellis / rotations, richer calibration metrics, MoE compensators

Slice: neighbouring method classes the paper does not compare (§7.1, §8), the richer metrics, and MoE quantization with compensators.
Sweep date: 2026-10-22.

## 1. Coverage

- **Harvest.**
  - The arXiv export API returned HTTP 429 for the whole session; the other sweep agents share this IP.
  - The OpenAlex free budget for the IP was also exhausted after 11 queries.
  - The main harvester was therefore the Hugging Face papers search API (`/api/papers/search`), which returns arXiv ids with full abstracts: 124 queries.
  - OpenAlex added 11 queries, which also picks up Zenodo and non-arXiv preprints.
  - Query families:
    - VQ, lattice, E8, Leech, trellis, TCQ, codebook, AQLM, PV-Tuning, QuIP#, QTIP, VPTQ.
    - Rotation, Hadamard, incoherence, QuaRot, SpinQuant, learned rotation, rotation with low-rank.
    - Fisher, Kronecker, two-sided, end-loss, GuidedQuant, output-adaptive Hessians, OBD, YAQA, diagonal-vs-full Hessian.
    - Calibration and domain shift, re-rounding and learned rounding, GPTQ asymmetric calibration and error propagation.
    - MoE quantization, MoE low-rank compensators, expert shared bases, MoE bit allocation, MiLo, TileQ.
    - GGUF, llama.cpp, imatrix, KLD and flip-rate evaluation.
- **Volume.**
  - 4,775 unique records harvested.
  - 1,525 pass a quantization-and-LLM filter: 1,384 dated 2024 or later, 1,136 dated 2025 or later. All ~1,500 titles were screened by hand.
  - About 140 abstracts were read in full and 61 arXiv abstract pages fetched to verify id, title, date and authors.
  - About 12 papers were read in full text or detailed HTML, plus 3 GitHub PRs and 1 HF model card.
- **Date range.** Emphasis on 2025-01 to 2026-10-21; foundational 2024 papers were re-verified.
- **Verification.** Every id below was fetched from arxiv.org/abs (title, date and authors confirmed), unless marked otherwise. Non-arXiv items were fetched from their landing pages.

## 2. Threats

### T1. The §7.1/§8 framing "vector and lattice quantizers ... outside the GGUF formats" is factually wrong as written. **Severity: must-fix.**

Three facts contradict it:

- **llama.cpp's own I-quants are lattice-codebook vector quantizers.**
  - PR #4773 (IQ2_XXS, ikawrakow) encodes magnitudes of groups of 8 weights with a 256-point subset of the E8 lattice. Its stated goal is "perplexity similar to QuIP#", and it explicitly omits QuIP#'s incoherence processing and Hadamard transforms.
  - PR #5196 (IQ3_XXS) uses "256 D4-lattice points" for groups of 4 at 3.0625 bpw.
  - We use IQ2_XS, IQ1_S and IQ3-class files throughout §4 and §5.
  - Fetched: github.com/ggml-org/llama.cpp/pull/4773 and /pull/5196.
- **Trellis types already exist as GGUF in the ik_llama.cpp fork.**
  - The fork ships IQ1_KT, IQ2_KT, IQ3_KT and IQ4_KT.
  - PR #113 (ikawrakow/ik_llama.cpp, Nov 2024 to Jun 2025) borrows QTIP's "3INST" generator, with no Hadamard and no Viterbi.
  - It reports about 0.2 bpw saved at equal quantization error against the i-quants and k-quants on Llama-3.1-8B.
  - On LLaMA-v2-7B at about 2 bpw, IQ2_KT gets PPL 6.36 against QTIP's 6.82 without fine-tuning, at 0.125 bpw more.
- **An equal-bytes study of a rotated trellis code inside GGUF/llama.cpp appeared 3 days ago.**
  - Source: Goyal (Genrius AI), "Small-State Coset Trellis Coding at IQ2_XXS Size: An Equal-Bytes Study in llama.cpp", Zenodo 10.5281/zenodo.23481757, 2026-10-19. Not on arXiv, not peer reviewed. Verified via the OpenAlex record W7222942398 and the HF model card Genrius-AI/Llama-3.1-8B-Pingala8-2bit.
  - Method: an 8-state Ungerboeck code over 4-weight symbols, behind a random Hadamard rotation and QuIP's LDLQ rounding, stored in GGUF. It needs an unmerged CUDA patch.
  - Qwen3-4B (post hoc): wikitext PPL 16.64, against 28.61 for IQ2_XXS and 18.15 for the best IQ2_KT.
  - Llama-3.1-8B (pre-registered):
    - Strided wikitext: 9.14, against 12.35 for IQ2_XXS and 10.04 for IQ2_KT.
    - With a BOS token at every window: wikitext 8.44 against 9.36, and C4 13.42 against 15.56, both against IQ2_KT.
    - The file is about 11 MB smaller than IQ2_XXS.
  - Qwen3-14B: it loses to IQ2_KT (9.57 against 9.40) and ties on C4.
  - Ablation: "removing the trellis memory costs 0.076 nats ... 14% of the gain over IQ2_XXS". About 86% of the gain over IQ2_XXS therefore comes from rotation, LDLQ re-rounding and column scales, not the trellis code.

**Affected text:**

- §7.1 "Baselines not compared": "vector and lattice quantizers at 2 bits, which are outside the GGUF formats".
- §8 neighbouring-classes paragraph: "vector and lattice quantizers at 2 bits, which are strong baselines outside the GGUF formats".

**Suggested reframe.** "llama.cpp's I-quants are themselves E8/D4 lattice-codebook quantizers without incoherence processing. What we do not compare are the classes that add incoherence processing (QuIP#, QTIP), trained codebooks (AQLM with PV-Tuning) or trellis codes. Trellis types already exist as GGUF in the ik_llama.cpp fork (IQk_KT), and a 2026 equal-bytes study puts a Hadamard-rotated, LDLQ-rounded trellis file at IQ2_XXS size in llama.cpp. We did not run them."

### T2. Rotation plus re-rounding at equal bytes does help at 8B, inside GGUF. This scopes C1. **Severity: must-cite-and-discuss.**

- **Sources.** The same Zenodo study as T1. Supporting evidence:
  - PolarQuant (arXiv:2603.29078, 2026-03-30): "Hadamard rotation alone accounts for 98% of the quality improvement" (Qwen3.5-9B, Q5 grid).
  - Tetra (arXiv:2609.35465, 2026-09-28, pre-registered, one L40S): a Leech-lattice file at 2.73 bits per parameter scores "23.6 [MMLU] points above llama.cpp's IQ2_XXS (2.48 bits per parameter)" at 4B.
- **Why it matters for C1.** C1 says re-choosing codes under a captured activation covariance gives no resolved gain at 8B and 27B. T1's study re-chooses codes with QuIP's LDLQ, which is GPTQ under H, on Llama-3.1-8B, one of our models. It gets a large equal-bytes gain, and that gain holds on C4.
  - This does not contradict C1 as scoped: our grid is frozen and unrotated, theirs is new and rotated.
  - It does locate the missing ingredient. Incoherence changes what the K/I-quant grid can represent, so "re-rounding fails at size" should be scoped to unrotated grids.
- **Affected text.**
  - §3.1 and §7.5: "gives no resolved gain at 8B and 27B".
  - §7.4 "Re-rounding at size" candidate tests: add "rotate first".
  - §8 "Re-selecting codes on a fixed grid".

### T3. Training-free low-rank compensation on a rotated VQ base recovers far more than ours, held-out corpus included. This weakens C2's generality. **Severity: must-cite-and-discuss.**

- **Source.** arXiv:2610.08164, Song et al., "Align, Then Correct: Training-Free Two-Stage Low-Rank Compensation for Extremely Quantized LLMs", 2026-10-06. Full text read.
- **Setup.** Rank-64 FP16 adapters on every linear layer, on a QuIP# base (RHT, LDLQ, E8P12, no fine-tuning) at 2.2 bits.
- **Qwen3-8B.**
  - WT2: 12.43 to 10.26 (FP16 9.73).
  - C4, held out: 19.19 to 17.17 (FP16 15.24). That is 51% of the gap recovered, against 31% for the best baseline, LQ-LoRA.
- **Qwen3-4B.**
  - C4: 29.21 to 21.36 (FP16 19.84), 84% of the gap recovered.
- **Mechanism.**
  - Stage 1 is an asymmetric calibration with a full output-Fisher weighting. Stage 2 is a rank-constrained natural-gradient step on re-measured statistics.
  - They argue the symmetric target (W−Ŵ)X is high-rank. This is consistent with our §4.1.
- **The Table 4 ablation matters.**
  - Asymmetric alone does not help: WT2 12.50 against 12.34.
  - Symmetric plus Fisher: 12.26.
  - Asymmetric plus Fisher: 10.26.
  - So the gain needs both changes. Our recipe has neither: it is symmetric, one-sided and input-whitened.
- **Cost.**
  - The adapters are 349 MB at Qwen3-8B (their Appendix G, Table 11), about 0.35 bpw, or ~15% of a 2.2-bit base.
  - The 0.35 bpw figure is our arithmetic, not theirs.
  - Their output-statistic cache is 47.5 GiB.
  - There is **no same-byte control** (higher-bit or mixed-type base at equal memory). The only nearby reference is QuIP# at 3 bits on Qwen3-8B, C4 16.30, which spends about 0.8 bpw more.
- **What it means for C2.**
  - Our "recovered fraction 7/12/32%" and "0.25-0.96 of wikitext gain off-distribution" describe our one-sided symmetric recipe, not low-rank correction in general.
  - The paper's bottom line, that correction loses to same-byte stock types, is not refuted: they never ran that control.
  - It also conflicts with §3.2, where two-sided made the outcome worse. They show a two-sided (output-Fisher) objective can work when the output factor is estimated over many samples. We attribute our failure to the T estimate; this paper is direct evidence for that reading.
- **Affected text.** §4.5, §7.1 "Baselines not compared", the §8 "Richer metrics" paragraph, and §7.4 "Estimators for two-sided metrics".

### T4. HIGGS "linearity theorem": a published result that a layer-wise ℓ2 surrogate does predict perplexity. Tension with C5 / §4.7. **Severity: should-cite.**

- **Source.** arXiv:2411.17525, Malinovskii et al., 2024-11-26.
- **Claim.** "A direct relationship between the layer-wise ℓ2 reconstruction error and the model perplexity increase". It uses this for data-free Hadamard plus MSE-optimal grids, and for DP bit allocation.
- **What our §4.7 says.** A surrogate that improved failed to predict the outcome seven times. Reviewers may cite HIGGS against us.
- **Reconciliation to state.**
  - HIGGS's linearity is for rotated, near-isotropic errors with per-layer coefficients.
  - Our surrogates are calibration-whitened and change the error's direction, not only its size.
- **Supporting the C5 side:**
  - arXiv:2609.26173 (2026-08-09): within a component type, reconstruction error explains "<10% of the variance in perplexity sensitivity in 27 of 36 cases"; median R² = 0.044.
  - arXiv:2609.09867 (2026-09-09): KL composition residuals are "26x and 42x the sums of absolute midpoint pricing errors".
  - arXiv:2609.01587 (2026-09-01): "cheap signals that correlate with quantization damage do not necessarily identify where restoring precision improves accuracy".

### T5. MoE: "no common subspace" could be read as contradicted by shared-basis MoE compressors. **Severity: should-cite (wording risk).**

- **Sources that share a weight basis across experts:**
  - MoBE (arXiv:2508.05257): basis matrices shared across experts.
  - Sub-MoE (arXiv:2506.23266): joint SVD with a shared U.
  - KBVQ-MoE (arXiv:2602.11184): KLT-guided SVD components shared across experts.
  - BitsMoE (arXiv:2606.00079, 2026-05-22): "Shared-basis Spectral Decomposition"; Qwen3-30B-A3B at 2-bit averages 64.29%, 2.80 pp over GEMQ.
- **Why there is no actual conflict.** Our §4.6 finding is about the pooled whitened *quantization residual*, which is near-isotropic. These papers find shared structure in the *weights*.
- **Suggested edit.** §4.6 and §8 should say "residual" explicitly and cite one of these as the contrasting weight-level result. As it stands, a reader may see a contradiction that does not exist.

### T6. Minor items

- **ITQ3_S** (arXiv:2603.27914, 2026-03-30). It claims an IQ3_S-based FWHT-rotated 3-bit format with a fused inverse FWHT in the MMQ kernel, on an RTX 5090, with PPL "competitive with FP16". **Unverified:** the abstract gives no numbers or models, and I did not find code. Low credibility; cite at most as a pointer.
- **llama.cpp PR #21038** ("llama : rotate activations for better quantization", ggerganov, merged April 2026; fetched).
  - It adds Hadamard rotation of Q/K/V for KV-cache quantization. Weights are not rotated.
  - Example: Qwen3.5-9B q4_0 KV, mean KLD 0.007979 to 0.005059.
  - The mainline therefore now carries rotation infrastructure. This is relevant if §7.1 implies rotations cannot live in llama.cpp.

## 3. Support

**C1 (calibration coverage, re-rounding damage off-distribution)**

- arXiv:2604.13806 DASH-Q (2026-04-15): full cross-channel Hessian methods "degrade at low bit-widths due to noisy curvature estimates from limited calibration data". A diagonal approximation filters that noise; up to +14.01% zero-shot. This is a candidate mechanism for our 8B/27B failure, which uses full covariance from wikitext only.
- arXiv:2602.05902 CoreQ (2026-02-05): fully applying mismatch correction "can overfit limited calibration data".
- arXiv:2607.00908 TASA (2026-07-01): "using only target-task calibration data can degrade post-quantization performance, whereas incorporating general-domain data stabilizes sensitivity estimation" (the "Alignment-Diversity Tradeoff").
- arXiv:2601.18306 (2026-01-26): English-only calibration hurts other languages; matching calibration to the evaluation language helps most.
- arXiv:2507.18553 (2025-07-24): GPTQ run back-to-front is Babai's nearest-plane algorithm on the lattice defined by H. This gives our frozen-grid sweep a clean geometric statement and a worst-case bound. Cite in §8 "Re-selecting codes".

**C2 (same-byte stock types beat correction; "spend bits, don't repair")**

- arXiv:2609.01587 (2026-09-01), 9 models: "spending [a matched budget] globally on finer quantization granularity beats locally repairing the most recoverable layers ... by 21-52 points". Recovery is diffuse in 8 of 9 models.
- arXiv:2607.12266 (2026-07-14): additive or coverage-model allocators "attain the lowest KL divergence among the compared allocators on models from 30B to 355B parameters" at matched memory.
- arXiv:2509.20214 Q-Palette (NeurIPS 2025): information-theoretically optimal bit allocation for Gaussianized weights, with a mixed-scheme quantizer-choice framework. This is the systematic version of our type-allocation control. Numbers were not extracted.
- §4.1 (residual not low-rank):
  - arXiv:2610.07904 ApexQuant (2026-10-06): "a fresh random rotation returns each residual to the uniform distribution on the hypersphere". After rotation the residual has no low-rank structure to capture.
  - The "Align, Then Correct" paper (T3) says the symmetric target "spreads across many singular directions".
- Appendix A.3 (equalization harms):
  - arXiv:2608.25188 "Transforms for LLM Quantization: The Great Inversion" (2026-08-25) proves that grouped shared-scale quantizers reward *within-group flattening*, whereas transform coding rewards concentration.
  - Our power-of-two equalization concentrates dynamic range into 16-element sub-blocks. The Great Inversion predicts exactly the harm we measured, which is a theoretical basis for A.3's "format interaction" mechanism.

**MoE (§4.6: promotion beats compensators)**

- MoE mixed-precision allocators are the systematic version of our one-line promotion:
  - MxMoE (arXiv:2505.05799): "2.4 lower Wikitext-2 perplexity than GPTQ at 2.25-bit".
  - QuantMoE-Bench (arXiv:2406.08155): structure-aware precision; shared or frequently used experts need more bits.
  - Q-Strata (arXiv:2608.30564).
  - NatScale (arXiv:2610.20353): lowest native-to-quantized KL in 9 of 9 settings.
  - GEMQ (arXiv:2605.23078).
- E8-QAT (arXiv:2610.27699, 2026-10-20) runs 2-bit lattice QAT on **Qwen3.6-35B-A3B**, our MoE model, and "matches the performance of the full-precision model" on 4 tasks. This is a trained-artifact reference point on our exact model.

**C3/C5 (KLD as instrument)**

- arXiv:2610.00694 (2026-09-30), 802 compressed copies of 19 models:
  - The flip rate tracks total variation at a median ratio of 1.05. KL converts to flips only through its square root, with a factor that varies fourfold across models and corpora.
  - KL ranks a pair of compressors wrongly in 11% of cases. Relevant to how §5 frames KLD.
- arXiv:2607.28196 (2026-07-30): low-rank (SVD) truncation passes PPL, MMLU and output-fidelity guards yet invents procedure steps in agentic runs. Fidelity measures miss coherent error.

## 4. Must-cite missing

None of these is in cited.txt.

| Priority | arXiv / id | Title (short) | Where |
|---|---|---|---|
| 1 | Zenodo 10.5281/zenodo.23481757 | Small-State Coset Trellis Coding at IQ2_XXS Size (llama.cpp, equal bytes) | §7.1, §8, §3.1/§7.4 |
| 1 | 2610.08164 | Align, Then Correct (two-stage low-rank compensation; QuIP# base) | §4.5, §8 richer metrics, §7.4 |
| 1 | llama.cpp PR #4773 / #5196; ik_llama.cpp PR #113 | I-quants as E8/D4 lattice codebooks; IQk_KT trellis types | §7.1, §8 |
| 2 | 2402.04396 / 2406.11235 / 2401.06118 / 2405.14852 | QuIP#, QTIP, AQLM, PV-Tuning (the named classes; currently uncited) | §8 neighbouring classes |
| 2 | 2404.00456 / 2405.16406 | QuaRot, SpinQuant (rotation class) | §8 |
| 2 | 2411.17525 | HIGGS / linearity theorem | §4.7 / §8 measurement |
| 2 | 2507.18553 | GPTQ as Babai's nearest plane | §8 re-selecting codes |
| 2 | 2608.25188 | Great Inversion (transforms vs grouped quantizers) | Appendix A.3 |
| 2 | 2609.01587 | Next bit spent globally beats local repair | §4.3/§8 |
| 3 | 2610.09212 | CurveTQ: rotation and curvature-weighted search are substitutes | §8 / §7.4 |
| 3 | 2604.13806 | DASH-Q (diagonal curvature robust to calibration noise) | §3.1 / §7.4 |
| 3 | 2601.11663 | Activation sensitivity: gradient-weighted Gram H_D = X^T D X unifies AWQ/GPTQ/Fisher | §8 richer metrics |
| 3 | 2505.17974 | Generalized Fisher-weighted SVD (Kronecker-factored Fisher for low-rank) | §8 richer metrics |
| 3 | 2405.15025 / 2504.02692 / 2505.11695 | OAC (output-adaptive Hessian); GPTAQ (asymmetric calibration; formerly "GPTQv2"); Qronos | §8 / §7.4 end-to-end objective |
| 3 | 2505.05799 / 2406.08155 / 2606.00079 | MxMoE, QuantMoE-Bench, BitsMoE | §4.6 / §8 MoE |
| 3 | 2508.05257 or 2506.23266 | MoBE / Sub-MoE (shared *weight* basis across experts) | §4.6 wording |
| 4 | 2610.00694 | Divergence to decision flips (TV vs KL) | §2.5 / §5 |
| 4 | 2609.26173, 2609.09867 | Reconstruction error does not predict sensitivity; KL pricing does not compose | §4.7 |

Already cited and consistent with this sweep: GuidedQuant, OBD-LLM, KronQ, ARHQ, YAQA (2505.22988), MiLo, TileQ, 2512.17073, ResQ, LRC.

- **TileQ** (2605.09281) shares low-rank factors across input and output dimensions of experts via 2D tiling. "Tiles within experts" is a fair summary; it does not claim a shared residual subspace.
- **MiLo** (2504.02658) is calibration-free and uses adaptive rank.

## 5. Next-step ideas

Ranked by expected payoff per GPU-day on one RTX 5090 with llama.cpp.

1. **Rotate, then re-round, inside a stock type.** This is the direct follow-up to T1/T2 and tests whether C1's failure at size is a non-rotation artifact.
   - On Llama-3.1-8B, apply a random Hadamard to the input side of ffn_down. Exact folding is blocked by the norm gain, so this needs an online Hadamard. The activation-rotation code merged in PR #21038 is the template.
   - Then requantize to IQ2_XS/Q2_K with and without our frozen-grid GPTQ sweep. Score wikitext, FineWeb-Edu and code paired against the unrotated files.
   - The coset-trellis study's code is public (github.com/Genrius-AI/pingala8-llm) and can serve as a reference baseline at IQ2_XXS bytes on the same model.
2. **Asymmetric, output-Fisher correction (T3) against our same-byte controls.**
   - We already have llama-gradmatrix and the gradient captures. Implement Stage 1 (asymmetric target, Fisher-weighted) on Q2_K/IQ2_XS at 0.8B and 8B.
   - Compare against the mixed-type controls of §4.3. If it still loses at equal bytes, C2 becomes much stronger; if it wins, the paper needs that caveat.
3. **Diagonal or damped whitening for re-rounding at 8B/27B** (DASH-Q hypothesis), plus the token-gradient-weighted Gram H_D = XᵀDX of arXiv:2601.11663.
   - Both reuse existing captures and cost CPU sweeps plus scoring only.
   - They test the "noisy full covariance overfits wikitext" explanation for cross-domain damage.
4. **Add ik_llama.cpp IQ2_KT/IQ3_KT at equal bytes as an in-family baseline in §4.3.** Cheap; it closes the "VQ/trellis not compared" gap partially without leaving GGUF.
5. **Replace the MoE promotion rule with a KL-driven allocator** (additive or coverage model, arXiv:2607.12266) on Qwen3.6-35B-A3B. Also try correcting the dense tensors there, which §7.4 already lists as open work.
6. **Report total variation and flip rate next to KLD** from the existing reference logits (arXiv:2610.00694). Zero GPU cost; strengthens §5's instrument discussion.

## 6. Verification notes

- **Fetched and confirmed.** Every arXiv id in this report was fetched from arxiv.org/abs: title, date and first authors match.
- **Full text or detailed HTML read:**
  - 2610.08164 (main text plus Appendix G).
  - 2609.02652.
  - The other numbered claims come from the verified abstracts.
- **Non-arXiv sources fetched:**
  - llama.cpp PRs #4773, #5196 and #21038.
  - ik_llama.cpp PR #113.
  - The Zenodo study, via its OpenAlex record and the HF model card. The PDF itself was not read; numbers come from the abstract and the model card.
- **Unverified:**
  - ITQ3_S numbers: none available.
  - Q-Palette quantitative results: not extracted.
  - The 0.35 bpw adapter overhead in T3 is my arithmetic from the 349 MB figure, not stated by the authors.
  - Tetra's IQ2_XXS comparison is the author's own measurement (one GPU, pre-registered).
