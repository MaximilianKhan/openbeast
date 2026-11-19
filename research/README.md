# research/ — index

Two research programs ran on the RTX 5090 rig between 2026-08-03 and
2026-10-09, and this tree is their record:

- **beast-rank** ([`lowrank/`](lowrank/)): post-training repair of
  quantized models in llama.cpp. Written up as a paper on 2026-10-09.
- **langaware** ([`langaware/`](langaware/)): raising a local model's pass
  rate in an undertrained language (Zig 0.16) without fine-tuning. Closed
  2026-09-30.

**Start with [`FINDINGS.md`](FINDINGS.md)** (what each program found, how
sure we are, what was not run), then the paper,
[`lowrank/paper/final/paper.pdf`](lowrank/paper/final/paper.pdf). Most of
the other notes are dated working records. They are kept as written, and
many carry conclusions that later runs narrowed or overturned. Before you
quote a number from one, check its status below and the note that
supersedes it.

Not here: the eval suites and boards ([`evals/README.md`](../evals/README.md)),
the DGX Spark pair and the model it serves
([`docs/SPARK_HYDRA.md`](../docs/SPARK_HYDRA.md)), and the harness research
([`docs/HARNESS_NOTES.md`](../docs/HARNESS_NOTES.md)). None of the notes
below is about the Spark pair.

## Status, in one word

| Status | Meaning |
|---|---|
| **current** | The reference for its subject today. Kept up to date. |
| **superseded** | Its plan or conclusion was replaced. The *Superseded by* column says by what, and the note carries a dated line saying so. |
| **archived** | An accurate dated record (an experiment, a journal, a review, a literature sweep). Not maintained. Later results may qualify it; [`FINDINGS.md`](FINDINGS.md) and the paper say how. |

Dates are the date the note records (its run, its plan or its last dated
entry), not the commit date. Most of the tree was committed in two
imports: on 2026-08-11 and on 2026-10-09. A date marked *c.* is inferred
from the campaign phase because the note itself carries none.

## Reading notes

- **"v5-fast" is not the v5 suite.** In these notes, "v5-fast" is the
  112-unit pinned fast subset of the **v4** suite (`evals/suites/v5-fast.json`
  then). It was renamed `v4-fast` on 2026-11-15, when v5 became a real,
  71-unit suite. Read every "v5-fast" here as `v4-fast`.
- **"The champion"** is Qwen3.6-27B on the 5090 rig, the production model
  of 2026-08/09. It is not the model the Spark pair serves.
- **Paths that moved.** `scratch/post_e32.sh` (cited by the beast-rank
  journal and E32) is now [`scratch/archive/post_e32.sh`](../scratch/archive/post_e32.sh).
  `scratch/TOOLS_SOTA_REVIEW-2026-09-10.md` (cited by the langaware journal)
  is [`langaware/TOOLS_SOTA_REVIEW-2026-09-10.md`](langaware/TOOLS_SOTA_REVIEW-2026-09-10.md).
  `docs/reviews/FULL-REVIEW-2026-09-29.md` (cited by the langaware journal)
  was never committed; the re-audit it triggered is
  [`scratch/tier3-verdict-reaudit-2026-09-29.txt`](../scratch/tier3-verdict-reaudit-2026-09-29.txt).
  `evals/cache-quarantine-2026-09-29/` and `evals/results/` are rig-local
  and gitignored. Paths under `/home/max/…` are the 5090 rig's.
- **Experiment ids and directory numbers** diverged mid-campaign: "E14" is
  the fused kernel (`14-fused-kernel`), "E14-cond" is task conditioning
  (`17-task-conditioned`), "E17"/"E18" are entropy reconstruction
  (`18-entropy-reconstruction`). Elsewhere the id is the directory number.
  `11-alternation` and `19-fisher-alloc` hold data and scripts only; their
  write-ups are in [`lowrank/JOURNAL.md`](lowrank/JOURNAL.md).

