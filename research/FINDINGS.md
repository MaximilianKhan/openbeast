# OpenBeast research findings

State of record as of 2026-10-16. Two research programs have run on this
rig (one RTX 5090): **beast-rank**, on post-training repair of quantized
models in llama.cpp, and **langaware**, on raising a local model's success
rate in a language it was undertrained on. This page states what each
found, how sure we are, and what was not done. Every number has a file
behind it; the paths are given.

The formal write-up of beast-rank is
[`lowrank/paper/final/paper.pdf`](lowrank/paper/final/paper.pdf) — *What
Post-Training Repair Buys on llama.cpp Quantization: A Paired Audit on and
off the Calibration Distribution* (Max Khan; TMLR submission build
`paper-tmlr.pdf`, preprint `paper-tmlr-preprint.pdf`). The 2026-10-15/16
submission campaign that produced its current numbers is
`lowrank/experiments/36-submission/` (pre-registration `PLAN.md`, results
`RESULTS-*.md`).

## beast-rank: what repair of an aggressively quantized model buys

Models: Qwen3-0.6B, Qwen3.5-0.8B, Qwen3.6-27B (heretic-v2), Qwen3.8-27B,
Qwen3.6-35B-A3B (MoE), and Llama-3.1-8B (the one non-Qwen model). One GPU.
Instrument: KL divergence against BF16 reference logits with paired
per-chunk statistics, scored on llama.cpp b10865 (every hybrid-architecture
row was re-scored after llama.cpp's Gated-DeltaNet normalization fix,
b10829), on **three corpora**: wikitext-2 test (the calibration
distribution), FineWeb-Edu and a code corpus no calibration touched.

### Findings that hold

1. **Re-rounding a Q2_K file's codes helps small models and not large
   ones.** (`36-submission/RESULTS-D.md`, `RESULTS-B.md`, `RESULTS-G.md`)
   - 0.8B: −33% KLD on wikitext (t = −28.4, 40/40 chunks).
   - Llama-3.1-8B and Qwen3.6-27B: −1.7% / −3.7% on wikitext, unresolved
     (|t| < 2); worse on both held-out corpora (t = +7.8 to +12.6). The
     project's earlier 27B "−13.3%" was a legacy Q6_K-derived pair and does
     not reproduce under single-step provenance or with the earlier Grams.
   - Mechanism at 27B (independent recomputation,
     `36-submission/checks/`): the sweep halves the whitened error of the
     tensors it covers while their unweighted error rises 16–25%. Those
     tensors carry 76–80% of the file's KLD (oracle diagnostic B5); the
     re-round removed ~5% of it.
2. **Re-rounding transfers only to the kinds of text its calibration
   covers.** 0.8B grid of five calibrations × three corpora: prose-only
   calibration damages code (t = +13.6, +8.9), code-only damages prose
   (t ≈ +20); calibrations containing both improve all three. Calibration
   sampling alone moves the gain by ≤ 0.018 KLD.
3. **Refitting the grid beats freezing it.** Free-grid GPTQ at identical
   format and bytes beats frozen-grid re-rounding in 11 of 12 cells
   (0.8B, 8B, 27B), and still does not beat the untouched file at 8B/27B.
   (`RESULTS-E.md`)
4. **Low-rank correction improves its base everywhere, about half as much
   off-distribution, and more the thinner the base** (recipe-fixed 27B
   recovery: 7% / 12% / 32% at Q2_K / IQ2_XS / IQ1_S on wikitext; 3% / 8% /
   16% on FineWeb-Edu). (`RESULTS-D.md` §T1.9)
5. **Against the stock types the correction falls short.**
   - Qwen3.6-27B: ahead of Q3_K_S by 5% on wikitext (t = −2.62, replicates
     the pre-fix −2.85), **behind it on FineWeb-Edu (t = +3.26)**, level on
     code; a same-byte mixed-type control beats it on all three corpora
     (t = +2.4, +6.5, +7.1). (`RESULTS-A.md`)
   - Qwen3.5-0.8B: a pure-quantization control 1.1% smaller has a third of
     the corrected crown's KLD on every corpus, 40/40 chunks each
     (t = −21 to −32). (`RESULTS-D.md`)
   - Llama-3.1-8B: ahead of Q3_K_S on wikitext (t = −17.4) and FineWeb-Edu
     (t = −3.8), level on code. (`RESULTS-G.md`)
   - IQ3_XS at fewer bytes beats the corrected file on every corpus at 8B
     and 27B (t ≤ −3.58).
6. **Three placements of the low-rank bytes could not be distinguished
   within a stated bound** at 27B (largest 90% bound 0.0045 KLD).
   (`35-final-reanalysis/RESULTS.md` A2)
7. **KL divergence to the reference ranks fidelity, not quality.** At a
   2,048-token context, released GSQ-RCO checkpoints are further from BF16
   than their untrained baselines (t = +8.0, +5.0) while assigning the text
   higher likelihood in the same run (t = −1.6, −2.5). (`RESULTS-C.md`)
8. **Better surrogate, same or worse outcome — seven times** (§4.7 of the
   paper), the largest being the 27B re-round.
9. **Equalization before quantization harms at every strength and
   without an imatrix** — no interior optimum; not double-counting.
10. **Serving patches**: fused kernels +79% decode at 0.6B, +2.7% at 27B;
    allocator fix +11–20% at 0.6B, nothing at 27B. One GPU; not
    re-measured after the rebase.
11. **MoE**: per-expert correction loses to a one-line promotion (n = 20,
    Q4-referenced). Capture vs width: sublinear in rank, one width step.

### In flight / not done

- Capability replication (`36-submission/f_capability.sh`): three runs per
  arm of GSQ-RCO IQ3_S, UD-IQ3_S and UD-IQ3_S + our correction on the
  112-unit coding suite; ~12 h per run. The paper's §5.5 waits on it.
- Not run: a 0.6B control; a BF16-referenced MoE table; learned-rounding
  and vector-quantizer baselines; a third width inside one family.

### Corrections made along the way

The record contains its own errors and their repairs, now including two
from this campaign: the 27B re-rounding gain (legacy provenance; gone under
single-step), and the 27B margin over Q3_K_S (on-distribution only). Each is
in `lowrank/JOURNAL.md` and the paper's §7.6.

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
