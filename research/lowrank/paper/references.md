> ⚠ see review/ corrections 2026-08-04

# beast-rank master bibliography

Built 2026-08-04 from all 15 reports in `../prior-art/` plus `OUTLINE.md` /
`../RESULTS_ROLLUP.md`. One entry per source, deduplicated across reports.
Titles/short-names are as recorded in the reports (verified there against
abs/HTML pages on their sweep dates); anything soft is in §Verification flags.
"Role" = what claim of OURS the source supports or contrasts with.

**Report codes:** qec=arxiv-qec-lineage · wa=arxiv-whitening-allocation ·
str=arxiv-structures · out=arxiv-outliers-scaling · ker=arxiv-kernels ·
cod=arxiv-codesign-mining · phy=arxiv-physics-inspired · fp4=arxiv-fp4-composition ·
frh=arxiv-functional-rank-healing · kv=arxiv-kv-holistic ·
sur=survey-lowrank-compression · math=math-methods · up=upstream-landscape ·
gguf=gguf-tooling · int=llamacpp-internals

---

## 1. Quantization-error-correction lineage (Q + low-rank residual)

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2303.08302 | ZeroQuant-V2 / LoRC (preprint 2023) | Origin of quant+LR-residual; rank-8 unweighted SVD = stabilizer not fixer; Table 8 (INT8 factors free) directly licenses our Q8_0 factors | qec, sur, ker, math |
| 2402.02446 | LQER / L²QER (ICML 2024) | The published statement of our premise: raw residual is Marchenko–Pastur → plain-SVD capture ≈ r/d (our E03 null); its diagonal scale ≡ QERA-approx ≡ our imatrix whitening (E05); W2 needs k=256. Repo unlicensed — reference only | qec, wa, sur, out, ker, phy, math, up |
| 2410.06040 | QERA (ICLR 2025) | Our reference math: Thm 1 (full R_XX^{1/2}) vs Thm 2 (diagonal = literally the imatrix statistic); our diag→fullcov pipeline is exactly QERA-approx→exact; exact-vs-approx gap widens as bits drop. Apache-2.0 code | qec, wa, sur, ker, cod, phy, math |
| 2410.21271 | EoRA (NVIDIA; ICLR-W 2026, venue soft) | Closest industry neighbor: eigenspace projection = QERA-exact in different clothes; ~3× full-vs-diag at 3-bit; rank sweep unsaturated at r512; the only published fused quantized-GEMV+LR kernel (in GPTQModel); NC license — implement from QERA math, never this code | qec, wa, sur, ker, fp4, cod, phy, math |
| 2405.18886 | CALDERA (NeurIPS 2024) | Thm 4.1 (1−k/2n)² — one leg of our capture-law citation triangle; own tables show fixed-rank benefit collapsing with scale (r64 buys 0.86 ppl at 7B, r128 buys 0.05 at 70B); joint LDLQ↔LPLR alternation + best-iterate tracking + quantized factors at 2 bpw; Table 6 unfused-decode loss = the serving cautionary tale. Repo license null | qec, wa, str, out, ker, sur, cod, phy, math |
| 2606.01412 | GPTQ-intrinsic LoRA / OLrC / Bid-Up (2026) | Theory leg of the triangle: lower bound with J = NN′/(r(N+N′+1)+2) ⇒ fixed-r share decays ~2r/d, rank must scale ~linearly with width; Bid-Up frozen-grid monotone alternation → our best-iterate/re-round protocol. Tables didn't survive HTML — pull PDF | qec, wa, cod |
| 2412.01129 | RILQ (AAAI 2025) | The plateau's published diagnosis: 2-bit error is inherently high-rank, layer-local SVD saturates (rank-insensitivity σ=0.07 once loss is model-level) — names the cross-layer headroom our calibration-only SRP idea targets | qec, cod, math |
| 2310.08659 | LoftQ (ICLR 2024) | Alternating quantize↔SVD template with black-box quantize step; T=1 captures most (our alternation protocol descends from it); its 2-bit numbers are POST-fine-tune — never compare to PTQ | qec, cod, math |
| 2311.12023 | LQ-LoRA (ICLR 2024) | Two-sided diagonal-Fisher congruence = published twin of our diagonal whitening; ILP precision allocation at fixed rank (allocates bits, never rank); Fisher pays only ≤2.75 bits. MIT | qec, wa, math |
| 2305.14314 | QLoRA (NeurIPS 2023) | The no-correction baseline: NF4 + LoRA(B=0), diverges at 2-bit — context anchor for why residual correction exists | qec |
| 2310.11028 | LPLR (NeurIPS 2023) | Sketching theory precursor to CALDERA; Thm 3.2 bound floors at tail energy ⇒ fixed-k capture ∝ k/d for flat spectra. MIT (pilancilab/matrix-compressor) | qec |
| 2506.02077 | ODLRI (Findings of ACL 2025) | Role inversion: low-rank owns activation-salient outliers BEFORE quantization (0.999 outlier-subspace capture) — rank pays on structure, not bulk; init > iteration | qec, wa, out, sur |
| 2501.18475 | CLoQ (2025) | Closed-form whitened init beats LoftQ's 5-round alternation at INT2; source of the damping recipe λ = 0.01·Tr(H)/d we adopted | qec, cod |
| 2606.00494 | ProjQ (ICML 2026) | Quantizer/corrector co-adaptation: shape the noise onto the correctable subspace (attacks WHY the residual is incompressible); Prop 5.4 monotone convergence with a black-box quantizer → our projected-imatrix idea. Numbers conflict across extracts — pull PDF | qec, cod |
| 2602.02001 | SRR "Preserve-Then-Quantize" (ICML 2026) | First principled per-matrix rank split (preserve top subspace vs reconstruct error); its random-matrix probe implicitly predicts capture ~ r/d — never measures it across widths (we do) | qec |
| 2601.05684 | FLRQ (2026) | Strongest anti-uniform-rank evidence: allocated avg rank 39 (0.24 bpw) beats uniform r256 (1.60 bpw) at 2-bit — the published twin of our measured-alloc win / "870 MB mostly wasted" | qec, str, wa |
| 2603.08185 | SERQ (2026) | Row-structured single-matrix correction survives W4A4 where two-factor LR fails — sidesteps width collapse via row sparsity, not spectra. CC BY 4.0 | qec, wa, sur, ker, math |
| 2412.07902 | LRC (Microsoft Research, 2024) | Strongest empirical rank-∝-d corroboration: r/d = 10% halves, 30% closes the W4A4 gap; verified at algorithm level it does NOT propagate between layers — the gap our SRP proposal names; "computational costs not studied" (no kernel) | qec, str, ker, cod, phy |
| 2412.14363 | ResQ (Purdue/d-Matrix; ICML 2025) | r = d/8 high-precision PCA subspace — rank proportional to width, hard-coded in production print; also the "shrink the denominator" whitening mechanism for our o/down fix | qec, str, out |
| 2505.07004 | GuidedQuant (SNU/Samsung/Google; ICML 2025) | Block-diagonal end-loss Fisher whitening; best pure-2-bit numbers in the whole lineage WITHOUT a correction branch — the whitening ceiling our metric ladder (law #4) points toward | qec, wa |
| 2411.05007 | SVDQuant (MIT Han Lab; ICLR 2025 spotlight) | Engineered concentration: smoothing manufactures the spike its rank-32 branch eats — why tiny rank works for them and not on generic LLM residuals; fusion data (naive +50% latency → 5–10% fused) is our kernel template; Nunchaku ships LR-over-NVFP4 (diffusion only). deepcompressor Apache-2.0 | qec, str, out, ker, sur, fp4, frh, phy |
| 2605.00140 | ARHQ (2026) | Whiten by the RESIDUAL covariance G_x (where the error lives, not where activations are large) — the metric variant our SRP gets for free; 4B-scale only. CC BY-SA 4.0 | qec, wa |
| 2504.09629 | QEP — Quantization Error Propagation (2025) | Sequential propagated-activation compensation, closed-form, quantizer-only; largest calibration-only 2-bit rescues in print (INT2 RTN 90.7→12.25); one half of our SRP composition; α-damping on FFN = its overfit control | cod, str, phy |
| 2607.14630 | Cross-Layer Error Compensation (2026) | Exact cross-layer error recursion — but autodiff-optimized and 1.5B-scale only; brackets the calibration-only cross-layer gap from the trained side. CC BY 4.0, patent pending | qec, cod |
| 2606.11244 | SPEAR (2026) | Quantization error is token-dependent; CKA layer-sensitivity = ready-made allocation signal | qec |
| 2602.24059 | Quant Experts (CVPR 2026) | Routed mixture of low-rank compensators (VLMs) — the routing lane we reject (we are byte-bound, not FLOP-bound) | qec |
| 2607.23047 | MixQuant (2026) | Per-layer sensitivity depends on upstream quantized config — caveat adopted for our allocation (FP-context scores misallocate) | qec |
| 2603.25385 | GlowQ (2026) | Shared right factor per input-sharing group + restore-only-the-payers — evidence most layers don't repay their rank budget; mitigates rather than fuses correction overhead | qec, ker |
| 2506.13771 | LittleBit (NeurIPS 2025) | Sub-1-bit fully-low-rank weights — brackets our regime from below (at extreme rates low-rank carries everything) | qec |
| 2604.00821 | OBD-LLM (2026) | Input-only whitening provably suboptimal for decomposition — bi-directional (Kronecker-Hessian) whitening; our untested two-sided-residual gap | qec |
| 2604.16940 | D-QRELO (2026) | Our exact recipe (1-bit base + LR correction of its quantization error) applied to SFT deltas | qec |
| 2605.09281 | TileQ (2026) | MoE 2D-tiled structured low-rank quantization — relevant only if we target the 35B-A3B | qec, str |
| 2512.17073 | MoE low-rank compensators (2025) | Router-guided compensation of Top-n experts / streamed compensators for offloaded experts — MoE-side cousin | qec, str |
| 2606.04238 | Recover-LoRA (AMD, 2026) | RILQ-family trained 2-bit recovery via logit distillation — the training route we don't take | qec |
| 2501.16385 | FBQuant (IJCAI 2025) | Sub-branch correctors OVERFIT calibration; negative-feedback regularization — the cautionary result echoed by QEP's α-damping | qec |
| 2601.19675 | LoPRo (2026) | Quant-grid-respecting block permutation + Walsh–Hadamard rotation + mixed-precision LR — preconditioning the residual without the online-Hadamard tax | qec, str |
| 2606.01556 | TwinQuant (ICML 2026) | LLaMA3-8B weight spectra flat beyond rank 256 — independent confirmation of our capture-law ceiling; manifold-learned quantization-friendly subspaces + fused dual-component kernel | qec, fp4 |
| 2410.14713 | QuAILoRA (NeurIPS-W 2024) | Calibrated-SVD quantization-aware LoRA init, 4-bit — periphery | qec |
| 2402.05147 | ApiQ (2024) | Joint LoRA+quant init preserving activations — training-side, periphery | qec |
| 2405.16528 | LoQT (2024) | Factors periodically merged into quantized weights during training — quantized pre-training, not PTQ | qec |
| 2402.05445 | IR-QLoRA (ICML 2024) | Entropy-maximizing quant + information-elastic LoRA — fine-tuning method; **arXiv ID not re-verified** | qec |
| 2411.07762 | ASER (2024) | Whitened-SVD compensation + smoothing at W4A8 — direct LQER sibling | qec |
| 2410.09615 | SLiM (2024) | Quant + 2:4 sparsity + saliency low-rank adapter — one-shot triple composition | qec, str |
| 2406.17542 | CDQuant (2024) | Coordinate-descent GPTQ replacement — composable base quantizer | qec |
| 2406.06385 | LR-QAT (2024) | Low-rank QAT — training-side, periphery | qec |
| 2604.08118 | Codebook-init basin (2026) | Initialization determines the optimization basin at ≤2-bit — echoes CLoQ/ODLRI: init dominates iteration | qec |
| 2405.20973 | LCQ (2024) | Rank>1 codebook quantization — periphery | qec |
| 2504.14569 | NoWag (2025) | Pruning/quant framework — periphery, ruled adjacent | qec |
| 2602.00135 | LLaVA-FA (2026) | Frequency-domain joint decomposition — periphery | qec |
| 2509.00404 | Metis (2025) | Residual-after-spectral-split is flat — independent flatness confirmation of our premise | qec |
| 2504.02658 | MiLo (2025) | Our exact architecture on MoE: HQQ base + truncated-SVD compensators, INT3-quantized factors, kurtosis/traffic rank policy, zero-waste INT3 kernels — and the compensator is explicitly UNFUSED (field-is-empty evidence) | str, ker |
| 2604.07955 | ResComp / "Rethinking Residual Errors" (2026) | Claims compensation impact scales inversely with bit-width — the qualitative version of our measured F7 recovery curve; **extract lossy, local PDF saved — verify before citing** | cod, phy |
| 2507.17417 | Same-harness eval survey (2025) | Fair comparison taxonomy: activation-statistics-scaling vs iterative vs training-based — our related-work skeleton | qec |
| 2505.05530 | Low-bit DNN survey (2025) | 24-subcategory map — positioning only | qec |
| 2507.09428 | Operator-factorization theory (2025) | Why alternating compression steps converge and when they trap — backs our T≤3 alternation budget | qec |
| 2601.02455, 2508.12094, 2601.11200 | adjacent propagation/compensation (ASR/diffusion/calib-data) | Listed for completeness in the propagation lane — not mined | cod |
| 2603.01376, 2506.13472, 2603.19296, 2606.08565, 2510.18650, 2607.08643, 2505.11076, 2505.12942, 2505.21077, 2510.01718, 2607.09694, 2505.20112, 2606.07098 | 3BASiL · ROSAQ · TTQ · EinSort · Binary-Quadratic · BiSCo-LLM · Double-Binary-Factorization · A³ · NBL · (2) attention-side · ResSVD/ERC-SVD · SigmaScale | Swept and ruled out as off-lineage (one collective line in qec §VI) | qec |

## 2. Whitening & allocation theory (incl. the pure-SVD-of-W baseline ladder)

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2210.17323 | GPTQ (2022/23) | The layer-wise Gram objective (H = 2XXᵀ) everything descends from; 1%-of-mean-diag damping and 128×2048-token calibration conventions we adopted; LDLQ lineage | wa, cod, math |
| 2403.07378 | SVD-LLM (ICLR 2025) | Cholesky-of-Gram whitening theorem (truncated whitened σ_i = exact loss) — establishes numerical viability of our Cholesky route at 7B–70B; also the pure-SVD ladder (ppl 7.73 @20%) proving factorization-of-W alone is dead. Apache-2.0 | wa, sur, math, frh |
| 2503.12340 | SVD-LLM V2 (NAACL 2025) | Type-grouped heterogeneous compression ratios — q/k/v/o/gate/up/down have different truncation curves; independent confirmation of our E05 per-kind map | wa, sur |
| 2605.15626 | IO-SVD (2026) | Double-sided whitening (input Gram × output KL-curvature) + global greedy component pool — the metric rung above input covariance; needs backprop | wa |
| 2509.25136 | BALF (2025) | Budgeted greedy activation-aware factorization; whitening generalized to rank-deficient activations — allocation math reusable | wa, sur, frh |
| 2502.02723 | Dobi-SVD (ICLR 2025) | Differentiable rank selection — rejected: the greedy auction is its published equal at a fraction of the cost. MIT | wa, sur, frh |
| 2306.02272 | OWQ (2023) | Hessian-diag × error column sensitivity — the cheap column-outlier metric we already own via imatrix | wa |
| 2605.18475 | GAMMA (2026) | Gumbel-soft bits + augmented Lagrangian → knapsack ILP for global bit allocation — the bit-side allocator we deferred | wa |
| 2504.07389 | TaCQ (2025) | Task-circuit KL saliency for mixed precision — the fancy end of sensitivity; future work | wa |
| 2607.07964 | KronQ (2026) | Kronecker-factored layer Hessian (grad-cov ⊗ act-cov) — the two-sided metric upgrade path; deferred | wa |
| 2601.20745 | Hestia (2026) | Hessian-trace signal now considered affordable at LLM scale — sensitivity-metric context | wa |
| 2506.01967 + 2309.15531 + 2404.03605 | outlier-geography trio (titles not recorded) | Massive-activation outliers live at ffn_down inputs; error concentrates in first/last down_proj — says our expensive 25600-dim Grams sit exactly where sensitivity lives | wa |
| 1305.5870 | Gavish–Donoho optimal hard threshold (2013) | 2.858·σ_med MP-bulk edge = principled per-tensor rank CAP — components below the edge are provably wasted bytes; feeds our allocation | wa, phy |
| 1311.0851 / 1405.7511 | Optimal singular-value shrinkage | Shrink retained σ toward the spike estimator — free quality nibble on top of truncation. **Two different IDs cited by wa vs phy for "optimal shrinkage" — resolve which before citing** | wa, phy |
| 2602.07465 | Calibration-token scaling study ("-adjacent", **soft ID**) | PPL improves to ~4M calibration tokens, knee 500k–1M — justifies our 262k–524k budget | wa |
| 2606.21847 | UniRank (2026) | Global sort-and-truncate component allocation (SVD-only): up to 61.8% ppl reduction vs uniform rank | qec, wa |
| 2510.19389 | ARA (2025) | Gradient-learned rank masks under budget; finds q/k compressible — partially OPPOSITE our E05 map (different objective: truncating W vs correcting R) — a contrast worth one sentence | qec |
| 2602.22268 | AutoQRA (2026) | The only coupled (bit-width, rank) per-layer budget — but evolutionary/BO search on the training side | qec |
| 2607.20205 + 2603.13792 + 2512.13733 + 2509.25622 | StatLoRA · IGU-LoRA · Differentiable Rank Selection · Layer-wise Dynamic Rank | LoRA-fine-tuning-side rank allocation one-liners — periphery | qec |
| 2607.19391 | LAARA (2026) | Fisher scores vary wildly by projection type → normalize per-type + log-compress before ranking — practical allocator hygiene we follow | math |
| 2405.10616 | Bayesian-opt allocation (2024) | Search-based rank allocator — context row | math |
| 2606.00573 | "LASER" allocation follow-up (2026) | Allocation-lane citation in math-methods — **name collision with 2312.13558 (the ICLR'24 LASER); verify title/ID before citing either** | math |
| 1012.0197 | Gillis & Glineur — weighted low-rank is NP-hard (SIAM 2011) | Why the whole field (and we) use one-sided full or two-sided diagonal weighting: the general problem is NP-hard | math |
| 0909.4061 | Halko–Martinsson–Tropp randomized SVD (SIAM Rev. 2011) | Basis of our rsvd; power-iteration/oversampling guidance for flat spectra (q=r+128, niter≥8) | math |
| 2207.00112 | FWSVD (ICLR 2022) | First importance-weighted SVD; collapses at LLM scale (ppl 1727 @20%) — and lane-c proof that Fisher metric ≠ Frobenius (better task acc at worse reconstruction) | sur, frh, phy |
| 2312.05821 | ASVD (2023, v5 2025) | Diagonal activation scaling + Σ^{1/2}-splitting (we use it for Q8 factors) + STRS rank allocation; shows SVD-then-4-bit composes badly. MIT | sur, math, frh |
| 2410.05437 | ESPACE (NVIDIA; NeurIPS 2024) | Activation-side projection: 50% compression at ~+0.2 ppl exists — but only with retraining; the ceiling-mover we can't afford | sur, str |
| 2502.01403 | AdaSVD (2025) | Adaptive SVD compensation — pure-decomposition family, doesn't change conclusions | sur |
| 2505.03801 | Global Rank & Sparsity Optimization (ICLR 2026) | RPCA-style global (cross-layer) budget allocation — rank allocation matters, published | sur |

## 3. Structures beyond low-rank

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 0912.3599 | Robust PCA (Candès et al., 2009) | The L+S exact-split theorem behind the whole sparse+low-rank lane | str |
| 2306.03078 | SpQR (2023) | Sub-1% FP16 outliers carry the bulk of quantization damage; tail FRACTION is d-independent — the one capture channel immune to our width collapse; reference sparse-matvec CUDA kernels | str |
| 2306.07629 | SqueezeLLM (2023) | Dense-and-sparse split + Fisher-diagonal sensitivity computable at LLM scale — precedent for both our sparse lane and Fisher metric | str, out, phy |
| 2306.11222 | LoSparse (2023) | First explicit "LR takes the shared bulk, sparse takes the spikes" framing (BERT-era, trained) | str |
| 2409.13652 | OATS (2024) | Training-free W = S + L by alternating thresholding weighted by input second moments (imatrix-equivalent) — directly reusable math | str |
| 2502.00899 | HASSLE-free (2025) | Unifies sparse+LR under the activation-weighted objective we already compute; optimum is always a mix (sparsity takes kurtosis, LR takes spectrum) | str, frh |
| 2406.13868 | SDQ (2024) | Sparse-decomposed quantization for inference efficiency — periphery | str |
| 2508.02668 | LOST (2025) | LR+sparse used for pre-training — the split is natural for LLM weights, not just residuals | str |
| 2601.07475 | ARCQuant (2026) | Correction as appended quantized residual CHANNELS inside one unified NVFP4 GEMM — the field's in-format dodge of the fusion problem we solve by fusing | str, fp4 |
| 2110.08152 | Kronecker GPT compression (2021) | Kronecker on GPT-2 — needs retraining; family context | str |
| 2212.10650 | KronA (2022) | Kronecker PEFT — family context | str |
| 2401.16367 | TQCompressor (2024) | Kronecker + permutations, GPT-2 scale, retraining required | str |
| 2307.00526 | TensorGPT (2023) | Tensor-train of the embedding matrix only | str |
| 2401.14109 | CompactifAI (Multiverse, 2024) | MPO-tensorized LLaMA-2-7B — the healing pass is load-bearing (GSM8K −23% at headline ratio); the dream's best advert and its deflation | str, phy, frh |
| 2507.08836 | CompactifAI independent eval (2025) | Third-party check on the above | phy |
| 2505.20132 | Tensorization position paper (2025) | Honest about absent large-scale PTQ wins for tensor networks | phy |
| — (no ID) | Saten (EMNLP 2025) | Even the TT camp now adds a sparse component + fine-tunes — converges with the S+L lane. **No arXiv ID recorded — resolve** | str |
| 2506.02818 | ProcrustesGPT (2025) | Rotate W to fit the structure before factorizing — a §0.4-legal move (rotation folded into neighbors) | str |
| 2606.03465 | Rethinking Tensor Decompositions for Post-Training LLM Compression (2026) | The load-bearing NEGATIVE: TT/Tucker/CP/Kronecker underperform plain SVD at equal params post-training — kills the family except our untested grid-aligned rearrangement conjecture | str, phy |
| 2501.19135 | TT-LLM on systolic accelerator (2025) | Hardware feasibility exists; quality leans on fine-tuning | phy |
| 2112.00029 | Pixelated Butterfly (2021) | Butterfly structured training — family context | str |
| 2204.00595 | Monarch (2022) | Block-diagonal×perm×block-diagonal; expressivity wins require gradient training — dead for closed-form correction at small p (our §C math) | str, phy |
| 2310.12109 | Monarch Mixer (2023) | Monarch architecture-side — context | str, phy |
| 2410.21262 | BLAST (NeurIPS 2024) | Shared-basis-per-block-row structured matrix; subsumes LR/Monarch — but gradient-fitted; wins only at large budgets | str |
| 2408.17383 | MoRe (2024) | Monarch beats LoRA at 10× fewer params WHEN TRAINED — expressivity ≠ closed-form fit | str |
| 2510.11192 | Sparse block-diagonal LLM acceleration (2025) | Hardware-side: block-diagonal GEMMs map well to accelerators | str |
| 2405.15013 | Butterfly sparse GEMM memory management (2024) | GPU-proven butterfly GEMM — serving feasibility for lane-b structures | phy |
| 2601.13563 | ButterflyMoE (2026) | Butterfly structure in MoE — context | phy |
| 2410.03765 | Basis Sharing (ICLR 2025) | Consecutive layers share one singular basis — the positive datapoint for our cross-layer shared-basis lane (12× rank amplification at equal bytes) | str, sur |
| 2605.30836 | Cross-Layer Subspace Coupling (2026) | The guardrail NEGATIVE: weight-space cross-layer sharing improves reconstruction 46% yet degrades ppl — prescription = per-layer activation-weighted objectives, which our variant keeps | str |
| 2310.11454 | VeRA (2023) | Frozen shared random pair + per-layer scales — the serving pattern (shared factors resident once) our shared-basis lane borrows | str |
| 2310.02556 | NOLA (2023) | Per-layer weights as combos of shared random bases — params decoupled from d | str |
| 2405.17604 | LoRA-XS (2024) | W += B·C_l·A with shared frozen B,A + tiny per-layer core — the exact structure of our shared-basis proposal | str |
| — (no ID) | MoE-SVD (2025) | SVD sharing across experts works BECAUSE experts share input distribution — supports task/distribution-conditioned rank collapse. **No arXiv ID recorded** | str, frh |
| 2402.10193 | BitDelta (2024) | "Your fine-tune may only be worth 1 bit" — delta compression cousin validating residual-specific structure | str |
| 2406.08903 | Delta-CoMe (2024) | Quantize the tail of singular vectors instead of truncating — transfers verbatim to our factors; 2–4× capture-per-byte multiplier (our r-Q8 finding's published cousin) | str |
| 2502.18755 | M-ANT (2025) | Finer group-wise quantization — residual structure increasingly grid-shaped (feeds grid-aligned conjecture) | str |
| 2509.03054 | Dynamic grouping (2025) | Same lane as M-ANT | str |
| 2602.02126 | Two-stage grid quantization (2026) | Same lane | str |
| 2509.22075 | CoSpaDi (2025) | Sparse-dictionary factorization — midpoint between LR and sparse; group-LR with learned groups | str |
| 2411.08212 + 2601.04823 + 2607.21978 + 2607.26052 | PERFT · DR-LoRA · MoE²-LoRA · confidence-adaptive routing | Routed low-rank adapters (fine-tuning) — routing multiplies capture-per-FLOP, not per-byte; rejected for our byte-bound regime | str |

## 4. Outliers, rotations (the rejected lane), and scaling laws

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2208.07339 | LLM.int8() emergent features (2022) | Outlier head is ~0.1% of dims (6 features at 6.7B) and its COUNT doesn't scale with d — mechanism 1 of our capture collapse (head dilution). + Dettmers blog (vendor) | out |
| 2402.17762 | Massive Activations (2024) | <10 activations per model at 100,000× median, fixed coordinates, input-independent bias terms — the O(1) head our diagonal whitening feeds on | out |
| 2411.07191 | The Super Weight (2024) | Single scalar weights whose removal explodes ppl; published coordinates — target list for our sparse-patching lane E-1 | out |
| 2603.05498 | Spike/Sparse/Sink anatomy (2026) | SwiGLU as directional quadratic amplifier; early step-up / late step-down injection — mechanistic map for where to spend correction bytes | out |
| 2309.17453 | StreamingLLM / attention sinks (2023) | Sink-token lineage — why sink-adjacent channels are structurally load-bearing | out |
| 2604.10098 | Attention-sink survey (2026) | Sink literature roll-up — context | out |
| 2603.17771 | Sinks as gradient regulators (2026) | Outlier structure is functional — context | out |
| 2606.20743 | Outliers rebuilt after ablation (2026) | Models re-grow protected outliers — the structure is essential, not incidental | out |
| 2306.12929 | Quantizable Transformers (2023) | Sinks = softmax no-op mechanism; trainable-away only — you cannot patch them away, must represent them | out |
| 2307.13304 | QuIP (2023) | Incoherence processing origin; first viable 2-bit — the anti-strategy: rotation spends the outlier head our correction wants | str, out, phy |
| 2402.04396 | QuIP# (2024) | RHT → "ball-shaped sub-Gaussian" weights → white residual: SVD of a post-rotation residual buys ≈ r/d and no more — the rejected rotation lane's own theory | str, out, phy |
| 2406.11235 | QTIP (2024) | Trellis codebooks — strongest pure-base competitor our corrected-Q2 must beat (with GuidedQuant) | str |
| 2404.00456 | QuaRot (2024) | Foldable R1 rotations, W4A4 — rotation belongs before quantization or not at all (our §0.4 lemma's foil); also KV-side rotation precedent | str, out, phy, kv |
| 2405.16406 | SpinQuant (2024) | LEARNED rotations beat random — and its offline R2 fold is the key precedent for our KV-equalization-as-tensor-edit idea | str, out, phy, kv |
| 2403.06082 | FrameQuant (2024) | Fusion-frame redundancy at ~2.2 effective bits — isotropization variant | str, out |
| 2406.01721 | DuQuant (2024) | Rotation + zigzag permutation outlier spreading | str |
| 2501.13987 | OSTQuant (2025) | Jointly learned orthogonal+scaling transform | str |
| 2509.09679 | ButterflyQuant (2025) | Cheap learnable butterfly rotations — lane-b structure reappearing inside the rotation camp | out, phy |
| 2604.26378 | CoQuant (2026) | Joint weight–activation subspace projection for mixed precision | str |
| 2212.09720 | k-bit inference scaling laws (Dettmers & Zettlemoyer, 2022) | 4-bit near-universally optimal across 19M–176B; sub-4-bit is where scale differences appear — frames our 2–3 bpw target zone | out |
| 2411.17691 | Low-bit favors undertrained LLMs (2024) | QiD grows with training tokens, shrinks with size → big-model residuals are noise-like — mechanism 3 of the capture collapse ("less harm, less correctable harm") | out |
| 2411.04330 | Scaling laws for precision (2024) | Same direction as above — corroboration | out |
| 2410.12119 | PTQ scaling laws (2024) | Same lane | out |
| 2505.14302 + 2502.02631 + 2509.22935 | QAT scaling law · ParetoQ · compute-optimal QAT | Training-side scaling context — one-line cites | out |
| 2012.13255 | Intrinsic dimensionality of fine-tuning (Aghajanyan, 2020) | Task subspaces SHRINK with scale — the functional-vs-energy rank wedge: KL-relevant residual directions are few even when energy is high-rank | out, frh |
| 1804.08838 | Measuring intrinsic dimension (Li et al., 2018) | Origin of intrinsic-dimension measurement | out |
| 2106.09685 | LoRA (2021) | r=1–4 suffices at 175B for ADAPTATION — the update-subspace result our correction problem must be distinguished from | out |

## 5. Kernels & serving

| Source | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2508.01506 | FlashSVD (Duke; AAAI 2026) | Streaming kernels for factorized inference, −70% peak activation memory (prefill/encoder regime); lesson: total fusion can lose to partial (their V2 +60%) | ker, sur, frh |
| 2605.08314 | FlashSVD v1.5 (2026) | Our −33% pathology published: 1,174 launches/token, launch-bubble dominated; per-layer CUDA graphs + factor packing → 2.55× decode — the non-kernel baseline our fused kernel must beat | ker, sur |
| 2310.18547 | Punica SGMV (MLSys 2024) | ~40 µs/LoRA-op FLAT across ranks 8–64 at batch 1 — published proof our r16-slower-than-r64 inversion is launch-bound | ker |
| 2311.03285 | S-LoRA (MLSys 2024) | MBGMM/MBGMV + unified paging — separate-kernel structure, nothing for batch 1 | ker |
| 2408.11743 | Marlin (2024) | fp16×int4 batch-1–32 GEMM; bias-hook + atomicAdd-output = two ready composition hooks for a bias-shaped correction | ker |
| 2603.11873 | AdaFuse (Baidu; AAAI 2026) | LoRA up/down each cost as much as the full backbone matmul at seq-1, rank-independent (our pathology, third publication); fused adapter-switch recovers 2.4× | ker |
| 2510.00206 | LoRAFusion (2025) | Fuse LoRA's memory-bound pieces into neighbors, leave base GEMM alone — same principle as our Phase 1/2 split | ker |
| 2603.22276 | Scaling DoRA (2026) | Fused Triton adapter chains, ~4× traffic cut — Triton prototype viability before hand-CUDA | ker |
| 2007.01277 | HFuse (CGO 2022) | Source-level horizontal kernel fusion, 2.5–60.8% | ker |
| 2512.22219 | Mirage MPK (2025) | Persistent-kernel compiler, 1–2 µs task transitions — the ceiling, not the first step | ker |
| 2605.11581 | Ada-MK (2026) | Launch overhead ≈14.6% of decode even in TensorRT-LLM; consumer-Ada megakernel +23.6% | ker |
| 2605.30571 | "Memory-Bound but Not Bandwidth-Limited" (2026) | Eager-vs-graph delta ≈20.6% of batch-1 step on H100 — quantifies what graphs alone recover | ker |
| 2501.01005 | FlashInfer (2025) | Serving-kernel context — swept | ker |
| 2401.11240 + 2507.01438 + 2507.08833 | CaraServe · EdgeLoRA · "LoRA Is Slower Than You Think" | Batching/serving papers, no GEMV fusion — field-is-empty evidence | ker |
| DOI 10.1145/3620666.3651369 | CUTLASS EVT (ASPLOS 2024) | Epilogue visitor trees cannot host a new contraction dim — the rejected vehicle for B·t | ker |
| — (NeurIPS 2024 proceedings, no arXiv) | LoRA-Inlaid | Quantized-base sharing across LoRAs; compute stays in SGMV kernels | ker |
| github: nunchaku-tech/nunchaku + deepcompressor | SVDQuant engine | The fusion design template: Aᵀx into activation-quantize, B·t into GEMM epilogue (50%→5–10%); no GEMV variant — our re-derivation is the gap | ker, fp4, sur, frh |
| github: NVlabs/EoRA + ModelCloud/GPTQModel | EoRA code + productization | The only fused quantized-GEMV+LR in public code (inside GPTQModel); ships as standard LoRA adapter, unfused in the HF/vLLM path — "the branch NVIDIA parked" | ker, fp4, sur |
| github: turboderp-org/exllamav3 | EXL3 fused GEMV | Cooperative-launch pre-transform→GEMV→post-transform in ONE kernel — production proof of our Phase-3 shape | ker |
| github: turboderp-org/exllamav2 | EXL2 q_gemm | SIMT small-m path, no epilogue hooks — surveyed, not a model | ker |
| github: vLLM (lora/ops/triton_ops, PR #13096, marlin/machete csrc) | vLLM LoRA + quant kernels | Shrink/expand replaced SGMV for CUDA-graph capturability; stacked-QKV single calls; no fusion into quantized GEMM anywhere | ker |
| github: punica-ai/punica · S-LoRA/S-LoRA · predibase/lorax | serving-kernel repos | Kernel sources for the SGMV/BGMV family — batch-throughput machinery, useless at batch 1 | ker |
| github: HazyResearch/Megakernels + "No Bubbles" blog | Hazy megakernel | ~1.3 µs/kernel floor under graph replay; 78% HBM bandwidth — defines our win condition | ker |
| github: mirage-project/mirage · aoli-al/HFuse | megakernel/fusion repos | Reference implementations | ker |
| github: Zishan-Shao/FlashSVD | FlashSVD repo | Reference for streaming factorized kernels | ker |
| NVIDIA blog: llama.cpp CUDA graphs (+ llama.cpp #6763) | vendor doc | Batch-1 graph replay integration we build on; max ~1.2× H100 | ker |
| llama.cpp issue #19217 | LoRA breaks CUDA-graph reuse | Action item: our −33% may partly be silent graph fallback; also motivates capture-stable fused path | ker, up |
| Colfax EVT deep-dive · Red Hat Marlin/Machete articles · cuBLASDx docs · SGLang PR #15512 | vendor/web docs | Kernel-engineering background; SGLang = only LoRA side-stream overlap precedent (weights, not compute) | ker |
| llama.cpp source @ 0ef6e55ed | mmvq.cu / quantize.cu / ggml-cuda.cu / llama-graph.cpp / llama-adapter.cpp / gguf-py | Ground truth for the integration map: `build_lora_mm` window, `has_fusion` epilogue hook, quantize_q8_1 as Aᵀx host, adapter format/shape contracts, dequant/quantize coverage | ker, int, gguf |

## 6. FP4 formats

| Source | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2509.25149 | Pretraining LLMs with NVFP4 (NVIDIA, 2025) | Format-definition paper (E2M1, blocks of 16, FP8-E4M3 + FP32 two-level scales, 4.5 bpw) | fp4 |
| 2505.19115 | FP4 All the Way (2025) | Fully quantized FP4 training — format context | fp4 |
| 2512.02010 | Four Over Six (2025) | Adaptive block scaling for NVFP4 — the scale-metrology lever llama.cpp #25153 is landing; format-side twin of our law #4 | fp4 |
| 2509.23202 | Bridging the Gap for Microscaling FP4 (ISTA; ICLR 2026) | The composition theorem: NVFP4's group-16 neutralizes outlier mitigation → FP4 residual is outlier-poor, covariance-structured — predicts OUR whitened correction is the right species for it (and SVDQuant-style outlier-eaters starve) | fp4 |
| 2603.08747 | Diagnosing FP4 inference (2026) | Per-layer/block FP4 sensitivity heterogeneity — the phenomenon our measured-sensitivity allocation exploits | fp4 |
| 2601.14277 | Which Quantization Should I Use? (2026) | llama.cpp unified eval, K-quants only — confirms no peer-reviewed NVFP4-vs-K-quant head-to-head exists; #23853 community numbers are best available | fp4 |
| 2602.11287 + 2604.08826 | HiFloat4 (+ Ascend pretraining) (Huawei, 2026) | Spectral anisotropy as THE low-bit-training obstacle, treated with spectral decomposition — low-rank thinking on the training side | fp4 |
| 2603.10444 | Mean bias in FP4 training (2026) | FP4 error anatomy — context | fp4 |
| 2605.20402 | Decomposing MXFP4 quantization error (2026) | Reducible bias + recoverable deadzone + irreducible floor — clean error taxonomy worth citing | fp4 |
| 2606.15652 | MosaicQuant (2026) | Inlier–outlier disaggregation for unified 4-bit LLM quant — LLM-side SVDQuant descendant | fp4 |
| 2606.26587 | SharQ (2026) | Activation sparsity + FP4 — same lane | fp4 |
| 2604.17789 | DuQuant++ (2026) | Fine-grained rotation for microscaling FP4 — rotation lane, not ours | fp4 |
| 2606.07618 | ScaleSweep (2026) | NVFP4 block-scale optimization — NVIDIA's scale-metrology lane | fp4 |
| 2605.12245 | SOAR (2026) | Same lane | fp4 |
| 2601.20088 | QAD — Quantization-Aware Distillation for NVFP4 (NVIDIA, 2026) | NVIDIA's actual accuracy-recovery route = retraining; contrast anchor: our training-free fc-on-NVFP4 (E16, −22% KLD) is unpublished *for LLM PTQ error correction* (qualifier load-bearing — SVDQuant/Nunchaku ship low-rank-on-NVFP4 for diffusion; sweep unfinished, review F12) | fp4 |
| NVIDIA blog "Introducing NVFP4" | vendor doc | 4.5-bpw accounting reference | fp4 |
| vLLM llm-compressor W4A4-FP4 docs | vendor doc | NVIDIA PTQ recipe lane | fp4 |
| llama.cpp discussion #23853 | advanced-gguf-quantizer equal-bpw ledger | Community NVFP4-vs-Q4_K_M numbers (KLD 0.045 vs 0.022) — the baseline our E16 composition is judged against | fp4 |
| llama.cpp NVFP4 PR stream (#19769, #25153, #24481, #25730, #26311, #20644, #21074, #25419, #25430, #23961) | upstream code | Type landed + active NVIDIA optimization stream; imatrix-aware scale search (#25153) = upstream converging on Four-Over-Six by itself | fp4, int, up |
| Towards Data Science: "Boost 2-bit LLM accuracy with EoRA" | web | EoRA gains biggest at 2–3-bit — productization context | fp4 |

## 7. Functional rank & healing

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2312.17244 | LLM Surgeon (ICLR 2024) | Strongest training-free objective in print (K-FAC curvature, closed-form compensation) — still stalls at 1.25–1.4×; the calibration-week frontier | frh |
| 2606.23568 | SVD-Surgeon (2026) | Closed-form second-order rescale of retained singular values — portable post-fit for our factors (free-rider heal stack) | frh |
| 2607.03057 | LACE-SVD (2026) | 2026 SOTA closed-form loss-aware factorization: ppl 32.6 at 1.67× on 7B — the sharpest number proving the pure-factorization wall stands (E01 confirmed) | frh |
| 2601.07197 | Fisher-aligned subspace diagnostics (2026) | Fisher concentrates where activation second-moments miss — candidate metric upgrade (law #4) | frh |
| 2510.05544 + 2607.17568 | Pareto-guided low-rank · CoCurve | 2026 refinements (cross-layer error, curvature allocation) — constants, not scaling escapes | frh |
| 2602.03051 | SAES-SVD (2026) | Closed-form suppression of cross-layer accumulated error — attacks the failure mode of any per-tensor pipeline (ours included) | sur, frh |
| 2312.11983 | FLAP (AAAI 2024) | Zero-training bias recompute from calibration baselines — free heal we can fold into any factor fit | frh |
| 2605.15491 | Ghosted Layers (2026) | Closed-form linear patch for removed layers — heal-stack component | frh |
| 2505.02819 | ReplaceMe (2025) | Closed-form transform replacing pruned blocks — heal-stack context | frh |
| 2401.15024 | SliceGPT (2024) | 25%-sliced 70B → 99% zero-shot after tiny RFT; Alpaca-RFT ≫ WikiText-RFT — recovery is distribution-matching, feeds our task-conditioned-factorization escape route | frh |
| 2305.11627 | LLM-Pruner (2023) | ~50k-sample LoRA heal → 95% on easy suites — tiny-heal ceiling datapoint | frh |
| 2502.07864 | TransMLA (NeurIPS 2025) | GQA→MLA SVD conversion, 93% KV cut — the only ≥2×-adjacent factorization with a first-class serving story, but 6B-token recovery (outside home-rig budget) | frh, kv |
| 2310.06694 | Sheared-LLaMA (2023) | ≥2× requires 50B continued-pretraining tokens — the honest price list | frh |
| 2407.14679 | Minitron (NVIDIA, 2024) | ~94B distillation tokens for 15B→8B/4B — same price list | frh |
| 2411.19146 | NVIDIA Puzzle (2024) | ~45B distill tokens for Nemotron-51B — same | frh |
| 2310.02277 | Junk-DNA hypothesis (2023) | Hard-task damage from pruning is monotonic and IRREVERSIBLE under fine-tuning — kills tiny-heal for our max-intelligence use case | frh |
| 2606.03328 | Calibration-mix study (2026) | Single-source heals overfit the easy source — calibration-diversity requirement for our pipeline | frh |
| 1906.04721 | DFQ (2019) | Data-free bias correction — the standard free mean-shift fix | frh |
| 2408.09632 | MoDeGPT (ICLR 2025) | Best prune+factorize citizen: 25–30% at 90–95% zero-shot, hours, no backprop — ceiling is 30%, not 50% | sur, frh |
| 2404.09695 | LoRAP (2024) | Attention → low-rank, FFN → channel pruning (FFN resists factorization — matches our E01 kind table) | frh |
| 2510.26446 | SSLC (2025) | Joint sparse+LR with second-order reconstruction — lane cousin | frh |
| — (COLING 2025, no ID) | CFSP | 20–35% band prune — **no arXiv ID recorded** | frh |
| 2412.18110 | SlimGPT (2024) | Same band | frh |
| 2402.09025 | SLEB (2024) | Same band | frh |
| 2606.07819 | Joint prune+quant (2026) | Same band | frh |
| 2312.13558 | LASER (ICLR 2024) | Truncating selected late-layer MLPs IMPROVES factual accuracy — sharpest "deep spectral tail stores noise" statement; per-task, per-matrix only | frh |
| 2310.15213 | Function vectors (Todd, 2023) | A single residual vector carries an ICL task — functional rank is tiny per behavior | frh |
| 2310.15916 | Task vectors (Hendel, 2023) | Same finding, independent | frh |
| 2406.11717 | Refusal direction (Arditi, 2024) | One direction mediates refusal — behavior-level low-rank evidence | frh |
| 2502.17420 + 2603.27518 + 2607.02396 | Refusal-cone corrections (Wollschläger et al., 2025–26) | The correction: it's a multi-dimensional cone and single-direction ablation fails ≥8B — even "one behavior one direction" inflates with scale | frh |
| 2505.14808 | ICL subspace geometry (2025) | Task representations = unions of low-dim subspaces; angles govern generalization — grounds the task-conditioned collapse hypothesis (E14) | frh |

## 8. Physics-inspired (RG / RMT / information geometry / tensor networks)

| arXiv | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| math/0403022 | BBP phase transition (Baik–Ben Arous–Péché, 2004) | Below-threshold spikes are information-theoretically invisible to spectral methods — the plateau's autopsy: rank spent below the bulk edge fits noise | phy |
| 2301.05331 | Spiked-model detection thresholds (2023) | Low-degree hardness: no efficient algorithm recovers sub-threshold spikes — strengthens the ceiling | phy |
| 1810.01075 + 1901.08276 | Martin–Mahoney heavy-tailed self-regularization | Trained LLM weight spectra are heavy-tailed power laws, not MP — use heavy-tailed bulk edges, not Gaussian, in our E-RMT audit (weightwatcher tooling) | phy |
| 2002.06716 | Predicting quality from spectra (2020) | Spectral diagnostics of trained nets — audit tooling lineage | phy |
| 2507.17912 | SETOL (2025) | Semi-empirical theory of learning — heavy-tail context | phy |
| 2411.01974 | Extensive-rank denoising beyond rotational invariance (2024) | Every whitened-SVD-truncation method (ours included) is a rotation-invariant estimator with a computable optimal error; beating it REQUIRES non-invariant structure (rows/cols/sparsity) — the theorem behind our sparse/block escape lanes | phy |
| 2605.03634 | Free decompression (2026) | Free-probability machinery for spectra through products — analysis toolkit | phy |
| 1912.00827 | RMT for mixtures of nonlinearities (2019) | Spectra through nonlinearities — toolkit | phy |
| — (no arXiv) | Pennington–Worah, Nonlinear RMT for deep learning (NeurIPS 2017) | Free-probability foundations — cite from proceedings | phy |
| 2207.00112 / 2505.17974 | FWSVD / GFWSVD (Kronecker Fisher) | Lane-c metric upgrade: two-sided K-FAC-style Fisher whitening of the residual — the E-IG experiment's citations (see also §2) | phy |
| 1410.3831 | Mehta–Schwab: variational RG ↔ RBMs (2014) | RG–deep-learning mapping — inspiration lineage, not an algorithm | phy |
| 1906.05212 | Is deep learning an RG flow? (2019) | Same lineage | phy |
| 2510.25553 | RG for DNNs (2025) | RG by marginalizing low principal components; links universality to compression | phy |
| 1903.00804 | Neural-network RG holographic mapping (2019) | The legitimate half of the holography meme (RG depth = radial dimension) — quarantines lane (e) | phy |
| 2103.05363 | MWQ multiscale wavelet quantization (2021) | Wavelet-subband quantization precedent for scale-separated treatment | phy |
| 2512.00862 | HBLLM: Haar-wavelet 1-bit PTQ (2025) | 13B ppl 6.71 at 1.08 bpw — real low-bit evidence that scale-separated beats flat when bits are scarce; the lane-b (multiscale correction) anchor | phy |
| 2409.12924 | WaveletGPT (2024) | Architecture-side multiscale — completeness only | phy |
| 1602.02244 | FMM as hierarchical low-rank (2016) | H/H²/HODLR: "coarse global, fine local" is literally block-GEMM-servable — lane-b structure | phy |
| 1807.01883 + 1808.02376 | Multiscale NNs on H-matrix bases (2018) | NN-shaped H-matrices exist — weak but real precedent | phy |
| 2409.07028 | Error-bounded H-matrix NN compression (2024) | PINN-scale only — weak evidence, noted | phy |
| 1711.03357 | MERA compact NNs (2017) | 14,000× layer compression — but trained from scratch; kills transfer to PTQ residuals | phy |
| 1912.10572 | MERA ↔ MPO in scale space (2019) | Companion math for lane (a) | phy |
| 1802.08313 + 2511.22522 | Deep learning ↔ AdS/CFT (I & II) | The arrow points the other way (NNs solve holographic inverse problems) — lane (e) kill-list citations so we never relitigate | phy |
| 2602.22345 | RMT frames for transformer weights (**soft ID**, "Research Square lineage") | FFN tracks MP, attention deviates with Tracy–Widom edges — consistent with our per-kind E05 map; verify ID | wa |

## 9. KV cache & serving budgets

| Source | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2604.11501 | Quantization Dominates Rank Reduction for KV-Cache (2026) | The decisive negative: at equal bytes INT4 beats rank reduction by 4–364 ppl — kills low-rank KV for us before runtime concerns even start | kv |
| 2407.21118 | Palu (ICLR 2025) | 50% KV via latent low-rank — the KV-side counterpart we evaluated and declined (loses to quant at our byte counts; no llama.cpp path). MIT | kv, sur |
| 2505.24357 | ReCalKV (2025) (+ STAR-KV / OjaKV / DynaCalKV — **no IDs recorded**) | Palu successor family — context | kv |
| 2405.04434 | DeepSeek-V2 MLA (2024) | Native latent-KV attention — proof mainstream stacks reward factorization when the ARCHITECTURE declares it; llama.cpp-supported | kv |
| 2502.14837 | MHA2MLA (2025) | −92% KV with 0.3–0.6% pretrain data — conversion cost datapoint | kv |
| 2503.11132 | X-EcoMLA (2025) | Distilled MLA upcycling, 3.4B tokens — same | kv |
| 2503.18893 | xKV (2025) | Cross-layer SVD of KV, +3× even on MLA — context | kv |
| 2408.05646 + 2410.03111 + 2410.14731 | EigenAttention · LoRC-KV · MatryoshkaKV | 40–75% KV methods that degrade or need distillation — context | kv |
| 2401.18079 | KVQuant (2024) | Per-channel pre-RoPE K is the dominant 4-bit error source — the channel analysis our KV-imatrix tensor-edit builds on | kv |
| 2402.02750 | KIVI (2024) | K per-channel / V per-token asymmetry — unanimous with our q8_0-K + q4_0-V recommendation | kv |
| 2605.17757 | OSCAR (Together, 2026) | Attention-aware Gram matrices → eigenrotations; V-side absorbable, K-side needs a kernel — nearest published cousin of our fusability analysis | kv |
| 2501.16383 | RotateKV (2025) | Calibrated reorder + grouped FWHT at 2-bit — partial fusability | kv |
| 2606.24033 | Block-GTQ (2026) | Per-RoPE-frequency-block bit allocation — component validated separately for our package | kv |
| — (no IDs) | SVDq / KVLinC / CQ / AQUA-KV | 1.25–2.5-bit KV exotica — **IDs not recorded; resolve if cited** | kv |
| 2507.06457 | Hybrid-ratio sweep (2025) | 72-model sweep: recall collapses only above ~6:1 linear:full — our 3:1 arch is in the sweet spot | kv |
| 2510.26692 | Kimi Linear (2025) | 3:1–7:1 hybrid attention — same conclusion | kv |
| 2501.08313 | MiniMax-01 (2025) | Same | kv |
| 2309.06180 | PagedAttention / vLLM (2023) | The 65/30/5 datacenter budget decomposition that does NOT transfer to batch-1 consumer serving — our gap to fill with measured numbers | kv |
| 2402.16363 | LLM-Viewer (2024) | Budget-analysis tooling — context | kv |
| 2506.15155 | eLLM (2025) | KV 2.5–5× weights at datacenter batch — the contrast to our weights-dominated regime | kv |
| llama.cpp PRs #12801, #13306, #16095, #26185; discussions #5932, #20969, #23470; issue #21385 | KV/MLA upstream surface | MLA latent cache + CUDA FA; JohannesGaessler's measured KV-quant deltas (K more sensitive than V); calibrated per-head KV closed unplanned — no imatrix-for-KV ships anywhere | kv |

## 10. Metrics

| Source | Name (venue/year) | Role for our paper | Cited in |
|---|---|---|---|
| 2407.09141 | Accuracy is Not All You Need (Microsoft; NeurIPS 2024) | KLD correlates with answer flips at Spearman 0.981 — the justification for our mean-KLD + top-1 metric stack | out |
| 2604.13440 | A KL Lens on Quantization (2026) | Forward-only per-layer KL sensitivity beats SQNR (τ 0.79 vs 0.76) — supports KL-guided allocation over energy | out |
| 2606.19558 | Displacement Is Not Direction (2026) | KLD↔benchmark correlation collapses in the near-baseline "silent zone" — calibration limit: don't over-read small KLD deltas once corrected-Q2 nears Q3_K_M | out |
| llama.cpp discussion #4110 | ikawrakow: PPL vs KLD for quant comparison | The upstream maintainers' preferred lens — our PRs and paper lead with KLD | out, up |

## 11. llama.cpp upstream, tooling, and process ground truth

| Source | What it is | Role for our paper | Cited in |
|---|---|---|---|
| llama.cpp discussion #8831 | "Using LQER to improve low-bit quants" (compilade, 2024-08) | THE upstream prior-art thread our deployment story revives: adapter-from-GGUF-pair, embedded-adapter metadata, imatrix ≈ L²QER's S — all still unbuilt; contains jukofyork's plain-SVD-at-4-bit negative result our whitened-2–3-bpw claim must answer head-on | up |
| ik_llama.cpp discussion #15 | ikawrakow: "Will LQER improve k- and i-quants?" (2024-08) | The on-record prediction ("no") against weak-baseline LQER — our answer post-E27: byte-fair MIXED+fc = paired PARITY with Q3_K_S (not ">"; the legacy margin flipped sign), the skeptic mostly right about correction-vs-ladder, wrong about the frozen grid's codes (free 15-34%). [row corrected 2026-08-04 — it still sold the retracted ">" result; round-2 paper review S5] | up |
| ik_llama.cpp discussion #2213 | trellis-vs-IQ4_KS (2026-07) | "Advantage of fancy machinery decreases with bpw" — independently tells us to aim at 2–3 bpw (matches our law #5) | up |
| llama.cpp PR #8332 | LoRA adapter refactor (ngxson, merged 2024-07) | The runtime already computes y = Q·x + s·B·(A·x) on any quant base — our zero-change serving path | up, int |
| llama.cpp PRs #11446 → #12801 | DeepSeek MLA (merged 2025-04) | The merged low-rank-serving precedent AND the PR playbook (months of public iteration, benchmarks, ownership) | up |
| llama.cpp PR #8151 · #15091 · #19375 | ternary quants · gpt-oss MXFP4 · Qwen3Next | Merge patterns: types land when a model/vendor ships them — why we ship adapters, not a new ggml_type | up |
| llama.cpp discussion #15400 | XQUANT (ggerganov reply) | Maintainer posture calibration: paper-driven memory tricks are "sandbox-first, master-later" | up |
| Issue #20977 + discussion #20969 + ik issue #1509 | TurboQuant affair (2026-03) | Process cautionary: unintegrated kernels + MSE numbers = rejected; integrated KLD + tok/s vs incumbent = the only currency | up, kv |
| CONTRIBUTING.md + AGENTS.md | contribution rules | KLD evidence bar (official), no third-party deps (no LAPACK — SVD stays in Python tooling), issue-before-PR, strict AI-disclosure policy | up, int |
| PR #25662 "Infcore" | bot-closed in 2 minutes | The machine gate in front of human review — hygiene requirement | up |
| LoRA-surface issue set: #26207, #18050/#18466/#18193, #16555/#16475, #19217/#21552, #13485, #6536/#15212/#11031, #9065, #10671/#17447/#15890, convert breakage tail (#24101, #23047, #21125, #21864, #20058) | adapter-subsystem terrain | Known bugs/limits we inherit or dodge (static always-on adapter avoids most); #13485 = JohannesGaessler's stake in the LoRA path (CUDA reviewer map) | up |
| Issues #4611, #4176, #956 | early low-rank musings; tall-skinny GEMM pathology | Field-was-empty evidence + the old performance concern our fused kernel answers | up |
| mergekit PR #333 (arcee-ai) / thomasgauthier/LoRD | LoRA extraction tooling | jukofyork's extraction lineage — adjacent tooling prior art | up |
| gguf-py + llama-quantize/imatrix/perplexity toolchain @ 0ef6e55ed | tooling recon | Ground truth our pipeline stands on: dequant coverage, Python-quantize gaps (K/IQ need C++), adapter shape contracts, alpha/scale semantics, IQ-requires-imatrix rules | gguf, int |
| weightwatcher (Martin–Mahoney tooling) | spectral-audit tool | Off-the-shelf ESD/power-law fits for our E-RMT per-tensor audit | phy |
| github repo licenses (verified in-report): QERA Apache-2.0 · SVD-LLM Apache-2.0 · deepcompressor Apache-2.0 · LoftQ MIT · LQ-LoRA MIT · matrix-compressor MIT · ASVD MIT · Dobi-SVD MIT · Palu MIT · SERQ CC BY 4.0 · ARHQ CC BY-SA 4.0 · EoRA NC (see flags) · CALDERA null · LQER none | licensing snapshot | Governs what we may implement from vs read-only (QERA math is the clean-room source) | qec, wa, sur |

---

## Verification flags (carry into the citation pass; resolve before camera-ready)

1. **QERA (2410.06040):** calibration sample count unverified (appendix truncated; likely 32×2048 SlimPajama). HTML "Llama-3.1-80B" = 70B.
2. **LQER (2402.02446):** scale formula Eq. 13–14 extracted from an appendix paraphrase — check PDF before reimplementing/citing exactly.
3. **CALDERA (2405.18886):** calibration details ("256–512 RedPajama samples") sub-extracted; T_out/T_in defaults in appendix/code; repo license null (all-rights-reserved).
4. **EoRA (2410.21271):** license read twice with two answers (CC BY-NC-SA 4.0 vs NVIDIA Source Code License-NC) — both non-commercial; venue "ICLR 2026 workshops" is soft.
5. **GPTQ-intrinsic LoRA (2606.01412):** experimental tables (Qwen3) did not survive HTML extraction — pull PDF for any number.
6. **ProjQ (2606.00494):** 2-bit numbers conflict across extracts (Wiki2 22.42 vs 8.17 — likely different tables/settings) — pull PDF.
7. **ResComp (2604.07955):** extract lossy; local PDF saved (path in arxiv-codesign-mining.md Part 2 table) — read before citing.
8. **IR-QLoRA (2402.05445):** arXiv ID not re-verified.
9. **LoftQ / LQ-LoRA ICLR-2024 venues:** from common knowledge, not re-verified; LoftQ 2-bit numbers are post-fine-tune — never compare against PTQ perplexities.
10. **LASER name collision:** 2312.13558 (ICLR 2024, truncation-improves-accuracy) vs 2606.00573 (allocation follow-up in math-methods) are different works sharing an acronym — verify 2606.00573's actual title.
11. **Optimal shrinkage ID split:** wa cites 1311.0851, phy cites 1405.7511 for the same "shrink, don't truncate" upgrade — 1405.7511 is the canonical Gavish–Donoho shrinkage paper; confirm what 1311.0851 is before citing it.
12. **Soft/missing IDs:** calibration-scaling study ("2602.07465-adjacent"); RMT-frames "2602.22345 lineage"; Saten (EMNLP 2025); MoE-SVD (2025); CFSP (COLING 2025); STAR-KV/OjaKV/DynaCalKV; SVDq/KVLinC/CQ/AQUA-KV; outlier-geography trio titles (2506.01967/2309.15531/2404.03605); Pennington–Worah (no arXiv — cite proceedings); LoRA-Inlaid (proceedings URL only); EVT (DOI only).
13. **Semantic Scholar indexing lag** noted for EoRA (7 citations shown); LQER/QERA citation lists verified genuine.
14. **All llama.cpp file/line references** are pinned to commit `0ef6e55ed` (2026-08-03); re-pin before quoting in the paper.
15. **Draft load-bearing + still flagged (round-2 paper review C1, 2026-08-04):** the draft now cites 2606.01412 (Bid-Up monotonicity, §2.3 — flag 5), 2606.00494 (ProjQ, §2.4/§6.3 — flag 6), and 2604.07955 (ResComp, §4.4 — flag 7) in load-bearing positions; the three PDF pulls above are SUBMISSION BLOCKERS, not camera-ready nice-to-haves. RILQ (2412.01129), SVDQuant (2411.05007), SVD-LLM (2403.07378) were added to the draft 2026-08-04 (§1.2/§4.1, §5-template via existing cites, §2.2) closing the load-bearing-dozen gap.

---

## Counts

- **299 bibliography rows** across the eleven theme tables (measured; grouped families and ruled-out sets counted as single rows).
- **292 unique arXiv IDs referenced** (measured by extraction; includes the 13 explicitly-ruled-out IDs, grouped one-liner families, and IDs cited inside another entry's role line).
- **~35 non-arXiv sources** (GitHub threads/PRs/issues, vendor docs, blogs, DOI/proceedings, tooling), with the llama.cpp/ik_llama.cpp surface grouped where the reports grouped them.
- Every entry above lists the prior-art report(s) that cite it; the deep extraction (exact tables, algorithms, licenses) lives in those reports.

## The load-bearing dozen (the intro MUST cite)

1. **LQER / L²QER — 2402.02446** · the published statement of our premise (MP residual ⇒ SVD capture ≈ r/d) and the diagonal scale that IS imatrix whitening.
2. **QERA — 2410.06040** · the closed-form optimum; our pipeline is literally QERA-approx → QERA-exact; the clean-room (Apache-2.0) math source.
3. **CALDERA — 2405.18886** · Thm 4.1's (1−k/2n)² bound — capture-law triangle leg 1 — plus the 2-bit joint-optimization reference and the unfused-serving warning.
4. **GPTQ-intrinsic LoRA — 2606.01412** · triangle leg 2: information-theoretic lower bound forcing rank ∝ width.
5. **RILQ — 2412.01129** · triangle leg 3 / the boundary diagnosis: 2-bit error is high-rank, layer-local correction saturates; cross-layer is the escape.
6. **ZeroQuant-V2 / LoRC — 2303.08302** · origin of the lineage; INT8-factors-free licenses our Q8_0 factors.
7. **EoRA — 2410.21271** · closest industry neighbor: NVIDIA's parked training-free lane, LoRA-shaped serving, the one fused-GEMV precedent.
8. **SVDQuant — 2411.05007** · engineered concentration (why rank-32 works for them and can't for generic residuals) + the fusion cost template (50%→5–10%).
9. **GPTQ — 2210.17323** · the Gram objective and calibration/damping conventions everything (including llama-imatrix) descends from.
10. **SVD-LLM — 2403.07378** · the Cholesky-whitening theorem at LLM scale and the pure-SVD ladder proving factorization-of-W alone is dead.
11. **Accuracy is Not All You Need — 2407.09141** · KLD-tracks-flips (Spearman 0.981) — the license for our metric stack (paired with llama.cpp #4110, the maintainers' currency).
12. **llama.cpp Discussion #8831 (+ ik_llama.cpp #15)** · the upstream prior-art thread and the on-record skeptic's prediction our measurements answer — the deployment story's anchor.

Runners-up (cite in §2/§3, not necessarily the intro): 2212.09720 (k-bit scaling — the 2–3 bpw target zone), 2412.07902 + 2412.14363 (rank ∝ d empirics), 2601.05684 (FLRQ — allocation beats uniform rank), 2606.03465 (tensor-decomposition negative), 2509.23202 (FP4 outlier-neutralization — why fc composes with NVFP4).
