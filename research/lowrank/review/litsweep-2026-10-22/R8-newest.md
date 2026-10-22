# R8: the newest material (arXiv, 2026-06-01 to 2026-10-22)

Slice: a sweep in date order of LLM weight quantization and compression submitted since June 2026, plus quantization of hybrid and linear-attention models, evaluation and reproducibility critiques, and 2026 surveys of 2-3-bit quantization.

## 1. Coverage

- **Harvest.** I ran 22 arXiv API queries, each sorted by submittedDate in descending order and paginated with `start=` until the results went past 2026-06-01. The newest entry is dated 2026-10-21. Two survey queries went back to 2025-10 and 2026-01. The queries covered:
  - the broad query `abs:quantization AND (abs:LLM OR abs:"language model" OR abs:LLMs)`, which returned 588 entries in range;
  - `ti:quantization`, which returned 718;
  - "low-bit", "low-rank" plus compression, 2-bit / 3-bit / sub-4-bit, post-training quantization, weight quantization, and GGUF / llama.cpp;
  - Mamba / linear attention / DeltaNet / hybrid / state space / Qwen3.5 / Qwen3.6 / Gated DeltaNet together with quantization;
  - KL divergence with quantization, vector quantization, error compensation and correction, rounding / codebook / mixed precision / bit allocation, and QAT / ternary;
  - reproducibility / perplexity / evaluation protocol with quantization, and evaluation or benchmark terms with compression;
  - agentic / long-context / reasoning with degradation;
  - surveys, SoKs and empirical studies;
  - fused LoRA / adapter kernels.
- **Store and screening.** The store holds 2,892 unique entries; the script and data are at `~/Documents/openbeast-litsweep-raw-2026-10-22/r8/`. A keyword prefilter (a compression term plus an LLM or architecture term) left **1,451 titles, and I screened every one by hand**. I read **about 125 abstracts** in full.
- **Full-text reads.** I read the HTML full text of the 12 closest papers: 2610.11226, 2610.08164, 2609.33923, 2609.01587, 2609.38599, 2609.09867, 2608.11786, 2609.31155, 2610.19498, 2609.04098, 2607.28196 and 2610.20826.
- **Verification.** Every ID, title and date below comes from the arXiv API record I fetched. Quoted numbers come from the fetched abstract or full text. Nothing below is from memory. None of the IDs below appears in cited.txt.

## 2. Threats

### T1. 2610.11226: "When Lower Reconstruction Loss Hurts: Distributionally Robust Refinement for Low-Bit LLM Quantization" (2026-10-08). Claim C1. **Must cite and discuss. This is the single most important new paper for us.**

**What they show.** They refine the integer codes on a fixed grid (scales, zero-points and the other layers frozen), which is our re-rounder's setting in generic group-128 PTQ. The setup is Llama-3.2-1B with 3-bit AWQ, calibrated on WikiText-2:
- In "18 of 32 cases, the modified weights yield lower reconstruction loss but higher model perplexity (PPL) on the same calibration data".
- Of 560 single-layer code replacements that all lower calibration reconstruction loss, "413 (73.8%) [AWQ] and 264 (47.1%) [GPTQ] increase PPL" on held-out WT2.
- "Further minimizing reconstruction loss increases held-out C4 PPL by 0.69%."
- In the §4.5 ablation, reconstruction-only refinement (the same code search with the robust term off) on top of GPTQ W4 Llama-3.2-3B gets "PPL increasing by orders of magnitude" as calibration size grows. They state that more calibration data does not fix this (their Theorem B.2).
- Their Theorem 3.1 gives the mechanism. A channel near zero in the calibration data hides weight error that new data then exposes.

**Their fix.** DRQ minimizes the worst-case reconstruction loss over a constrained set of input-activation distributions, still refining codes only. It improves held-out C4 at every size they test:
- Llama-3.1-8B GPTQ W3: C4 15.39 → 14.91. Note that WT2 goes 8.60 → 8.61.
- 70B W2: C4 31.77 → 27.11.
- It also improves Qwen3-30B-A3B W3.

