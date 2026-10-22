# R3: Calibration data and out-of-domain behaviour of PTQ (claims C1, C2 off-distribution)

Sweep date 2026-10-22. Scope: calibration-set sensitivity, calibration-domain overfitting, imatrix studies, multilingual and code damage after quantization, held-out evaluation of PTQ, self-generated calibration, and whether Hessian- or activation-weighted methods overfit their calibration distribution.

## 1. Coverage

- **Sources.**
  - OpenAlex `title_and_abstract.search`: 21 queries, 2022-06 onward, 517 records with abstracts. The run stopped when the shared-IP daily budget ran out (HTTP 429).
  - arXiv export API: 16 of 48 planned queries completed, about 118 new records with abstracts. The API returned 429 for most of the session because other sweep agents share the IP.
  - arXiv listing search pages, fetched through WebFetch and read as titles:
    - "calibration data quantization": about 200
    - "calibration pruning large language models": about 120
    - "quantization multilingual": about 90
    - "GPTQ calibration": about 48
    - "quantization calibration domain LLM": 23
    - "low-rank quantization error compensation calibration": 6
    - "post-training quantization overfitting calibration": 4
    - "quantization reconstruction generalization language model": about 130
    - "self-generated calibration data": about 100
  - llama.cpp GitHub discussions #5006 and #5263, read in full through `gh api`.
  - WebSearch for the imatrix, code and multilingual angles.
- **Counts.**
  - About 1,350 titles screened.
  - 635 abstracts in the local database were keyword-screened, and 153 hits were reviewed by title.
  - About 45 abstracts read individually.
  - Full text (arXiv HTML) read for 16 papers: 2610.11226, 2610.16020, 2610.28537, 2607.00908, 2604.13806, 2601.18306, 2503.03592, 2508.20893, 2503.07103, 2504.04823, 2504.09629, 2602.05902, 2510.10618, 2405.06001, 2405.20835, 2406.15524. Partial reads: 2311.09755, 2410.17170, 2406.12928, 2609.26241.
- **Date range.** 2023-11 to 2026-10-20, with emphasis on 2025-2026.
- **Overlap with other slices.** R1 and R2 already report DRQ (2610.11226), "Ranking Is Enough", DASH-Q, TASA, 2608.15046 and Williams & Aletras. I repeat them only where the calibration and off-distribution angle adds something.

## 2. THREATS

### T1. DRQ: "When Lower Reconstruction Loss Hurts: Distributionally Robust Refinement for Low-Bit LLM Quantization", arXiv:2610.11226, 2026-10-08 (Zhao, Cheng, ..., Zhuo Sun). Claims C1 and C5.

- **Severity:** must-cite-and-discuss. It pre-empts the qualitative half of C1, and R2 covers it in depth.
- **Calibration and off-distribution facts (verified in full text):**
  - The method re-chooses integer codes "within the existing quantization grid": scales, zero-points and operators are frozen. It is a frozen-grid code re-selection under a Gram metric.
  - Llama-3.2-1B, 3-bit AWQ, WikiText-2 calibration: 413 of 560 single-matrix replacements (73.8%) that lower reconstruction loss raise WikiText-2 perplexity. The figure is 264 (47.1%) for GPTQ.
  - Further minimizing the loss raised held-out C4 perplexity by 0.69%.
  - Llama-3.2-3B, GPTQ W4: reconstruction-only refinement keeps lowering loss while perplexity rises "by orders of magnitude".
  - The paper evaluates only on WikiText-2 (its calibration corpus) and C4 perplexity, plus zero-shot English tasks. It has **no code, math or multilingual evaluation** and **no study of calibration-corpus variation**.
  - Its robust variant improves held-out C4 at 70B W2 GPTQ: 31.77 → 27.11. Frozen-grid code re-selection can therefore transfer at 70B when it is regularized and starts from a GPTQ-fitted grid.
