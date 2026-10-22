# R1 — Low-rank error correction / compensation of quantized LLM weights (claim C2 + low-rank-compressibility statements)

Sweep date 2026-10-22. Agent slice: LQER / QERA / EoRA / CALDERA / LoRC / RILQ / ResQ / LRC / SVDQuant / ASER / QER / LoftQ-style lineage, 2023 → Oct 2026.

## 0. Bottom line

- **Nothing fatal.** I found no published paper that compares a low-rank quantization-error correction against a **same-byte stock-type or mixed-precision control** with paired statistics, on or off the calibration distribution. In the formal literature that comparison is still missing, and it is the paper's main contribution under C2. Every 2025-2026 correction paper I read in full compares against other correctors at equal **rank** (Align-Then-Correct, EoRA, QERA, SERQ, GlowQ, SRR), not against spending the same bytes on base bits.
- **Closest pre-emption is informal and already in the reference list, but the paper undersells it.** ik_llama.cpp discussion #15 (Aug 2024) does more than "predict" failure. (a) ikawrakow tabulates L²QER at 4.30 bpw against IQ4_XS at 4.25 bpw and Q4_K_S/IQ4_K at 4.5 bpw on LLaMA-1/2 7B/13B. A fewer-byte I-quant beats the correction on all four models (e.g. ΔPPL 0.220 vs 0.075 on LLaMA-v1-7B), which is the 8B/27B IQ3_XS finding in miniature, done across papers. (b) He states the same-byte criterion explicitly: "given the extra bits spent, is the improvement better than ... using quantization mixes ... with the same extra bits spent." (c) compilade reports an informal test on OpenELM-270M: carve+LQER helped pure Q2_K "but wasn't really better than the default Q2_K mix". (d) ikawrakow's `try_svd` branch reports that "SVD does not add any value to LLMs quantization when the quantization works reasonably well", with SVD both before and after quantization. §8 should say that the same-byte question and an informal answer were already on record upstream, and that this paper supplies the controlled, paired, held-out version. **Must-cite-and-discuss (re-frame).**
- **Off-distribution shrinkage and "recovery grows as bits shrink"** are corroborated, not contradicted. EoRA's own tables give larger gains at 3-bit than at 4-bit (LLaMA3-8B ARC-C +10.84 vs +1.54) and smaller gains at 13B than at 7B. Align-Then-Correct (2610.08164, 6 Oct 2026, not cited) reports a held-out-to-in-sample singular-value shrinkage ratio of 0.64 → 0.85 as calibration grows. A ternary-correction note (Zenodo) states that perplexity recovery "is largely domain-adaptation to the correction's training corpus".
- **No 2025-2026 paper does low-rank correction on GGUF / llama.cpp K- or I-quants.** The only GGUF-specific 2026 items are allocation (a per-tensor KLD-knapsack mixed-precision study for llama.cpp, Preprints.org, 20 Oct 2026), trellis coding at IQ2_XXS size (Zenodo) and GSQ-RCO releases. All of them support the "type allocation beats adapter" reading rather than threaten it.
- **Cross-slice alert (C1, not my slice):** DRQ (arXiv:2610.11226, 8 Oct 2026) re-chooses integer codes **on the existing frozen grid** and shows that lower calibration reconstruction loss can raise held-out perplexity. On Llama-3.2-1B 3-bit, 73.8% of AWQ loss-reducing edits and 47.1% of GPTQ ones raised held-out WT2 PPL. This is the nearest published neighbour of the re-rounder and of C1's "damages held-out corpora" finding. Must-cite for C1 and C5.

## 1. Coverage

- **Queries:** 179 logged.
  - arXiv: 49 abstract-field queries through arxiv.org/search (1,175 records). export.arxiv.org returned HTTP 429 for most of the session because sibling agents share the IP; the arxiv.org/search HTML route recovered after back-off.
  - OpenAlex `title_and_abstract.search`: 61 queries, filtered to ≥2023, before the shared daily budget ran out.
  - Hugging Face papers semantic search: ~70 queries at 120 results each.
  - Web search: ~12 queries.
  - GitHub GraphQL discussion search on ggml-org/llama.cpp and ikawrakow/ik_llama.cpp.
  - Full-text reads: 2610.08164, 2410.21271 (EoRA v5), 2610.11226 (DRQ), the BALANCER IJCAI-26 PDF, ik_llama.cpp #15 (whole thread), llama.cpp #8831 (whole thread), and the Zenodo records below.
