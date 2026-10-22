# R2: Re-rounding and code re-selection on a fixed grid (claim C1, plus C5)

Sweep date: 2026-10-22. Agent slice: GPTQ and its successors, learned rounding, frozen and refit grids, calibration-domain effects, layer-surrogate failure.

## 1. Coverage

- **Engines.** The arXiv API (export.arxiv.org) returned HTTP 429 to us for most of the session because other sweep agents were sharing the limit. Only `abs:GPTQ` (147 hits) came back through it. I then switched to the arXiv website's abstract search (arxiv.org/search, searchtype=abstract, up to 400 results per query), scraped by `r2/s.py`. Raw store: `~/Documents/openbeast-litsweep-raw-2026-10-22/r2/store.json`.
- **Supplementary sources.** WebSearch/WebFetch on GitHub (intel/auto-round docs, llama.cpp issues #4165 and #11024 via `gh`), ICLR/NeurIPS/ICML listings, and arxiv.org/html full texts.
- **Queries.** About 100 arXiv query strings in three batches (q2.txt: 73, q3.txt: 23, plus the API ones). They covered GPTQ/OPTQ, optimal brain, AdaRound/BRECQ/FlexRound, learned/adaptive rounding, rounding error, error compensation/feedback/propagation, coordinate descent, closest vector/Babai, layer-wise and block reconstruction, reconstruction loss vs end loss, calibration data/set/domain/multilingual/code, overfitting/generalization in PTQ, weight drift, regularization, GGUF, llama.cpp, k-quant, imatrix, requantization, frozen scales, quantization grid, scale search/refinement, Hessian/second-order/Kronecker/Fisher, asymmetric calibration, RTN, SignRound/AutoRound, OmniQuant, QuIP, 2-bit PTQ, integer/mixed-integer programs, distributionally robust, fixed grid, integer codes, damping and OPTQ regularization.
- **Screened.** 760 unique arXiv records (title + abstract stored), all screened by title, with abstracts read for roughly 130 plausible hits. Date range 2017 to 2026-10-21, concentrated on 2024-2026.
- **Full text read** (HTML or abstract page fetched) for 2610.11226, 2605.05693, 2608.07019, 2608.15567, 2609.38599, 2609.31560, 2604.18556, 2504.09629, 2504.04823, 2406.17542, 2604.13806, 2406.12928, 2501.06417, 2311.09755, 2512.04746, 2505.11695, 2504.02692 and 2507.18553, plus the intel/auto-round GGUF accuracy doc.
- **Caveat.** arXiv site search returns surprisingly few hits per query (e.g. "AdaRound" = 5). Very common terms are therefore probably under-sampled. The closest 2025-2026 work surfaced reliably through the specific queries.

## 2. THREATS

Severity: fatal / must-cite-and-discuss / should-cite / minor. **No fatal threat was found.** Two recent papers pre-empt parts of the C1/C5 *mechanism* framing. Two GGUF-format results show that a *different* code re-selection does gain at 8B. Those four need text changes.

### T1. DRQ: "When Lower Reconstruction Loss Hurts: Distributionally Robust Refinement for Low-Bit LLM Quantization" (arXiv:2610.11226, 2026-10-08; Zhao, Cheng, ..., Zhuo Sun)
- **Claims:** C1 (held-out damage of frozen-grid code refinement) and C5 (surrogate fails to predict outcome). Severity: **must-cite-and-discuss**.
- **What it shows.** DRQ "refines the integer codes representing quantized weights within the existing quantization grid, keeping quantization parameters and inference operators unchanged", which is exactly our frozen-grid setting outside GGUF.
- **Abstract:** "lower reconstruction loss can even degrade model performance on the same calibration data … weights with lower reconstruction loss on calibration data can have higher loss than other weights when the distribution of input activations changes."
- **Numbers** (Llama-3.2-1B/3B, WikiText-2 calibration):
  - Of 560 single-layer modifications that lowered calibration loss, 73.8% (AWQ) and 47.1% (GPTQ) raised held-out WT2 PPL.
  - Further loss minimisation raised C4 PPL by 0.69%.
  - Reconstruction-only refinement of GPTQ W4 on Llama-3.2-3B raised PPL "by orders of magnitude" as calibration grew from 16 to 256 sequences.
  - At 2-bit GPTQ, DRQ raised WT2 calibration loss by 1.78% but cut C4 loss by 15.11%.
- **Fix it proposes.** A constraint that the reconstruction loss may rise only by a preset percentage on checking subsets, plus a worst case over a set of activation distributions. It reports gains up to 70B (GPTQ W2 accuracy 48.23 to 54.56).
- **Affects:**
  - §3.1 "At size the sweep improves its proxy and not the outcome" and its "one reading … the sweep moves error out of the directions the calibration activations occupy and into the others".
  - §4.7 (surrogate table).
  - §8 "Re-selecting codes on a fixed grid".
  - Abstract/§1 phrasing that presents held-out damage as a finding.
- **Suggested handling.** Cite it as concurrent work that reports the same phenomenon in generic integer PTQ. State our differences: deployed GGUF Q2_K grids, single-step provenance, code as an uncovered corpus, paired per-chunk KLD, and the size axis 0.8B to 27B. DRQ's evaluation has no code corpus and no KLD.

### T2. SARQC / RQC: "Saliency-Aware Regularized Quantization Calibration for Large Language Models" (arXiv:2605.05693, 2026-05-07; same group as DRQ)
- **Claims:** C1 (proposed future fix), C5. Severity: **must-cite-and-discuss**.
- **Objective:** minimise ‖WX − ŴX‖² + λ‖(Ŵ − W)S‖² with S = diag(mean|X|/mean|W|). In GPTQ form this replaces the Gram by G + λSSᵀ.
- **Motivation:** the abstract argues that reconstruction-only calibration on "limited or unrepresentative calibration data can pull quantized weights away from the original floating-point weights". The text says "a smaller reconstruction error does not guarantee a smaller weight drift".
- **Numbers (Table 1):**
  - W2 GPTQ → SARQC-GBS(saliency) PPL: LLaMA2-13B 792 to 118; Qwen3-MoE-30B 38.0 to 16.7.
  - W3 gains are small (LLaMA-30B 4.99 to 4.72).
  - The identity-λ variant is *worse* than GPTQ at LLaMA2-7B W3 (7.49 vs 6.80).
  - λ ablation peaks at λ = 0.2.
- **Affects:**
  - §3.1 "the sweep … raising the unweighted error by 16-25%". That observation is SARQC's "weight drift".
  - §3.1 "nothing pulling the codes toward their original values in directions the calibration does not excite".
  - §7 future work, "a regularizer toward the original codes in directions the calibration does not excite". This is now published, in the weight-drift form.
- **Suggested handling.** Cite it there and reword future work as testing SARQC-style regularization on GGUF grids.

### T3. JARQ: "Joint Alternating Refinement for Quantization" (arXiv:2609.38599, 2026-09-29, v2 2026-10-02; Wang, Lyu, Chang)
- **Claim:** C1 "refit beats frozen grid 11/12". Severity: **must-cite-and-discuss** (pre-emption of the finding, not of the GGUF setting).
- **Abstract:** "Group-wise post-training quantizers … round weights onto a grid that is not refit to the resulting integer codes. We show that this leaves accuracy on the table."
- **Table 4b ablation:**
  - "full JARQ beats a scale refit alone in 16 of 16 comparisons" and "code updates on the host grid alone raise perplexity in 5 of 16". Example: Qwen3-4B GPTQ W3 WT2 15.15 → 15.90 with codes only.
  - Lowers PPL in 90/96 comparisons overall.
  - Llama-2 RTN W3 gain: 7B −6.88% vs 13B −4.54%.
- **Affects:** §3.4 closing sentence ("Freezing llama-quantize's grid is therefore a restriction that costs quality and not a feature"). This is now an established result, so present §3.4 as confirmation inside GGUF and cite JARQ.

### T4. SchurQuant Table 3 (arXiv:2608.15567, 2026-08-16; already cited)
- **Claims:** C1 (refit vs frozen), C5. Severity: **should-cite** (add to §3.4).
- **Fixed vs refit grid at Qwen3-4B 2-bit:**

  | Curvature | Δℒ fixed → refit | PPL fixed → refit |
  |---|---|---|
  | Raw | 1.645% → 0.872% | 2344.8 → 152.95 |
  | Schur | 1.165% → 0.550% | 324.8 → 86.6 |

- **Reference-weight regularisation** λ/2‖W − W_ref‖², motivated by "even an exact optimizer can overfit a layer-wise surrogate". At λ = 0 → 1, mean accuracy rises 2-bit +2.58 pp, 3-bit +3.06 pp, 4-bit +0.98 pp.
- **Also reported:** "At 3 and 4 bits … the same optimizer worsens perplexity while changing mean zero-shot accuracy only marginally." Example: 3-bit PPL 21.26 for SchurOpt vs 15.79 for GPTQ at the same objective.
- **Affects:**
  - §8 already cites SchurQuant for the surrogate point. §3.4 should also cite its fixed/refit ablation.
  - Its 2-bit gain over the best baseline does **not** shrink with size: 0.6B 6.7 pp, 3B 6.8, 8B 10.5, 13B 12.4.

### T5. ReQuant Table 6 (arXiv:2608.07019, 2026-08-07; already cited)
- **Claim:** C1, partly support and partly a scale counter-example. Severity: **should-cite** (add the numbers in §3.1).
- **Support.** At Qwen3-235B-A22B W4 (GPTQ+QuaRot, WikiText-2 calibration), 4 sweeps lower WT2 KL 0.075 → 0.064 and UltraChat KL 0.041 → 0.035. Yet UltraChat PPL rises 3.98 → 4.12, NuminaMath PPL rises 2.49 → 2.53, and WT2 PPL is unchanged at 4.53. That is frozen-grid code refinement damaging out-of-calibration text at 235B, the largest-scale corroboration of C1 available. The KL/PPL disagreement is also a small C3-style example.
- **Counter-example.** Gains over GPTQ at Llama-3-70B W4A4 are +7.98 accuracy points, larger than at 8B (+0.53). With activation quantization, re-rounding gains therefore do not universally shrink with size.
- **Its Limitations:** "solution quality depends on the calibration set and the frozen scales/zero-points".
- **Suggested handling.** Scope C1's "small-model effect" to weight-only Q2_K files under a one-shot sweep.

### T6. AutoRound GGUF export (intel/auto-round, `docs/gguf_alg_ext_acc.md`; method papers SignRound arXiv:2309.05516 and SignRoundV2 arXiv:2512.04746)
- **Claim:** C1 "at 8B … no resolved gain". Severity: **must-cite-and-discuss**. It is a released tool that re-optimises rounding (and min/max) *inside* llama.cpp K-quant formats with block-wise sign-gradient tuning, and it is not cited.
- **Average of 10 lm-eval tasks, "Optimized RTN" → "AutoRound+alg_ext":**

  | Model | q2_k_s | q3_k_s |
  |---|---|---|
  | Llama-3.1-8B | 0.5535 → 0.5740 (87.9% → 91.2% of BF16) | 0.6040 → 0.6081 |
  | Qwen3-8B | 0.5901 → 0.5962 | — |
  | Qwen3-30B-A3B | 0.6386 → 0.6460 | — |

- **Mitigations:**
  - The baseline is AutoRound's own "optimized RTN … inspired by llamacpp", not llama-quantize with an imatrix.
  - The docs recommend `--iters 0` (no tuning) for GGUF at every bit width except 3.
  - No variance is reported.
  - The objective is block output reconstruction (end-ish), not our layer-local quadratic.
- **Affects:** §3.1 and §8. Our negative result is about one-shot layer-local GPTQ re-rounding. A block-output, gradient-tuned re-selection of Q2_K_S codes reports a gain at Llama-3.1-8B. Name it as the strongest untested external baseline in the same format. §7.1 currently excludes "learned-rounding methods, which need a training loop", but AutoRound is cheap (about 30 min at 8B per its table) and runs in the same format.

### T7. GSQ on GGUF with frozen Q3_K scales (arXiv:2604.18556; already cited)
- **Claim:** C1 scope. Severity: **should-cite** (one sentence).
- **Setting.** GSQ's Table 9 sets "Group scales lr" = 0 for Q3/4/5_K, so the Q3_K_M runs are frozen-grid code re-selection with a block-staged trained objective. The calibration mixture (OpenThoughts, OpenPlatypus, FineWeb-Edu) matches its evaluation tasks.
- **Average gain over Unsloth:**
  - Qwen3-8B Q3_K_M: 60.52 → 61.61.
  - Qwen3-4B Q3_K_M: 55.70 → 57.83.
  - At Q2_K, where scales are trained: 8B 50.03 → 56.28; 4B 35.01 → 48.34.
- **Affects:** §3.1 / §8 wording that re-rounding is "a small-model effect". It is a small-model effect *for the one-shot layer-wise sweep*. A trained objective on the same frozen K-quant grid reports gains at 8B, with in-domain calibration.

### T8. Calibration-robustness counter-evidence (older)
- **Claim:** C1 cross-domain damage at every size. Severity: **minor** (cite for contrast).
- **Williams & Aletras 2023/2024, arXiv:2311.09755.** GPTQ 4-bit downstream spread across five calibration sources is 0.1-0.5 points (LLaMA 7B-33B), and its overall range is 0.9-1.6%.
- **Paglieri et al., arXiv:2405.20835.** Modern models (Llama-2/3, Mistral, Command-R 35B) are "significantly more robust" to calibration choice than OPT.
- **Why not a contradiction.** Both are at 4 bits, on zero-shot accuracy, with general-text calibration only, and neither has a code corpus. Q2_K, KLD and prose-vs-code are a different regime. A reviewer may cite them, so one sentence of contrast in §3.1 pre-empts it.

### T9. HIGGS "linearity theorem" (arXiv:2411.17525, 2024-11-26; Malinovskii et al.)
- **Claim:** C5 ("surrogates (layer reconstruction) failed to predict outcome"). Severity: **should-cite-and-discuss**. Not in cited.txt.
- **Abstract:** "a 'linearity theorem' establishing a direct relationship between the layer-wise ℓ2 reconstruction error and the model perplexity increase due to quantization." It is used for data-free quantization and dynamic-programming bit allocation on Llama-3.1/3.2 and Qwen.
- **Why it matters.** A reviewer could read it as "layer error *does* predict outcome". The resolution actually strengthens our §3.1 reading. HIGGS's predictor is the *unweighted* relative ℓ2 error (with per-layer coefficients). On our 27B tensors the *whitened* error halved while the *unweighted* error rose 16-25% and the outcome got worse, which is the direction the linearity theorem predicts.
- **Affects:** §3.1 "At size the sweep improves its proxy …" and §4.7. Suggest one sentence noting that the calibration-whitened proxy failed while the unweighted error moved with the outcome, consistent with HIGGS. It also suggests a cheap check (§5 idea 6).

## 3. SUPPORT

| arXiv | Title (date) | Supports | Key finding (verified) |
|---|---|---|---|
| 2504.04823 | Quantization Hurts Reasoning? An Empirical Study on Quantized Reasoning Models (v2 2025-08-18) | C1; contrast with C2 | GPTQ 3-bit on R1-Distill-Qwen-1.5B: WikiText2 → Numina-Math calibration raises the 5-benchmark average 28.3 → 38.1 (+9.8). AWQ moves 1.8, FlatQuant 0.2, KVQuant 0.4. Methods that use calibration "only to compute quantization parameters" are "more robust to domain shift than GPTQ". This mirrors our re-rounding vs imatrix/correction contrast. |
| 2504.09629 | QEP: Quantization Error Propagation (NeurIPS 2025) | C1 (scale, domain) | Table 4, Llama-2-7B: GPTQ calibrated on PTB is *worse than RTN* on WikiText2 (+0.07 PPL). C4 gives −0.25, WT2 −0.46. Figure 1 shows "greater improvements … at lower bitwidths and smaller model sizes". Full propagation "can lead to overfitting … when the calibration dataset is small and insufficiently representative". |
| 2406.17542 | CDQuant (2024) | C1 (scale) | A better solver of the same layer quadratic gains less as size grows within a family. PaLM2 INT3 PPL gain: Gecko 4.0%, Otter 2.7%, Bison 0.7%. Gemma-1 7B 61.5% vs Gemma-2 27B 1.6%. |
| 2604.13806 | DASH-Q: Robust Ultra Low-Bit PTQ via Stable Diagonal Curvature Estimate (EuroMLSys'26) | C1 mechanism; C5; C3-adjacent | "off-diagonal Hessian entries are highly susceptible to sampling noise". Diagonal-only IRLS beats GPTQ at 2-bit (Llama-3.1-8B PPL 28.33 → 16.98). GPTQ 3-bit Llama-3.1-8B has PPL 8.33 yet MT-Bench 0.98 and zero-shot average 40.4%, a PPL-vs-utility disagreement. |
| 2503.03592 | English K_Quantization of LLMs Does Not Disproportionately Diminish Multilingual Performance (rev. 2026-01-22) | C1 contrast | llama.cpp imatrix language (English/Norwegian/Malayalam) on Llama-3.3-70B K-quants: all differences non-significant. The diagonal imatrix is domain-robust, which isolates the full-Gram error feedback as the domain-sensitive step. |
| 2609.31560 | Generalization behavior of OPTQ and the role of regularization (2026-09-25; George & Saab) | C1 | Generalisation bounds for OPTQ in which λ "plays an important role". They propose λ = β‖X‖²_F/(m^{1/3} min{m,N}^{2/3}) instead of the 1% rule: "OPTQ performs poorly when improperly regularized". The experiments are synthetic only. |
| 2508.04853 | Provable PTQ: Theoretical Analysis of OPTQ and Qronos (2025-08-06) | C1 | Error bounds depend explicitly on the calibration data and the regularisation parameter. |
| 2609.26173 | Component Type, Not Reconstruction Error, Predicts Attention Quantization Sensitivity (2026-08-09) | C5 | Nine models (1.3B-8B), 3,808 measurements. Reconstruction error explains <10% of PPL-sensitivity variance in 27/36 cases (median R² = 0.044). |
| 2610.18139 | Revisiting Error Feedback for Model Quantization (2026-10-13) | C5 | Optimal error-feedback design "reveal[s] that trace optimality need not improve finite-level performance". |
| 2406.12928 | Evaluating the Generalization Ability of Quantized LLMs (2024) | C1 (nuance), C5 | Calibration choice moves results by up to 70%. "Calibration set with the same distribution as the test data are not necessarily optimal" on one benchmark; in-distribution calibration helps on another. |
| 2411.17691 | Low-Bit Quantization Favors Undertrained LLMs (ACL 2025) | C1 (scale context) | Larger or less-trained models suffer less quantization-induced degradation under GPTQ. This is context for why repair headroom differs with size. |
| 2607.00908 | Beyond Activation Alignment: The Alignment-Diversity Tradeoff in Task-Aware LLM Quantization (2026-07-01) | C1 (calibration mix) | "using only target-task calibration data can degrade post-quantization performance, whereas incorporating general-domain data stabilizes sensitivity estimation". This parallels our five-calibration grid, where only mixed calibrations improve all corpora. |
| 2410.17170 | Self-calibration for Language Model Quantization and Pruning (2025) | C1 | "unrepresentative calibration examples can harm model performance". Model-generated calibration is competitive and is a candidate "covering" calibration. |
| llama.cpp #4165 / PR #1106 (GitHub) | upstream | C1 context | The k-quants author reported being "on par with best known GPTQ result for 7B, and better for 13B". The community long held that imatrix replaced GPTQ in llama.cpp (#11024). |

## 4. MUST-CITE MISSING (not in cited.txt)

Ranked by priority.

1. **arXiv:2610.11226 DRQ** (T1). Same setting, same phenomenon, two weeks old.
2. **arXiv:2605.05693 SARQC/RQC** (T2). The regulariser our §7 proposes.
3. **arXiv:2609.38599 JARQ** (T3). Refit beats frozen; codes-only can hurt.
4. **AutoRound / SignRound arXiv:2309.05516** (plus SignRoundV2 arXiv:2512.04746 and the intel/auto-round GGUF export) (T6). The only other tool that re-optimises codes inside llama.cpp K-quant formats.
5. **arXiv:2504.04823** (Quantization Hurts Reasoning). GPTQ's calibration-domain sensitivity vs AWQ.
6. **arXiv:2504.09629 QEP**. GPTQ worse than RTN under a mismatched calibration; gains shrink with size.
7. **arXiv:2004.10568 AdaRound** and **arXiv:2208.11580 OBC/Optimal Brain Compression**. §8 names "learned or adaptive rounding" as a class without citing anything. TMLR reviewers will expect at least AdaRound, BRECQ (arXiv:2102.05426) and FlexRound (arXiv:2306.00317) by id, even without numbers.
8. **arXiv:2607.27042 GPTQ-2D: Cubic-Time Two-Sided Adaptive Rounding** (2026-07-29). It is the two-sided Kronecker-metric sweep of our §3.2, done in cubic time. Cite it in §3.2, since our nested sweep is a variant of it. FastKron (arXiv:2608.06291) and WaterKron (arXiv:2609.14706) are further Kronecker-Hessian quantizers.
9. **arXiv:2507.18553 GPTQ = Babai's nearest plane** (ICLR 2026). Our §2.3 calls the problem "a closest-vector problem on the frozen grid", so this is the canonical citation. Its error bound assumes no clipping, which Q2_K's 2-bit codes violate.
10. **arXiv:2504.02692 GPTAQ** (ICML 2025) and **arXiv:2505.11695 Qronos** (ICLR 2026). Standard GPTQ successors (asymmetric calibration, correcting earlier-layer error). One clause in §8.
11. **arXiv:2406.17542 CDQuant**. Coordinate descent on the same quadratic, with gains shrinking with size. Pairs with ReQuant.
12. **arXiv:2411.17525 HIGGS linearity theorem** (T9).
13. **arXiv:2609.26173** and **arXiv:2610.18139**. Independent C5 evidence for §4.7.
14. Should-cite: **arXiv:2607.00908 TASA**, **arXiv:2604.13806 DASH-Q** (off-diagonal Gram noise), **arXiv:2609.31560** (OPTQ λ generalisation), **arXiv:2311.09755** and **arXiv:2405.20835** (calibration-robustness contrast), and **arXiv:2503.03592** (imatrix language robustness on K-quants).

## 5. NEXT-STEP IDEAS

Ranked by expected payoff on one RTX 5090 with llama.cpp, all at 8B/27B Q2_K with the three-corpus paired-KLD harness.

1. **Ridge/saliency damping sweep in the existing re-rounder** (SARQC, OPTQ-λ, SchurQuant λ). Replace G + 0.01·mean(diag)·I with G + λ·D, for D = I and D = diag(imatrix), and λ ∈ {0.01, 0.1, 0.3, 1}×mean(diag). It is a one-line change, CPU-only, and reuses the existing Grams. It tests the paper's own held-out-damage hypothesis directly. λ → ∞ with D = diag(imatrix) recovers llama-quantize's own rounding, so the sweep interpolates between the file and the re-round. Highest payoff per hour.
2. **AutoRound `gguf:q2_k_s` as an external same-format baseline at Llama-3.1-8B.** Run `--iters 0` and tuned/alg_ext, then score on wikitext / FineWeb-Edu / code KLD against our re-round and the imatrix file. GPU time is roughly 30 min to 1 h. This either neutralises T6 or tells us that block-output objectives are what is missing.
3. **DRQ-style guarded acceptance.** Accept a column's or tensor's code changes only if the whitened error under a *second* Gram (FineWeb-Edu or code capture) does not rise by more than x%. Alternatively, minimise the max over the three Grams instead of their sum. This is the held-out-Gram test §3.1 already names, turned into a method. It needs two extra Gram captures.
4. **Diagonal / denoised-Gram re-rounding** (DASH-Q). Re-round with the off-diagonal entries shrunk (G_shrunk = (1−α)G + α·diag(G)). This tests whether the off-diagonal sampling noise drives the 8B/27B failure, and pairs with the existing three-disjoint-calibration-thirds data.
5. **Asymmetric/propagated targets** (GPTAQ / QEP α = 1/2). Capture Grams on the quantized model's inputs, with targets from BF16. The paper's sweep is input-only and layer-independent. QEP reports that partial propagation (α = 1/2) avoids overfitting. This needs a patched imatrix capture over the Q2_K model and is moderate effort.
6. **Zero-GPU diagnostic: the unweighted vs whitened error ledger** (HIGGS linearity). For every re-rounded tensor at 0.8B/8B/27B, log the change in unweighted relative ℓ2 error next to the whitened error, and regress per-file ΔKLD on Σ α_l·Δ(unweighted). If the unweighted error explains the 8B/27B damage, §3.1's reading gets direct support from existing files.
7. **Self-generated covering calibration** (arXiv:2410.17170). Re-round with model-generated text as the Gram source, testing whether "calibration that covers its intended text" can be had without curating corpora.
8. **Joint scale + bounded multi-code moves** (JARQ, SchurQuant), restricted to the Q2_K fields llama.cpp stores. Lower priority, because our alternation (A.4) already found no end-to-end gain and JARQ's own gains shrink at 13B and above.

## 6. Verification notes

- **arXiv ids and titles.** Every id and title above was fetched from arxiv.org (search page, abs page, or html) during this session. The 2026 ids were all confirmed this way.
- **Number provenance.** Numbers marked as table values come from WebFetch extractions of the arXiv HTML. The summariser model did the extraction, so recheck any number before quoting it in the paper. The key ones were cross-checked in a second targeted fetch: ReQuant Table 6, JARQ Table 4b and SchurQuant Table 3.
- **DASH-Q date.** The arXiv stamp on v2 reads 2026-09-16, the EuroMLSys'26 venue is April 2026, and the id is 2604.
- **AutoRound numbers.** These come from a GitHub doc (intel/auto-round `docs/gguf_alg_ext_acc.md`), not a peer-reviewed paper. They have no variance estimates, and the baseline is not llama.cpp imatrix.
- **Not verified.** The intel/auto-round GGUF internals: whether it tunes K-quant sub-scales and mins, and whether it uses the imatrix. The docs do not say.
