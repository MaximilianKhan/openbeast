# R5: GGUF / llama.cpp quantization, trained low-bit checkpoints, and mixed-precision allocation

Sweep date: 2026-10-22. Slice: academic evaluation of llama.cpp K- and I-quants; trained low-bit checkpoints (GSQ, RCO, QAT/QAD, Unsloth UD, AutoRound, "trained GGUF"); per-tensor and mixed-precision bit allocation.

## 1. Coverage

- **arXiv API.** 17 query families ran (sorted by submission date, 100 to 200 results each). They covered: K-quant; mixed precision with LLM; bit and bit-width allocation; layer-wise mixed precision; Hessian, sensitivity and salience with allocation; knapsack, integer programming, dynamic programming and budget; QAT, 2-bit and ternary; KL or perplexity against benchmarks with fidelity, misleading or proxy; llama.cpp, GGUF, ollama, imatrix and Unsloth; learned, adaptive and Gumbel rounding; calibration with domain, held-out or overfit; low-rank with mixed precision or rank allocation; MoE mixed precision; reasoning, agentic or code low-bit evaluation; KL divergence on quantized LMs.
  - The database holds **about 900 unique abstracts** (`r5/db.json`). All of them were keyword-screened, and about 300 titles from 2024-06 onward were read by eye. About 60 abstracts were read in full, and about 15 papers were read in full text.
  - arXiv rate-limited the shared IP (HTTP 429) for long stretches, so some planned narrow queries were merged into broad OR queries.
- **Web.** About 30 WebSearch and WebFetch queries over Hugging Face model cards, GitHub (IST-DASLab/GSQ, IST-DASLab/RCO, IST-DASLab/gptq-gguf-toolkit, ik_llama.cpp discussions), Unsloth docs, ByteShape and third-party reproductions.
- **Date range.** Mainly 2024-06 to 2026-10-21.
- **Verification.** Every arXiv id below was fetched from arxiv.org or returned by the arXiv API with its title. Non-arXiv sources are marked as such.

## 2. Threats

### T1. C3's mechanism sentence is contradicted by the published objectives. Must fix wording.

- **Sources:** GSQ arXiv:2604.18556 v2, Algorithm 2 and Appendix H; RCO arXiv:2605.00649 v3 (2026-09-27), Eq. 6 and Appendix E.
- **GSQ is a teacher-matching method.** Its block-wise loss is ‖f(x; w̄) − f(x; w)‖²_F, a reconstruction of the full-precision block outputs. Its end-to-end scale stage uses "the distillation objective described in Section 4.2".
- **RCO is also teacher-matching.** "RCO minimizes KL divergence on the calibration dataset" (Eq. 6). In its LLM experiments the calibration set is 256 FineWeb-Edu sequences.
- **The model card says something slightly different.** It says RCO optimizes "directly on the task loss", and it never names the calibration data.
- **Affected text:** §5.4, "a file trained against data, as these are, can give up some of that fidelity and still assign the text higher likelihood", and the matching line in §5.6. By their own papers, the released files were optimized to approximate the BF16 teacher, on an undocumented calibration distribution, not against data labels.
- **Suggested rewrite:** "optimized against the teacher on a calibration distribution we cannot inspect". Treat the wikitext NLL gain as a calibration-domain effect, not as the result of training on data.
- **The card's own perplexities support the narrower reading:**
  - IQ2 class: UD-IQ2_S is better than GSQ-RCO IQ2_XS on C4 (12.78 vs 12.98) and FineWeb-Edu (9.08 vs 9.19). GSQ-RCO is better only on wikitext (7.69 vs 8.02).
  - IQ3 class: C4 11.76 vs 11.75 and FineWeb-Edu 8.34 vs 8.34, a tie.
  - The GSQ-RCO likelihood advantage is therefore wikitext-specific. Our §5.4 already reports that the FineWeb-Edu NLL is level, which is consistent.
- **Severity: must-fix wording.** The empirical C3 numbers are not threatened.

