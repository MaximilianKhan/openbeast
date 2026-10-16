# 7. Limitations and open work

## 7.1 Limitations

**Models and hardware.** Five of the six models are Qwen-family: Qwen3-0.6B, Qwen3.5-0.8B, Qwen3.6-27B (as the heretic-v2 finetune), Qwen3.8-27B and Qwen3.6-35B-A3B. Llama-3.1-8B is the one model outside the family; on it the re-rounding result and the I-quant comparison replicate, and the correction does better against the adjacent K-quant than on Qwen (§3.1, §4.3). One non-Qwen model does not establish generality across families. Every measurement comes from one GPU (an RTX 5090) on one workstation.

**Baselines not compared.** The re-rounder is compared with the file it starts from, with stock rungs above and below it, and with a free-grid GPTQ baseline at identical format and bytes (§3.4). It is not compared with learned-rounding methods, which need a training loop, or with vector and lattice quantizers at 2 bits, which are outside the GGUF formats. The type-allocation controls were built by a fixed promotion rule and were not compared with Hessian-aware mixed-precision allocation methods. These are named as classes of work; we did not run them and do not cite numbers for them.

**Distribution.** Calibration is on wikitext-2 train unless stated. The comparisons of §3.1 (at 0.8B, 8B and 27B) and §4.3 are also scored on FineWeb-Edu and a code corpus that no calibration touched; §4.5 and §5.3 have FineWeb-Edu only; the 2,048-token comparison of §5.4, the 0.6B rows and the mixture-of-experts table have no held-out corpus. The held-out scoring changed two conclusions: the 27B margin over Q3_K_S (§4.3) and the generality of re-rounding (§3.1). Two corpora are not a benchmark of distribution shift; both held-out corpora are English text or code from public sources, and the code corpus is small (400 KB).

**Statistics.** The t statistics quantify evaluation-chunk variance on one corpus at a time. Calibration-sampling variance was measured once, at 0.8B, and is small against the effects reported (§3.1); it was not measured at 27B. No family-wise correction is applied; the paired tables carry enough contrasts that margins near |t| = 2-3 are suggestive. The wikitext 27B comparison is a reproduction on the same 100 chunks, not an independent sample; the held-out corpora are the independent samples. Blocks in the block bootstrap are runs of consecutive chunks and not articles.

**The instrument.** KL divergence to the reference measures fidelity to it. That is the right measure for a method that only approximates the reference, and it does not rank a trained artifact against an untrained one (§5.4). Task-level evidence remains limited: one multiple-choice benchmark at one byte count (§4.3), and one coding suite on three IQ3-class files (§5.5). None of our re-rounded files was run on the coding suite.

**Controls.** Mixed-type controls now exist at 0.8B (one by the registered rule, one byte-matched after registration), at 27B and on the mixture-of-experts model; each is a single designed point, built by a rule and not optimized. None was built at 0.6B.

**Width.** The regression of §4.2 has one step on the width axis and its intervals resample tensors, not models. Llama-3.1-8B (d = 4,096) was not added to it, because it would change family and width at once.

**References and precision.** Rows labeled legacy are scored against a Q6_K or Q8_0 file and not BF16 (measured contamination at 27B: 0.0032 KLD). The mixture-of-experts table is scored against a vendor Q4-class file at 20 chunks. At 27B, "full covariance" means block-diagonal for ffn_down (§2.2), the VRAM columns were not measured, and the rank allocation used sensitivity priors from 0.6B. Rows from before the Gated-DeltaNet normalization fix in llama.cpp (b10829) are paired within one build and never tabled beside current-build rows; every hybrid-architecture comparison in §3.1, §4.3, §4.5 and §5.4 was re-scored on the current build.

**Alternation.** The ordering claim of §3.3 is paired (t = +17.6). The claim about alternation rounds is weaker: the 0.6B sequence is unpaired, the 0.8B continuation has no recorded error bars, and the 27B gain from alternation is about one unpaired standard error.

**Speed.** One GPU, one host. Session-to-session drift reached 9%; only one speed result has raw output in the supplementary material; nothing was re-measured after the patch set was rebased (§6.1).

