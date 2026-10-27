# OpenBeast research findings

State of record as of 2026-10-09. Two research programs have run on this
rig (one RTX 5090): **beast-rank**, on post-training repair of quantized
models in llama.cpp, and **langaware**, on raising a local model's success
rate in a language it was undertrained on. This page states what each
found, how sure we are, and what was not done. Every number has a file
behind it; the paths are given.

The formal write-up of beast-rank is
[`lowrank/paper/final/paper.pdf`](lowrank/paper/final/paper.pdf)
(source: `lowrank/paper/final/sections/`, built by `build.py`).

## beast-rank: what repair of an aggressively quantized model buys

Models: Qwen3-0.6B, Qwen3.5-0.8B, Qwen3.6-27B (heretic-v2, an uncensored
community finetune), Qwen3.8-27B, Qwen3.6-35B-A3B (MoE). All Qwen-family,
all on one GPU. Instrument: KL divergence against BF16 reference logits at
a 512-token context, with paired per-chunk statistics. The t values below
are conditional on one calibration sample and one evaluation corpus, and no
family-wise correction was applied, so margins near |t| = 2–3 are
suggestive. Robust statistics (sign, Wilcoxon, block bootstrap) for the
load-bearing pairs are in `lowrank/experiments/35-final-reanalysis/`.

### Findings that hold

1. **On this card, the smaller file decoded faster.** The 27B at Q6_K uses
   23.6 GB and runs at 61 tok/s; at Q2_K it uses 13.0 GB and runs at 99.7
   tok/s. Two points on one GPU: a motivation, not a general result.
   (`lowrank/RESULTS_ROLLUP.md`)

2. **Re-rounding improves a given K-quant file at zero bytes; it does not
   make Q2_K the best choice at its byte point.** Keep llama-quantize's
   grids byte-for-byte, re-choose only the codes under an
   activation-covariance metric, and write a standard GGUF.
   - 0.8B, Q2_K: perplexity −15%, KLD −34%, top-1 +6.3 points (paired NLL
     t = −44.9, n = 580; KLD t = −30.3, n = 40). (`experiments/24-yaqa-lite/`)
   - **A smaller stock rung beats it.** At 0.8B the stock IQ3_XXS file
     (412 MB) has lower KLD than re-rounded Q2_K (436 MB) by 0.0657
     (paired t = +10.8, 39/40 chunks; same reference and chunks, verified).
     At 27B the only comparable rung scored, IQ3_XXS, is 0.6 GB larger and
     lower by 0.0526 on the 20 shared chunks (t = +4.6; the two sides
     differ in provenance). (`experiments/35-final-reanalysis/RESULTS.md` A3)
   - 27B, Q2_K: KLD −13.3%, top-1 +1.07 points (t = −7.0, n = 100).
     Legacy-provenance pair (a requantization of a Q6_K file); perplexity
     does not move; no held-out test at 27B; only the Q2_K tensors are
     swept. (`experiments/27-bf16-rederivation/results-100ch-paired.txt`)
   - It depends on calibration. Calibrated on wikitext alone it improves
     unseen prose (−5.5% PPL) and is **worse than the untouched model on
     code** (3.470 vs 3.083 PPL). Calibrated on a wikitext/code mix it
     improves both. (`paper/DEPLOYABLE-WINS.md`)
   - The tool needs the higher-precision source weights and a Gram capture
     on a patched llama-imatrix; it does not work from a GGUF alone.

3. **Low-rank correction works and is behind spending the same bytes on
   quantization types.** At 27B, 100 chunks (the pre-registered final
   sample, after ties at 20 and 40), every artifact one quantization step
   from BF16:
   - the corrected configuration is ahead of Q3_K_S by 0.0048 KLD (5%,
     t = −2.85, 71/100 chunks; Wilcoxon p = 8.5e-5; block-bootstrap 95%
     interval [−0.0073, −0.0019]);
   - it is behind a same-byte mixed-type control by 0.0063 (t = +2.62,
     62/100; no difference in NLL; p = 0.061 after a factor of six);
   - both margins are small, on-distribution and unreplicated. The
     registration fixed n = 100 and a parity rule; it registered no win
     rule and no adjustment for three looks;
   - **the margin over Q3_K_S is not uniform**: −0.0001 on chunks 1–40
     (t = −0.04), −0.0079 on chunks 41–100 (t = −3.32); the 40-chunk run is
     the exact prefix of the 100-chunk run. The correction's gain over its
     own base is stable (−0.0090, t = −8.5; −0.0093 and −0.0089 in the two
     blocks); what moves is the mixed base against Q3_K_S.
   (`experiments/27-bf16-rederivation/`, `experiments/35-final-reanalysis/`)