**Effect on us.** Our C1 finding (code re-choice on a frozen grid cuts the proxy and damages held-out text) is now published in generic PTQ, three weeks earlier. Our novelty narrows to GGUF K-quants, the 0.8B/8B/27B scale, KLD, and FineWeb plus code as held-out corpora.

Worse, DRQ supplies the mechanism we explicitly left untested. See §3.1, l.176: "One reading fits the held-out damage: the sweep moves error out of the directions the calibration activations occupy … We did not test it directly." DRQ also shows a robust variant of the same move does help held-out text at 8B. That weakens the generality of "re-rounding damages … at every size": it holds for the reconstruction-only objective, not for code re-choice as such.

**Affects.** The abstract's item (1), §3.1 (l.117 and l.176), §4.7 (surrogate rows), and §8 "Re-selecting codes on a fixed grid".

**Suggested sentence.** "Concurrently, DRQ (arXiv:2610.11226) reports the same reversal in generic group-wise PTQ (lower calibration reconstruction loss, higher held-out perplexity in 47-74% of single-layer code changes) and fixes it with a worst-case objective over activation distributions; we did not test that objective on K-quants."

### T2. 2610.08164: "Align, Then Correct: Training-Free Two-Stage Low-Rank Compensation for Extremely Quantized LLMs" (2026-10-06). Claim C2. **Must cite and discuss.**

**What they show.**
- Existing training-free compensators "calibrate symmetrically … which yields a compensation target that is inherently high-rank — so a fixed rank budget captures only a small fraction of it." They also minimize only the second-order term, so "a first-order descent direction larger than the applied compensation itself remains in every layer".
- In their words, EoRA, QERA and ProjQ "land within [x] perplexity of one another — consistent with the view that they are near-optimal solutions of the same objective, so that further recovery requires changing the objective rather than solving it better".
- Their two-stage method uses a Fisher-weighted asymmetric output alignment, then a rank-constrained natural-gradient step. At 2-bit QuIP# it takes WT2 from 12.43 to 10.26 on Qwen3-8B and from 21.11 to 13.22 on Qwen3-4B. On held-out C4 it recovers 51% and 84% of the FP16 gap, against 31% and 63% for the best baseline.
- At 3-4 bits on Qwen3-8B, their compensated model has C4 perplexity below FP16.

**Effect on us.** Our corrector is QERA Theorem-1 style, which is the symmetric, second-order class. A reviewer can argue that C2's "loses to stock types" verdict holds for the symmetric objective only. It does not invalidate our same-byte result: they compare against the uncorrected base at equal bit-width and run no byte-matched controls. It does mean the claim must be scoped.

The paper also corroborates our §4.4 indistinguishability result. Variants that solve the same objective land together.

**Affects.** §2.2, §4.3 and §4.4, §7.1 (Limitations), and §8 "Low-rank correction" (QERA/EoRA lineage).

### T3. Agentic single-run evidence is fragile, which weakens the "+12 tasks (88 vs 76)" in C3 and §5.5. **Must cite in §5.5 and the F write-up.**

- **2609.04748**, "Same Request, Different Answer: Quantization Amplifies Cache-Induced Divergence in LLM Serving" (2026-09-04). This one is on llama.cpp with GGUF weights. With prefix reuse on, cache-hit and recompute paths diverge in "37.4 percent of episodes at 16-bit and 82.5 percent at four-bit" (Qwen2.5-7B, 80-episode agentic tool-use workload). Without prefix reuse, runs reproduced every token. **Direct implication for F:** record and fix llama-server's prefix-cache and slot-reuse settings across arms. Otherwise "3 runs" mixes cache-path noise into the comparison, and that noise grows as bits drop.
- **2610.07781**, "Quantization Effects on Tool-Failure Recovery Vary Across Prompts and Evaluation Designs" (2026-10-06). The 8-bit vs 4-bit comparison "changes direction across prompts and evaluation targets". For Qwen the per-prompt difference ranges from −50.0 to +35.0 points. Scoring each variant on its own clean-passing tasks versus the shared task set reverses the sign.
- **2609.26693**, "Measuring the Serving Stack Instead of the Model: Hidden Confounds in Local Tool-Use Evaluation" (2026-09-22). Ollama, llama.cpp, vLLM and SGLang tool-call templates change measured fidelity, and rejection is misclassified as a model non-call.
- **2607.27275**, "Flat Score, Amplified Failures" (2026-07-29). On τ²-bench the 4-bit score change is null within ±7.5 points, yet process failures amplify up to 2.5× and are hidden by the error budget.
- **2609.32042**, "Quantization Thresholds Replicate, Failure Modes Do Not" (2026-09-25). This is GGUF Q8_0→Q2_K agentic tool use: the cliff between 3-bit and 2-bit replicates across three models, and the failure modes do not.

