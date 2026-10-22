# Literature sweep, 2026-10-22 (before TMLR submission)

Eight parallel agents swept arXiv (plus OpenReview, Hugging Face papers,
OpenAlex, llama.cpp GitHub) against the paper's claims C1-C5 as stated in
`BRIEF.md`. Roughly 9,000 titles/abstracts screened in total, about 140 papers
read in full. The arXiv export API rate-limited the shared IP for much of the
session, so several agents screened by title through arxiv.org search or the
Hugging Face papers API; each report states its own coverage. Raw stores
(abstract dumps, scripts) were kept out of git, at
`~/Documents/openbeast-litsweep-raw-2026-10-22/`.

| Report | Slice |
|---|---|
| R1-lowrank.md | low-rank error correction (C2) |
| R2-rounding.md | re-rounding / code re-selection (C1, C5) |
| R3-calibration.md | calibration data, off-distribution damage (C1, C2) |
| R4-metrics.md | KLD vs likelihood vs tasks (C3) |
| R5-gguf-alloc.md | GGUF research, trained checkpoints, bit allocation |
| R6-serving-refs.md | fused adapter kernels (C4); reference venues |
| R7-vq-moe-metric.md | vector/lattice quantizers, richer metrics, MoE |
| R8-newest.md | everything June-October 2026; next steps |

The twelve load-bearing ids were re-fetched from arxiv.org by hand
(titles and dates match): 2610.11226, 2610.08164, 2601.20088, 2606.19558,
2609.00049, 2609.33923, 2606.11244, 2609.04748, 2605.05693, 2609.38599,
2610.00694, 2406.15524.

## Verdict

No paper contradicts a number in the paper. The framing changes:

- **C1 (re-rounding)** is published in general form outside GGUF (DRQ
  2610.11226; Shin et al. 2406.15524 for pruning; QEP 2504.09629; refit-beats-frozen
  in JARQ 2609.38599 and SchurQuant). The paper's contribution is the
  confirmation on llama.cpp's frozen K-quant grid with held-out code and
  FineWeb-Edu at 8B/27B. "No gain at size" is scoped to the unrotated,
  one-shot, layer-wise sweep (GSQ and a rotated GGUF preprint do gain).
  The proposed regularizer already exists (SARQC 2605.05693).
- **C2 (correction vs same-byte controls)** is not pre-empted: no paper
  runs a same-byte stock or mixed-type control against low-rank correction.
  It is scoped to symmetric (QERA/EoRA-class) correction, since
  Align-then-Correct (2610.08164) recovers far more with an asymmetric
  two-stage recipe (and builds no control).
- **C3 (KLD vs likelihood)**: the concept is published (2606.19558,
  "Displacement Is Not Direction"; QAD 2601.20088); the paired matched-byte
  case is ours. The paper's explanation "a file trained against data" was
  wrong: GSQ and RCO both fit the BF16 model's outputs. Check run on our own
  logs (`experiments/36-submission/logs-b/ctx2048-*.log`): GSQ-RCO IQ3_S's
  wikitext perplexity at 2,048 tokens is 5.833 against the BF16 reference's
  5.856 (ln ratio −0.0039 ± 0.0021), so the likelihood edge is not recovered
  quality; lower perplexity than the reference is itself a departure
  (REAL-Q 2609.00049).
- **C4 (kernels)**: llama.cpp adapter cost is still ours; "few ship a fused
  decode path" was outdated (SPEAR 2606.11244, TwinQuant, FLRQ, DecDEC).
- Factual fix: llama.cpp's IQ2_XXS/IQ2_XS (E8) and IQ3_XXS (D4) are lattice
  codebooks, so "vector and lattice quantizers outside GGUF" was wrong.
- Venues found for ten arXiv-only references (R6 §b).

## Next experiments (ranked, 5090 + llama.cpp)

1. Strongest training-free correction (asymmetric, output-Fisher,
   2610.08164) against a principled same-byte type allocator (spectral probe,
   2609.33923), on held-out corpora.
2. Robust re-rounding: DRQ-style objective, Gram shrinkage toward the
   diagonal, accept a change only if a held-out Gram's error does not rise.
   Mostly CPU on existing captures.
3. Rotate then re-round inside IQ2_XS/Q2_K at 8B (llama.cpp #21038 rotates
   activations).
4. Zero-decode-cost correction: fit only K-quant block scales.
5. Fidelity vs quality on the GSQ files: total variation, flip rate, NLL,
   tasks, and BF16-generated text, from logits already captured.