### T2. An independent KLD table on the same artifacts agrees on wikitext and FineWeb, and reverses on a third corpus. Should cite; scope C3 to the corpora tested.

- **Source:** agentionai/Qwen3.8-27B-AP-GGUF model card on Hugging Face (non-archival, written by a competing quantizer).
- **Setup:** BF16 reference converted with stock `convert_hf_to_gguf.py`, llama.cpp `26bc85e42`, `--chunks 60 -c 2048`. The ISTA rows are their `-mtp` builds.

| corpus | GSQ-RCO-IQ3_S (11.29 GiB) | UD-IQ3_S (11.21 GiB) | closer to BF16 |
|---|---|---|---|
| wikitext-2 | 0.0665 | 0.0470 | UD |
| "neutral web" (seeded FineWeb slice) | 0.0432 | 0.0404 | UD, slightly |
| private held-out technical documentation | 0.0594 | 0.0617 | GSQ-RCO |

- **What agrees with us:** UD is closer on wikitext, and near-tied on FineWeb. These match our §5.2 and §5.3.
- **What reverses:** On an unpublished third corpus the trained file is closer.
- **Also on that page:**
  - GSQ-RCO-IQ2_XS held-out KLD is 0.2263; no UD-IQ2_S row is given.
  - One 70-question MMLU-Pro run: AP-IQ2_S 77.1% vs GSQ-RCO-IQ2_S 75.7%.
- **Affected text:** the abstract and §5.6 phrase C3 as "further from BF16 by KLD … at matched bytes". That holds on wikitext, is marginal on FineWeb-Edu, and is not universal.
- **Suggested change:** add "on the two public corpora we measured".
- **Severity: should cite as a third-party corroboration plus caveat.** The held-out corpus is not reproducible.

### T3. GPTQ inside llama.cpp K-quants, with the grid refit, plus K-quant type allocation, was already released (Sept 2025). Must cite for C1 and C2.

- **Source:** IST-DASLab/gptq-gguf-toolkit on GitHub. First commit 2025-09-09; "add gptq for gguf + packing-script" 2025-09-10. Not a paper; cite as software.
- **What the code does:** I read `quant/gptq/src/gptq.py` and `quantizer.py`. It runs a GPTQ sweep over Q2_K–Q6_K and recomputes the K-quant super-block scale and min (`get_scale_and_zero`) at each super-group boundary inside the sweep. This is exactly the "GPTQ allowed to refit the grid inside the same format" arm of our §3.4.
- **It also allocates types:** EvoPress per-layer type allocation with a KL fitness, using FineWeb-Edu calibration (2,097,152 tokens).
- **README results, Llama-3.1-8B-Instruct, perplexity on C4 / FineWeb-Edu / wikitext-2:**
  - At 3.01 bpw: EvoPress-kQuant 13.17 / 10.66 / 7.89 vs UD-Q2_K_XL 13.75 / 11.13 / 8.29.
  - At 4.88 bpw: 11.18 / 9.23 / 6.84 vs UD-Q4_K_XL 11.23 / 9.30 / 6.84.
- **For C1:** our frozen-grid re-rounder versus refit-grid GPTQ is not the first GPTQ-on-K-quant implementation. Our novelty is the frozen-grid ablation and the held-out damage finding.
- **For C2:** this is independent evidence that type allocation inside the format beats a vendor dynamic mix at equal bits.
- **Affected text:** §3.4 and §8, "Re-selecting codes on a fixed grid".
- **Severity: must cite** (same lab as GSQ and RCO, and directly on our format).

### T4. Same-byte K-quant allocation on Qwen3.8-27B in llama.cpp, and RTN error is "spectrally flat". Must cite for C2 and C5; partly pre-empts the C2 control.

- **Source:** arXiv:2609.33923, Kennedy & Kennedy, "Quantization Error Is Spectrally Flat: A Single Random Probe Is a Calibrated, Data-Free Sensitivity Estimator…", v1 2026-09-27, v2 2026-09-29.
- **Allocation result (Table 6):** Qwen3.8-27B, llama.cpp K-quant types one per tensor, one shared imatrix, 40×2048 wikitext-2 test chunks.

