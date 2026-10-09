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

Models: Qwen3-0.6B, Qwen3.5-0.8B, Qwen3.6-27B (heretic-v2), Qwen3.8-27B,
Qwen3.6-35B-A3B (MoE). Instrument: KL divergence against BF16 reference
logits at a 512-token context, with paired per-chunk statistics.

### Findings that hold

1. **Smaller weights decode faster.** Batch-1 decode is
   memory-bandwidth-bound. The 27B at Q6_K uses 23.6 GB and runs at 61
   tok/s; at Q2_K it uses 13.0 GB and runs at 99.7 tok/s. Accuracy is the
   only price of compression. (`lowrank/RESULTS_ROLLUP.md`)

2. **Re-rounding is a zero-byte quality lever.** Keep llama-quantize's
   grids byte-for-byte, re-choose only the codes under an
   activation-covariance metric, and write a standard GGUF.
   - 0.8B, Q2_K: perplexity −15%, KLD −34%, top-1 +6.3 points
     (paired t = 30 to 45). (`experiments/24-yaqa-lite/`)
   - 27B, Q2_K: KLD −13.3%, top-1 +1.07 points (t = −7.0, n = 100);
     perplexity does not move. Only the Q2_K tensors are swept, and the
     pair is a requantization of a Q6_K file, not a single step from BF16.
     (`experiments/27-bf16-rederivation/results-100ch-paired.txt`)
   - It depends on calibration. Calibrated on wikitext alone it improves
     unseen prose (−5.5% PPL) and is **worse than the untouched model on
     code** (3.470 vs 3.083 PPL). Calibrated on a wikitext/code mix it
     improves both. A tool built on this needs mixed calibration and a
     held-out check by default. (`paper/DEPLOYABLE-WINS.md`)

3. **Low-rank correction works and still loses to spending the same
   bytes on quantization types.** At 27B, 100 chunks, every artifact one
   quantization step from BF16:
   - the corrected configuration beats the adjacent rung Q3_K_S
     (KLD t = −2.85);
   - a bare mixed-type control built at the same bytes beats the
     corrected configuration (t = +2.62);
   - the correction's gain over its own base is real (−0.0090 KLD,
     t = −8.5).
   The order at equal bytes is: type allocation, then correction, then
   the uniform rung. (`experiments/27-bf16-rederivation/`)

4. **The low-rank mechanism does not matter at equal bytes.** Correcting
   after quantization, carving the low-rank part out before quantization,
   and sharing a basis across tensors give indistinguishable quality at
   27B (all |t| < 2, n = 100) although their capture numbers differ
   widely. Only type allocation does better. One by-product is useful: a
   shared basis ties the flagship at 16.6% fewer adapter bytes.
   (`experiments/28-glowq-shared-a/`, `experiments/29-srr-split/`)

5. **Capture falls with width, and not as r/d.** A regression over 1,146
   tensors on four models fits capture ≈ a·r^0.68·d^−0.76 (diagonal
   whitening) and a·r^0.37·d^−0.52 (full Gram). The linear r/d rule is
   the worst of six forms tried. The width axis has a single step
   (1024 → 5120), so this is a fit, not a law.
   (`experiments/33-t110-capture-width/REPORT.md`)

6. **On an MoE the correction loses decisively.** Per-expert correction
   improves its base and loses to promoting one tensor group to Q4_K at
   6.9 paired standard errors. A basis shared across experts captures
   barely more than a random one. (`experiments/23-moe/`)

7. **NVFP4 from llama-quantize is a poor carrier on this architecture.**
   At 0.8B it is the worst 4-bit option (KLD 0.207 against 0.047 to 0.088
   for the K-quants at equal bytes); the correction halves that and still
   loses. (`experiments/34-e16-nvfp4-08b/`)

8. **Better surrogate, same or worse outcome — seven times.** Energy
   captured by rank allocation, a two-sided Kronecker metric, further
   descent of the same objective, importance moved into column scales,
   capture at equal bytes, capture of the base, and short-context KLD
   across refinement families each improved while the thing they stand in
   for did not. The table is §4.6 of the paper.

9. **Against a trained competitor, our instrument and a task suite
   disagree.** On the released GSQ-RCO checkpoints of Qwen3.8-27B
   (`experiments/32-t117-gsq-head-to-head/`):
   - a one-shot correction (about 13 CPU-minutes) beats them on KLD at
     both rungs on wikitext (t = −11.5, −9.5) and on FineWeb-Edu
     (t = −3.9, −3.4), and its gain over its own base holds on FineWeb-Edu,
     a corpus it was not calibrated on;
   - on wikitext KLD the trained checkpoint scores **below its own
     untrained baseline**; on FineWeb-Edu that gap nearly closes;
   - at a 2048-token context the trained checkpoint has the better
     perplexity at both rungs;
   - on the 112-unit agentic coding suite the trained IQ3_S checkpoint
     passes 88 units and its untrained baseline 76 (net +12, exact
     McNemar p = 0.012).
   So short-context KLD does not rank artifacts across refinement
   families. We did not run our own corrected artifact on the coding
   suite, and the IQ2 capability pair was not completed.

10. **Serving patches.** Fused kernels raise adapter decode by 79% at
    0.6B and 2.7% at 27B (interleaved, N = 10). A separate allocator fix
    raises base decode by 19.6% at 0.6B for any CUDA build with graph
    optimization. All from one GPU; not re-measured after the rebase to
    b10865. (`experiments/14-fused-kernel/`, `experiments/25-alloc-concurrency/`)

### What was not done

Of the seventeen runs the ablation audit priced, six were not run: the
calibration-mix grid, interpolation controls at 0.6B/0.8B, a held-out
pass over the ladder comparisons, the recovery curve with the recipe held
fixed, the equalization mechanism ablations, and a calibration-sampling
bound. Also open: a third model width, a single-step 27B re-round pair,
Q3_K/Q4_K/I-quant codecs for the re-rounder, and any capability run of our
own artifacts. The paper's §6 lists these beside the claims they limit.

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