Severity: must cite and discuss for the capability paragraph. If F is a 3-run replication, state the cache and template settings and use a paired per-task test (McNemar, as 2609.32042 does).

### T4. 2606.11244: "SPEAR: A System for Post-Quantization Error-Adaptive Recovery Enabling Efficient Low-Bit LLM Serving" (2026-06-04). Claim C4, §8 "Serving". **Should cite.**

SPEAR fuses its gated error compensators into low-bit GEMMs ("epilogue-integrated peer-reduction kernel … to fuse the post-EC computation into low-bit GEMMs"). It "recovers 56-75% of the perplexity gap between W4 and FP16 while adding less than 1% model memory overhead and maintaining latency comparable to a widely used 4-bit serving deployment."

TwinQuant (2606.01556, already cited, but only for rank) also ships "a fused dual-component kernel".

Both soften our sentence "Few released correction methods ship a fused decode path". Reword it to "few ship one for llama.cpp / GGUF".

### T5. 2609.38599: "JARQ: Joint Alternating Refinement for Quantization" (2026-09-29). Claim C1, §3.4. **Should cite. It mostly supports us.**

**Support.** "The best grid depends on the codes". Joint scale-and-code refinement lowers perplexity in 90 of 96 comparisons and lowers C4 in 44 of 48. This supports our "refitting the grid beats freezing it, 11/12".

**Threat angle.** In their Appendix C.5, "Codes only raises perplexity in 5 of the 16 comparisons, including WT2 for both GPTQ settings at W3A16". On Llama-3-8B GPTQ W3A16, "no variant improves WT2 over the host". This corroborates our 8B null.

JARQ moves many codes at once (bounded Babai proposals). A reviewer may ask whether our single-coordinate GPTQ sweep is simply a weak optimizer. Their result on a GPTQ host says no; cite it that way.

## 3. Support

### C1: calibration coverage and off-distribution damage

- **2606.03328**, "Rethinking Calibration for High-Sparsity LLM Pruning: From Single Sources to Capability Coverage" (06-02). This is about pruning. Strategies 2.85 points apart on commonsense "diverge by 51.9 points in code retention", and no single calibration source preserves all capabilities. A balanced mixture wins.
- **2607.00908**, "Beyond Activation Alignment: The Alignment-Diversity Tradeoff in Task-Aware LLM Quantization" (07-01). "Using only target-task calibration data can degrade post-quantization performance." They also find a "Perplexity Illusion": perplexity sensitivity versus reasoning sensitivity has Kendall τ≈0.
- **2608.11786**, "Language-Conditional Dequantization" (08-12). INT3 GPTQ gives "2-4x larger perplexity degradation on non-English languages". Per-language rank-2 LoRA corrections (trained) beat the "data-free low-rank baseline (LQER) by an order of magnitude", and they report a perplexity-accuracy disconnect. This also bears on C2: data-free LQER-class correction is weak.
- **2609.26241**, "Damage Predicts Recovery: When Calibration Data Matters in Compressing Financial LLMs" (08-12). Calibration choice matters only where compression causes large damage.
- **2608.21019**, "Target-Aware Calibration Data Selection…" (08-21). "No single calibration recipe should be expected to fit all targets."
- **2609.30820**, "Quantizing Looped Transformers: Feedback Exposure and Calibration Blindness" (09-25). "One-step GPTQ is worse than round-to-nearest (RTN) on the primary task metric for five [of nine] checkpoints", because its Hessian under-weights directions seen later.