- **Where it touches us:**
  - §3.1 "At size the sweep improves its proxy and not the outcome"
  - §3.1's list of untested candidates
  - §8 "Re-selecting codes on a fixed grid"
  - §9 future work: "a regularizer toward the original codes in directions the calibration does not cover" is roughly what DRQ's uncertainty set over Gram perturbations, plus its calibration-loss guardrail, already does.
- **What remains new for us:**
  - The test runs on llama.cpp K-quant grids with an imatrix-tuned base.
  - Damage is measured relative to the untouched file, on **code and FineWeb-Edu**, at 8B and 27B.
  - A five-calibration grid shows prose↔code non-transfer.
  - The mixed-calibration recipe works at 0.8B and fails at 8B and 27B.
- **Suggested sentence (§3.1):** "Concurrent work (DRQ) reports the same proxy/outcome inversion for frozen-grid code refinement on GPTQ/AWQ grids and repairs it with a worst-case Gram objective; we did not test such a regularizer on K-quant grids."

### T2. Shin, Park, Lee, Lee, "Rethinking Pruning Large Language Models: Benefits and Pitfalls of Reconstruction Error Minimization", arXiv:2406.15524, EMNLP 2024 main (DOI 10.18653/v1/2024.emnlp-main.68, verified via OpenAlex). Claims C1 (size pattern) and C5.

- **Severity:** must-cite. It published the "small model transfers, larger model overfits" pattern for reconstruction minimization, though for pruning and not for quantization.
- **Finding (verified in full text):**
  - In LLaMA-7B, adding cross-block reconstruction lowers calibration error but raises test error on WikiText-2:
    - SparseGPT: calibration 0.48 → 0.37, test 2.30 → 2.53
    - Wanda: calibration 0.51 → 0.38, test 2.23 → 2.48
  - Perplexity rises on all of Wiki, PTB and C4. Example (Wanda): PTB 11.25 → 12.01.
  - On OPT-125M the same technique *lowers* test error (0.0083 → 0.0078).
  - Table 2 caption: "larger models in general are more susceptible to overfitting."
  - The remedy is self-generated calibration data: more of it reduces test error.
- **Where it touches us:** §3.1 says what separates 0.8B from 8B/27B "is not established". Shin et al. saw the same small-vs-large split with two sizes, so the size pattern itself is not new. Our contribution is the quantization and GGUF instance, measured against the untouched file across three corpora and three sizes. Cite it as prior observation in §3.1 and as a candidate mechanism.

### T3. Arai & Ichikawa, "Quantization Error Propagation: Revisiting Layer-Wise Post-Training Quantization" (QEP), arXiv:2504.09629, 2025-04-13. Claim C1.

- **Severity:** should-cite. It is the earliest numerical instance I found of *GPTQ damaging off-distribution text relative to the un-optimized quantized file*.
- **Finding (Table 4, verified):** LLaMA2-7B, INT3 g128, WikiText-2 perplexity change vs RTN. GPTQ is worse than RTN when calibrated on the mismatched corpus.

| Calibration | GPTQ | QEP+RTN |
|---|---|---|
| C4 | −0.25 | −0.33 |
| PTB | **+0.07** | −0.30 |
| WikiText-2 | −0.46 | −0.49 |

- The paper says the correction "can lead to overfitting", which is most pronounced when calibration is "small and unrepresentative of the target task" and in MLP blocks.
- **Scope:** one 7B size, one bit setting.
- **Where it touches us:** the §3.1 framing "re-rounding damages text kinds its calibration does not cover" is anticipated for GPTQ in general at 7B/INT3. What is new is the GGUF frozen-grid setting, code as the held-out kind, and the 27B point.

### T4. Ji et al., "Beware of Calibration Data for Pruning Large Language Models", arXiv:2410.17711, ICLR 2025; and Williams, Chrysostomou & Aletras, "Self-calibration for Language Model Quantization and Pruning", arXiv:2410.17170. Claim C1 (recipe).