## Top level

| Note | Date | Status | What it is |
|---|---|---|---|
| [`FINDINGS.md`](FINDINGS.md) | 2026-10-09 | current | State of record for both programs: what held, how sure, what was not run |
| [`README.md`](README.md) | 2026-11-19 | current | This index |

## langaware/ — environment feedback for an undertrained language

The design doc is [`docs/LANG_AWARENESS_PLAN.md`](../docs/LANG_AWARENESS_PLAN.md)
(current; it carries the November pack v2/v3 work). The verdict files are in
[`scratch/`](../scratch/README.md).

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`langaware/JOURNAL.md`](langaware/JOURNAL.md) | 2026-08-20 → 2026-09-30 | archived | The campaign journal: discovery, diagnostics A/B, Tier-3 pack verdict, re-audit, fresh rerun | — (its findings are summarized in [`FINDINGS.md`](FINDINGS.md)) |
| [`langaware/BEAST_ASSIST_ROADMAP-2026-09-10.md`](langaware/BEAST_ASSIST_ROADMAP-2026-09-10.md) | 2026-09-10 | archived | 10-agent roadmap and stopping rule after the diagnostics rounds. Byte-identical copy of [`docs/reviews/BEAST_ASSIST_ROADMAP-2026-09-10.md`](../docs/reviews/BEAST_ASSIST_ROADMAP-2026-09-10.md) | — |
| [`langaware/TOOLS_SOTA_REVIEW-2026-09-10.md`](langaware/TOOLS_SOTA_REVIEW-2026-09-10.md) | 2026-09-10 | archived | 9-agent tool-suite review (72 findings). Byte-identical copy of [`docs/reviews/TOOLS_SOTA_REVIEW-2026-09-10.md`](../docs/reviews/TOOLS_SOTA_REVIEW-2026-09-10.md); the status of its top 10 is tracked in `docs/TODO.md` ("TOOLS SOTA REVIEW") | — |

## lowrank/ — beast-rank