### C2: low-rank correction versus allocation and stock types

- **2609.33923**, "Quantization Error Is Spectrally Flat: A Single Random Probe Is a Calibrated, Data-Free Sensitivity Estimator…" (09-27). **Must cite.** It overlaps our setting closely.
  - "Round-to-nearest error is spectrally flat": its effective dimensionality is 0.93-0.96× that of i.i.d. noise across 1,683 tensors. "No low-rank summary captures it". This corroborates our §4.1 and the Marchenko-Pastur observation in LQER.
  - **On Qwen3.8-27B, a greedy knapsack over llama.cpp K-quant types (Q2_K…Q8_0), with one shared imatrix, at the byte count of llama.cpp's IQ3_M mix, beats "the vendor IQ3_M mix by 5.5% perplexity and beats an oracle that allocates from the real-activation objective by 1.6%".** Their words: "That objective is nevertheless the wrong quantity to allocate from."
  - This independently supports our same-byte mixed-type result (C2) and the surrogate failures (C5). It is the same model family as our §5.
- **2609.01587**, "The Structure of Quantization Damage in LLMs: Why the Next Bit Should Be Spent Globally" (09-01). "At a matched precision budget, spending it globally on finer quantization granularity beats locally repairing the most recoverable layers for all 8 group-128-compatible models … by 21-52 points." Recovery is "diffuse: for 8 of 9 models, recovering 75% of the gap takes roughly half the layers". "Weight/reconstruction statistics all fail to locate where restoring precision recovers accuracy." This supports C2 (bytes in the base beat targeted repair) and C5.
- **2610.08164** (T2) also supports C2 partly, by agreeing that symmetric compensators are interchangeable (§4.4).
- **2606.03465**, "Rethinking the Role of Tensor Decompositions in Post-Training LLM Compression" (06-02). It reports "a fundamental mismatch between the shared subspaces assumed by tensor decompositions and the heterogeneous representations learned by modern LLMs". This fits our §4.4 shared-basis null and §4.6 (no shared expert subspace).
  - Contrast: **2610.09342**, "Shared Low-rank Basis Factorization for Data-free MoE Compression" (10-07), finds shared bases help for compressing *weights* across experts at 16B-122B. That is a different object from residuals.

### C3: KLD/fidelity is not quality

- **2609.31155**, "Teacher-Anchored Selection of Post-Training Quantized Models under Domain Shift" (09-25). "Minimum teacher distortion … does not minimize empirical target cross-entropy." The evidence is from CNN and ViT models, not LLMs. Cite as a general analogue.
- **2608.26612**, "Launch-Bound and Substitutable: Why Three Inference Optimizations Fail to Pay Off in MoE Models" (08-27). "Leaving the routers in FP16 lowers drift by 20% while raising loss, so routing fidelity and output quality are separable objectives."
- **2610.29302**, "Compressing Onboard Language Models for Space Robots under a Behavioral-Fidelity Requirement" (10-21). The KL-to-BF16 "fidelity mode" and a "target-adapted mode" that adds answer likelihood select different models. The target-adapted one gains 4.3-4.5 points of accuracy.
- Trained or corrected artifacts that beat their own reference on likelihood or accuracy, which is the mechanism behind our C3 row:
  - **2608.20953**, "Quantization-Aware Healing" (08-21): the 4-bit student "matches or beats its bfloat16 source on 7 of 9 benchmarks".
  - **2610.08164**: C4 perplexity falls below FP16 after compensation.
  - **2606.15682**, "ReQAT" (06-14): it "surpasses BF16 fine-tuning accuracy".
