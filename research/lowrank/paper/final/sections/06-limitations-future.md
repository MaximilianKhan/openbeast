# 7. Limitations and open work

## 7.1 Limitations

**Models and hardware.** Every model is Qwen-family: Qwen3-0.6B, Qwen3.5-0.8B, Qwen3.6-27B (as the heretic-v2 finetune), Qwen3.8-27B and Qwen3.6-35B-A3B. Every measurement comes from one GPU (an RTX 5090) on one workstation. Nothing here tests another model family.

**Baselines not compared.** The re-rounder was compared against the file it starts from and, at 0.8B, against stock rungs. It was never compared against free-grid GPTQ at equal bits, against vector or lattice quantizers at 2 bits, or against learned-rounding methods. The type-allocation control was built by hand from measured sensitivities and was not compared against Hessian-aware mixed-precision allocation methods. These are named as classes of work; we did not run them and do not cite numbers for them.

**Distribution.** Calibration and most evaluation share wikitext-2 (train and test splits). The corrected configurations carry more calibration-fitted capacity than the rungs they are compared with, and §4.8 measures 10-27% sensitivity to the corpus, larger than every contested ladder margin. Off-distribution we have: the re-rounder at 0.8B on a code corpus and on an unseen prose corpus (§3.1), and the correction increment at 27B on FineWeb-Edu (§5.3). The ladder comparisons of §4.3 have not been repeated on a held-out corpus, and neither has the 27B re-rounder.

**Statistics.** The t statistics are conditional on one calibration sample and one evaluation corpus. No family-wise correction was applied. The two contested 27B margins are a third look at accumulating data, one of them is not uniform across the test set, and neither is replicated (§4.3). Blocks in the block bootstrap are runs of consecutive chunks and not articles.

**The instrument.** Every KL-divergence number is at a 512-token context. In one pair of files it ordered them opposite to a coding suite (§5.5), and the only task evaluation inside our own comparisons tied the contested pair (§4.3). Task-level evidence in this paper is thin: one multiple-choice benchmark at one byte count, and one coding suite, one run per arm, on the competitor's files only. None of our corrected or re-rounded files was run on the coding suite.

**Controls.** The mixed-type control exists at 27B and for the mixture-of-experts model. It was not built at 0.6B or 0.8B, and it is a single designed point at each size where it exists.

**Width.** The regression of §4.2 has one step on the width axis and its intervals resample tensors, not models.

**References and precision.** Rows labeled legacy are scored against a Q6_K or Q8_0 file and not BF16 (measured contamination at 27B: 0.0032 KLD). The mixture-of-experts table is scored against a vendor Q4-class file at 20 chunks. At 27B, "full covariance" means block-diagonal for ffn_down (§2.2), the VRAM columns were not measured, and the rank allocation used sensitivity priors from 0.6B.

**Alternation.** The ordering claim of §3.3 is paired (t = +17.6). The claim about alternation rounds is weaker: the 0.6B sequence is unpaired, the 0.8B continuation has no recorded error bars, and the 27B gain from alternation is about one unpaired standard error.

**Speed.** One GPU, one host. Session-to-session drift reached 9%; only one speed result has raw output in the repository; nothing was re-measured after the patch set was rebased (§6.1).

**Verification rests on the project's own records.** Statements that a derivation was verified numerically, that a capture passed its gates, or that an experiment was registered before it ran cite the project's review notes and journal. Those are our records and have not been checked by anyone outside the project.

**Code hazards.** Cache directories originally carried no provenance fingerprint (fixed for the re-rounder; open for the spectra caches and one extractor). Column selection under Gram whitening ranks the wrong columns (the reported column runs predate full-covariance whitening) [source: research/lowrank/review/adversarial-code.md].

**How the work was done.** Experiments, analysis and drafting were carried out by AI research agents under the author's direction, on one workstation. The author is responsible for the claims; errors found in the record and their corrections are listed in §7.6.

## 7.2 Status of the ablation program

An audit of every cell behind this paper's claims counted 51: 17 clean, 15 confounded, 19 missing [source: research/lowrank/paper/ABLATION-PLAN.md]. The negative results are the cleanest cells (water-filling, shared basis, equalization, the Kronecker metric, mixture-of-experts against control: isolated, mostly paired, with |t| between 2.7 and 60). The confounds concentrate in the positive multi-point curves: the recovery curve of §4.5 varies adapter budget and rank mix across its points, and the 27B allocation row varied three factors at once.

The audit priced seventeen runs to close the gating cells. Eleven have been run and are reported above. Six have not, and the corresponding claims are scoped in the text:

- the calibration-sensitivity grid (mix ratio × method);
- mixed-type controls at the 0.6B and 0.8B byte counts;
- a held-out pass over the corrected configurations and their ladder rivals;
- the recovery curve with the correction recipe held fixed;
- the equalization ablations (no imatrix; a strength sweep);
- a bound on calibration-sampling variance alone.