4. **Three placements of the low-rank bytes could not be distinguished,
   within a bound.** Correcting after quantization, carving the low-rank
   part out before quantization, and sharing a basis across tensors differ
   at 27B by less than 0.0045 KLD (90% interval), under 50% of the
   correction's own increment (0.0090); for the two shared-basis variants
   the bound is 0.0018 (20%). This is a bound, not a demonstration of
   equivalence, on one model, one byte point, one rank, one corpus. One
   mixed-type control at the same bytes is better than all of them
   (t = +2.4 to +5.2). The "−16.6% adapter bytes" for the shared basis
   (280.8 vs 336.5 MB) is a computed size under a loader patch that was
   not written; the evaluated file is 336.5 MB.
   (`experiments/28-glowq-shared-a/`, `experiments/29-srr-split/`,
   `experiments/35-final-reanalysis/RESULTS.md` A2)

5. **Capture is sublinear in rank and, across a single width step, falls
   more slowly than r/d predicts.** A regression over 1,146 tensors on four
   models fits capture ≈ a·r^0.68·d^−0.76 (diagonal whitening) and
   a·r^0.37·d^−0.52 (full Gram). The linear r/d rule is the worst of six
   forms tried. The width axis has a single step (1024 → 5120), confounded
   with model size, so this is a fit, not a law.
   (`experiments/33-t110-capture-width/REPORT.md`)

6. **On an MoE the correction loses to a one-line promotion (n = 20,
   Q4-referenced).** Per-expert correction improves its base and loses to
   promoting one tensor group to Q4_K (t = +6.85, 0/20 chunks). A basis
   shared across experts captures barely more than a random one. The
   reference is the vendor UD-Q4_K_M file; in it `ffn_down_exps` is Q5_K
   (37 layers) and Q6_K (3), not Q4_K, so the control is not near-lossless
   against the reference by construction. (`experiments/23-moe/`,
   `experiments/35-final-reanalysis/RESULTS.md` A4)

7. **NVFP4 from llama-quantize is a poor carrier on this architecture.**
   At 0.8B it is the worst 4-bit option (KLD 0.207 against 0.047 to 0.088
   for the K-quants at equal bytes); the correction halves that and still
   loses. (`experiments/34-e16-nvfp4-08b/`)

8. **Better surrogate, same or worse outcome — six times, plus one weaker
   case.** Energy captured by rank allocation, a two-sided Kronecker
   metric, further descent of the same objective, importance moved into
   column scales, capture at equal bytes, and capture of the base each
   improved while KL divergence did not. The seventh entry (512-token KLD
   against a coding suite) is one pair, one run per arm, unreplicated. The
   table is §4.7 of the paper.

9. **Against released trained checkpoints (GSQ-RCO, Qwen3.8-27B), stated
   byte-honestly.** (`experiments/32-t117-gsq-head-to-head/`)
   - The arms are not byte-matched: GSQ-RCO 8.42 / 11.77 GB; untrained
     Unsloth baseline 8.37 / 12.04 GB; baseline plus our correction
     9.27 / 12.94 GB.
   - At matched bytes the **untrained baseline already has lower 512-token
     KLD than the trained checkpoint on wikitext** (+0.0715, t = +11.7;
     +0.0152, t = +8.6). On FineWeb-Edu that difference is unresolved or
     marginal (t = +1.66, +2.07).
   - Our correction (+0.9 GB) lowers its base further on both corpora. Of
     the wikitext gap between our arm and GSQ-RCO, 85–88% is the baseline's
     lead and 12–15% is the correction.
   - A stock file at near-equal total bytes (UD-Q2_K_XL, 9.83 GB) beats our
     9.27 GB arm (t = +11.6 wikitext, +10.7 FineWeb-Edu).
   - The registered prediction was half right: the competitor's deficit
     shrank on FineWeb-Edu as predicted; our increments were predicted to
     shrink too and mostly did not (IQ2_S 16% smaller, the other two
     unchanged).
   - Perplexity: at 512 tokens the ordering is unresolved (paired NLL
     t = +1.33, +0.58); at 2048 tokens the trained checkpoint is ahead
     (paired t = −3.33, −4.15, n = 145). KLD at 2048 was not measured.
   - Capability: one run per arm on the 112-unit coding suite, 88 vs 76
     (net +12, exact McNemar p = 0.012). Seven of the twelve are Zig, where
     both arms are near the floor (9/30, 2/30); four runs of a different
     finetune span 82–91, a 9-unit run-to-run spread.
   - In one cross-family pair (one run per arm), 512-token KLD and a
     coding suite ordered the artifacts oppositely; we treat 512-token KLD
     as unvalidated across refinement families. We did not run our own
     corrected artifact on the suite.
   - Cost of our arm: about 12 CPU-minutes of extraction, excluding
     obtaining the BF16 weights and the 48-chunk Gram capture on a patched
     build.