**Verification rests on our own records.** Statements that a derivation was verified numerically, that a capture passed its gates, or that an experiment was registered before it ran cite our review notes and experiment log. Those are our records and have not been checked by anyone else. The registration for the experiments added in this revision was committed to a public version-control history before the runs, which gives it an external timestamp.

**Code hazards.** The per-tensor caches of the re-rounder, the extractors and the spectra tools are keyed by tensor name only and carry no provenance fingerprint; in this revision every run used a fresh cache directory per arm, and the Llama-3.1-8B pipeline also stamped each cache with its inputs; the tools themselves are unchanged. Column selection under Gram whitening ranks the wrong columns (the reported column runs predate full-covariance whitening) [source: research/lowrank/review/adversarial-code.md].

**How the work was done.** Experiments, analysis and drafting were carried out by AI research agents under the author's direction, on one workstation. The author is responsible for the claims; errors found in the record and their corrections are listed in §7.6.

## 7.2 Status of the ablation program

An audit of every cell behind this paper's claims counted 51: 17 clean, 15 confounded, 19 missing, and priced seventeen runs to close the gating cells [source: research/lowrank/paper/ABLATION-PLAN.md]. All seventeen have now been run except the 0.6B half of the small-scale controls, and a referee's further requests have been run as well [source: research/lowrank/experiments/36-submission/PLAN.md]:

| item | where |
|---|---|
| calibration-sensitivity grid (five calibrations × two methods × three corpora) | §3.1, §4.8 |
| calibration-sampling bound | §3.1 |
| mixed-type controls at 0.8B | §4.3 |
| held-out pass over the 27B ladder comparisons | §4.3 |
| recovery curve with the recipe fixed | §4.5 |
| equalization without an imatrix, and a strength sweep | Appendix A.3 |
| 27B re-rounder under single-step provenance, with IQ2_M and IQ3_XXS brackets | §3.1 |
| KL divergence at a 2,048-token context for the head-to-head files | §5.4 |
| free-grid GPTQ baseline for the re-rounder | §3.4 |
| one model outside the Qwen family (Llama-3.1-8B) | §3.1, §4.3 |
| three capability runs per IQ3-class arm, and our corrected arm | §5.5 [PENDING:F] |

Two diagnostics were added after the 27B re-round failed its registered test, each registered before it ran: the share of the file's KLD carried by the tensors the sweep covers, and the same sweep with the earlier Gram capture (§3.1).

Not run: a mixed-type control at 0.6B (the 0.6B rows are legacy); the mixture-of-experts table against a BF16 reference (none is available to us); learned-rounding and vector-quantizer baselines; a third width inside one family.

## 7.3 Reproducibility and artifacts

Method descriptions, scripts, raw logs, per-chunk outputs and result tables are in the supplementary material, one directory per experiment; Appendix B maps each tag used in this paper to its directory. The statistics added in revision are produced by one script from existing logs [source: research/lowrank/experiments/35-final-reanalysis/README.md]. Large binaries (reference logits, Gram captures, quantized models and adapters, about 160 GB) are not included. They can be rebuilt from the recorded recipes; some early ones were deleted to reclaim disk, so a number resting on a deleted artifact is reproducible only by rebuilding it. Two reproduction gates were run: one rebuilt the 0.8B base with 335/335 tensors byte-identical and re-derived the re-rounded file byte-identical, and one reproduced the published 0.8B numbers before any new comparison [source: research/lowrank/experiments/26-lloyd-gauge/results.txt].

Three components are not stock llama.cpp: the Gram-capture extension to llama-imatrix (about 140 lines, environment-gated), the llama-gradmatrix tool, and the fused-kernel and allocator patch set (numbers reported at a build that predates b10865; since rebased onto b10865). The re-rounder and the extractors are standalone Python against gguf-py. Serving needs none of the patches: a re-rounded file is a standard GGUF, and a corrected model is a standard GGUF plus a standard LoRA-form adapter. Producing either needs the Gram-capture patch and the source weights.

## 7.4 Open work

