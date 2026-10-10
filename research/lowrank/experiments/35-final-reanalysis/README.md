# E35 — final reanalysis for the paper revision (2026-10-09)

CPU-only reanalysis of existing per-chunk logs, written in response to the
referee review and the numbers audit of `paper/final/`. No model was run and
no GPU was used. Nothing here changes a measured number; it adds robust
statistics, a bound, two paired comparisons that had not been computed, and
one metadata check.

| file | what it is |
|---|---|
| `reanalysis.py` | the whole analysis; `python3 reanalysis.py > RESULTS.md` |
| `RESULTS.md` | its output, verbatim (do not edit by hand) |

## Method

- Per-chunk values come from the project's canonical parser, imported
  unchanged from `../24-yaqa-lite/paired_stats.py` (`parse_kld_rows`,
  `parse_ppl_stream`, `per_chunk_from_cum`): llama-perplexity prints running
  means, and per-chunk means are recovered by differencing. The recovered
  means reproduce every paired mean, standard error, t and chunk count in
  `../27-bf16-rederivation/results-100ch-paired.txt` and
  `../32-t117-gsq-head-to-head/paired-*.txt`.
- Added per pair: 95% percentile bootstrap CI (10,000 resamples, seed
  20261009), exact two-sided sign test, Wilcoxon signed-rank test, a block
  bootstrap over non-overlapping contiguous blocks of 10 chunks, a one-sample
  t on the block means, and the lag-1 autocorrelation of the differences.
- Reference identity between logs from different runs is checked by
  recovering the reference's per-chunk NLL from each log
  (`ln PPL(Q) − ln(PPL(Q)/PPL(base))`) and comparing the series.
- Dependencies: Python 3, numpy 2.4.4, scipy 1.17.1. Section A4 additionally
  reads the tensor-info table of the MoE reference GGUF with llama.cpp's
  `gguf-py` if the file is on disk (memory-mapped; no weights read); it falls
  back to the quantize log alone if not.

## What it found (see RESULTS.md for the tables)

1. **27B, corrected configuration vs Q3_K_S (n = 100).** The margin
   (−0.0048 KLD, t = −2.85) survives the checks that do not assume
   independent chunks: block-bootstrap 95% CI [−0.0073, −0.0019], t on block
   means −3.19 (df 9), Wilcoxon p = 8.5e-5, lag-1 r = 0.00. It is **not
   uniform across the corpus**: chunks 1–40 give −0.0001 (t = −0.04) and
   chunks 41–100 give −0.0079 (t = −3.32); the two blocks differ at Welch
   t = 2.51 (p = 0.014). The 40-chunk run is the exact prefix of the
   100-chunk run (identical per-chunk values). The non-uniformity comes from
   the mixed base against Q3_K_S (+0.0092 on chunks 1–40, +0.0010 on 41–100),
   not from the correction increment, which is the same in both blocks
   (−0.0093, −0.0089).
2. **27B, corrected configuration vs the mixed-type control.** +0.0063,
   t = +2.62; block-bootstrap CI [+0.0032, +0.0100]; Wilcoxon p = 0.0023. A
   Bonferroni factor of six on the t p-value gives 0.061. No difference in
   NLL (t = +0.41).
3. **Mixed base vs Q3_K_S at n = 100:** +0.0043, t = +2.38 (the paper had
   quoted the n = 20 value, t = +3.6); t on block means +2.18 (p = 0.057);
   NLL favours the mixed base (t = −3.90).
4. **Bound on the low-rank placements.** 90% intervals of each placement
   minus the post-hoc correction: carve [−0.0011, +0.0045], shared basis at
   byte parity [−0.0015, +0.0004], shared basis at rank 128
   [−0.0018, +0.0001]. Largest bound 0.0045 KLD = 49% of the correction's
   increment (0.0090); for the two shared-basis variants, 0.0018 = 20%.
5. **Re-rounded Q2_K against stock rungs.** At 0.8B the stock IQ3_XXS file
   (412 MB, 24 MB smaller) has lower KLD than re-rounded Q2_K (436 MB) by
   0.0657 (paired t = +10.8, 39/40 chunks; same reference and chunks,
   verified). At 27B the only comparable rung measured, IQ3_XXS, is 0.6 GB
   *larger* than the legacy Q2_K pair and is lower by 0.0526 on the 20 shared
   chunks (t = +4.6, 20/20); the two sides differ in provenance.
6. **MoE reference.** `ffn_down_exps` is Q5_K (37 layers) and Q6_K (3) in
   the vendor UD-Q4_K_M reference, from both the quantize log and the GGUF
   header. It is not Q4_K, so the promotion control is a real requantization
   step and is not near-lossless against the reference by construction.
7. **2048-token perplexity, paired (n = 145).** GSQ-RCO vs its baseline:
   t = −3.33 (IQ2; ahead on 77/145 chunks, sign p = 0.51, Wilcoxon
   p = 0.035) and t = −4.15 (IQ3; 89/145, sign p = 0.008). At 512 tokens the
   paired NLL difference is unresolved (t = +1.33, +0.58).

## Limits of this reanalysis

- Per-chunk values are recovered from a cumulative stream printed to 4–5
  decimals, so each carries rounding noise of order (chunk index × 1e-5).
  This is small against the per-chunk spread and cancels in the mean.
- With n = 40 a block bootstrap over blocks of 10 has four blocks; those
  intervals are shown and should not be leaned on.
- Blocks are contiguous runs of 512-token chunks, not article boundaries:
  the logs do not record which article a chunk comes from.
- All intervals are conditional on one calibration sample and one evaluation
  corpus; they quantify evaluation-chunk variance only.