- **Records pooled:** 4,130 unique records (arXiv IDs plus non-arXiv OpenAlex works).
  - 1,040 match a broad slice filter (quantiz* AND low-rank/SVD/LoRA/adapter/residual/compensat*/reconstruct*).
  - 571 match the strict filter (quantiz* AND low-rank/SVD/LoRA/adapter AND LLM/transformer). All 571 were screened by title, ~250 at abstract level, and ~40 were opened.
  - Caveat: the arxiv.org/search scraper captured titles reliably but truncated abstracts, so arXiv-only hits were screened by title and opened when the title was relevant.
- **Date range:** 2023-01 → 2026-10-22, weighted to 2025-2026 (~70% of slice hits are 2026).
- **Query families:**
  - "low-rank compensation / correction / error reconstruction", "quantization error + low-rank / SVD".
  - Method names: LQER, QERA, EoRA, CALDERA, LoftQ, RILQ, SVDQuant, ASER, PiSSA/QPiSSA.
  - "low-rank plus quantized", "rank allocation", "bit budget low-rank", "mixed-precision low-rank".
  - "2-bit / sub-2-bit / ternary / binary low-rank".
  - "MoE low-rank compensation".
  - "GGUF", "llama.cpp quantization", "k-quants", "imatrix".
  - "calibration data / out-of-distribution / generalization" × quantization.
  - "SVD LLM compression / whitening / effective rank".
- **Gaps:**
  - The export.arxiv.org API was throttled, so arXiv coverage relied on the arxiv.org HTML search plus the HF and OpenAlex mirrors. OpenAlex has good 2026 coverage (it surfaced 2610.* papers).
  - OpenReview and the ACL Anthology were not crawled directly; ACL-indexed items appeared through OpenAlex.

## 2. THREATS

Severity scale: fatal / must-cite-and-discuss / should-cite / minor.

### T1. ik_llama.cpp discussion #15 "Will LQER improve k- and i-quants?" (ikawrakow, 2024-08-09) plus compilade's reply. Already cited, but under-described. Claim C2. **Must-cite-and-discuss (re-frame).** Verified by fetching the full thread via the GitHub GraphQL API.

- **Exact content.** Table of ΔPPL against f16:

  | quant | bpw | LLaMA-v1-7B | LLaMA-v1-13B | LLaMA-v2-7B | LLaMA-v2-13B |
  |---|---|---|---|---|---|
  | L²QER | 4.30 | 0.220 | 0.100 | 0.100 | 0.060 |
  | IQ4_XS | 4.25 | 0.075 | 0.054 | 0.065 | 0.048 |
  | Q4_K_S | 4.50 | 0.065 | 0.041 | 0.063 | 0.044 |

  This is a fewer-byte stock I-quant beating a published correction, though across papers with a re-calibrated PPL protocol.
- ikawrakow states the criterion verbatim: "the question is, given the extra bits spent, is the improvement better than what is achievable by using a different quant, using quantization mixes, etc., with the same extra bits spent."
- compilade (same thread, 2024-08-09): carving a low-rank part off and quantizing the remainder "does help with pure Q2_K compared to plain LQER, but wasn't really better than the default Q2_K mix ... on a small model (OpenELM-270M), and with F16 LoRA and a rank of 32."
- ikawrakow (2024-08-27) on the `ik/try_svd` branch: "SVD does not add any value to LLMs quantization when the quantization works reasonably well". This is RMSE-level, both before and after quantization. He also reports that pre-quantization SVD carve was "hopeless until you started approaching full SVD" except for early K/Q tensors.
- **Where it affects our paper.** §8 "The upstream discussion" currently reads "was met with the prediction (ik_llama.cpp #15) that mature quantizers would not benefit. Our measurements agree with that prediction for correction against the ladder." That understates it.
  - The same-byte framing, an informal mixed-type-control test (OpenELM-270M, Q2_K mix) and a fewer-byte I-quant win (4-bit LLaMA) are already on record.
  - §1.4 ("What we add is ... against ... a same-byte mixed-type control") stays true for a **controlled, paired, held-out, multi-scale** measurement, but should acknowledge these upstream observations as the informal precedent.
  - The upstream RMSE observation on SVD-before-quantization also pre-dates our §4.4 "placements indistinguishable" and §4.1 "neither weights nor residual are low-rank", qualitatively.
  - **Suggested sentence:** "The same-byte question was posed upstream (ik_llama.cpp #15): a 4.3-bpw L²QER result trailed IQ4_XS at 4.25 bpw across four LLaMA models, and an informal test on a 270M model found a carve-plus-correction no better than the stock Q2_K mix. We supply the controlled version: paired, held out, from 0.8B to 27B."

