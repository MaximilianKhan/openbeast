# 6. Limitations and open work

## 6.1 Limitations

The campaign was reviewed adversarially twice — statistics, claims, code, and then the paper draft itself [source: research/lowrank/review/] — and the limitations that survive are the paper's boundary conditions.

**Distribution.** Calibration and most evaluation share wikitext-2 (train/test split only). The corrected configurations carry far more calibration-fitted capacity than the rungs they compete with, and §4.7 measures 10-27% corpus sensitivity, larger than every contested ladder margin. What we have off-distribution is: the re-rounder at 0.8B on a code corpus (worse than bare when calibrated on wikitext alone; better when code is in the calibration mix) and on an unseen prose corpus (better in both variants) (§3.1); and the correction increment at 27B on FineWeb-Edu, where it holds at three rungs (§4.3c). The ladder comparisons of §4.3 — flagship versus rung versus control — have not been repeated on a held-out corpus, and neither has the 27B re-rounder.

**The instrument.** Every KL-divergence number is measured at a 512-token context. §4.3c shows two artifacts of one model whose ordering under that instrument reverses on 2048-token perplexity and on an agentic coding suite. Within a single refinement family the instrument agreed with the one task evaluation that could resolve anything (HellaSwag, §4.3); across families it did not. Task-level evidence in this paper is thin: one multiple-choice benchmark at one byte point, and one coding suite, one run per arm, on the competitor's rung only. None of our own corrected or re-rounded artifacts was run on the coding suite.

**Controls.** The mixed-quant interpolation control exists at 27B and for the MoE, and was never built at 0.6B or 0.8B. This is the reason §4.3 claims wins-from-below at small scale rather than wins.

**Width.** The capture regression of §4.2 has one step on the width axis (1024 → 5120) and its confidence intervals resample tensors, not models.

**References and precision.** Rows labeled legacy are referenced to a Q6_K or Q8_0 file rather than BF16 (measured contamination at 27B: 0.0032 KLD). The 27B endgame is single-step from BF16 and paired at 100 chunks; the remaining 27B lever comparisons (measured allocation versus uniform rank, alternation rounds) are inside unpaired error bars, the 27B VRAM columns were not measured, "full covariance" at 27B means block-diagonal for ffn_down (§2.2), and the 27B rank allocation used sensitivity priors transferred from 0.6B.

**Evidence grade on alternation.** The ordering claim of §4.8 (never stack re-rounding under a corrector) is paired-resolved (t = +17.6). The claim about alternation *rounds* is weaker: the 0.6B arc is unpaired, the 0.8B continuation has no recorded error bars, and the 27B increment from alternation is about 1σ.

**Speed.** All speed numbers come from one GPU. Session-to-session drift reached 9%; kernel gates are tuned on two model sizes; nothing was re-measured after the patch set was rebased (§5.4).

**Code hazards, disclosed.** Cache directories originally carried no provenance fingerprint (silently wrong on resume; fixed for the re-rounder, open for the spectra caches and one extractor). Column selection under Gram whitening ranks the wrong columns (latent; the reported column runs predate full-covariance whitening). A naive MoE Gram accumulation would have been wrong and was caught before any reported capture [source: research/lowrank/review/adversarial-code.md].

**How the work was done.** Experiments, analysis and drafting were carried out by AI research agents under the author's direction, on one workstation. That is why the review rounds and the pre-registration rule exist, and it did not prevent errors: this record contains a set of paired t-statistics that were inflated by dividing per-chunk differences by a per-token standard error (caught and recomputed with the canonical tool before any conclusion changed), two capability rows that were retracted when 85% of their failures turned out to be a crashed server, and a first "mixed-calibration" run that never reached its second corpus. Each is kept in the journal with its correction.

## 6.2 Status of the ablation program

An audit of every lever-at-scale cell behind this paper's claims counted 51 cells: 17 clean, 15 confounded, 19 missing [source: research/lowrank/paper/ABLATION-PLAN.md]. The nulls are the cleanest cells (water-filling, shared basis, equalization, the Kronecker metric, MoE versus control — isolated, mostly paired, 2.7 to 60σ). The confounds concentrate in the positive multi-point curves: the recovery curve of §4.4 varies adapter budget and rank mix across its points, and the 27B allocation row varied three factors at once.

The audit priced a matrix of seventeen runs to close the gating cells. Eleven have been run and are reported above: the 100-chunk BF16 reference; the paired 27B re-round (§3.1); the single-step 27B rebuild with its control, at 20, 40 and 100 chunks (§4.3); the first task evaluation (§4.3); the capture-versus-width regression (§4.2); the third-corpus held-out test and the full-metric mixed-calibration run (§3.1); the clean-Gram Kronecker run (§3.2); and the GSQ-RCO head-to-head on two corpora with one capability pair (§4.3c). Six have not been run, and the corresponding claims are scoped accordingly in the text:

