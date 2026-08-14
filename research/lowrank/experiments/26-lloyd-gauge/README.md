# E26 — Fisher-Lloyd fixed point (M4) + principled gauge-fixing (M5)
# on Qwen3.5-0.8B Q2_K

Two pre-registered missions from prior-art/MANIFOLD-CANDIDATES.md, run
sequentially on the 0.8B rig (BF16 reference `weights/Qwen3.5-0.8B-
BF16.gguf`, base `experiments/22-qwen35-08b/q35-08b-Q2_K.gguf`, Grams
`data/gram08b`, BF16-truth logits `data/bf16ref08b-40.logits`). Byte
discipline held throughout: every model variant is exactly 436,408,832
bytes of stock-servable Q2_K. All comparisons are paired per-chunk
(experiments/24-yaqa-lite/paired_stats.py); PPL = full wikitext-2-test
(580x512), KLD/top-1 = 40 chunks vs BF16 truth.

Verdicts up front (both reworded 2026-08-04 per
adversarial-round2-experiments F7/F8 — the NEGATIVES stand untouched;
the mechanism/existence claims outran the evidence):
- **M4 (servable Lloyd): NULL — no codes fixed point WITHIN THE
  PRE-REGISTERED BUDGET; limit cycle at the floor.** (F8 fix: the old
  "the hypothesized fixed point does not exist" asserted non-existence
  from an 8-iteration cap of a non-monotone alternation —
  best-iterate tracking, more iterations, and higher-precision scale
  storage were untried.) The codes<->scales alternation never reaches
  a codes fixed point in <=8 iterations (92/96 tensors at cap inside a
  ~0.1% limit cycle); it cuts the whitened surrogate a further 2.6%
  (mean) below single-pass RR, and the end-to-end effect is
  statistically ZERO (paired |t| <= 1.3 on all metrics, point
  estimates marginally worse). Fourth surrogate-vs-outcome datapoint.
  Single-pass RR-input remains the zero-byte deployment recipe.
- **M5 (gauge-fixing): on this arch only DIAGONAL folds exactly, and
  full-strength diagonal equalization is strongly ANTI-HELPFUL
  (t = +28..+60 paired, worse on 40/40 KLD chunks).** The quantizer's
  own imatrix-weighted objective degrades 6–68% per tensor. Mechanism,
  as far as THIS run discriminates (F7 fix: the old "importance
  double-counting … the same correction applied twice" described a
  configuration never run — e26_equalize transforms the imatrix
  exactly, so in the equalized run importance is counted ONCE, in the
  columns; in exact arithmetic the weighted objective is
  gauge-invariant): **moving importance from the fit weights into the
  column scales is strongly harmful for Q2_K's shared-sub-scale
  format** — up to 32x dynamic range injected into 16-element
  sub-blocks the shared 4-bit sub-scales cannot span, plus loss of an
  informative weighting for the affine fit. Whether a double-counting
  component exists independently is NOT discriminated here; the
  separating controls (no-imatrix equalization; alpha sweep — which
  are diagnostic ablations, not F7-forbidden post-hoc selection) are
  queued as ABLATION-PLAN T1.13. Result scoped to the pre-registered
  alpha=1 extreme on one format.

## Reproduction gate (prerequisite, passed before any new build)

1. Recipe: `llama-quantize --imatrix data/gram08b/diag.imatrix.gguf
   --tensor-type 'blk\.24\.=q5_k' <BF16> <out> Q2_K` reproduces the E22
   base with **335/335 tensors byte-identical** (only the imatrix-path
   KV string differs).
2. E13-on-0.8B baseline: a from-scratch e13b input-only sweep (fresh
   codes cache) rebuilt `q35-08b-Q2K-rr-input.gguf` **byte-identical**
   to the E24 artifact, and re-evaluation reproduced the published
   numbers exactly: PPL 28.0902, KLD 0.323369, top-1 72.686 (vs bare
   33.0877 / 0.4902 / 66.42).

## M4 — the honest servable Lloyd