| arm | size | PPL |
|---|---|---|
| stock IQ3_M | 12.58 GB | 6.336 |
| stock Q3_K_M | 13.30 GB | 6.207 |
| block-cosine × CKA allocation (matched to IQ3_M within 1.7%) | 12.79 GB | 5.991 |
| propagated block cosine allocation | 12.72 GB | 6.041 |

  - The allocated files beat the stock mix at the same bytes and the next larger stock type.
- **For C5:** allocating from the real-activation per-tensor GPTQ objective (the oracle, 6.087) loses to the block-output signal on 35 of 40 chunks (Wilcoxon p = 1e-5). The authors call the per-tensor objective "the wrong quantity to allocate from". This is an external instance of our surrogate-failure finding.
- **For C2:** RTN error has an effective dimensionality of 0.93–0.96 of the i.i.d. value across 1,683 tensors. The median is unchanged from 2 to 8 bits on the MoE. The authors write that "no low-rank summary captures it". This corroborates our "low-rank doesn't compress the residual" (95% energy needs 50–80% of rank).
- **What it does not do:** it has no low-rank arm, so our correction-vs-allocation comparison is still new.
- **Affected text:** §4.3 and §4.4 control discussion; §8 "Placement and allocation"; §7.1 limitation, "Hessian-aware mixed-precision … not compared".
- **Severity: must cite.** It is the closest published analogue of our mixed-type control, on our own model family and format.

### T5. KL to the teacher vs likelihood of data: the same split was published for QAT vs QAD. Must cite; narrows C3's novelty.

- **Source:** arXiv:2601.20088, "Quantization-Aware Distillation for NVFP4 Inference Accuracy Recovery" (NVIDIA, v3 2026-03-03).
- **Table 1, Llama Nemotron Super V1:**

| method | KL vs BF16 | cross-entropy vs labels |
|---|---|---|
| QAT | 0.311 | 0.408 (lower) |
| QAD | 0.004 | 0.416 |

  - The paper's wording: QAT "can match validation loss, [but] it significantly changes the model's output distribution".
- **The QAT arm is also worse on tasks** (AIME25 41.5 vs 45.6; Nemotron 3 Nano GPQA-D 66.0 vs 72.7).
- **What this means for C3:** a lower-NLL, higher-KL file is not evidence of higher capability either. Our framing, "KLD measures fidelity, not quality", should not be read as "NLL measures quality".
- **Affected text:** §5.4 and §5.6, and the abstract's C3 framing. Cite it as prior evidence of the same split; our contribution is observing it on released GGUF artifacts at matched bytes.
- **Severity: must cite and discuss.**

### T6. "KLD measures displacement, not direction" is already the thesis of a paper we cite only for a side point. Must re-cite in §5.

- **Source:** arXiv:2606.19558, Nikolić, Zadeh, Torres Sanchez, Moshovos, "Displacement Is Not Direction…". It is in cited.txt, but §8 uses it only for "perplexity secondary".
- **Findings:**
  - Among near-baseline GGUF quants (28 quants of Qwen3.6-35B-A3B, including Unsloth UD files), KLD vs benchmark Spearman is ρ = 0.00 in the "silent zone" (Devstral ρ = −0.24).
  - KLD tracks how many outcomes change, not whether the changes help.
  - Higher-KLD files win HumanEval and LCB head-to-heads; routing to the lower-KLD file is right 42–49% of the time.
- **Related, should cite:** arXiv:2609.07664, "Accuracy is Not Enough" (Qamar, Sparrenberg, … Sifa, Sept 2026). It runs the llama.cpp Q8_0→Q2_K ladder, and its divergence metrics "measure reference fidelity rather than correctness".
- **Related, should cite:** arXiv:2605.02404, "Statistically-Lossless Quantization" (Helcig, Kurtic, Alistarh, the GSQ/RCO lab). Task-lossless is reached at about 3.3 bpw; distribution-lossless needs 5–6 bpw.
- **Affected text:** §5.4, §5.6 and §8 "Measurement".
- **Severity: must cite in §5.** Without it, a reviewer will say the C3 lesson is known. Our addition is the trained-vs-untrained matched-byte instance with paired per-chunk statistics.

