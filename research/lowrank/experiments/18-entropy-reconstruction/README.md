> ⚠ see review/ corrections 2026-08-04

# E17/E18 — probability-driven reconstruction (Max directive 2026-08-04)

Charter: TODO.md "MAX DIRECTIVE". Literature grounding:
`prior-art/arxiv-entropy-reconstruction.md` (the sweep also MEASURED two
lanes dead on our own bytes: context modeling — adjacent-code mutual
information 0.0042 bits; entropy coding for storage — codes 97%
saturated, 4.1% lossless ceiling, parked as bytes-only).

## Surviving lane: per-code conditional-mean reconstruction

Lloyd's decoder half with frozen codes (BOF4/AF4 lineage): replace the
grid value for code j with E[w | code=j], per tensor — 4 floats/tensor,
~zero bytes, one `prmt.b32` LUT in the fused dequant path. Our claimed
edge (no prior work surfaced; the novelty sweep hit its search budget —
treat as unswept per review F22): fit the levels under the WHITENED/KLD
metric from our Grams instead of MSE.

## Step-1 kill-test — SURVIVED (2026-08-04)

Imatrix-weighted mean reconstruction bias per code, as a fraction of the
grid step (kill rule: <0.01 everywhere), 0.6B Q2_K:

| kind | code 0 | code 1 | code 2 | code 3 |
|---|---|---|---|---|
| attn_k | 0.009 | 0.024 | 0.025 | 0.015 |
| attn_q | 0.008 | 0.021 | 0.023 | 0.015 |
| ffn_gate | 0.007 | 0.024 | 0.022 | 0.015 |
| ffn_up | 0.009 | 0.020 | 0.023 | 0.015 |

Systematic 1.5-2.5%-of-step biases on codes 1-3, uniform across kinds —
llama-quantize's least-squares affine leaves nonlinear per-code
structure on the table. Expected effect size: the honest 0.1-1% KLD
band (upper half plausible at 2-bit).

## Step-2a (offline half) — tables fitted, effect size measured

Per-tensor whitened-metric levels fitted (step-scaled additive form
delta_j = c_j * dl so one 4-entry table serves all blocks; saved to
`tables06b.json`). **Whitened-error reduction: 1.17-1.32% mean per kind
(max 1.65%)** — the top of the sweep's honest band. Translation: expect
~1% KLD-class improvement, zero bytes, near-zero kernel cost.

**Verdict: a freebie, not a headline.** Ship inside the eventual fused-
kernel PR as the zero-cost companion (a 4-entry LUT riding the dequant
path); do not spend further standalone effort. Remaining: the serving
LUT itself (kernel Phase 3+ owns the tree) + the dither control arm.