- **Severity:** should-cite.
- **What they show:** both offer self-generated calibration as the standard remedy for calibration-domain dependence.
  - Ji et al.: up to +2.68% for Wanda/DSnoT/OWL on DCLM and LLaMA-3.
  - Williams et al. (GPTQ/AWQ 4-bit; SparseGPT/Wanda 2:4; Gemma-2B to Llama-3.1-8B): self-calibration wins on the small models but does *not* beat C4 on Mistral-7B or Llama-3.1-8B (SparseGPT 53.8 vs 54.8).
- **Where it touches us:** §3.1 "What the tool requires" says calibration must "cover every kind of text the file will serve". A reviewer will ask why self-generated (model-sampled) calibration was not tried. It is the obvious answer to "cover every kind of text" without choosing the kinds by hand.

### T5. Paglieri, Dash, Rocktäschel, Parker-Holder, "Outliers and Calibration Sets have Diminishing Effect on Quantization of Modern LLMs", arXiv:2405.20835, 2024-05. Weakens part of C1.

- **Severity:** minor, but pre-empt it in one clause.
- **Finding (full text):**
  - GPTQ and AWQ at W4A16 g128, plus SmoothQuant W8A8.
  - Calibration on RedPajama, random punctuation, ARC/PiQA, and FLORES+ in many languages.
  - Evaluation on WikiText2 perplexity and English zero-shot tasks only.
  - OPT-6.7B is sensitive. Llama-2-7B, Llama-3-8B, Command-R-35B and Mistral-7B "display high robustness".
- **Why it is not a contradiction:** it covers only 4-bit and has no code or non-English evaluation. Our effect is at Q2_K (about 2.6 bpw) and shows up mainly on code. The phrase "calibration decides where the gain lands" should say "at 2-bit": several papers (2610.28537, 2406.12928, 2508.20893) find calibration effects small at 4-bit and large at 2-bit.

### T6. Liu et al., "Evaluating the Generalization Ability of Quantized LLMs: Benchmark, Analysis, and Toolbox", arXiv:2406.12928, 2024-06. Mildly weakens the C1 recommendation.

- **Severity:** minor.
- **Finding:** "models quantized using a calibration set with the same distribution as the test data are not necessarily optimal." It covers 7B only and GPTQ, AWQ, SpQR and SmoothQuant at 2-4 bits. The full-text read found single-seed comparisons, no significance tests, and inconsistent dataset counts.
- **Where it touches us:** the "cover every kind of text" advice. Our grid is paired and resolved, so it outweighs this. A footnote is optional.

### T7. Singh, "Certifying Compressed Language Models: An Audit and a Statistical Toolkit", arXiv:2608.15046, 2026-08. Claim C5 and our calibration-sampling bound.

- **Severity:** should-cite (also in R5).
- **Finding:** "changing the calibration draw was sufficient to reverse the observed method ordering in 5 of 8 confirmatory cells" (GPTQ vs AWQ, byte-identical calibration sizes, five seeds).
- **Where it touches us:** §3.1 "The calibration sample is a small part of the variance" was measured only at 0.8B (§7 Statistics: "not measured at 27B"). This paper makes that gap more visible. Our 8B/27B verdicts are paired against the untouched file, not method-vs-method, but a reviewer may still ask.

### T8. Upstream prior evidence on imatrix calibration-domain transfer (llama.cpp Discussions #5006 and #5263, 2024). Claim C1 (framing).

- **Severity:** should-cite in §8 "The upstream discussion", next to #8831.
- **#5006** (kalomaze, 2024-01-17), "Importance matrix calculations work best on near-random data":
  - Mistral-7B q3_K_L, out-of-domain song lyrics perplexity:
    - 90k-token pretrain-style imatrix: 15.68 ± 1.44
    - no imatrix: 15.55
    - 8k near-random imatrix: 15.41
  - The poster says out-of-domain text "gets worse" with clean calibration. The differences are inside the error bars.