### Entry points and living files

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`lowrank/README.md`](lowrank/README.md) | 2026-08-03, status 2026-10-09 | current | The program's front page: thesis, original plan (kept as written), directory map | — (its opening plan is marked superseded inside) |
| [`lowrank/TODO.md`](lowrank/TODO.md) | 2026-10-09 | current | Parked queue: what is still open, and the referee-requested runs | — |
| [`lowrank/PROTOCOL.md`](lowrank/PROTOCOL.md) | 2026-08-04 | current | Methodology v2: the standard every result after the first review follows | — |
| [`lowrank/JOURNAL.md`](lowrank/JOURNAL.md) | 2026-08-03 → 2026-10-09 | archived | The append-only lab notebook, corrections included | — |
| [`lowrank/RESULTS_ROLLUP.md`](lowrank/RESULTS_ROLLUP.md) | 2026-08-04 → 2026-09-14 | archived | Every measured configuration on one page, with its correction headers | [`FINDINGS.md`](FINDINGS.md) as the summary |
| [`lowrank/MASTER-TABLE.md`](lowrank/MASTER-TABLE.md) | 2026-08-04 → 2026-10-09 | archived | The BF16-referenced comparison table (the paper's Table 1 in waiting) | the paper's tables |
| [`lowrank/NEXT-STEPS-2026-09-08.md`](lowrank/NEXT-STEPS-2026-09-08.md) | 2026-09-08 | superseded | The forward plan locked after the GSQ-RCO discovery | [`lowrank/TODO.md`](lowrank/TODO.md) (parked 2026-10-09) |
| [`lowrank/CONTRIBUTIONS-PLAN.md`](lowrank/CONTRIBUTIONS-PLAN.md) | 2026-09-08 | superseded | Four contribution lanes (C1 llama.cpp PR, C2 benchmark rows, C3 `gguf-refine`, C4 paper) | [`lowrank/TODO.md`](lowrank/TODO.md); the paper (C4) shipped 2026-10-09 |
| [`lowrank/contrib/c1/PR.md`](lowrank/contrib/c1/PR.md) | 2026-09-08 | current | Evidence pack and two patches for the C1 imatrix blind-spot PR. Not posted; llama.cpp forbids AI-written PR text, so the prose must be rewritten by hand | — |
| [`lowrank/kernels/README.md`](lowrank/kernels/README.md) | 2026-08-14 → 2026-08-15 | current | The vendored llama.cpp patch (correction kernels, re-rounding, Gram capture) and how to apply it | — |

### The paper

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`lowrank/paper/final/README.md`](lowrank/paper/final/README.md) | 2026-10-09 | current | How the paper is built (Typst and LaTeX) and which file is the source | — |
| [`lowrank/paper/final/paper.md`](lowrank/paper/final/paper.md) | 2026-10-09 | current | The whole paper as one Markdown file (generated from `sections/`) | — |
| [`lowrank/paper/final/sections/00-abstract.md`](lowrank/paper/final/sections/00-abstract.md) | 2026-10-09 | current | Abstract (source of truth: edit `sections/`) | — |
| [`lowrank/paper/final/sections/01-introduction.md`](lowrank/paper/final/sections/01-introduction.md) | 2026-10-09 | current | §1 Introduction | — |
| [`lowrank/paper/final/sections/02-method.md`](lowrank/paper/final/sections/02-method.md) | 2026-10-09 | current | §2 Method | — |
| [`lowrank/paper/final/sections/03-results-free-lever.md`](lowrank/paper/final/sections/03-results-free-lever.md) | 2026-10-09 | current | §3 Re-rounding at zero added bytes | — |
| [`lowrank/paper/final/sections/04-results-walls.md`](lowrank/paper/final/sections/04-results-walls.md) | 2026-10-09 | current | §4 Where low-rank correction falls short | — |
| [`lowrank/paper/final/sections/04b-gsq-head-to-head.md`](lowrank/paper/final/sections/04b-gsq-head-to-head.md) | 2026-10-09 | current | §5 Head-to-head with released trained checkpoints | — |
| [`lowrank/paper/final/sections/05-systems.md`](lowrank/paper/final/sections/05-systems.md) | 2026-10-09 | current | §6 Serving cost and the kernels | — |
| [`lowrank/paper/final/sections/06-limitations-future.md`](lowrank/paper/final/sections/06-limitations-future.md) | 2026-10-09 | current | §7 Limitations and open work | — |
| [`lowrank/paper/final/sections/07-related-work.md`](lowrank/paper/final/sections/07-related-work.md) | 2026-10-09 | current | §8 Related work | — |
| [`lowrank/paper/final/sections/08-appendix-a.md`](lowrank/paper/final/sections/08-appendix-a.md) | 2026-10-09 | current | Appendix A, additional negative results | — |
| [`lowrank/paper/OUTLINE.md`](lowrank/paper/OUTLINE.md) | 2026-08-03 → 2026-08-04 | superseded | The living outline of the draft | [`lowrank/paper/final/`](lowrank/paper/final/README.md) |
| [`lowrank/paper/ABLATION-PLAN.md`](lowrank/paper/ABLATION-PLAN.md) | 2026-08-04 | superseded | The missing-runs matrix (T1.x ids used across the tree) | the paper's §7 and [`lowrank/TODO.md`](lowrank/TODO.md) |
| [`lowrank/paper/DEPLOYABLE-WINS.md`](lowrank/paper/DEPLOYABLE-WINS.md) | 2026-08-04 → 2026-08-11 | superseded | The "deployable-everywhere wins" pitch | [`FINDINGS.md`](FINDINGS.md) findings 2 and 3 |
| [`lowrank/paper/references.md`](lowrank/paper/references.md) | 2026-08-03 → 2026-09-08 | superseded | Annotated master bibliography of the campaign | `paper/final/refs.json` for what the paper cites; the annotations stay useful |
| [`lowrank/paper/theory-alternation-convergence.md`](lowrank/paper/theory-alternation-convergence.md) | c. 2026-08-04 | archived | Theory note: convergence of quantizer/corrector alternation | — |
| [`lowrank/paper/theory-L6-family-subsumption.md`](lowrank/paper/theory-L6-family-subsumption.md) | 2026-09-09 | archived | Theory note: one master objective over four method families | — |
| [`lowrank/paper/draft/00-abstract.md`](lowrank/paper/draft/00-abstract.md) | 2026-08-04 | superseded | Draft abstract ("The Free Lever and the Measured Walls") | [`final/sections/00-abstract.md`](lowrank/paper/final/sections/00-abstract.md) |
| [`lowrank/paper/draft/01-introduction.md`](lowrank/paper/draft/01-introduction.md) | 2026-08-04 → 2026-09-09 | superseded | Draft §1 | [`final/sections/01-introduction.md`](lowrank/paper/final/sections/01-introduction.md) |
| [`lowrank/paper/draft/02-method.md`](lowrank/paper/draft/02-method.md) | 2026-08-04 → 2026-09-09 | superseded | Draft §2 | [`final/sections/02-method.md`](lowrank/paper/final/sections/02-method.md) |
| [`lowrank/paper/draft/03-results-free-lever.md`](lowrank/paper/draft/03-results-free-lever.md) | 2026-08-04 | superseded | Draft §3 | [`final/sections/03-results-free-lever.md`](lowrank/paper/final/sections/03-results-free-lever.md) |
| [`lowrank/paper/draft/04-results-walls.md`](lowrank/paper/draft/04-results-walls.md) | 2026-08-04 → 2026-09-14 | superseded | Draft §4 | [`final/sections/04-results-walls.md`](lowrank/paper/final/sections/04-results-walls.md) and [`04b-gsq-head-to-head.md`](lowrank/paper/final/sections/04b-gsq-head-to-head.md) |
| [`lowrank/paper/draft/05-systems.md`](lowrank/paper/draft/05-systems.md) | 2026-08-04 | superseded | Draft §5 | [`final/sections/05-systems.md`](lowrank/paper/final/sections/05-systems.md) |
| [`lowrank/paper/draft/06-limitations-future.md`](lowrank/paper/draft/06-limitations-future.md) | 2026-08-04 | superseded | Draft §6 | [`final/sections/06-limitations-future.md`](lowrank/paper/final/sections/06-limitations-future.md) |
| [`lowrank/paper/draft/07-related-work.md`](lowrank/paper/draft/07-related-work.md) | 2026-09-09 | superseded | Draft §7 | [`final/sections/07-related-work.md`](lowrank/paper/final/sections/07-related-work.md) |