- Fidelity metrics and guards can pass while behaviour changes:
  - **2607.28196**, "Fidelity Is Not Safety…" (07-30): compressed models pass perplexity, MMLU and data-free output-fidelity guards, yet invent procedure steps as agents. The cause is coherent low-rank (SVD) error, not pruning at the same perplexity.
  - **2606.10154**, "Quality Is Not a Safety Proxy Under Quantization" (06-08): a 7-level GGUF ladder plus AWQ and GPTQ, with 9 "hidden-danger" rows where quality held and refusal fell 12-68 points.
  - **2609.07664**, "Accuracy is Not Enough: A Divergence-Based Approach…" (09-07): JSD and TVD from BF16 to Q2_K. It argues for divergence as a fidelity signal and explicitly says it does "not directly establish correctness". Mixed Q4_K has lower divergence than Q4_0.
- **2607.10137**, "RDQ" (07-11). KL from FP16 correlates with log-perplexity at r=0.999 across untrained PTQ methods. Use this as the contrast case: for untrained files, KL tracks likelihood; our C3 says the link breaks for trained files.

### C5: methodology

- **2605.28873**, "Pre-Registering the Detectable Effect: A Paired-MDE Budget for 4-bit Quantization Benchmarks" (2026-05-25, just before the window). It gives a paired minimum-detectable-effect bound, and "much of the variance reported as 'benchmark unreliability' on n=100 subsamples is binomial sampling noise". This directly supports our pre-registration and paired statistics.
- **2609.18005**, "A Calibrated Instrument for Measuring How Inference Optimizations Affect Output Quality" (09-16). It uses a null condition whose measured difference must be zero.
- **2608.26389**, "LowRankArena" (08-26). Under standardized protocols, prior SVD-compression findings are "highly conditional".
- Surrogates fail:
  - **2609.26173**, "Component Type, Not Reconstruction Error, Predicts Attention Quantization Sensitivity" (08-09): reconstruction error explains "<10% of the variance in perplexity sensitivity in 27 of 36 cases", median R²=0.044.
  - **2609.09867**, "Contextual Utility of Quantization Moves in Extreme Low-Bit LLMs" (09-09): isolated option-KL "prices" of moves at a fixed bitrate mispredict composition, with residuals "26x and 42x the sums of absolute midpoint pricing errors". Six of eight moves reverse sign by context.
  - **2610.20826**, "Task-Phase-Conditioned Expert Routing Is Not a Reliable Proxy for Quantization Sensitivity in MoE FFN Tensors" (10-14): Qwen3.6-35B-A3B in ik_llama.cpp, scored by KLD. Routing-guided reallocation "fails to outperform the uniform baseline"; random allocation lands within 1 SE.
- Provenance:
  - **2610.19498**, "What You Sign Is Not What You Run: The Verifiability Gap in Quantized LLM Supply Chains" (10-14): 0 of 198 popular GGUF repos record their build environment. Re-deriving bit-exactly needs the FP-contraction setting, "for hybrid models, the per-tensor quantization recipe", and a shipped, hash-bound imatrix ("recomputing it did not match"). This supports our single-step provenance argument; consider recording these coordinates in Appendix B.
  - **2609.05881**, "Broken on Arrival: Silently Defective LLM Artifacts in Public Model Registries" (09-05): 1.6% of official Ollama GGUF artifacts solve 0/164 tasks.

## 4. Must-cite papers missing from cited.txt (ranked)

1. **2610.11226** DRQ (T1). Concurrent; pre-empts and explains C1's held-out damage.
2. **2610.08164** Align-then-Correct (T2). Scopes C2 to symmetric compensators.
3. **2609.33923** RAM / spectral flatness. Qwen3.8-27B K-quant allocation beats IQ3_M at the same bytes; RTN error is flat.
4. **2609.04748** Prefix-cache divergence amplified by quantization on llama.cpp. Needed for the F protocol.
5. **2609.01587** Global granularity beats local repair.
6. **2609.38599** JARQ. Refit-vs-frozen, plus codes-only failing on GPTQ W3 hosts.
7. **2608.11786** LCD. Off-distribution (multilingual) damage; trained rank-2 LoRA beats LQER ×10.
8. **2610.07781** and **2609.26693**. Fragility of agentic and tool-use evaluation (§5.5).
9. **2606.11244** SPEAR. Fused compensator path (§8 Serving).
10. **2605.28873** Paired-MDE pre-registration (C5).
11. Optional: 2610.19498 (GGUF provenance), 2609.26173 and 2609.09867 (surrogates), 2607.28196 (fidelity guards vs agent behaviour), 2607.00908 (calibration mix), 2609.31155 (distortion ≠ target CE).