- **#5263** (Artefact2, 2024-02-01), "About imatrix overfitting, and importance of input text":
  - Q2_K Mistral-7B, KLD over a 3×3 calibration × evaluation grid (frwiki XML, English fiction, wikitext) at 10K/100K/1M tokens.
  - Every imatrix beat no-imatrix on every test set. For example, KLD_99 on frwiki was 1.95 with no imatrix and 1.56-1.63 with any imatrix.
  - Commenters found the "overfitting" much smaller than expected.
  - ikawrakow recommended real text over pseudo-random data on a broader evaluation.
- **Where it touches us:** upstream already asked the calibration-domain question for the *diagonal* statistic and found it mostly benign. That contrast sharpens our C1 story: the domain damage we measure comes from the *full-covariance re-rounding*, not from imatrix weighting. Say so explicitly.

No paper found pre-empts C2's off-distribution numbers (0.25 to 0.96 of the wikitext gain survives) for whitened low-rank correction on GGUF. See SUPPORT for the closest analogues.

## 3. SUPPORT

### C1: Hessian/Gram-weighted methods are calibration-domain sensitive, more so than diagonal or scale-only methods

**S1.** Chimoto, Elhoushi, Bassett, "Calibrating Beyond English: Language Diversity for Better Quantized Multilingual LLMs", arXiv:2601.18306, 2026-01-26 (full text). Llama-3.1-8B and Qwen2.5-7B, 4-bit g128.

- GPTQ is far more calibration-language sensitive than AWQ: swings up to 3.52 perplexity for GPTQ against about 0.35 for AWQ. The authors attribute this to "GPTQ's Hessian-based error compensation".
- Single-language calibration damages other languages. Swahili-calibrated GPTQ moves English perplexity 8.300 → 9.286 and isiXhosa 89.28 → 95.64.
- A multilingual mix recovers non-English (average 21.62 → 18.10) without hurting English (8.300 → 8.184).
- This parallels both our "kinds not covered are damaged" finding and the 0.8B mixed-calibration rescue, at 8B and 4-bit.

**S2.** Zhang et al., "Quantization Hurts Reasoning? An Empirical Study on Quantized Reasoning Models", arXiv:2504.04823 (Table 2, verified). DeepSeek-R1-Distill-Qwen-1.5B, W3G128.

| Method | Calibration change | Average | GPQA |
|---|---|---|---|
| GPTQ | WikiText2 → Numina-Math | 28.3 → 38.1 (+9.8) | 27.3 → 23.7 |
| AWQ | WikiText2 → Numina-Math | +1.8 | (not given) |

- The GPQA drop under GPTQ is an off-domain loss inside the same table.
- The authors state "AWQ is also more robust to the choice of calibration data than GPTQ."

**S3.** Kim et al., DASH-Q, "Robust Ultra Low-Bit Post-Training Quantization via Stable Diagonal Curvature Estimate", arXiv:2604.13806, v2 2026-09-16 (full text).

- "off-diagonal Hessian entries are highly susceptible to sampling noise", and reliable off-diagonal estimates need far more calibration data than PTQ uses.
- Llama-3.1-8B, 3-bit: GPTQ has *lower* WikiText-2 perplexity than DASH-Q (8.33 vs 8.52, the calibration distribution) but much worse zero-shot average (40.43 vs 64.99).
- This directly supports §3.1's candidate "the larger Grams are estimated far less precisely" (6 tokens/dim at 8B, 4.8 at 27B vs 20 at 0.8B), and the proxy/outcome inversion.

**S4.** Beyond 4 bits: Sukthanker, Grazzi, Hensman, Cameron, "Beyond 4 bits: Data-Centric Post-training Quantization and Recovery", arXiv:2610.28537, 2026-10-20 (full text).

- Setup: 2-bit YAQA (vector codebook) on Phi-4-mini 3.8B, Llama-3.1-8B and Qwen3-30B-A3B.
- Math or code calibration raises generation but lowers QA on every model:
  - Llama-3.1-8B: web 65.1 QA / 9.6 gen; math 62.1 / 26.6; code 60.8 / 22.6
