# R4 — Evaluation metrics for compressed LLMs (claim C3)

Sweep date 2026-10-22. Slice: KLD / PPL / flips / top-1 / task accuracy, fidelity vs capability, "Accuracy is not all you need" (2407.09141) and its followers, quantized-beats-reference cases, QAT/distillation evaluation.

## 1. Coverage

- **Sources.**
  - arXiv HTML search, 30 queries, up to 200 results each, newest first. This gave 828 records.
  - arXiv export API, 23 queries. It was heavily rate-limited (HTTP 429, shared IP) and returned 264 records.
  - Semantic Scholar keyword search: 1 of 23 queries completed (111 records) before the rate limit stopped it.
  - Semantic Scholar citation graphs:
    - 38 citers of 2407.09141 (S2's coverage is incomplete);
    - 3 citers of 2606.19558;
    - 7 citers of GSQ 2604.18556;
    - 3 citers of RCO 2605.00649;
    - 1 citer of 2609.07664.
  - Web searches (about 15) covering practitioner sources: llama.cpp #4110, Unsloth docs, a Hugging Face Qwen3.8-27B discussion, and the GSQ-RCO model card.
- **Screened.**
  - About 1,100 unique records were harvested.
  - About 665 of them are about LLMs and compression; I screened those at title or abstract level.
  - I read about 70 abstracts in full.
  - I read the full text (PDF or HTML, grep plus targeted reads) of 12 papers: 2610.00694, 2606.19558, 2609.00049 (REAL-Q), 2604.18556 (GSQ), 2605.00649 (RCO), 2407.09141, 2605.02404, 2609.39223, 2610.28537, 2606.05682, 2609.26708, and the GSQ-RCO HF card.
- **Date range.** 2023 to 2026-10-21. The bulk is 2026.
- **Verification.** Every arXiv ID below was checked against arxiv.org/abs (citation_title and citation_date) or came directly from arXiv's own search output. Non-arXiv items are marked.

## 2. THREATS

### T1. GSQ and RCO are published as *fidelity-to-reference* optimizers, not "trained against data". Severity: **must-fix wording, must-cite-and-discuss**

- **Sources.** arXiv:2604.18556 (GSQ, ISTA, v2 2026-05-15) and arXiv:2605.00649 (RCO, Helcig & Alistarh, v3 2026-09-27). I read both full texts.
- **GSQ §4.1 "Within-block staging".** The q/k projections are trained "with linear reconstruction losses". v/o use "the self-attention output reconstruction loss". The MLP uses "the full block reconstruction loss". Calibration is "FineWeb-Edu … 4096 sequences of length 4096" in the Llama experiments. The optional scale-only end-to-end stage uses "the distillation objective".
- **RCO App. B, Eq. 6.** "The loss L(z∗) … is the KL divergence between the full-precision model and the model under assignment z∗, evaluated on a calibration set D". The calibration set in the paper is "256 FineWeb-Edu sequences". The paper also says: "RCO minimizes KL divergence on the calibration dataset, so the choice of calibration data determines which capabilities … [are] preserve[d]".
- **GSQ-RCO HF card (verified by fetch).** It reports no KLD and no top-1. It describes RCO as "gradient-based optimization directly on the task loss" without naming that loss. It names the imatrix ("1000 chunks of 4096 tokens") but not its dataset.
- **Consequence.** As published, both stages minimize distance to the BF16 reference on their calibration text: reconstruction error for GSQ, KL for RCO. Our lines 379 and 465 say "a file trained against data, as these are, can give up some of that fidelity and still assign the text higher likelihood". That mechanism is not supported by the method papers, and §5.1's own description ("trained against block-staged reconstruction") contradicts it.
- **A more parsimonious reading, consistent with our own §5.3 FineWeb result.**
  - If the released files used FineWeb-Edu-like calibration as in the papers, they are fidelity-optimized on a corpus other than wikitext.
  - That would explain why the GSQ-RCO KLD deficit is roughly 10x smaller on FineWeb-Edu than on wikitext.
  - This is the same calibration-coverage effect as our C1.
  - The wikitext NLL gain is then not "quality bought with fidelity". See T2.
- **Sections affected.** Abstract item (3); §1 contribution (3); §4.7 line 379; §5.4 line 465; §5.6/§7 "The instrument".
- **Recommended rewrite.** "files optimized for fidelity under a different calibration distribution", not "trained against data". Keep the empirical observation: KLD and NLL rank the pair differently on wikitext at 2,048 tokens.

### T2. arXiv:2609.00049 — REAL-Q: E2E LLM Quantization via Dynamic Gradient Descent (Zhang et al., PKU; 2026-08-30, v2 2026-09-10). Severity: **must-cite-and-discuss**

- I read the full text. Section 6.1, the Table 1 caption and App. D.1 state:
  - "All Qwen3 checkpoints used in our evaluation are post-trained models, for which the bf16 reference is generally not at the extremum of perplexity or zero-shot task accuracy (post-training optimizes neither metric)."
  - "any quantization-induced fluctuation in PPL or downstream accuracy—in either direction—does not by itself indicate quantization quality; only the KL divergence … directly measures fidelity".
  - "we occasionally observe quantized perplexities slightly below the bf16 baseline. This is expected … any perturbation—including quantization—can move PPL in either direction."
- **What it does to C3.**
  - It agrees that KLD measures fidelity.
  - It undercuts the inference that higher wikitext likelihood means better *quality*. Qwen3.8-27B is a post-trained (reasoning/chat) model, so a perturbation that lowers wikitext NLL may be drift back toward pretraining-style prose, not a capability gain.
  - Our paper currently frames C3 as "KLD measures fidelity and *not quality*", with NLL standing in for quality. A reviewer who knows REAL-Q will say that neither measure is quality, and that only task results (F) can carry the "quality" half.
- **Sections affected.** §5.4 last paragraph; §5.6; abstract (3); §7 "The instrument".
- **Fix.**
  - Say that KLD and NLL *disagree in ranking*.
  - Say that for a post-trained reference neither is a capability measure (cite REAL-Q).
  - Let the F replication decide the capability question.
  - Optional check (cheap): report the BF16 reference's own wikitext NLL. If GSQ-RCO's NLL is *below BF16's*, the "quality" reading is untenable and the REAL-Q reading is directly confirmed.

### T3. arXiv:2606.19558 — Displacement Is Not Direction: Evaluating Fidelity Metrics for Quantized LLM Deployment (Nikolić, Zadeh, Torres Sanchez, Moshovos; ByteShape/U. Toronto; 2026-06-17). Already cited as [20], but **mis-cited**. Severity: **must-cite-and-discuss (pre-empts the conceptual half of C3)**

- I read the full text. Its thesis is "KLD measures displacement, not direction."
  - Across a full cohort of public GGUF quants, KLD correlates with benchmark composite: Spearman ρ = −0.72 (Qwen3.6-35B-A3B, 28 quants) and −0.86 (Devstral-Small-2-24B, 41 quants).
  - In the near-baseline "silent zone" the correlation vanishes: ρ = +0.00 and −0.24 (p = 0.36).
  - It has concrete higher-KLD-but-better rows, both GGUF:
    - ByteShape IQ4_XS-4.15bpw: KLD 0.0504, composite 0.731, against Unsloth UD-Q4_K_XL at KLD 0.0135, composite 0.728;
    - Devstral: 0.0473 / 0.698 against 0.0222 / 0.696.
  - Recommendation: "fidelity metrics should filter damaged candidates, not rank near-baseline ones."
- **Limits.** None of its pairs is resolved. It has no likelihood reversal (its PPL and KLD move together), no matched-byte paired statistics, and no trained-vs-untrained framing.
- **What it does to C3.** The *idea* that KLD-to-reference measures fidelity and not quality, and can mis-rank GGUF quants against task scores, is published. Our [20] citation uses it only for "perplexity secondary" (line 103 and §8 "Measurement"). That is a misattribution, and a reviewer from that group will notice.
- **Novelty phrasing that survives.** C3 contributes:
  - (a) a *resolved, paired, matched-byte* instance (t up to 11.7 on KLD);
  - (b) where the reversal is against the text's *own likelihood* on the same tokens, not only a noisy benchmark composite;
  - (c) where the reversal depends on corpus (it largely vanishes on FineWeb-Edu), which points to calibration coverage as the mechanism.
- **Sections affected.** Line 103, §8 "Measurement", §5.4, abstract (3).

### T4. arXiv:2610.00694 — How Divergence Becomes Decision Flips in Compressed Language Models (B. Almeida Felicio; 2026-09-30). Severity: **should-cite (weakens our justification for KLD)**

- I read the full text. Across 802 compressed or perturbed copies of 19 models, flip rate tracks *total variation* at a median ratio of 1.05 (95% of configurations in 0.89–1.29).
  - KL converts to flips "only through its square root and a factor that varies fourfold across models and corpora".
  - Between two compressors whose flip rates differ by at least 10%, KL ranks them wrongly in 8.1% of pairs within a model and 10.8% of pairs across models; TV does so in 0.7% and 1.3%.
  - It states: "A flip rate or a divergence describes fidelity to the dense model, not benchmark quality." Matched-divergence example: on Gemma-3-4B, magnitude pruning costs 33.3 GSM8K points while Wanda *gains* 4.8 at the same 0.10 nats.
  - Its one trained case, Gemma-3 Q4_0 QAT, has *lower* KL than PTQ Q4_0 (0.29–0.57x), with flips at 0.55–0.77x.
- **What it does to our paper.** Line 103 says "KLD is used because it tracks answer flips ([19])". That should now be qualified; 2407.09141 reported Spearman 0.981 on MMLU, and this paper shows the KL-to-flips conversion is corpus-dependent. Our KLD rows are paired within model and corpus, so the within-pair ranking is mostly safe. Our cross-corpus comparisons are exposed, though: "0.25–0.96 of the wikitext gain" and the wikitext-vs-FineWeb deficit ratios.
- **Cheap fix.** Report TV and flip rate from the same logits (see §5).

### T5. arXiv:2606.27785 — Output-Space Allocation Costs for Calibration-Guided LLM Compression (2026-06-26). Severity: **minor**

- It is another clean rank disagreement between metrics after compression. ROCKET-ActCost gives +0.8 pp zero-shot accuracy (53.1 vs 52.3) while WikiText PPL rises 16% (61.46 vs 52.98) on Qwen3-8B at 50% compression.
- It shows that metric disagreement after calibrated compression has been reported before, though not for KLD vs NLL.

### Not a threat, though it looks like one

- **arXiv:2609.39223 — QATFactory (2026-09-30).**
  - It trains llama.cpp Q4_K checkpoints by QAD with a forward-KL objective.
  - The trained GGUFs have *lower* KL and higher accuracy than llama.cpp's PTQ: Qwen3-8B avg KL 0.0628 → 0.0436, accuracy 59.37 → 61.21.
  - So "trained GGUF further from reference" is not a general law. C3 must stay scoped to GSQ-RCO on wikitext.
  - Cite it as the counter-case.
- **Hugging Face discussion Qwen/Qwen3.8-27B #192 (non-arXiv, community; fetched).**
  - Frozenlock AutoRound INT4 has wiki PPL 7.946, *below* BF16's 7.993, while its text KLD is 0.0459, against 0.0117 for FP8.
  - The author reads this as "Quantization can act as a mild regularizer".
  - QUASAR-QAT NVFP4 is further by KLD than Unsloth NVFP4 (0.0682 vs 0.0475) *and* worse on PPL and tasks, so it is not a C3 instance.
  - It is a practitioner precedent for PPL below reference with higher KLD on this very model family. Mention it at most in a footnote.
- **Reddit r/LocalLLaMA thread 1jqnnfp (UNVERIFIED; snippet only, fetch blocked).** It reportedly says Google's Gemma-3 QAT Q4_0 scored better wiki PPL than BF16 while having worse KLD than Q6_K. Not citable as is.

## 3. SUPPORT

- **arXiv:2407.09141 (cited).**
  - The paper explicitly scopes distance metrics to mimicry: compression aims to mimic the baseline "and not necessarily to create a more capable model".
  - It says flips are "only a warning" that "may or may not materialize as visible degradation".
  - It also shows small positive accuracy deltas for some compressed models (e.g., GPTQ W8A16 Llama2-7b-chat +0.08 MMLU).
  - Quote the scoping sentence in §5.4: it supports "KLD is right for a method that only approximates the reference".
- **arXiv:2609.07664 — Accuracy is Not Enough (Qamar et al., 2026-09-07).** Uses JSD/TV against BF16 from BF16 down to Q2_K GGUF. Accuracy is "unstable, non-monotonic" under progressive quantization. It explicitly says divergence does not establish correctness or quality. Supports the view that fidelity and quality are separate axes.
- **arXiv:2605.02404 — Statistically-Lossless Quantization (Helcig, Kurtić, Alistarh; 2026-05-04; the GSQ/RCO group).**
  - It separates "task-lossless" from "distribution-lossless" and says "a model can be TL at a lower bitwidth without meeting the stricter DL criterion".
  - It also reports accuracy degrading "approximately linearly with the KL divergence" near lossless.
  - Useful: the GSQ-RCO authors' own framework already has a model that is task-good but distribution-far, which is exactly our pair.
- **arXiv:2606.03002 — Perplexity Can Miss SAE Feature Damage Under Quantization (2026-06-04).** "INT7 improves perplexity while degrading 18.7% of active SAE features". This is an independent case of quantization lowering PPL below the reference, and it supports the REAL-Q reading.
- **arXiv:2610.29302 — Compressing Onboard LMs … Behavioral-Fidelity Requirement (2026-10-21).** It formalizes the two modes our §5.4 argues for: a "fidelity mode" that accepts a substitution only when forward KL to BF16 decreases, and a separate "target-adapted mode" that adds answer likelihood.
- **arXiv:2607.08734 — The Illusion of Equivalency (2026-07-09).** Correctness Agreement shows behavioral shift while accuracy and PPL are preserved.
- **arXiv:2608.15046 — Certifying Compressed Language Models (A. Singh, 2026-08-15).**
  - "churn runs roughly five times the net accuracy delta".
  - Changing the calibration draw reversed GPTQ vs AWQ ordering in 5 of 8 confirmatory cells.
  - Strong support for C5 (paired stats, held-out calibration) and for caution on the single-run +12.
- **arXiv:2602.10144 — When LLMs get significantly worse (2026-02-09).** McNemar per-sample testing for degradation. Supports our exact-McNemar use in §5.5.
- **arXiv:2610.24346 — Task Value of Precision Upgrades in Closed-Loop Agents (2026-10-17).** In agentic runs, swapping in an action from a *second 4-bit checkpoint* changed about as many outcomes as swapping in the BF16 action, and rescues and damages nearly cancelled. Supports the claim that a single agentic run is one draw (§5.5) and motivates F's three runs.
- **arXiv:2610.05533 — What Does a Harness Repair? (2026-10-04).** Preregistered; harness defects reproduce under replication. Supports C5 and the pre-registration practice.
- **arXiv:2610.04403 — Saying, Not Knowing: GGUF-quantized SLMs (2026-10-03).**
  - Damage is "not calibrated by WikiText-2 perplexity".
  - Parameter count dominates (ρ = 0.72).
  - Supports held-out corpora (C5) and the size dependence in C1.
- **Unsloth Dynamic 2.0 docs (non-arXiv, fetched).**
  - They say KLD "should be one of the gold standards" and "Using perplexity is incorrect".
  - They also warn that Wikipedia-calibrated imatrix quants "naturally perform better on KL Divergence benchmarks that also use Wikipedia data".
  - This matters because the vendor whose file wins our KLD comparison advertises KLD as gold standard, while the GSQ-RCO card ranks by PPL and tasks. C3 sits exactly between the two published evaluation conventions, which is a good motivation sentence.
- **arXiv:2604.13440 — A KL Lens on Quantization (2026-04-14).** Forward vs reverse KL rank sensitivity differently (reverse τ ≈ −0.14 with PPL ranking). State which direction we use.
- **arXiv:2505.15353 — Establishing a Scale for KL Divergence in LMs (2025-05-21).** A common KL scale across quantization, fine-tuning and seeds. Useful for calibrating how large our ΔKLD of 0.015–0.07 is.

## 4. MUST-CITE MISSING (not in cited.txt)

1. **2609.00049 REAL-Q.** The post-trained reference is not at the PPL optimum, and PPL can move either way. This is the alternative explanation for C3.
2. **2610.00694 Divergence → flips.** TV, not KL, tracks flips; it qualifies the [19] justification on line 103.
3. **2605.02404 Statistically-Lossless Quantization.** Task-lossless vs distribution-lossless, from the GSQ/RCO authors.
4. **2609.39223 QATFactory.** The counter-case: a trained Q4_K GGUF with *lower* KL. It bounds the scope of C3.
5. **2609.07664 Accuracy is Not Enough.** The direct 2026 follow-up to [19], using GGUF BF16→Q2_K.
6. **2608.15046 Certifying Compressed LMs.** Churn of about 5x net delta; calibration draw reverses method order. For C5 and §5.5.

Also fix the use of the already-cited **2606.19558 [20]**: cite it for "displacement, not direction" in §5.4 and §8, not for "perplexity secondary".

Optional: 2606.27785, 2606.03002, 2610.29302, 2607.08734, 2610.24346, 2602.10144, 2604.13440.

## 5. How to phrase C3's novelty

C3 is **not pre-empted as an empirical result**. I found no paper or card showing a trained or refined quantized checkpoint resolved further from the reference by paired KLD at matched bytes while resolved better on likelihood of the same tokens. The **concept** is pre-empted, though:

- "KLD = fidelity, not quality": 2606.19558, 2610.00694, 2407.09141's own scoping sentence;
- "PPL below reference is expected for post-trained models": REAL-Q.

Suggested wording for the abstract and §5.4:

> "KL divergence to a reference is known to measure fidelity rather than quality ([19], [20], 2610.00694), and for post-trained references perplexity can move in either direction under perturbation (2609.00049). We give a resolved, matched-byte, paired instance in which the two disagree in *ranking*: released GSQ-RCO files of Qwen3.8-27B are further from BF16 than the untrained vendor file (t = +8.6 to +11.7) yet assign the same wikitext tokens higher likelihood at 2,048 tokens (t = −2.5 to −4.2), and the KLD gap shrinks about tenfold on FineWeb-Edu. That pattern fits files fitted to the reference under a different calibration distribution (both published objectives are fidelity objectives). Neither measure settles capability, which the replication in §5.5 addresses."

Drop "trained against data" (T1) and "KLD measures fidelity and not quality" as a novel claim. Keep "do not rank a refined artifact against an untrained one by KLD alone", with the citations above.

## 6. NEXT-STEP IDEAS (ranked by payoff per GPU-hour on one RTX 5090 with llama.cpp)

1. **Report the BF16 reference's own NLL (zero new compute; the logits already exist).**
   - If GSQ-RCO NLL is below BF16 NLL on wikitext at 2,048 tokens, the "higher quality" reading fails and REAL-Q's reading is confirmed.
   - This could be settled before submission.
2. **Total variation and flip rate from the stored logits (CPU only).**
   - Add TV and argmax-flip columns to Tables 5.x. This answers 2610.00694 and reviewer pushback on KL→flips.
3. **Score on BF16-self-generated text (about 1 GPU-hour).**
   - Generate about 40 chunks of chat-format or reasoning-trace text from BF16 Qwen3.8-27B, then score KLD and NLL for UD vs GSQ-RCO on it.
   - On on-policy text, NLL and KLD should agree. A disagreement only on wikitext is distribution-specific (the calibration-coverage story).
   - It also matches what agentic tasks exercise.
4. **Calibration-corpus probe (a few GPU-hours).** Score UD vs GSQ-RCO on C4 and code at 2,048 tokens. If GSQ's deficit tracks distance from FineWeb-Edu, that confirms that calibration coverage, not "training against data", drives C3. This links C3 to C1 as one finding: refinement helps where calibration covers and hurts elsewhere.
5. **Decision-margin analysis (CPU only, on the stored logits).** Fit m′ ≈ c·m + b for each file, as in 2608.06564, to see whether GSQ-RCO shrinks margins less or more than UD. That helps explain any task difference in F.
6. **Report F with the churn and equivalence statistics of 2608.15046:** churn alongside net change, a declared margin, and per-unit outputs released.
7. **Later work: a QAD Q4_K/IQ3 on Qwen3.5-0.8B (QATFactory-style forward-KL) against an NLL-trained variant at equal bytes.** This would directly test the trained-on-data vs trained-on-reference hypothesis that T1 shows the paper currently has no evidence for. At 0.8B it fits a 5090.