**Re-rounding at size.** Before more codecs (Q3_K, Q4_K, the I-quants, NVFP4; arXiv:2509.23202), the 8B and 27B failure needs an explanation: §3.1 shows the sweep optimizing its proxy while the outcome does not move. Candidate tests are a regularizer toward the original codes in directions the calibration does not excite, a metric estimated from far more calibration text, and an end-to-end objective in place of the layer-local one. Any packaged tool would need a held-out check before release, since a re-rounded file can be worse than the original on text its calibration missed [source: research/lowrank/paper/DEPLOYABLE-WINS.md].

**Estimators for two-sided metrics.** Following §3.2: damping the output-side factor toward the identity, multi-pass gradient captures, and an adjoint for the GDN layers to remove the gradient cut.

**Other directions.** Riemannian refinement of the closed-form factors and the Bregman-damped metric-modification scheme of arXiv:2507.09428 [source: research/lowrank/prior-art/MANIFOLD-CANDIDATES.md]; per-workload conditioned adapters (§4.8); a loader that aliases shared factors (§4.4); correction of the dense tensors of a mixture-of-experts model.

## 7.5 Summary

Re-rounding a Q2_K file's codes under an activation-covariance metric lowers its KL divergence by a third at 0.8B and gives no resolved gain at 8B and 27B, where it halves its own objective on tensors that carry most of the file's divergence and still damages held-out text; at every size where we scored held-out text, it damages kinds of text its calibration does not cover. Refitting the grid beats freezing it, and does not change that verdict at size. Low-rank correction improves its base on every corpus we scored, by less off the calibration distribution (0.25 to 0.96 of the wikitext gain) and by more the thinner the base. Against the stock types it falls short: an I-quant type at fewer bytes beats it at 8B and 27B on every corpus, its lead over the adjacent K-quant at 27B exists only on the calibration distribution (on Llama-3.1-8B it holds on both prose corpora), and a same-byte mixed-type control beats it on every corpus at 27B and by a factor of three at 0.8B. Three placements of the low-rank bytes could not be distinguished within a stated bound. Against one family of released trained checkpoints, KL divergence to the reference and likelihood of wikitext disagree in the same run, so for those files the comparison measures what each file optimizes rather than which is better; [PENDING:F — capability replication]. The kernel and allocator patches reduce the adapter's decode cost at small scale and change little at 27B. The most transferable results are methodological: held-out corpora reversed the 27B ladder margin and exposed the cross-domain damage of re-rounding; single-step provenance erased the earlier 27B re-rounding gain; same-byte controls outranked every corrected configuration on Qwen; and a surrogate that improved failed to predict the outcome seven times (§4.7).

## 7.6 Corrections made to the record

The project record contains errors that were found and corrected before this paper, and they are listed here so that a reader of the repository can find them.

- A first set of 27B comparisons, unpaired and against a requantized reference, showed a margin for the corrected configuration that reversed sign under single-step provenance (§4.3).
- A set of paired t statistics for the head-to-head of §5 was inflated by dividing per-chunk differences by a per-token standard error. They were recomputed with the canonical tool before any conclusion changed; the corrected values are the ones in §5.2.
- Two capability rows were retracted when 84-87% of their failures turned out to come from a crashed server.
- A first "mixed-calibration" run never reached its second corpus, because the two corpora were concatenated and not interleaved.
- An early statement that capture falls as r/d rested on two points; §4.2 replaces it with a fit in which r/d is the worst of six forms.
- A journal entry asserted that the GSQ-RCO checkpoints were trained on FineWeb-Edu; the card and code do not say so (§5.3).
- Every hybrid-architecture row in the previous version was scored on a llama.cpp build that predates its Gated-DeltaNet normalization fix. The paired comparisons were valid within that build; this version re-scores them on the current build and reports both where they differ (§4.3).
- The previous version stated that at 0.6B and 0.8B "controls beat crowns" by inference from interpolation between rungs; §4.3 now measures it at 0.8B.
- The previous version reported re-rounding at 27B as a 13.3% KL-divergence gain. That pair was legacy provenance; under single-step provenance the gain is 3.7% and unresolved, and held-out text is harmed (§3.1).

Each is kept in the journal with its correction [source: research/lowrank/JOURNAL.md; review/adversarial-claims.md].