### Experiments

Each directory holds its scripts and raw logs next to the write-up. All
are dated records; the paper and [`FINDINGS.md`](FINDINGS.md) state what
survived. A "⚠ see review/ corrections 2026-08-04" line at the top of a
write-up means the 2026-08-04 adversarial review ([`lowrank/review/`](#review))
changed how its result may be stated.

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`01-svd-spectrum/README.md`](lowrank/experiments/01-svd-spectrum/README.md) | 2026-08-03 | archived | E01: SVD spectrum census of Qwen3-0.6B (weights are not low-rank) | — |
| [`03-residual-rank/README.md`](lowrank/experiments/03-residual-rank/README.md) | 2026-08-03 | archived | E03: is quantization error low-rank? | — |
| [`04-served-v0/README.md`](lowrank/experiments/04-served-v0/README.md) | 2026-08-03 → 2026-08-04 | archived | E04: v0 end-to-end, residual correction served as a LoRA adapter | — |
| [`04b-27b/README.md`](lowrank/experiments/04b-27b/README.md) | 2026-08-04 → 2026-08-15 | superseded | E04c: scale replication on the production 27B (legacy-provenance ladder) | [`27-bf16-rederivation/`](lowrank/experiments/27-bf16-rederivation/README.md) |
| [`05-whitened-residual/README.md`](lowrank/experiments/05-whitened-residual/README.md) | 2026-08-03 | archived | E05: whitened residual census, the go/no-go | — |
| [`07-allocation/README.md`](lowrank/experiments/07-allocation/README.md) | 2026-08-04 | archived | E07: rank allocation, energy water-filling vs measured sensitivity | — |
| [`08-structure-probes/README.md`](lowrank/experiments/08-structure-probes/README.md) | 2026-08-04 | archived | E08: three structure bets from the arXiv sweep | — |
| [`09-subq2-main-event/README.md`](lowrank/experiments/09-subq2-main-event/README.md) | 2026-08-04 | archived | E09: corrected IQ2_XS against the quant ladder | — |
| [`10-full-covariance/README.md`](lowrank/experiments/10-full-covariance/README.md) | 2026-08-04 | archived | E10: full-covariance whitening | — |
| [`13-rerounder/README.md`](lowrank/experiments/13-rerounder/README.md) | 2026-08-04 | archived | E13: frozen-grid error-feedback re-rounding | — |
| [`14-fused-kernel/REPORT.md`](lowrank/experiments/14-fused-kernel/REPORT.md) | 2026-08-04 | archived | E14 phase 1: fused adapter epilogue in mmvq | — |
| [`14-fused-kernel/REPORT-PHASE2.md`](lowrank/experiments/14-fused-kernel/REPORT-PHASE2.md) | 2026-08-04 | archived | E14 phase 2: fold `t = A·x` into the activation-quantize launch | — |
| [`14-fused-kernel/REPORT-PHASE2B.md`](lowrank/experiments/14-fused-kernel/REPORT-PHASE2B.md) | 2026-08-04 | archived | E14 phase 2b: Q8_0 adapter factors in the fused paths | — |
| [`14-fused-kernel/REPORT-PHASE3.md`](lowrank/experiments/14-fused-kernel/REPORT-PHASE3.md) | 2026-08-04 | archived | E14 phase 3: quantized-t dp4a epilogue | — |
| [`15-iq1-carrier/README.md`](lowrank/experiments/15-iq1-carrier/README.md) | 2026-08-04 | archived | E15: the IQ1_S carrier | — |
| [`16-nvfp4-composition/README.md`](lowrank/experiments/16-nvfp4-composition/README.md) | 2026-08-04 | archived | E16: low-rank correction on NVFP4 (its novelty claim was narrowed by [`recon-2026-08-11`](lowrank/prior-art/recon-2026-08-11.md)) | — |
| [`17-task-conditioned/README.md`](lowrank/experiments/17-task-conditioned/README.md) | c. 2026-08-04 | archived | "E14-cond": task-conditioned correction | — |
| [`18-entropy-reconstruction/README.md`](lowrank/experiments/18-entropy-reconstruction/README.md) | 2026-08-04 | archived | E17/E18: probability-driven reconstruction | — |
| [`20-gradgram/REPORT.md`](lowrank/experiments/20-gradgram/REPORT.md) | 2026-08-04 | archived | E20: output-gradient Gram capture | — |
| [`21-rank-sweep/README.md`](lowrank/experiments/21-rank-sweep/README.md) | 2026-08-04 | archived | E21: incremental rank-response sweep | — |
| [`22-qwen35-08b/README.md`](lowrank/experiments/22-qwen35-08b/README.md) | 2026-08-04 | archived | E22: BF16-pure replication on Qwen3.5-0.8B | — |
| [`22-qwen35-08b/HELDOUT-METHOD.md`](lowrank/experiments/22-qwen35-08b/HELDOUT-METHOD.md) | 2026-08-04 | archived | Exact construction of the R7 held-out and mixed-calibration arc | — |
| [`23-moe/README.md`](lowrank/experiments/23-moe/README.md) | 2026-08-04 | archived | E23: MoE expert-stack correction on Qwen3.6-35B-A3B (decisive negative) | — |
| [`23-moe/REPORT-capture.md`](lowrank/experiments/23-moe/REPORT-capture.md) | 2026-08-03 → 2026-08-04 | archived | E23: MUL_MAT_ID activation-Gram capture | — |
| [`24-yaqa-lite/README.md`](lowrank/experiments/24-yaqa-lite/README.md) | 2026-08-04 | archived | E24: Kronecker-metric re-rounding on Qwen3.5-0.8B | — |
| [`25-alloc-concurrency/REPORT.md`](lowrank/experiments/25-alloc-concurrency/REPORT.md) | 2026-08-04 | archived | E25: allocator-level concurrency fix for `GGML_CUDA_GRAPH_OPT` | — |
| [`26-lloyd-gauge/README.md`](lowrank/experiments/26-lloyd-gauge/README.md) | 2026-08-04 | archived | E26: Fisher-Lloyd fixed point and gauge-fixing | — |
| [`27-bf16-rederivation/README.md`](lowrank/experiments/27-bf16-rederivation/README.md) | 2026-08-04 | archived | E27: the 27B story re-derived BF16-pure (the basis of FINDINGS 3) | — |
| [`28-glowq-shared-a/README.md`](lowrank/experiments/28-glowq-shared-a/README.md) | 2026-08-11 | archived | E28: shared-A basis across input-sharing groups | — |
| [`29-srr-split/README.md`](lowrank/experiments/29-srr-split/README.md) | 2026-08-11 | archived | E29: preserve-then-quantize split at 27B | — |
| [`30-muon-spectrum/PREREG.md`](lowrank/experiments/30-muon-spectrum/PREREG.md) | 2026-09-08 | archived | E30 pre-registration: Muon-provenance spectrum flatness | — |
| [`31-seeded-basis-duel/PREREG.md`](lowrank/experiments/31-seeded-basis-duel/PREREG.md) | 2026-09-08 | archived | E31 pre-registration: seeded basis vs learned whitened factors | — |
| [`32-t117-gsq-head-to-head/README.md`](lowrank/experiments/32-t117-gsq-head-to-head/README.md) | 2026-09-08 → 2026-09-11 | archived | E32: GSQ-RCO head-to-head on Qwen3.8-27B (the basis of FINDINGS 9) | — |
| [`32-t117-gsq-head-to-head/iq-stability-repro.md`](lowrank/experiments/32-t117-gsq-head-to-head/iq-stability-repro.md) | 2026-09-15 | archived | IQ-artifact CUDA launch-timeout repro, run output | — |
| [`33-t110-capture-width/REPORT.md`](lowrank/experiments/33-t110-capture-width/REPORT.md) | 2026-09-11 | archived | E33: capture-vs-width regression (FINDINGS 5) | — |
| [`33-t110-capture-width/DATA-LOCATION.md`](lowrank/experiments/33-t110-capture-width/DATA-LOCATION.md) | 2026-09-11 | archived | Where E33's 91 MB of spectra live (outside git, on the 5090 rig) | — |
| [`34-e16-nvfp4-08b/README.md`](lowrank/experiments/34-e16-nvfp4-08b/README.md) | 2026-09-11 → 2026-09-14 | archived | E34: correction on a self-quantized NVFP4 base, 0.8B (FINDINGS 7) | — |
| [`35-final-reanalysis/README.md`](lowrank/experiments/35-final-reanalysis/README.md) | 2026-10-09 | current | E35: CPU reanalysis of existing logs for the paper revision | — |
| [`35-final-reanalysis/RESULTS.md`](lowrank/experiments/35-final-reanalysis/RESULTS.md) | 2026-10-09 | current | E35 results (robust statistics A1–A5 cited by FINDINGS) | — |

All experiment paths above are under `lowrank/experiments/`.

### Review

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`lowrank/review/adversarial-stats.md`](lowrank/review/adversarial-stats.md) | 2026-08-04 | archived | Adversarial review: statistics and method | — |
| [`lowrank/review/adversarial-claims.md`](lowrank/review/adversarial-claims.md) | 2026-08-04 | archived | Adversarial review: claims against evidence, fairness of comparisons | — |
| [`lowrank/review/adversarial-code.md`](lowrank/review/adversarial-code.md) | 2026-08-04 | archived | Adversarial review: the load-bearing research code | — |
| [`lowrank/review/adversarial-round2-experiments.md`](lowrank/review/adversarial-round2-experiments.md) | 2026-08-04 → 2026-08-05 | archived | Round 2: the post-review experiments | — |
| [`lowrank/review/adversarial-round2-paper.md`](lowrank/review/adversarial-round2-paper.md) | 2026-08-04 | archived | Round 2: referee simulation on the draft | — |
| [`lowrank/review/coherence-audit.md`](lowrank/review/coherence-audit.md) | 2026-08-04 | archived | Coherence audit of the whole tree after the E24/E26/E27 wave (the "P" ids cited in notes) | — |
| [`lowrank/review/verification-2026-09-08-rco-and-kernels.md`](lowrank/review/verification-2026-09-08-rco-and-kernels.md) | 2026-09-08 | archived | Verification memo: RCO vs the equivalence class; kernel-contamination audit | — |