### T2. Align, Then Correct: Training-Free Two-Stage Low-Rank Compensation for Extremely Quantized LLMs. arXiv:2610.08164, 2026-10-06 (Song, G. Lee, J. Lee, Choi; the RILQ group). Not cited. Claims C2 (off-distribution) and C5 (surrogates). **Must-cite-and-discuss.**

- **Calibration and held-out results.** Calibration is WikiText-2 only; C4 is explicitly "held-out".
  - Qwen3-8B at 2-bit (QuIP#): WT2 PPL 12.43 → 10.26, C4 19.19 → 17.17, closing 51% of the C4 gap to FP16 against 31% for the best baseline.
  - Qwen3-4B: WT2 21.11 → 13.22 (below FP16's 13.64, i.e. calibration overfit), C4 29.21 → 21.36 (84% closed).
- **Shrinkage diagnostic.** Held-out/in-sample singular-value ratio 0.6403 → 0.8517 when calibration grows 4×. This is direct evidence that the whitened correction's captured directions partially fail to transfer.
- **Rank and comparison set.** Rank 64 for all methods. The authors argue the symmetric whitened target is "inherently high-rank", which matches our §4.1/§4.2.
- **No same-memory or higher-bit control.** Adapters are 264-349 MB FP16 per model (not counted in bits) and bpw is not reported.
- **Why it matters.**
  - (i) It is the newest strongest member of the lineage and uses a Fisher-weighted **asymmetric** target plus a natural-gradient second stage, i.e. it attacks exactly the off-distribution shrinkage we report.
  - (ii) Its WT2-below-FP16 result on Qwen3-4B is a published instance of the correction fitting the calibration corpus.
  - (iii) It never builds a byte-matched control, so our §4.3 control result stands as unaddressed by it.
- **Affects** §8 (lineage paragraph) and §4.3 "Reading". We cannot claim that the best current correction would also lose to the control, because we did not test an asymmetric / Fisher target. State this as a limitation, or as next work (§5 idea #1).

### T3. EoRA (arXiv:2410.21271). Already cited. Claim C2 (scale and bit trend). **Should-discuss (support, but note its counter-framing).**

- **Gains by bit width.** On LLaMA3-8B, rank 128, the uncompensated → EoRA gain is:
  - 3-bit: ARC-C +10.84, MathQA +6.74, GSM8K +11.45.
  - 4-bit: ARC-C +1.54, MathQA +3.14, GSM8K +2.96.
- **Gains by model size.** ARC-C at 3-bit: LLaMA2-7B +4.18, LLaMA2-13B +2.22. At 4-bit: 7B −0.08, 13B +0.25.
- **Its counter-framing.** Appendix A.2 claims "EoRA-enhanced models outperform smaller models quantized at higher precision" (3-bit 13B+EoRA 15.08 vs 4-bit 7B 9.93 on GSM8K). That is a **cross-model**, not byte-matched, comparison.
- **Its own byte data contradicts that framing.** 3-bit+EoRA LLaMA3-8B (4.78 GB) still trails uncompensated 4-bit (5.35 GB) on ARC-C (31.74 vs 45.90) and GSM8K (11.90 vs 27.74), which supports "factors do not replace base bits".
- **Affects** §4.5. We currently say recovery growth "agrees with ... EoRA's results". We can cite the numbers, and add the size trend (smaller gain at 13B) as published corroboration of shrinking benefit with scale, alongside CALDERA.

### T4. DRQ, "When Lower Reconstruction Loss Hurts: Distributionally Robust Refinement for Low-Bit LLM Quantization". arXiv:2610.11226, 2026-10-08. Not cited. Claims C1 and C5 (outside my slice; flagged). **Must-cite-and-discuss for C1/C5.**

- DRQ "refines the integer codes ... within the existing quantization grid" (scales and zero-points frozen), which is the same move as our re-rounder.
- **Calibration-overfit evidence.** Calibration is WT2 (128×2048).
  - Llama-3.2-1B, 3-bit: 18 of 32 modifications lowered reconstruction loss but raised PPL **on the calibration data itself**.
  - 413/560 (73.8%, AWQ) and 264/560 (47.1%, GPTQ) loss-reducing edits raised held-out WT2 PPL.
  - Further loss minimization raised C4 PPL 0.69%.
  - Llama-3.2-3B, 4-bit: more calibration (16 → 256 seqs) lowered loss while PPL "rose by orders of magnitude".
- **Models.** Llama-3.2-1B/3B, Llama-3.1-8B/70B, Qwen3-30B-A3B.
- **Effect on our paper.** It pre-empts part of C1's qualitative message (reconstruction-minimizing code re-selection on a frozen grid overfits calibration) and C5's "surrogates failed 7 times". Our differentiators: GGUF K-quant block hierarchy, BF16-KLD paired per-chunk, three corpora, scale to 27B. §8 "Re-selecting codes on a fixed grid" must add it next to ReQuant and SchurQuant. Pass to the C1 slice owner.

### T5. "Ranking Is Enough: A Controlled Study of Per-Tensor Mixed Precision for llama.cpp" (S. Rasal). Preprints.org, DOI 10.20944/preprints202610.1935.v1, 2026-10-20. Not cited. Claim C2 control, §7.1 "Baselines not compared". **Should-cite (supportive; strengthens our control argument).**

- **Method.** Per-linear-layer KLD measured across nine llama.cpp formats, then a multiple-choice knapsack under a bit budget, exported as a standard GGUF.
- **Setup.** Qwen3 0.6B/1.7B/8B and Llama-3.2-1B; three calibration seeds; held-out text; pre-registered rules.
- **Results.**
  - 15-40% lower KLD than the stock mixes at equal size at four of five rates.
  - −26% KLD at 3.5 bpw at equal size.
  - A diagonal-Fisher per-entry score matches measured KLD from 1B up; per-layer Hessian-trace scores do worse.
- **Effect on our paper.** Our mixed-type controls are hand-built by a promotion rule. A systematic allocator in the same ecosystem beats stock mixes by a further 15-40%, so the gap between "corrected configuration" and "best same-byte stock-type file" is plausibly **larger** than we report. Cite in §7.1 Controls and §8 Placement and allocation.
- **Caveat.** Single-author preprint, 2 days old, not peer reviewed. Verified via the OpenAlex record; preprints.org returned 403 to a direct fetch.

### T6. AutoQRA (arXiv:2602.22268, 2026-02-25) and BALANCER (IJCAI-26, DOI 10.24963/ijcai.2026/475). Not cited. Claim C2 (joint bits and rank under one budget). **Should-cite (minor threat to novelty framing).**

- **AutoQRA** searches per-layer bit width × LoRA rank under a fixed memory budget. It is a fine-tuning setting, with no PTQ correction and no held-out KLD.
- **BALANCER** does a training-free greedy global allocation of bits **and** ranks.
  - LLaMA-3.1-8B (WT2 PPL, FP16 6.63): Q-Mix (mixed bits, no rank) at 3.6 bits gives 6.78±0.43; Q4+LR at 4.0 bits gives 6.72±0.54. So mixed precision **without** low-rank at 0.4 fewer bits is within noise of uniform-4-bit + low-rank.
  - It never compares the two at equal bytes, and the numbers are odd (RTN Q4 9.92; CALDERA 6.68 at 4.0 bits).
  - Its calibration (64 WT2 + 1,000 C4 sequences) overlaps its evaluation corpora.
- Both papers show the "rank vs bits under one budget" idea is in the literature as an **optimization** framing. Neither reports the audit result that bits win. **Affects** §8 sentence "FLRQ and the integer program of LQ-LoRA allocate rank and bits in the same spirit": add AutoQRA and BALANCER.

### T7. Non-archival practitioner notes on Zenodo (2026). **Minor (support, low evidentiary weight).**

- **"Distillation Recovers the Post-Training Ternary Reasoning Collapse (but Does Not Beat a Third Trit-Plane)"**, de Souza Andrade, DOI 10.5281/zenodo.21248307, 2026-07-07. LoRA-KD initialized from the quantization-error SVD, on a K=2 trit-plane base. "plain K=3-PTQ beats K=2+distill on all six models ... spending the bits on a third plane dominates spending them on a trained correction" (p≈0.016). Not byte-matched (rank-16 ≈ 5%, rank-64 ≈ 19% of weight memory, against an extra plane).
- **"Where to Spend the Bits ... Correcting Ternary LLMs"**, same author, DOI 10.5281/zenodo.21247472. On BitNet-b1.58-2B-4T with rank-8 FP16 corrections: "the perplexity recovery is largely domain-adaptation to the correction's training corpus"; "even the oracle ceiling does not lift MMLU or GSM8K"; Hessian-trace allocation is "worse than random at 1.58 bits". Supports C2 (off-distribution) and C5.
- **"Eve's Embrace of Extreme Quantization v1.0"**, DOI 10.5281/zenodo.23323723, 2026-10-12. On OPT-125M, one block: a rank-51 repaired 1.67-bpw fork reaches PPL 118.5; a local 2-bit control (2.27 bpw, more bytes) reaches PPL 105.96. Not same-byte. Ignore unless needed.

### T8. Language-Conditional Dequantization (arXiv:2608.11786, 2026-08-12, N. Thomas). Not cited. Claim C2 (distribution conditioning, §4.8). **Should-cite.**

- Per-language rank-2 LoRA corrections trained on an INT3-GPTQ base.
- Closes 70-83% of the PPL gap for non-Latin-script languages but only 17-28% of the GlobalMMLU accuracy gap.
- Beats an equal-capacity language-agnostic correction by 3-9 points on distant languages.
- Corroborates that a correction's benefit is tied to the distribution it is fit on (our §4.3, §4.8). It is trained, not closed-form. No byte-matched higher-bit control.

### T9. "English K_Quantization of LLMs Does Not Disproportionately Diminish Multilingual Performance" (arXiv:2503.03592, Borgersen & Goodwin, v4 2026-01-22). Not cited. Claim C2/C1 (calibration-distribution sensitivity at scale). **Should-cite as contrast.**

- llama.cpp imatrix (diagonal) calibration in English, Norwegian or Malayalam for Llama-3.3-70B, evaluated on MixEval EN/NO. The authors conclude that current quantization practice does not disproportionately harm multilingual performance (the abstract reports no effect sizes).
- **How it bears on us.** A reviewer could cite it against "calibration distribution matters". Our answer is that the effect we measure is in the **full-covariance whitened correction** (and the GPTQ re-rounder), which is far more calibration-specific than the diagonal imatrix. Worth one sentence in §7.1 Distribution.

### Not threats (checked)

- **SPEAR** (2606.11244): token-gated error compensators recovering 56-75% of the W4 PPL gap at <1% memory. Rank structure and same-memory comparison are not stated in the abstract.
- **"From Signal Degradation to Computation Collapse"** (2604.19884): training-free repair mitigates signal degradation but not 2-bit computation collapse. Consistent with our IQ1_S result (large recovery, still uncompetitive).
- **"Generalization behavior of OPTQ"** (2609.31560): theory bounding test error by calibration error. Relevant to C1, not C2.

- **SERQ** (2603.08185, ICLR 2026): W4A4/W4A8; compares against other error-reconstruction and rotation methods; no byte control.
- **QuaSAR** (2608.14149): ViT, solver stability.
- **ODLRI** (2506.02077, Findings ACL 2025): Q+LR initialization; no byte control.
- **HGF** (2602.05269): 1.58-bit trained, TinyStories.
- **Recover-LoRA 2-bit** (2606.04238): synthetic-data KD.
- **LoPRo** (2601.19675), **UniQL** (2512.03383), **LittleBit/LittleBit-2** (2506.13771, 2603.00042), **D-QRELO** (2604.16940).
- **"A Comprehensive Evaluation on Quantization Techniques for LLMs"** (2507.17417, v4 2026-10-21): abstract says "combining low-rank compensation with GPTQ occasionally outperforms using GPTQ alone". Mild support. The numeric section was not reachable in the HTML render (marked partially verified).
- **QER** (CloudCom 2025, DOI 10.1109/cloudcom67567.2025.11331377): trained low-bit-aligned LoRA.
- **PCST** (Zenodo 10.5281/zenodo.22304196): 60+ methods on LLaMA-7B, including "selective low-rank residuals". The final 2.05 GiB artifact "trails standard Q3_K_M" and "local weight block reconstruction error often fails to predict end-to-end" behaviour. Minor support for C2 and C5.

## 3. SUPPORT (citable corroboration)

| Claim | Paper | What it shows |
|---|---|---|
| C2 gain smaller off-distribution | 2610.08164 Align-Then-Correct | Held-out/in-sample singular-value shrinkage ratio 0.64-0.85. WT2 falls below FP16 on Qwen3-4B (calibration fit) while C4 keeps a gap. |
| C2 off-distribution | Zenodo 21247472 | "perplexity recovery is largely domain-adaptation to the correction's training corpus" |
| C2 off-distribution | 2608.11786 LCD | A correction fit to a language helps that language; a language-agnostic one of equal capacity does 3-9 pts worse |
| C2 recovery grows as base thins | EoRA 2410.21271 (cited) | 3-bit gains 3-7× the 4-bit gains (LLaMA3-8B); "more effective when compression error is larger" |
| C2 recovery grows as base thins | RILQ 2412.01129 (cited), Align-Then-Correct | Gains largest at 2-bit |
| C2 benefit shrinks with scale | EoRA | 3-bit ARC-C +4.18 (7B) vs +2.22 (13B) |
| C2 benefit shrinks with scale | CALDERA (cited) | Fixed-rank benefit shrinks with size, per its own tables |
| C2 factors do not replace base bits | EoRA | 3-bit+EoRA 4.78 GB < uncompensated 4-bit 5.35 GB on ARC-C/GSM8K |
| C2 factors do not replace base bits | ik #15 | L²QER at 4.30 bpw loses to IQ4_XS at 4.25 bpw |
| C2 mixed-type allocation beats ladder | "Ranking Is Enough" (Preprints 202610.1935) | 15-40% KLD reduction vs stock llama.cpp mixes at equal size |
| C2 mixed-type allocation beats ladder | BALANCER | 3.6-bit mixed-precision without rank ≈ 4.0-bit + LR |
| C2 residual / weights not low-rank | Yu & Wu, AAAI 2023 "Compressing Transformers: Features Are Low-Rank, but Weights Are Not!" (DOI 10.1609/aaai.v37i9.26304; arXiv version not found) | Weights near full rank |
| C2 residual / weights not low-rank | LQER (cited) | Slow singular-value decay of E_q |
| C2 residual / weights not low-rank | Align-Then-Correct | Symmetric target "inherently high-rank" |
| C2 residual / weights not low-rank | ik #15 / try_svd | Needs near-full SVD |
| C2 placements indistinguishable | ik #15 | Carve-before-quantize "hopeless until ... full SVD" except early K/Q; RMSE-level and informal |
| C5 surrogates mislead | DRQ 2610.11226 | Lower calibration reconstruction loss raises held-out PPL |
| C5 surrogates mislead | PCST (Zenodo) | Block reconstruction error fails to predict end-to-end |
| C5 surrogates mislead | Zenodo 21247472 | Hessian-trace allocation worse than random |
| C5 surrogates mislead | "Ranking Is Enough" | Per-layer Hessian-trace scores underperform measured-KLD ranking |

## 4. MUST-CITE MISSING (not in cited.txt)

1. **arXiv:2610.08164** Align, Then Correct (2026-10-06). The current state of the art in the exact lineage, with held-out C4 and a shrinkage diagnostic. §8 lineage paragraph, plus one sentence in §4.3 "Reading".
2. **arXiv:2610.11226** DRQ (2026-10-08). Frozen-grid code refinement that overfits calibration. §8 "Re-selecting codes", §3.1 (C1 owner).
3. **arXiv:2411.07762** ASER (AAAI 2025): whitening-SVD error reconstruction. A canonical lineage member that GlowQ lists beside LQER and QERA; currently absent from §8.
4. **ik_llama.cpp #15 / llama.cpp #8831** (already cited): **re-describe** as in T1, not as a bare prediction.
5. **"Ranking Is Enough"** (Preprints.org 202610.1935, 2026-10-20). Systematic per-tensor mixed precision in llama.cpp. §7.1 Controls / Baselines not compared.
6. **arXiv:2503.03592** (imatrix calibration language at 70B). §7.1 Distribution, as a contrast case.

Recommended, should-cite:
- arXiv:2602.22268 AutoQRA.
- BALANCER (IJCAI-26).
- arXiv:2608.11786 LCD.
- arXiv:2506.02077 ODLRI (Findings ACL 2025).
- arXiv:2603.08185 SERQ (ICLR 2026), if the related work wants W4A4 completeness.
- Yu & Wu AAAI 2023, for "weights are not low-rank".

## 5. NEXT-STEP IDEAS (ranked by expected payoff on one RTX 5090 + llama.cpp)

1. **Asymmetric / Fisher-weighted correction against the same-byte control.**
   - Re-extract the 27B and 8B corrections with Align-Then-Correct's Stage-1 target, an output-side Fisher weight, built from the Gram captures we already have plus one gradient-statistics pass.
   - Score against the existing control on all three corpora.
   - This is the one obvious reviewer objection ("you tested the symmetric whitened form; the 2026 SOTA is asymmetric") and it reuses all existing infrastructure.
   - Cost: hours at 8B; a day at 27B (statistics cache is the bottleneck, ~30-50 GB/model per the paper).
2. **Optimized type allocation as the control.**
   - Replace the hand-built promotion-rule control with a per-tensor KLD-knapsack allocator (as in "Ranking Is Enough") at the corrected file's bytes, at 0.8B and 8B.
   - Expected result: the control's lead grows, which converts "one designed control" (a stated limitation) into a systematic one.
   - Cheap: per-tensor KLD sweeps at 0.8B/8B take minutes to hours.
3. **Joint allocation: is any low-rank byte ever worth it?**
   - Add "rank-r factor on tensor t" as an item in the same knapsack, with its measured ΔKLD and byte cost.
   - If the knapsack never picks factors over type promotions at any budget, that is a clean, general version of C2.
   - If it picks them only at IQ1/IQ2-class bases, that quantifies where correction pays, consistent with our recovery curve.
4. **Calibration-mixture correction.**
   - Re-whiten with Grams pooled over wikitext + FineWeb-Edu + code (or DRQ-style worst-case/robust Grams) and rescore held-out recovery.
   - This tests whether the off-distribution halving is a property of the correction or of a single-corpus Gram. Cheap: Gram capture plus re-extraction.
5. **Bytes-matched reproduction of ik #15 at 4-bit on a modern model.**
   - L²QER-style correction on IQ3_XS/Q3_K_M vs IQ4_XS at equal bytes on Llama-3.1-8B.
   - This extends the audit to the 4-bit regime that the literature (LQER/QERA/EoRA) actually reports. Cheap.

## 6. Verification log

- **Fetched and read (abstract or full text):**
  - 2610.08164 (html, full), 2410.21271v5 (html, full), 2610.11226 (html, first ~100k chars).
  - Abstracts: 2603.08185, 2604.18117, 2607.14630, 2602.22268, 2507.17417 (abstract and partial html), 2608.24070, 2608.14149, 2608.11786, 2506.02077, 2411.07762, 2602.05269, 2606.04238, 2512.03383, 2303.08302, 2606.11244, 2604.19884, 2609.31560, 2503.03592.
  - BALANCER PDF from ijcai.org (pdftotext).
  - ik_llama.cpp discussion #15 and llama.cpp discussion #8831 (GitHub GraphQL, full threads).
  - OpenAlex records for: Ranking Is Enough (W7223139655), Small-State Coset Trellis (W7222942398), PCST (W7208787767), Eve's (W7221543339), QER (W7124969355), ternary Zenodo pair (W7167627047, W7167616856, plus zenodo.org pages for both).
- **Partially verified:**
  - "Ranking Is Enough": OpenAlex abstract only; preprints.org returned 403.
  - 2507.17417: numeric section not rendered.
  - Yu & Wu AAAI 2023: via web search snippets and the AAAI DOI page listing; full text not fetched.
- **Not verified:** Research Square "Sensitivity-aligned Mixed-precision Quantization and Low-rank Adaptation for LLMs" (DOI 10.21203/rs.3.rs-11015135/v1, 2026-10-16). OpenAlex has no abstract and the full text was not fetched. Do not cite without reading.