**Hybrid and linear-attention quantization** (relevant because every Qwen model in the paper is a Gated-DeltaNet hybrid; cite one or two in §7.4):
- **2609.04098**, "Why Gated DeltaNet Survives 4-Bit Quantization: NVFP4 W4A4 for the Recurrent Half of a Hybrid 27B LLM" (09-03). On Qwen3.8-27B, quantizing all 496 linears including GDN matches BF16 within seed noise. "The supposedly fragile gate projections are the least sensitive." The delta rule holds injected noise at a plateau.
- Recurrent-*state* quantization for GDN/KDA:
  - DAMP (2608.27513): "INT4 and NVFP4 collapse it to near zero".
  - Low-Bit Recurrent States (2609.30950): Gramian-weighted bit allocation; 6-bit is within 0.005 nats.
  - LeapQuant (2609.38166), STEPQuant (2609.38169), the scale-state feedback paper (2610.15139) and low-discrepancy dither (2609.39185).
- DASC (2608.30386), state-checkpoint compression for prefix reuse.

**Surveys.** I found no dedicated 2026 survey of 2-3-bit LLM weight quantization on arXiv. The nearest are 2608.25188 "Transforms for LLM Quantization: The Great Inversion and Format Co-Design" (a survey of the transform stage) and 2608.29667 "A Target-Centric Survey of Quantization-Aware Training". Neither is needed for this paper.

**The 2-bit state of the art outside GGUF**, background for §7.1's "vector/lattice baselines" limitation:
- Tetra Leech-lattice at 2.7 bits per parameter (2609.35465)
- the fused multi-shell Leech-lattice decoder (2609.02652)
- CurveTQ curvature-weighted trellis (2610.09212)
- SAGE 2-bit VQ (2610.14745)
- UniSVQ (2606.10520)
- E8-QAT on Qwen3.6-35B-A3B (2610.27699)
- DuoRate per-expert 2.3-2.9 bpw on Qwen3-30B-A3B (2610.13328)
- QATFactory, which exports QAT/QAD directly to llama.cpp Q4_K (2609.39223)

## 5. Next-step ideas (ranked by expected payoff on one RTX 5090 with llama.cpp)

Where the field stands in October 2026:
1. Fixed-grid code refinement is a crowded micro-field: ReQuant, SchurQuant, JARQ, DRQ, and the ReRound diffusion-guided rounding in 2608.11045. Its open problem is generalization, not optimization.
2. Training-free low-rank compensation has moved to asymmetric, first-order objectives (2610.08164).
3. Several groups now say the bytes belong in allocation and granularity, not repair (2609.01587, 2609.33923, RCO, DuoRate).
4. Evaluation is becoming the bottleneck: agentic fragility, serving confounds, and fidelity versus quality.
5. Hybrid GDN models are the deployed default, with almost no GGUF-specific quantization study.

A strong follow-up sits where those meet: **"Allocation, not repair: a held-out, paired audit of byte allocation for hybrid models in GGUF, with a metric that separates fidelity from quality."** The ideas below, in order of payoff:

**1. Distributionally robust re-rounding on K-quants. Highest payoff per GPU-hour; mostly CPU.** Port DRQ's worst-case objective into our re-rounder. The Gram is the only input that changes: use a mixture or shrinkage set, such as calibration Gram ⊕ held-out Gram ⊕ diag-plus-identity, or DRQ's constrained activation set. Then rerun the 8B and 27B Q2_K cells on wikitext, FineWeb-Edu and code.

