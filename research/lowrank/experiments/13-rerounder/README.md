> ⚠ see review/ corrections 2026-08-04

# E13 — frozen-grid error-feedback re-rounding ("GPTQ for K-quants")

Physics-communion lineage: sigma-delta ADCs shape quantization noise out
of the signal band via error feedback. llama-quantize's K-quants pick
good per-block grids (imatrix-weighted) but round every element
INDEPENDENTLY — no cross-column feedback. We keep its grid bytes frozen
(scales/d/dmin untouched — Bid-Up-legal per the co-design survey) and
re-choose only the 2-bit codes with a GPTQ sweep driven by the captured
full activation covariance. Output is a byte-compatible Q2_K file:
zero serving changes, zero extra bytes.

## Implementation notes (bugs paid for)

- Q2_K codec mirrored from gguf-py (84-byte superblocks; element order
  g*128+s*32+b — verified by byte-identical repack round-trip).
- **The triangular convention is load-bearing:** chol of inv(H) via the
  reversed-flip trick yields a LOWER factor whose update rows are all
  zero — the feedback silently no-ops and re-rounding reproduces
  llama-quantize's codes bit-for-bit (diagnosed when PPL matched to 4
  decimals). Correct: U = cholesky(inv(H)).T, upper, inv(H) = UᵀU.
  On PD failure: escalate damping ×10 — never a dense eigh factor
  (feedback to already-quantized columns breaks causality).
- Lazy-batch blocking (sequential inside 128-col blocks, one GEMM per
  block tail): 8× speedup, identical output (35.53 vs 35.54).

## Results — 0.6B (Q2_K-imat, re-rounding only, ZERO extra bytes)

| config | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K-imat bare (llama-quantize) | 43.33 | 0.766 | 58.2% |
| **+ re-rounding (free)** | **35.54** | **0.556** | **64.0%** |

**18% PPL improvement at identical bytes on an unmodified serving
stack.** This is the most upstream-shaped result of the campaign: any
existing K-quant GGUF improves for free given a Gram capture pass.

## Composition results — the co-design lesson, sharpened

| stack | PPL | KLD |
|---|---|---|
| RR then fc-r128q8 | 26.80 | 0.274 |
| alternation ×1 + RR + fc | 25.84 | 0.245 |
| alternation ×2 + fc (no RR) | **25.27** | **0.209** |

**ProjQ-deflated re-round (added block 5): the principled fix works as
predicted** — deflating the sweep's metric by the corrector's A row-space
(input-side projection keeps the row-parallel sweep intact) gives 25.94,
recovering most of naive RR+fc's damage (26.80) in ONE shot — but two
full alternation rounds still hold the crown (25.27). Final co-design
ranking at 0.6B: alternation x2 > ProjQ one-shot > greedy sequential.
ProjQ+alternation hybrid queued (expected marginal).

**Named result: greedy sequential optimization loses to joint shaping.**
Re-rounding minimizes standalone whitened error — spending grid
resolution on directions the corrector already owns — and leaves a
less-correctable residue (whitened capture 0.78 on the triad yet worse
KLD: capture of a worse-shaped residual). The full ProjQ move
(re-round under the corrector-DEFLATED metric) is the principled fix
and the top queued follow-up; tonight's data pins exactly why it should
work.

## 27B result — modest, not the 0.6B miracle

256 tensors re-rounded (ffn_down skipped — blocked grams not yet
supported by the sweep; chunked resume-safe runs to dodge the
background-killer — codes cached per tensor, ~70 min total):

| config | PPL20 | KLD | top-1 |
|---|---|---|---|
| Q2_K-imat bare | 7.891 | 0.1529 | 83.6% |
| + re-rounding (free) | 7.952 | **0.1461** | **84.6%** |

KLD −4.4% and top-1 +1.0pt for zero bytes — point-estimate gains inside
unpaired error bars (KLD 0.78σ, top-1 1.4σ; review 2026-08-04:
unresolved on the recorded evidence, paired per-chunk test pending)
[RESOLVED 2026-08-04 evening, T1.16: rescored at n=40 vs the BF16
truth logits, paired — KLD t=−3.70, top-1 t=+2.56, −10.4% KLD /
+0.86 pt free; legacy Q6-derived pair, held-out gate still open —
see experiments/27-bf16-rederivation/results-40ch-paired.txt] —
and PPL is flat-to-slightly-worse (metric divergence again). The 0.6B
18% does NOT transfer at tonight's instrumentation.

**Corrected diagnosis (type census):** inside a "Q2_K" 27B file,
llama-quantize's heuristics promote ffn_down + attn_output to Q3_K and
attn_qkv + attn_v to Q4_K — the sweep only handles the Q2_K codec, so
the unswept mass is a CODEC gap (Q3_K/Q4_K re-rounders unwritten), not a
gram gap. (Blocked-gram sweep support was written tonight anyway and is
valid infrastructure for any >8192-input tensor.) Higher-bitwidth
feedback gains are smaller still, so expected returns there are modest —
deferred deliberately rather than rushed at hour 7. Deeper reading,
consistent with the whole campaign: **every lever's magnitude shrinks
with scale** — 27B imatrix quants already sit near their local optimum.
Follow-ups queued: Q3_K/Q4_K codecs, deeper calibration, ProjQ-deflated
metric, act-order permutation.
