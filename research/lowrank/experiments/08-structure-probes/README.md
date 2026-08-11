# E08 — structure probes: three bets from the arXiv sweep, measured

The structures survey (`prior-art/arxiv-structures.md`) ranked candidate
structures to beat dense low-rank's scale-invariant capture-per-byte
constant. Tonight's falsifiable three, all on 0.6B / Q2_K-imat:

## 1. Q8_0-quantized factors — **WIN (free bytes)**

r64 adapter, F16 → Q8_0 factors: 80.8 → 42.9 MB (53%), PPL 31.29 →
31.26 (parity/noise). **Factor quantization halves adapter bytes at zero
quality cost** — every budget doubles its effective rank. Ship all
future adapters Q8 (constraint: rank multiple of 32). Matches LoRC's
≤0.02 ppl claim. **The bet, measured: r128-Q8 = 85.8 MB → PPL 28.46,
identical to r128-F16 (28.48, 161.5 MB) at HALF the bytes — i.e. at
r64-F16's byte cost the quality jumps 31.29 → 28.46.** First lever that
beats the dense-F16 capture-per-byte law outright: constant ×2.

## 2. Exact-column patches (one-hot A) — **null at 0.6B**

Implemented as `--columns` mode (a column patch IS a rank-k adapter with
one-hot A — same serving cost, zero new kernels). On the two kinds where
diagonal whitening is weakest: col-ffn_down KLD 0.732 vs SVD 0.723;
col-attn_o 0.749 vs 0.749 (tie). SVD's in-metric optimality survives its
metric's bias, at this scale. Kept open for 27B (columns are SVD-free ⇒
cheap there; the outlier head strengthens with width).

**27B rematch (end of block 2): columns close the gap.** col-r64 F16
(870 MB): KLD 0.1447 / top-1 84.59% / PPL20 7.779 — beats uniform-r64
SVD-F16 on PPL (7.869), within noise of the allocated-Q8 flagship
(0.1415), while capturing LESS whitened energy (0.05 vs 0.07). Two
findings: (a) the outlier head strengthens relatively with width exactly
as the literature predicts — columns went from losing at 0.6B to tying
at 27B; (b) whitened energy decouples further from functional value at
scale (worse in-metric, equal KL). Column patches are also SVD-free
(seconds to extract at 27B vs ~10 min) and trivially Q8-able —
col-r128-Q8 at the same bytes is the first experiment of the next
session's undercard, before the full-covariance main event.

## 3. Cross-layer shared basis — **null, decisively**

rsvd probe (v2 — v1's exact concat-SVDs ate the CPU and were killed;
lesson: probe with rsvd first): per-kind shared basis vs per-layer own
basis, whitened capture at r64:

| kind | own@64 | shared@64 | shared@128 |
|---|---|---|---|
| attn_q | 0.44 | 0.08 | 0.14 |
| attn_k | 0.50 | 0.13 | 0.23 |
| attn_v | 0.45 | 0.12 | 0.20 |
| attn_o | 0.29 | 0.09 | 0.17 |
| ffn_gate | 0.31 | 0.07 | 0.12 |
| ffn_up | 0.25 | 0.05 | 0.09 |
| ffn_down | 0.27 | 0.14 | 0.21 |

Whitened residual subspaces do not overlap across layers — shared@128
loses to own@64 everywhere. The ~12× amplification lane is CLOSED for
left-basis sharing on this model (consistent with arXiv:2605.30836's
weight-space negative result; activation weighting does not rescue it).

## Standing after E08

Surviving levers for beating the r/d law: **Q8 factors (proven)**,
**measured-sensitivity allocation (27B margin unresolved — the row WAS
run (E07, 0.5σ); pending is the PAIRED verdict, not the run:
ABLATION-PLAN T2.9. Wording fixed 2026-08-04 per coherence-audit
P3.7)**,
**full-covariance whitening (instrumentation blueprint filed)**,
**rotate/concentrate-then-quantize (fold-in at requant time)**. Dead:
energy water-filling, exact columns (at 0.6B), shared bases.