### Prior art

Literature and code sweeps. [`recon-2026-08-11.md`](lowrank/prior-art/recon-2026-08-11.md)
supersedes every "first" or "unpublished" claim in the sweeps dated before
it; the paper's related-work section is the final word.

| Note | Date | Status | What it is |
|---|---|---|---|
| [`recon-2026-08-11.md`](lowrank/prior-art/recon-2026-08-11.md) | 2026-08-11 | archived | 8-lane synthesis of the post-08-04 literature (the novelty arbiter) |
| [`recon-2026-09-08.md`](lowrank/prior-art/recon-2026-09-08.md) | 2026-09-08 | archived | 4-lane follow-up sweep, 08-11 → 09-08 |
| [`paper-notes-2026-09-08.md`](lowrank/prior-art/paper-notes-2026-09-08.md) | 2026-09-08 | archived | Deep-read notes: RCO, DAM, OBD-LLM |
| [`survey-lowrank-compression.md`](lowrank/prior-art/survey-lowrank-compression.md) | 2026-08-03 | archived | Survey: low-rank and hybrid quant+low-rank compression |
| [`math-methods.md`](lowrank/prior-art/math-methods.md) | c. 2026-08-03 | archived | Math and methods survey for low-rank correction |
| [`arxiv-qec-lineage.md`](lowrank/prior-art/arxiv-qec-lineage.md) | 2026-08-03 | archived | The quantization-error-compensation lineage |
| [`arxiv-whitening-allocation.md`](lowrank/prior-art/arxiv-whitening-allocation.md) | 2026-08-03 | archived | Covariance whitening and rank allocation |
| [`arxiv-outliers-scaling.md`](lowrank/prior-art/arxiv-outliers-scaling.md) | 2026-08-03 | archived | Outliers, rotations, quantization scaling laws |
| [`arxiv-kernels.md`](lowrank/prior-art/arxiv-kernels.md) | 2026-08-03 | archived | Fusing low-rank branches into quantized GEMV |
| [`arxiv-codesign-mining.md`](lowrank/prior-art/arxiv-codesign-mining.md) | 2026-08-03 | archived | Quantizer/corrector co-design, recovery vs bit-width |
| [`arxiv-physics-inspired.md`](lowrank/prior-art/arxiv-physics-inspired.md) | 2026-08-03 | archived | Physics-inspired compression structures |
| [`arxiv-structures.md`](lowrank/prior-art/arxiv-structures.md) | 2026-08-03 → 2026-08-04 | archived | Structured alternatives to dense low-rank |
| [`arxiv-manifolds-infogeo.md`](lowrank/prior-art/arxiv-manifolds-infogeo.md) | 2026-08-03 → 2026-08-04 | archived | Information geometry applied to compression |
| [`arxiv-manifolds-landscape.md`](lowrank/prior-art/arxiv-manifolds-landscape.md) | 2026-08-04 | archived | Loss-landscape geometry applied to quantization |
| [`arxiv-manifolds-riemannian.md`](lowrank/prior-art/arxiv-manifolds-riemannian.md) | 2026-08-04 | archived | Riemannian optimization for low-rank fitting |
| [`MANIFOLD-CANDIDATES.md`](lowrank/prior-art/MANIFOLD-CANDIDATES.md) | 2026-08-04 → 2026-08-11 | archived | The discrete experiment list (M1–M10) the manifold sweeps produced |
| [`arxiv-entropy-reconstruction.md`](lowrank/prior-art/arxiv-entropy-reconstruction.md) | 2026-08-04 | archived | Probability-driven reconstruction at dequant time (E17 charter) |
| [`arxiv-fp4-composition.md`](lowrank/prior-art/arxiv-fp4-composition.md) | 2026-08-04 | archived | FP4 microscaling × low-rank correction |
| [`arxiv-functional-rank-healing.md`](lowrank/prior-art/arxiv-functional-rank-healing.md) | 2026-08-04 | archived | Function-preserving factorization and light healing |
| [`arxiv-kv-holistic.md`](lowrank/prior-art/arxiv-kv-holistic.md) | 2026-08-04 | archived | KV cache and the total-VRAM budget |
| [`gguf-tooling.md`](lowrank/prior-art/gguf-tooling.md) | 2026-08-03 | archived | GGUF tooling recon (llama.cpp `0ef6e55ed`) |
| [`llamacpp-internals.md`](lowrank/prior-art/llamacpp-internals.md) | 2026-08-03 | archived | llama.cpp LoRA internals: can it serve the correction unchanged? |
| [`upstream-landscape.md`](lowrank/prior-art/upstream-landscape.md) | 2026-08-03 | archived | Upstream landscape for low-rank weight serving in llama.cpp |