### T7. Mixed-type reallocation does not automatically win. Should cite to bound C2.

- **Source:** arXiv:2610.20826, Y. Liu, 2026-10-14.
- **Setup:** ik_llama.cpp IQK types, Qwen3.6-35B-A3B, budget-neutral per-layer reallocation of routed-expert types guided by routing statistics. KLD on 647 chunks of The Stack.
- **Results:**

| regime | uniform KLD | best reallocation KLD | verdict |
|---|---|---|---|
| ~4 bpw ("flat") | 0.014725 | 0.015276 | z ≈ 0.9, neutral |
| ~2.7 bpw ("steep") | 0.034321 | 0.0381–0.0383 | z > 4, all lose |

  - Random allocations land within 1 SE of the guided ones.
- **Bearing on C2:** our "same-byte mixed-type control beats the correction" depends on how the control is built. Ours used measured per-kind KL sensitivities. A badly guided allocation would lose to uniform, and possibly to the correction.
- **Suggested change:** strengthen §4.3 by saying the control was sensitivity-guided and predicted in advance. This is already partly stated, so cite this as a caution.
- **Severity: should cite.**

### T8. Minor items

- **The card's capability deltas are 1-problem events.** The ISTA GSQ-RCO card has BF16 AIME25 = 100.00. Its claimed AIME25 gains of 3.33 and 10.00 are 1 and 3 problems out of 30. This supports our caution in §5.5. Optional to mention.
- **Unsloth re-uploaded its Qwen3.8 files.** It released "Dynamic 3.0" Qwen3.8-27B GGUFs on 2026-08-19 (HF unsloth/Qwen3.8-27B-GGUF discussion #74). It says MTP was removed from quants at or below UD-Q2_K_XL (8.37 GB and smaller), and the release drew community reports of regressions.
  - Our §5.1 lists UD-IQ2_S at 8.37 GB and UD-IQ3_S at 12.04 GB.
  - **Provenance fix:** record the HF revision/sha of the UD files used. The GSQ-RCO card's own UD baselines may come from a different upload revision. Should fix.
- **The imatrix calibration language barely matters for stock K-quants.** arXiv:2503.03592: English, Norwegian and Malayalam imatrix for Llama-3.3-70B K-quants gave non-significant MixEval differences.
  - This is not a contradiction of C1. It contrasts usefully: a diagonal imatrix weighting is robust to calibration domain, while our full re-rounding damages held-out text.
  - Optional cite in §3.

## 3. Support

- **C1 (held-out damage and calibration sensitivity):**
  - arXiv:2608.15046 (Singh, Aug 2026): changing only the calibration draw reversed the GPTQ-vs-AWQ ordering in 5 of 8 confirmatory cells; churn is about 5× the net delta.
  - arXiv:2510.10618 (calibration data curation): calibration "representativeness and diversity in activation space" determines retained capability.
  - Unsloth docs (non-archival): Wikipedia-calibrated imatrices give misleadingly good wikitext KLD. Agention's table shows the same: a recalibrated build dropped held-out KL 15–18% while wikitext rose up to 9%.
- **C2 (allocation is the stronger lever, and low-rank does not compress the residual):**
  - 2609.33923 (T4).
  - gptq-gguf-toolkit (T3).
  - arXiv:2609.01587 (Hu & Ramachandran, Sept 2026): at a matched +0.146 bpw budget, spending bits globally on finer granularity beats repairing the best layers locally for all 8 models, by 21–52 recovery points. This is analogous to "spend bytes on the base, not a local repair".
  - RCO arXiv:2605.00649: at 2.25 bpw on Qwen3-8B, RCO allocation gives FineWeb-Edu PPL 20.47 vs EvoPress 21.40.
  - GAMMA arXiv:2605.18475: learned allocation at 2.5 bpw matches fixed 3-bit.
  - CASA arXiv:2609.25916: scalar sensitivity proxies distort inter-module ranking by up to √(κ(A)κ(B)); largest gains below 3 bpw.
- **C3:** T5 (2601.20088), T6 (2606.19558, 2609.07664, 2605.02404), and T2 for the wikitext and FineWeb direction. Also:
  - arXiv:2610.04403 (GGUF ladder, 27 artifacts): Q2 damage "not calibrated by WikiText-2 perplexity".
  - arXiv:2601.14277 (Kurt): files with equal PPL (7.43) differ on GSM8K by 3.4 points.
  - arXiv:2606.03002: perplexity improves while SAE features degrade.
- **C5 (surrogates fail; methodology):**
  - arXiv:2609.26173: within a component type, reconstruction error explains under 10% of perplexity-sensitivity variance in 27 of 36 cases (median R² = 0.044).
  - arXiv:2609.01587: cheap signals do not localize recoverable damage.
  - 2609.33923: the per-tensor objective is the wrong allocation target.
  - arXiv:2610.19498 ("What You Sign Is Not What You Run"): 0 of 198 top GGUF repos are bit-for-bit re-derivable from disclosed inputs; the imatrix must be shipped and hash-bound. This supports our single-step provenance discipline.
  - arXiv:2608.15046: paired equivalence tests and per-item release.
  - arXiv:2607.12266: per-layer effects explain 85–93% of the loss variance across layer sets. This is consistent with our near-additive per-kind sensitivities (isolated sum 0.352 vs joint 0.346).
- **Capability context for §5.5:**
  - arXiv:2609.32042: GGUF Q8_0→Q2_K agentic tool use; the 3-bit to 2-bit cliff replicates across 3 models with McNemar p < 0.001; failure modes do not replicate.
  - arXiv:2606.25519: quantization inflates reasoning tokens.
  - arXiv:2608.06564: GGUF flips fewer tool-call decisions than RTN at 3 bits.

## 4. Must-cite missing (not in cited.txt)

1. arXiv:2609.33923, Spectrally Flat: same-byte K-quant allocation on Qwen3.8-27B; flat RTN spectrum. (T4)
2. arXiv:2601.20088, NVIDIA QAD: QAT has lower CE but 78× higher KL. (T5)
3. IST-DASLab/gptq-gguf-toolkit (GitHub, 2025-09): GPTQ with grid refit for K-quants plus EvoPress type allocation. (T3)
4. arXiv:2609.07664, "Accuracy is Not Enough": llama.cpp ladder; divergence equals fidelity, not correctness. (T6)
5. arXiv:2605.02404, Statistically-Lossless Quantization: task-lossless and distribution-lossless need very different bpw. (T6)
6. arXiv:2410.14649, EvoPress (ICML 2025 camera-ready per arXiv comments): the allocation baseline behind the GGUF toolkit and RCO's comparisons. It fills the "Hessian-aware/mixed-precision allocation, not cited" hole that §8 admits. Pair it with one of CASA arXiv:2609.25916 or GAMMA arXiv:2605.18475 as a 2026 representative.
7. arXiv:2610.20826: allocation can lose to uniform in the ik_llama.cpp IQK types. (T7)
8. arXiv:2609.01587: global vs local spending at a matched budget. (C2 and C5 support)
9. arXiv:2609.26173: reconstruction error does not predict sensitivity. (C5)
10. Optional, for "has anyone evaluated llama.cpp quants academically":
    - arXiv:2601.14277 (Kurt, K-quants and legacy types, no I-quants, no KLD).
    - arXiv:2610.04403.
    - arXiv:2610.19498.
    - arXiv:2505.23786 ("Mind the Gap", ICML 2025: GGUF quantization-interval attack; the first academic treatment of the K-quant interval geometry).
    - Sparrenberg et al., "Small and Fast LLMs on Commodity Hardware: Post-Training Quantization in llama.cpp", IEEE DSAA 2025. Not on arXiv; seen via the Lamarr Institute and bonndoc listings. Full text not read; **unverified content**.

## Answers to the slice's key questions

- **Has anyone evaluated llama.cpp K- and I-quants academically?**
  - Yes for K-quants and legacy types: 2601.14277, 2609.07664, 2607.08734, 2610.04403, 2609.32042, 2608.06564, 2606.10154 and 2503.03592.
  - I-quants appear only as baselines: the IQ3_M row in 2609.33923, and the ik IQK types in 2610.20826.
  - None of them puts a low-rank correction against the type ladder.
- **Has anyone shown that mixed-type allocation beats added low-rank bytes at equal size?** Not found.
  - The closest work is joint bit and rank search: AutoQRA arXiv:2602.22268 (fine-tuning), MLoRQ arXiv:2507.09616 (ViTs), and LoRaQ arXiv:2604.18117 (diffusion transformers, low-rank branch bits at equal overhead). None runs the head-to-head.
  - Allocation-only wins at equal bytes on GGUF exist (2609.33923; the gptq-gguf-toolkit), which makes our C2 comparison credible, but it is still new.
- **Are there GSQ/RCO follow-ups, critiques or replications?**
  - No archival critique or replication was found.
  - Non-archival: agentionai KLD table (T2); ramgpt.org reproduction, which found wikitext PPL for GSQ-RCO 2.5 bpw of 6.6645 vs UD-IQ2_S 6.7960, the card's C4 favoring UD, and GSQ-RCO decoding 1.7% slower; a zephel01 blog (58 of 60 questions, small n).
  - RCO v3 (2026-09-27) mentions GGUF only for MoE expert counts.
  - The public GSQ repo still has no GGUF code path. I checked this: 122 files, the only GGUF item is `assets/qwen3_8b_gguf.png`, and the last commit is 2026-07-05. The public RCO repo has no GGUF files either. Our §5.1 statement stands.
- **Has any paper evaluated Unsloth UD quants?**
  - Only as baselines: GSQ arXiv:2604.18556 (UD Q2_K and Q3_K_M as initialization), the ByteShape cohort arXiv:2606.19558, and the gptq-gguf-toolkit README.
  - Unsloth has no paper. Its claims come from its docs: UD 3.0 claims "+10% top-1"; Divergence-300@32 is its proposed metric.

## 5. Next-step ideas (ranked by expected payoff on one RTX 5090 with llama.cpp)

1. **Correction vs allocation at equal bytes on Qwen3.8-27B, against the 2609.33923 allocator.** Build their probe-based K-quant allocation (or a KL-greedy knapsack) at the bytes of our corrected arm, and also give the adapter's bytes to the allocator. This turns our single hand-built control into a systematic one and answers §7.1. Cheap: CPU quantize plus about 3 KLD runs.
2. **Score GSQ-RCO vs UD on a third public corpus (code) and a long-context corpus.** This tests whether T2's held-out reversal is real or an artifact of a private corpus, at about 1 GPU-hour. Also record the UD revision sha (T8).
3. **Run the gptq-gguf-toolkit GPTQ-refit on Q2_K for our 0.8B/8B/27B cells** as an external implementation of our refit-grid arm. It replicates C1's 11/12 result with third-party code, and tests whether its FineWeb-Edu calibration avoids our held-out damage. This would show whether the damage comes from the calibration domain or from re-rounding itself.
4. **Use a QAD-style "KL vs CE" pair as an explicit two-axis readout.** For every §5 file, report KL-to-BF16 alongside NLL on held-out text, plus the ByteShape "volume vs direction" decomposition (flip count vs net task delta) on the replication-F units. That makes C3 quantitative instead of anecdotal.
5. **A low-rank correction aimed only at the tail.** 2610.20826 and agention both show allocation losses concentrated in the 99th-percentile KLD. A rank-r correction on the tensors that drive p99 KLD might beat allocation on the tail even where it loses on the mean. Low cost: we already have per-chunk KLD.