This also performs the untested test from §3.1 l.176 (the same computation under a Gram captured on held-out text). The predicted outcome is binary and pre-registrable: either robust re-rounding removes the held-out damage at 27B, which turns C1 into a positive method, or it does not, which shows that K-quant's frozen super-block scales make it different from group-128 INT.

**2. A principled same-byte type allocator for llama.cpp, as the new baseline everything must beat.** Our strongest result is that a hand-built mixed-type file beats correction. Make it systematic. Allocate per tensor over the K-quant and I-quant types under an exact byte budget, using:
- (a) a propagated, block-output KLD signal (2609.33923's probe, or our own captured Grams);
- (b) RCO's exact dynamic program;
- (c) the uniform-granularity rule of 2609.01587.

Score all arms with our paired per-chunk KLD on three corpora at 0.8B, 8B and 27B, against IQ3_M, Unsloth UD and GSQ-RCO. A 5.5% perplexity gain over IQ3_M on Qwen3.8-27B is already reported, so the bar is clear. Our held-out protocol tests whether that gain survives off wikitext, which nobody has checked.

**3. Close the C2 scoping hole.** Implement the two-stage asymmetric plus natural-gradient compensator of 2610.08164 on one K-quant base (Q2_K or IQ2_XS at 8B) and run it against our same-byte controls. Also try the GPTAQ-style asymmetric target alone. If it still loses to the same-byte mixed file, C2 generalizes to the best training-free objective of 2026, and that is a very citable negative. Cost is one statistics pass with gradients, which is feasible at 8B on a 5090, plus our existing kernels for serving.

**4. Separate fidelity from quality properly (extends C3).** Build a three-axis evaluation for trained versus untrained artifacts:
- KLD to reference;
- held-out NLL;
- a decision-level measure: flip rate and margin slope (2608.06564's m′≈cm+b) or correctness agreement (2607.08734).

Then run a seeded agentic suite with prefix-cache-off and slot-pinned llama-server, fixed templates, paired McNemar per task, and an MDE computed in advance (2605.28873).

Candidates: GSQ-RCO, QATFactory-exported Q4_K QAD files, DRQ- or JARQ-refined files, and stock files. The question is when KLD stops ranking quality. That is a methods paper the area currently lacks.

**5. A GGUF study of hybrid (Gated-DeltaNet) models by tensor role.** All our Qwen models are 3:1 GDN:attention hybrids. 2609.04098 says the GDN projections and gates are the *least* sensitive at 4 bits, while community GGUF recipes keep them high.
- Measure KLD per tensor role at 2-3 bits on 27B. Then re-allocate bytes from GDN to FFN and attention at a fixed file size.
- Optionally add recurrent-state quantization in llama.cpp (Gramian or decay-weighted, per 2609.30950 and DAMP), for long-context VRAM on the 5090.

This is cheap, directly useful to OpenBeast, and nearly unstudied in the GGUF format.

**6. Provenance and reproducibility as an artifact.** Adopt 2610.19498's coordinates: FP-contraction flag, per-tensor recipe, hash-bound imatrix. Release bit-reproducible GGUFs with re-derivation scripts. This is low cost and strengthens TMLR's reproducibility criterion. It can go in the current camera-ready.

**7. Lower priority.**
- Screen corrected and allocated files for agentic "invented steps" from coherent low-rank error (2607.28196). Our adapters add coherent components.
- Try a trained per-domain rank-2 LoRA (the LCD recipe) as the "trained" counterpart to our data-free correction at the same bytes.
- Fused adapter kernels matter only if the correction survives ideas 2 and 3.

## 6. Verification note

All IDs, titles and dates above come from the arXiv API Atom records fetched in this session (store: `~/Documents/openbeast-litsweep-raw-2026-10-22/r8/store.json`). I quoted numbers only from fetched abstracts, or from the fetched HTML for the 12 full-text papers listed in §1.

For 2610.08164 the HTML dropped some numbers that were inline math. The figures I quote (12.43→10.26, 21.11→13.22, 51%/84% vs 31%/63%) come from its API abstract. The "below FP16" statement is in the HTML text, but the values themselves were stripped.

Nothing above is unverified.