- the calibration-sensitivity grid (mix ratio × method), which would turn §3.1's rescue demonstration into an ablation;
- interpolation controls at the 0.6B and 0.8B byte points;
- a held-out pass over the corrected configurations and their ladder rivals at all scales;
- the recovery curve with the correction recipe held fixed;
- the equalization mechanism ablations (no imatrix; an alpha sweep);
- a bound on calibration-sampling variance alone.

Also open: the IQ2-rung capability pair and a capability run of our own arm (§4.3c); a single-slot run-to-run floor for the coding suite; a 27B re-round pair with single-step provenance; and a third model width.

## 6.3 Reproducibility and artifacts

Method descriptions, scripts, raw logs, per-chunk outputs and result tables for every experiment are in the project repository under `research/lowrank/experiments/`, one directory per experiment, and Appendix A maps each tag used in this paper to its directory. Large binaries — reference logits, Gram captures, quantized models and adapters, about 160 GB — are not in the repository. They are rebuildable from the recorded recipes, and some early ones were deleted to reclaim disk, so a number resting on a deleted artifact is reproducible only by rebuilding it. Re-runnability was measured twice: one reproduction gate rebuilt the 0.8B base 335/335 tensors byte-identical from the recorded recipe and re-derived the re-rounded artifact byte-identical, and another reproduced the 0.8B published numbers exactly before any new comparison was run.

Three components are not stock llama.cpp: the Gram-capture extension to llama-imatrix (about 140 lines, environment-gated), the llama-gradmatrix tool, and the fused-kernel and allocator patch set (reported numbers at build 0ef6e55ed; vendored rebased onto b10865). The re-rounder and all extractors are standalone Python against gguf-py. The served artifacts need none of the patches: a re-rounded file is a standard GGUF, and a corrected model is a standard GGUF plus a standard LoRA-form adapter.

## 6.4 Open lanes

**Codec coverage for the free lever.** The re-rounder speaks only Q2_K. Q3_K and Q4_K codecs would reach the promoted tensors that mute the 27B result, I-quant codecs would allow a like-for-like head-to-head with GSQ, and an NVFP4 codec (nearest-of-8 E2M1 on frozen FP8 scales) targets a format whose residual is covariance-structured (arXiv:2509.23202). The intended tool is `gguf-refine model.gguf`, shipping with a mixed-calibration default and a held-out validation gate as requirements: §3.1's measurement makes an ungated version of this tool a quality hazard [source: research/lowrank/paper/DEPLOYABLE-WINS.md].

**Estimator repair for two-sided metrics.** The ranked follow-ups from §3.2: damping or shrinkage of the output-side factor toward the identity (does the harm vanish smoothly?), multi-pass gradient captures, and a delta-net adjoint to remove the gradient cut. More coverage of the same single-pass estimator is ruled out by the clean-Gram run.

**Geometry.** Of the differential-geometry candidates we surveyed [source: research/lowrank/prior-art/MANIFOLD-CANDIDATES.md], the survivors are ProjQ-style metric surgery (already positive: 25.94 PPL one-shot at 0.6B) with its Bregman-damped variant (arXiv:2507.09428), Riemannian refinement of the closed-form factors on fixed-rank manifolds, and the information-geometry reading of the whitening ladder. The Kronecker global metric is measured anti-helpful at current estimator quality, and closed-form Fisher allocation with one-sided proxies is a confirmed trap.

**Product lanes.** Per-workload conditioned adapters (+10.3% on-task at equal bytes, selectable per request through the existing LoRA API); a sparse-A fused kernel (top correction directions live on 2-5% of input channels); the shared-A adapter (−16.6% adapter bytes at tied quality, §4.3b); correction of the dense tensors of an MoE and sub-2-bit expert carriers; and the upstream-shaped bundle — the re-rounder with its Gram-capture extension, the allocator fix, and the fused kernels — each useful on its own.

## 6.5 Closing statement

The ledger of this study: one lever that is free at fixed calibration breadth, paired-resolved at 0.8B and at 27B; a correction paradigm that is real, composable and convergence-certified, that beats the adjacent ladder rung at 27B and loses to a same-byte mixed-type control, and whose three low-rank forms are interchangeable at equal bytes; a kernel and allocator patch set that helps users who will never load an adapter; a head-to-head in which our instrument favored our arm and a task suite favored the competitor over its own baseline; and a protocol that would have prevented our first week of overclaims. The quantization ladder that ships in llama.cpp is close to its local optimum, and type allocation is the axis along which it can still be improved at equal bytes. The codes on its frozen grids are not at their optimum. That asymmetry — the walls around the corrections, the freedom inside the grids — is the finding.