The E13 re-rounder optimizes codes on a frozen grid; the E17/E18 lane
showed llama-quantize's affine fit leaves per-code structure on the
table. Fusing them SERVABLY means the decoder may not change — so the
reconstruction-table half is restricted to what folds into bytes the
Q2_K format already stores: the per-superblock fp16 d/dmin (the affine
component). The 4-bit sc/m sub-scales stay frozen (pre-registered).

Alternation, both halves descending the SAME damped whitened metric
H = S + 0.01*mean(diag S)*I (S = input Gram):
- **codes-step**: E13's exact GPTQ/LDLQ column sweep on the current
  grid (identical code path to the published RR — iteration 1's
  codes-step IS single-pass RR).
- **scales-step**: codes frozen; per-ROW joint generalized least
  squares for (d, dmin) of all superblocks in the row under H
  (superblocks couple through off-diagonal S), ridge 1e-8, solution
  cast to fp16 (the stored format), keep-old guards on d<0/non-finite.
Stop: codes fixed point, |dJ|/J < 1e-5, or 8 iterations. Validation
before the run (`test_lloyd.py`): grid expansion == the verified E13
decoder on real bytes; byte-roundtrip repack; scales-step == explicit
whitened lstsq on 10 synthetic problems and J descends.

### Convergence (the fixed-point question the mission asked)

**No tensor reached a codes fixed point.** Iteration counts: 92/96 at
the pre-registered cap of 8; 3 stopped at 6 and 1 at 5 by the
J-plateau rule (min/med/max = 5/8/8). Universal shape: J drops for
3–4 iterations (iteration 1 captures 70% of the total drop, median
68%), then enters a small LIMIT CYCLE (~0.1% amplitude) rather than a
fixed point — the greedy-sequential codes-step and fp16 rounding of
the scales-step make the alternation non-monotone at the floor, the
same phenomenon as E11's two-round law and M6's damped-alternation
territory. Whitened-objective gain over the single-pass RR grid: mean
2.61% / median 2.55% / max 4.74%. Keep-old guards fired: 0.

### Results — Lloyd vs single-pass RR (the pre-registered comparison)

| config (436,408,832 B each) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K bare              | 33.0877 | 0.4902 | 66.42% |
| + RR input-only (E13)  | 28.0902 | 0.3234 | 72.69% |
| + Lloyd x8 (M4)        | 28.1153 | 0.3248 | 72.14% |

Paired per-chunk (Lloyd minus RR-input): dNLL +0.0009 ± 0.0022
(t = +0.40, 296/580), dKLD +0.0015 ± 0.0034 (t = +0.43, 18/40),
dtop −0.55pt ± 0.42 (t = −1.32) — **all unresolved; a 2.6% surrogate
improvement produced zero measurable end-to-end change** (the RR-vs-
bare win itself replicates at t = −27..−47, so the harness resolves
real effects fine). This is the fourth paired datapoint in the
campaign's surrogate-vs-outcome file (E24's Kronecker T made the
surrogate BETTER and the outcome WORSE; here better surrogate, flat
outcome): below some floor, whitened-proxy descent stops predicting
KLD. The E17/E18 step-1 bias measurement (1.5–2.5%-of-step) was real,
but the affine servable slice of it is worth ~nothing once the code
sweep has already spent the same metric.

## M5 — gauge-fixing: what folds EXACTLY on qwen35, measured

Graph reading (`llama.cpp/src/models/qwen35.cpp`): every candidate
insertion point for a rotation sits behind an RMSNorm **with learnable
gamma**; a rotation R cannot be absorbed into a diagonal gamma, and
pushing R through instead requires a QuaRot-style rewrite of the whole
residual stream (embedding, every reader/writer, conv/recurrent state
paths, MTP eh_proj concat). Out of scope by the mission's own rule —
**the exactly-foldable group on this architecture is DIAGONAL, so M5
is equalization, not rotation. That is the result.**

Consumer sets (verified against the graph, hard-fail if violated):
- `attn_norm` -> {attn_q, attn_k, attn_v} (6 classic layers) |
  {attn_qkv, attn_gate, ssm_beta, ssm_alpha} (18 linear layers)