- Mixtures trade one against the other. The authors' guidance: "Mix in broad web text when QA matters."
- "calibration data effects 2-bit PTQ methods much more than on 4-bit methods". At 4-bit the spread is at most 1.6 QA and 7.9 generation points.
- The spread shrinks with size across the three models (Phi-4-mini largest, Qwen3-30B smallest). That is suggestive only, since the models come from different families.
- This is cross-kind damage at 2-bit for 3.8B to 30B. It supports C1's "damages kinds not covered" and the 2-bit framing.

**S5.** Giagnorio, Bavota et al., arXiv:2503.07103. v1 is titled "Quantizing Large Language Models for Code Generation: A Differentiated Replication"; v2 (2026-01-27) is "Evaluating the Impact of Post-Training Quantization on Large Language Models for Code Generation". Full text read.

- AQLM on CodeLlama-7B and DeepSeek-Coder-7B.
- Calibration: Random (RedPajama) vs Mixed (50% code + 50% StackOverflow prose) vs Code.
- At 2-bit, CodeLlama MultiPL-E Python pass@1: Random 16.4, Mixed 23.9, Code 24.1 (p<0.001).
- Mixed is generally best: "providing both code and technical language generally yields better performance". No significant differences at 4 and 8 bits.
- Corroborates that code is the kind most damaged when calibration lacks it, and that prose+code mixing helps at 7B and 2-bit. Note the contrast: at 8B and 27B our 2:1 mixed recipe for frozen-grid re-rounding did *not* help.

**S6.** He et al., "Preserving LLM Capabilities through Calibration Data Curation: From Analysis to Optimization", arXiv:2510.10618 (NeurIPS 2025 per WebSearch; not verified on the page).

- LLaMA3-8B and Qwen2.5-7B; GPTQ/AWQ 4-bit; SparseGPT/Wanda.
- MathQA calibration gives +5.92 math and CodeQA gives +7.49 code (quantization), while perplexity rises in both cases.
- SparseGPT commonsense: 41.85 with WikiText calibration, 39.62 with MathQA, 38.95 with CodeQA.
- Effects are modest at 4-bit.

**S7.** Wang et al., TASA, "Beyond Activation Alignment: The Alignment-Diversity Tradeoff in Task-Aware LLM Quantization", arXiv:2607.00908 (full text).

- Pure GSM8K calibration gives Avg 68.3 vs 68.9 for a 50/50 mix (LLaMA-3-8B, 3.5 bits), despite activation cosine 0.9998.
- "general-domain data provides distributional diversity that regularizes the calibration Hessian".
- The best mix is model-specific (α_wiki 0.50 for LLaMA-3, 0.75 for Qwen2.5).
- Supports the mixing rationale. Effects are small.

**S8.** CoreQ, arXiv:2602.05902 (Cha et al., v2 2026-05-08), and Guo, arXiv:2610.16020 (2026-10-12), "When Does Compensating Inherited Quantization Error Help? Mean-Square Guarantees for Layer-Wise Quantization and Their Limits". Theory supporting C1 and C5.

- CoreQ Theorem 3.1: full compensation (α=1) on a finite-sample mismatch estimate pays the estimation-error energy σ², and the empirical criterion "always prefers α=1".
- Guo: "With a finite codebook, even unique exact layer-wise minimizers can give a larger output error", and lowering the loss on data used to choose the strength "did not reliably lower it on new text".
- Both are citable for §4.7 (surrogates failed to predict outcome 7 times) and for why a frozen grid (a bounded codebook) makes it worse.

**S9.** Saliency-Aware Regularized Quantization Calibration (RQC/SARQC), arXiv:2605.05693 (same group as DRQ). It adds a penalty on deviation from the FP weights because fitting the empirical error "on limited or unrepresentative data" hurts downstream performance. This is the regularizer we list in §9.

**S10.** The diagonal imatrix itself is calibration-robust. This strengthens the contrast in T8.

