# E01 — SVD spectrum census of Qwen3-0.6B

**Hypothesis (H1):** LLM weight matrices are not low-rank — the rank needed
to capture ≥95% of Frobenius energy will exceed the rank budget that 2×
compression allows, for most projection types. (If H1 is FALSE, plain
truncated SVD is back on the table and the whole quant-residual detour is
unnecessary — that would be a happy surprise, not a failure.)

**Secondary question:** which projection types are MOST compressible? The
per-layer/per-type ranking directly seeds the rank-allocation work (E05).

## Method

- Model: `weights/Qwen3-0.6B-Q8_0.gguf` (Q8_0 dequant error ≪ truncation
  effects being measured; spectra are faithful to F16 within noise).
- For every 2D tensor: dequantize to float32 (gguf-py), economy SVD
  (numpy LAPACK), record the full singular-value vector.
- Report per tensor: shape, r90/r95/r99/r999 (rank for that energy
  fraction), and the compression ratio C(r95) = mn / (r95·(m+n)).
- Energy = cumulative σ² fraction (Frobenius). Spectra saved to
  `spectra.npz` for plotting; summary to `census.csv`; findings appended
  below after the run.

## Run

```bash
python3 research/lowrank/experiments/01-svd-spectrum/census.py \
    weights/Qwen3-0.6B-Q8_0.gguf \
    --outdir research/lowrank/experiments/01-svd-spectrum/
```

Build: llama.cpp gguf-py @ 0ef6e55ed, numpy 2.4.4.

## Result — 2026-08-03, H1 CONFIRMED

197 tensors, 43 s. Full data: `census.csv`, spectra in `spectra.npz`.

| kind | count | r95/full rank | C@95% (compression) | stable rank |
|---|---|---|---|---|
| attn_q | 28 | 61.5% | **1.09** | 50 |
| attn_k | 28 | 49.1% | **1.02** | 59 |
| attn_v | 28 | 55.2% | **0.91** | 138 |
| attn_o | 28 | 68.6% | **0.98** | 135 |
| ffn_gate | 28 | 76.5% | **0.98** | 77 |
| ffn_up | 28 | 78.5% | **0.96** | 192 |
| ffn_down | 28 | 76.6% | **0.98** | 131 |
| embed | 1 | 89.4% | 1.11 | 9 |

**Reading:** capturing 95% of Frobenius energy requires ~50–80% of full
rank everywhere → factorization at that rank saves ≈ nothing (C ≈ 1.0), and
for V/FFN it *costs* memory (C < 1). Even the friendliest projection
(attn_q, C=1.09) is nowhere near 2×. The stable-rank column shows why the
"LLMs are low-rank" intuition persists: the TOP of the spectrum is
concentrated (stable rank 50–200 of 1024), but the tail is long and fat,
and 95% energy lives deep in it.

**Consequences:**
1. Plain truncated SVD of W is DEAD as a compression route at our targets.
   (Null hypothesis E02 is now optional — run it only if we need the
   perplexity-cliff curve as a paper baseline.)
2. The entire 2–10× ambition rides on structure OTHER than W's spectrum:
   quantization residuals (E03), activation-weighted geometry (E05), or
   both. E03 is promoted to the critical path.
3. Caveat for the paper: Frobenius energy ≠ functional importance —
   activation-aware weighting can shift effective spectra substantially
   (that is E05's whole premise). This census bounds the *unweighted* case
   only.