- `post_attention_norm` -> {ffn_up, ffn_gate}
- blk.24 (MTP) skipped — no Grams (the known MTP imatrix blindspot).

Pre-registered config (single, no sweep): s_j = nearest **power of
two** to sqrt(diag(S)_j/geomean), gamma' = gamma/s, W' columns *= s.
The pow2 restriction makes the fold a bit-exact gauge transformation
(FP multiply commutes with pow2 scaling): the fold is verified
elementwise at build (hard fail on any inexact BF16 product) and
end-to-end on GPU — **equalized BF16 vs stored BF16 logits: KLD
0.000000, top-1 100.000%**. The imatrix transforms exactly with the
gauge (in_sum2' = in_sum2/s^2, pow2-exact); 186/186 entries carried,
138 rescaled. s range [0.5, 16]. Requant with the identical recipe
gives the identical per-tensor type layout at identical bytes.

### Results — equalized Q2_K vs bare Q2_K (paired)

| config (436,408,832 B each) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K bare                   | 33.0877 | 0.4902 | 66.42% |
| Q2_K equalized (M5)         | **41.7311** | **0.6844** | **61.53%** |

Paired per-chunk (eq minus bare; positive = equalization worse):
dNLL +0.2321 ± 0.0039 (t = +59.8; eq better on 2/580), dKLD +0.1942 ±
0.0069 (t = +28.3; 0/40), dtop −4.89pt ± 0.42 (t = −11.6; 1/40).

Mechanism diagnostic (what it does and does not show — reworded
2026-08-04 per adversarial-round2-experiments F7): mapping the
equalized quant back to the original gauge and scoring the
imatrix-weighted error per tensor gives eq/bare ratios 1.06–1.68 on
folded tensors and **exactly 1.000 on the untouched ssm_out control**
— the quantizer's own objective got worse. This shows the fit
degraded; it does NOT discriminate "double-counting" from sub-block
dynamic-range blowup (and cannot — the equalized run counts
importance once, in the columns). Supportable diagnosis: format
interaction (range blowup + de-informed affine fit). AWQ-style
partial exponents (alpha<1) and the no-imatrix control were not run
tonight; they are DIAGNOSTIC ablations for the mechanism, not
post-hoc headline selection, and are queued (ABLATION-PLAN T1.13) —
round-1 F7 forbids the latter, not the former.

## What would falsify / follow-ups (not run)

- M4: best-iterate tracking inside the limit cycle (CALDERA-style) and
  a scales-only ablation (refit d/dmin on ORIGINAL codes) would
  separate the decoder-half's contribution; not pre-registered tonight.
- M5: alpha-sweep of the equalization exponent, and equalization
  targeted at the NON-imatrix-weighted case (where the double-count
  argument vanishes) — e.g. quantization without imatrix, or formats
  without per-block weighted fitting.

## Artifacts

- `e26_lloyd.py` (M4 tool), `e26_equalize.py` (M5 tool),
  `test_lloyd.py` (pre-run validation, all green).
- `q35-08b-Q2K-lloyd.gguf`, `q35-08b-Q2K-eq.gguf`, `q35-08b-BF16-eq
  .gguf` (gauge-check input), `imx-eq.gguf` (+ copy at
  `data/grameq8/diag.imatrix.gguf`, path-length-matched for byte-parity
  of the quantize KV), `repro-rr-input.gguf` (recipe-check gguf
  deleted after the 335/335 byte-identity result; recipe in
  results.txt).
- State: `state-lloyd/` (per-tensor codes/d/dmin + J traces in
  stats*.json, fingerprinted), `codes-repro/`.
- Logs: `build-repro.log`, `build-lloyd.log` + `worker-[0-4].log` (the
  sweep ran as 5 fingerprint-checked compute-only workers on disjoint
  tensor sets — driver-level parallelism only, algorithm identical),
  `quantize-eq.log`, `kld-*.log`, `ppl-*.log`,
  `kld-eq-bf16-gaugecheck.log`; numbers: `results.txt`.