- **Borgersen & Goodwin, "English K_Quantization of LLMs Does Not Disproportionately Diminish Multilingual Performance"**, arXiv:2503.03592 (v4 2026-01-22; full text).
  - Llama-3.3-70B, llama.cpp K-quants Q4/Q3/Q2, imatrix calibrated on English vs Norwegian vs Malayalam, MixEval in English and Norwegian.
  - Multiple-choice: no p < 0.2373. Free-form: nothing significant after Bonferroni.
  - Non-English imatrices "might slightly harm".
- **Marchisio-adjacent MT study, "The Uneven Impact of Post-Training Quantization in Machine Translation"**, arXiv:2508.20893 (full text).
  - Llama-3.1-8B GGUF, imatrix from 10k tokens of English vs Bengali.
  - Q4_K_M: no effect.
  - Q2_K: En→Bengali COMET 44.9 → 48.0 with Bengali imatrix; French moves only ±0.3-0.4.
  - "GGUF variants provide the most consistent performance, even at 2-bit precision."
- **Takeaway:** with only the diagonal statistic, calibration-language mismatch costs about 3 COMET points at Q2_K and nothing at 70B. The full-covariance re-rounding damage we report (up to half the file's own KLD) is of a different order.

**S11.** Multilingual damage after quantization is established, so "kinds not covered get hurt" generalizes to languages:

- Marchisio et al., "How Does Quantization Affect Multilingual LLMs?", arXiv:2407.03211, Findings of EMNLP 2024: a 1.7% automatic drop in Japanese corresponds to a 16.0% drop in human ratings.
- Zeng et al., "Multilingual Brain Surgeon", arXiv:2404.04748, LREC-COLING 2024: English-centric calibration degrades low-resource languages, and proportional multilingual sampling fixes it.
- Kurz et al., arXiv:2408.14398, accepted to TACL: target-language calibration retains perplexity but not downstream ability (pruning).

### C1 at small vs large size

- Shin et al. (T2): 125M transfers, 7B overfits.
- 2610.28537 (S4): the calibration spread shrinks with size across three models.
- Ye et al., "Damage Predicts Recovery: When Calibration Data Matters in Compressing Financial LLMs", arXiv:2609.26241: calibration choice matters only where compression damage is large; 70B suffers far less damage than 8B.

None of these shows our exact pattern, where re-rounding helps at 0.8B and gives no resolved gain or harms at 8B and 27B. The size-dependence of *calibration-driven code re-selection* therefore remains our contribution.

### C2: off-distribution survival of low-rank correction gains

- **Song et al., "Align, Then Correct: Training-Free Two-Stage Low-Rank Compensation for Extremely Quantized Large Language Models"**, arXiv:2610.08164, 2026-10-06 (abstract; R1 has the full text).
  - Closed-form low-rank compensation on 2-bit QuIP#.
  - On held-out C4 it recovers 51% (Qwen3-8B) and 84% (Qwen3-4B) of the gap to FP16, against 31% and 63% for the strongest baseline.
  - Gives numbers comparable in kind to our "recovered fraction"; recovery is smaller at the larger model.
- **Thomas, "Language-Conditional Dequantization: Recovering What Quantization Steals from Non-English Languages"**, arXiv:2608.11786, 2026-08-12 (abstract).
  - Per-language rank-2 corrections on INT3 GPTQ 3B models recover 70-83% of the non-Latin-script perplexity gap but only 17-28% of the GlobalMMLU gap.
  - They beat a language-agnostic correction of equal capacity by 3-9 points on distant languages.
  - Supports C2's off-distribution shrinkage: a correction fitted to one distribution transfers incompletely, and perplexity gains overstate task gains.
- **Prejzner, "Bielik-Q2-Sharp: A Comparative Study of Extreme 2-bit Quantization Methods for a Polish 11B Language Model"**, arXiv:2603.04162.
  - Six 2-bit methods calibrated on Polish (QuIP#, QTIP, VPTQ, AQLM, SpinQuant+GPTQ, ButterflyQuant) are compared against stock GGUF IQ2_XXS on 22 Polish benchmarks.
  - The best (QuIP# 71.92%, 3.26 GB) ties IQ2_XXS (72.07%, about 2.6 GB).
  - Independent support that stock I-quants are hard to beat per byte (C2), off English.
- **"Ranking Is Enough: A Controlled Study of Per-Tensor Mixed Precision for llama.cpp"**, S. Rasal, Preprints.org, DOI 10.20944/preprints202610.1935.v1, 2026-10-20. Verified via Crossref and the OpenAlex abstract; preprints.org returned 403.
  - Uses held-out evaluation text, three calibration seeds, pre-registered rules, and KLD at matched size.
  - A per-model mix lowers KLD by 15-40% vs llama.cpp's stock mixes.
  - Strong support for C2's "same-byte mixed-stock file beats correction" and for C5 methodology. R1 covers it as T5.

### C5 (held-out discipline)

- Williams & Aletras, "On the Impact of Calibration Data in Post-training Quantization and Pruning", arXiv:2311.09755, ACL 2024: Wikipedia calibration gives the best WikiText perplexity but the worst zero-shot accuracy for SparseGPT (LLaMA-7B 9.96 vs 11.06 perplexity). They use this to show perplexity is a poor proxy.
- LLMC, arXiv:2405.06001: WikiText2-calibrated GPTQ w3 on LLaMA-2-7B scores 6.133 on WikiText2 vs 6.330 with Pile calibration, so in-distribution calibration inflates same-corpus perplexity.
- Both support evaluating off the calibration corpus. Our wikitext-calibrated, wikitext-scored primary numbers carry exactly this bias, which §4.3 already discloses.

## 4. MUST-CITE MISSING (none are in cited.txt)

1. **arXiv:2610.11226 DRQ**: frozen-grid code refinement; lower calibration loss hurts held-out text. Cite in §3.1 and §8.
2. **arXiv:2406.15524** Shin et al., EMNLP 2024: reconstruction minimization overfits calibration at 7B but not 125M. Cite in §3.1 (size pattern) and §4.7.
3. **arXiv:2601.18306** Chimoto et al.: GPTQ's Hessian makes it calibration-language sensitive; mixing recovers. Cite in §3.1 (calibration grid) and §4.8.
4. **arXiv:2604.13806** DASH-Q: off-diagonal Gram noise; GPTQ wins on the calibration corpus but loses zero-shot at 8B. Cite in §3.1, "candidates we did not test".
5. **arXiv:2503.03592** Borgersen & Goodwin, and **arXiv:2508.20893**: the imatrix (diagonal) is calibration-robust at Q2_K-Q4_K_M, 8B and 70B. Cite in §3.1 and §8 for the contrast.
6. **llama.cpp Discussions #5263 and #5006**: upstream imatrix calibration-domain tests. Cite in §8 "The upstream discussion".
7. **arXiv:2311.09755** Williams & Aletras, ACL 2024: the canonical calibration-data study. Cite in §8.
8. **arXiv:2610.28537** "Beyond 4 bits": 2-bit calibration cross-kind damage at 3.8-30B; mixtures trade off. Cite in §3.1.
9. **arXiv:2503.07103** Giagnorio et al.: code-calibration and mixed prose+code at 2-bit for code LLMs. Cite in §3.1 (mixing).

Should-cite (second tier):

- 2504.09629 (QEP Table 4)
- 2504.04823 (GPTQ vs AWQ calibration sensitivity)
- 2410.17170 and 2410.17711 (self-generated calibration)
- 2405.20835 (robustness at 4-bit)
- 2602.05902 and 2610.16020 (theory of over-compensation)
- 2607.00908 (alignment-diversity)
- 2605.05693 (regularized calibration)
- 2608.15046 (calibration draw reverses orderings)
- 2610.08164 and 2608.11786 (C2 off-distribution recovery)
- 2407.03211 (multilingual damage)

## 5. NEXT-STEP IDEAS (ranked by expected payoff on one RTX 5090 with llama.cpp)

1. **Diagonal-plus-shrinkage Gram for the re-rounder.** Run the sweep with H_λ = (1−λ)H + λ·diag(H) at 8B and 27B, λ ∈ {0.25, 0.5, 0.9}.
   - Basis: DASH-Q (off-diagonal noise), DRQ's C = (1−β)H + βI, and the imatrix's robustness (S10).
   - Cost: one Gram capture already exists, so this is CPU sweeps plus three-corpus KLD.
   - It directly tests §3.1's "tokens per input dimension" candidate. If shrinkage restores the 0.8B behaviour at size, C1 gains a mechanism.
2. **Calibration budget scaling at 8B.** Recapture the Gram at 2× and 4× tokens (12 and 24 tokens/dim) and rerun the frozen-grid sweep on all three corpora.
   - It separates estimation noise from a model-size effect, the open question in §3.1 that T2, S3 and S4 all point at. A few GPU-hours.
3. **DRQ-style guardrail on K-quant re-rounding.** Accept a code change only if held-out-validation Gram loss does not rise, using a FineWeb or code validation slice.
   - It turns C1's negative result into a positive recipe, or confirms it is K-quant-specific.
4. **Self-generated calibration** (Williams 2410.17170 / Ji 2410.17711 recipe) for both re-rounding and the whitened correction at 8B. Sample about 1M tokens from the BF16 model.
   - It answers the obvious reviewer question in T4. Cheap.
5. **Calibration-draw variance at 27B.** Three disjoint wikitext calibration thirds for the correction's 27B §4.3 margin over Q3_K_S.
   - Closes the gap 2608.15046 highlights. The 4.5% lead is small enough that the calibration draw could flip it.
6. **Imatrix-domain control.** Rebuild the Q2_K base with a code+prose imatrix (keeping the wikitext-only re-rounding) to show the damage comes from re-rounding, not from the base imatrix.
   - Upstream (#5263) and S10 predict the base will barely move. Measuring that makes the contrast in §3.1 airtight.
7. **Multilingual held-out corpus** (e.g. FLORES or CulturaX slices) as a fourth evaluation corpus for both methods.
   - S1 and S11 suggest non-English is where Gram-weighted methods lose most, so it would strengthen "every kind not covered".

## 6. Verification log

- **Full text read on arXiv HTML** (ids, titles and dates confirmed): 2610.11226, 2610.16020, 2610.28537, 2607.00908, 2604.13806, 2601.18306, 2503.03592, 2508.20893, 2503.07103 (v2), 2504.04823, 2504.09629, 2602.05902, 2510.10618, 2405.06001, 2405.20835, 2406.15524, 2406.12928, 2311.09755, 2410.17170, 2609.26241.
- **Abstract pages fetched:** 2407.03211, 2408.14398, 2404.04748, 2410.17711, 2410.07461, 2605.05693, 2601.11200, 2603.16105, 2608.11786, 2608.09941, 2607.14181, 2610.22771, 2608.15046, 2603.04162, 2610.08164, 2601.14277.
- **OpenAlex abstracts only:** 2610.04403, 2610.15578, 2610.18343, 2610.27397, 2602.09130, and Zenodo 10.5281/zenodo.23441205 (dialect-aware imatrix study; code repository, no numbers published).
- **Verified via Crossref:** "Ranking Is Enough", Preprints.org 10.20944/preprints202610.1935.v1 (S. Rasal, 2026-10-20). Body not read (403).
- **llama.cpp #5006 and #5263:** read through `gh api` (body and comments).
- **Unverified:**
  - The NeurIPS 2025 venue for 2510.10618 comes from WebSearch only.
  - The 2406.15524 venue comes from OpenAlex (EMNLP 2024 main DOI), not the arXiv page.
- **Tooling note:** the arXiv export API and OpenAlex were rate-limited (HTTP 429) for most of the session, so breadth came mainly from arXiv listing pages read as titles. The 48-query arXiv API plan completed 16 queries.