10. **Serving patches (one GPU; raw bench output exists only for the
    N = 10 pair).** Fused kernels raise adapter decode by 79% at 0.6B (one
    same-session pair) and 2.7% at 27B (interleaved, N = 10). A separate
    allocator fix raised 0.6B base decode by 19.6% in the better of two
    sessions and 11% in the other, on a loaded host, with no measurable
    change at 27B. Not re-measured after the rebase to b10865.
    (`experiments/14-fused-kernel/`, `experiments/25-alloc-concurrency/`)

### What was not done

Of the seventeen runs the ablation audit priced, six were not run: the
calibration-mix grid, interpolation controls at 0.6B/0.8B, a held-out
pass over the ladder comparisons, the recovery curve with the recipe held
fixed, the equalization mechanism ablations, and a calibration-sampling
bound. Also open: a third model width, a single-step 27B re-round pair,
Q3_K/Q4_K/I-quant codecs for the re-rounder, and any capability run of our
own artifacts. The review of the paper added: re-rounded Q2_K against the
IQ rungs, paired, at 27B; three more capability runs per IQ3 arm plus our
arm; KLD at a 2048-token context; free-grid GPTQ and learned-rounding
baselines; a non-Qwen model. The paper's §7 lists these beside the claims
they limit.

### Corrections made along the way

The record contains its own errors and their repairs: a first set of 27B
"wins" that became a tie and then a narrow result under clean provenance;
a "law" that was two points; a set of t-statistics inflated by a wrong
standard error; two capability rows that were a crashed server. Each is in
`lowrank/JOURNAL.md` with its correction, and `lowrank/review/` holds the
two adversarial reviews that forced most of them.

## langaware: environment feedback for an undertrained language

Question: can a local 27B model, weak in Zig 0.16 because the language
changed after its training data, be brought up without fine-tuning?
Instrument: the 30 Zig units of the OpenBeast eval suite, paired by unit,
replicated. (`langaware/JOURNAL.md`; verdict files in `scratch/` of the
main tree.)

1. **A short pack of verified, version-specific language facts in the
   prompt works.** Fresh rerun of 2026-09-30, six cells, no cache:
   Qwen3.8-27B-Uncensored passes 10 and 11 of 30 without the pack and 24
   and 21 with it. Pooled over the two replicates: 26 units rescued, 2
   regressed, net +24, exact McNemar p < 0.0001. Counted once per unit
   rather than once per replicate, 18 units improve, 1 worsens and 11 do
   not change (sign test p = 7.6e-5). Dropping one rescue that hit the
   wall-clock timeout after its solution had already validated gives net
   +23. The champion model (Qwen3.6-27B) goes from 23 to 28 of
   30 (7 rescued, 2 regressed, p = 0.18).
2. **The effect is idiom adoption, not copying.** The longest verbatim
   overlap between pack and solution is 46 characters with or without the
   pack; the visible change is the model using the current API forms.
3. **It is an in-sample result, measured under greedy decoding.** The
   pack's curated section was written against failures on these same 30
   units, every cell decoded greedily at four concurrent jobs while
   production samples at temperature 0.6, and the cells ran from a tree
   with uncommitted changes. A held-out Zig set was designed and has not
   been run. Treat the size of the effect as an upper bound until it is.
4. **Same-configuration churn is about 30%** on these units at four
   concurrent jobs against a shared-context server (9 of 30 units flip
   between identical runs). Any single-run comparison on this suite is
   uninformative; the replicate design was necessary.
5. **Compiler feedback after a write helps a little and harms nothing**
   (the reactive arm; small effect, zero regressions in its trials).
6. **Two earlier readings were wrong and are retracted in the journal.**
   The first run's +13 included rows from a dead server and from
   thread-limit exhaustion; cleaned, it was unresolved (+10, p = 0.064).
   Its "negative on the champion" was the thread-limit artifact. Fixing the
   harness and re-measuring, rather than re-analysing, produced the
   result above.

Not done: the held-out Zig set, the escalation A/B, and a single-slot
churn floor.

## The rules these two programs left behind

- Compare against the strongest deployed baseline and build the same-byte
  control; a rung chosen from below proves little.
- Pair by chunk or by unit, and decide inside two standard errors only
  with paired statistics.
- One quantization step from the reference, or a provenance column.
- Write the configuration and the decision rule down, with a time, before
  the run.
- Before believing a failure count, look at how the failures failed.
- A surrogate that improved is not a result.