The review of this paper adds experiments that the existing logs cannot supply:

- re-rounded Q2_K against the IQ2 and IQ3 rungs, paired, at 27B under single-step provenance;
- three more single-slot capability runs per IQ3-class arm, and a run of our corrected arm;
- KL divergence at a 2048-token context for the four files of §5.4;
- the mixture-of-experts table against a BF16 reference;
- free-grid GPTQ and learned-rounding baselines for the re-rounder;
- one model outside the Qwen family, and a third width.

## 7.3 Reproducibility and artifacts

Method descriptions, scripts, raw logs, per-chunk outputs and result tables are in the project repository under `research/lowrank/experiments/`, one directory per experiment; Appendix B maps each tag used in this paper to its directory. The statistics added in revision are produced by one script from existing logs [source: research/lowrank/experiments/35-final-reanalysis/README.md]. Large binaries (reference logits, Gram captures, quantized models and adapters, about 160 GB) are not in the repository. They can be rebuilt from the recorded recipes; some early ones were deleted to reclaim disk, so a number resting on a deleted artifact is reproducible only by rebuilding it. Two reproduction gates were run: one rebuilt the 0.8B base with 335/335 tensors byte-identical and re-derived the re-rounded file byte-identical, and one reproduced the published 0.8B numbers before any new comparison [source: research/lowrank/experiments/26-lloyd-gauge/results.txt].

Three components are not stock llama.cpp: the Gram-capture extension to llama-imatrix (about 140 lines, environment-gated), the llama-gradmatrix tool, and the fused-kernel and allocator patch set (numbers reported at build 0ef6e55ed; rebased onto b10865). The re-rounder and the extractors are standalone Python against gguf-py. Serving needs none of the patches: a re-rounded file is a standard GGUF, and a corrected model is a standard GGUF plus a standard LoRA-form adapter. Producing either needs the Gram-capture patch and the source weights.

## 7.4 Open work

**Codec coverage for re-rounding.** The re-rounder implements only Q2_K. Q3_K and Q4_K codecs would reach the promoted tensors at 27B; I-quant codecs would allow re-rounding the rung that currently beats re-rounded Q2_K at 0.8B, and a like-for-like comparison with GSQ; an NVFP4 codec is a further target (arXiv:2509.23202). A packaged tool would take source weights and calibration text and would need a mixed-calibration default and a held-out check, since §3.1 shows a narrowly calibrated file can be worse than the original [source: research/lowrank/paper/DEPLOYABLE-WINS.md].

**Estimators for two-sided metrics.** Following §3.2: damping the output-side factor toward the identity, multi-pass gradient captures, and an adjoint for the GDN layers to remove the gradient cut.

**Other directions.** Riemannian refinement of the closed-form factors and the Bregman-damped metric-modification scheme of arXiv:2507.09428 [source: research/lowrank/prior-art/MANIFOLD-CANDIDATES.md]; per-workload conditioned adapters (§4.8); a loader that aliases shared factors (§4.4); correction of the dense tensors of a mixture-of-experts model.

## 7.5 Summary

Re-rounding improves a Q2_K file at identical bytes, with paired resolution at 0.8B and at 27B, under broad calibration; at 0.8B a smaller stock file is better still. Low-rank correction improves its base at every size, is narrowly ahead of the adjacent stock type at 27B on a margin that is not uniform across the test set, and is behind a same-byte mixed-type control. Three placements of the low-rank bytes could not be distinguished within a stated bound. Against released trained checkpoints, the matched-byte untrained baseline has lower short-context KL divergence on one corpus and ties on another, and a single capability pair points the other way. The kernel and allocator patches reduce the adapter's decode cost at small scale and change little at 27B.

## 7.6 Corrections made to the record

The project record contains errors that were found and corrected before this paper, and they are listed here so that a reader of the repository can find them.

- A first set of 27B comparisons, unpaired and against a requantized reference, showed a margin for the corrected configuration that reversed sign under single-step provenance (§4.3).
- A set of paired t statistics for the head-to-head of §5 was inflated by dividing per-chunk differences by a per-token standard error. They were recomputed with the canonical tool before any conclusion changed; the corrected values are the ones in §5.2.
- Two capability rows were retracted when 84-87% of their failures turned out to come from a crashed server.
- A first "mixed-calibration" run never reached its second corpus, because the two corpora were concatenated and not interleaved.
- An early statement that capture falls as r/d rested on two points; §4.2 replaces it with a fit in which r/d is the worst of six forms.
- A journal entry asserted that the GSQ-RCO checkpoints were trained on FineWeb-Edu; the card and code do not say so (§5.3).

Each is kept in the journal with its correction [source: research/lowrank/JOURNAL.md; review/adversarial-claims.md].