All prior-art paths above are under `lowrank/prior-art/`.

### Upstream

| Note | Date | Status | What it is | Superseded by |
|---|---|---|---|---|
| [`lowrank/upstream/comment-23575-READY.md`](lowrank/upstream/comment-23575-READY.md) | 2026-09-08 | archived | Confirmation comment on llama.cpp PR #23575: **posted** 2026-09-08 | — |
| [`lowrank/upstream/issue-draft-mtp-imatrix-blindspot.md`](lowrank/upstream/issue-draft-mtp-imatrix-blindspot.md) | 2026-08-04 | superseded | Draft issue #1: imatrix never calibrates MTP/NextN tensors | It duplicated upstream #23476; it became the posted comment above |
| [`lowrank/upstream/issue-draft-lora-decode-overhead.md`](lowrank/upstream/issue-draft-lora-decode-overhead.md) | 2026-08-04 | superseded | Draft issue #2: LoRA adapter decode overhead at batch 1 | Its mechanism was falsified by the E14 phase-2 kernel work (header note) |
| [`lowrank/upstream/issue-draft-training-kv-backprop.md`](lowrank/upstream/issue-draft-training-kv-backprop.md) | 2026-08-04 | superseded | Draft issue #4: no gradient through the training KV path | A prior report exists (#21037); reframe as a confirmation comment (header note) |
