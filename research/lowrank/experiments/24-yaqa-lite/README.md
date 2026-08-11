# E24 — YAQA-lite: Kronecker-global-metric re-rounding (M1) on Qwen3.5-0.8B

Mission (M1, MANIFOLD-CANDIDATES): upgrade the E13 re-rounder's
objective from input-side whitened l2 to the Kronecker global metric
tr(T dW S dW^T) using BOTH captured factors — S = E[xx^T] (input Grams,
`data/gram08b`) and T = E[gg^T] (output-gradient Grams,
`data/gradgram08b`) — YAQA's (arXiv 2505.22988) Kronecker-factored
Hessian A (x) B instantiated with our measured factors. Frozen Q2_K
grids, codes only, zero extra bytes. Implementation: `e13b_yaqa.py`
(a copy; E13 untouched), flag `--t-gram-dir`.

Verdict up front: **the Kronecker metric with our measured T HURTS.**
Input-only re-rounding is a large paired-resolved free win on this arch
(-15% PPL, -34% KLD, t = 30-45); adding the grad-side factor gives back
a third of it (t = +8 to +15 vs input-only). And the composition law
holds a third time: NO re-rounder belongs under the fc corrector —
bare+fc still beats RR+fc at t = +7 to +18. Every number below is a
paired per-chunk comparison (the R1 repair from the adversarial review;
no unpaired sigma anywhere).

## The formulation (derived, then verified)

For the objective tr(T dW S dW^T) the Hessian in column-major vec is
S (x) T, and inv(S (x) T) = (U_S (x) U_T)^T (U_S (x) U_T) with U_S, U_T
upper triangular (inv(S) = U_S^T U_S etc.). U_S (x) U_T is upper
triangular in lexicographic (column-major, row-minor) order, so exact
GPTQ/LDLQ generalizes to a NESTED sweep:

- within each column j: a sequential ROW sweep with T's Cholesky
  feedback: e~_r = (w_r - w^_r)/U_T[r,r]; w_{r'>r} -= e~_r U_T[r,r'];
- across columns: E13's usual update with err replaced by
  f = (U_T^T e~)/U_S[j,j]; W[:, j+1:] -= f * U_S[j, j+1:].

Setting T = I collapses f to E13's err exactly — the input-only control
is the same code path.

**Negative result inside the derivation (worth keeping): a diagonal-T
row weighting is provably a NO-OP for frozen-grid per-element
rounding.** Each row's objective is minimized independently and a
positive per-row scale cannot move its argmin — so "scale each row's
error by its T-diagonal" (the cheap version of row weighting) changes
nothing. Off-diagonal T (per-row Cholesky feedback) is the only channel
through which the output-side factor can alter any code. Hence the full
Kronecker LDLQ above, not a reweighting.

Validation before any real weights (`test_kron.py`, kept here):
1. nested implementation == brute-force GPTQ over the explicit
   kron(U_S,U_T) factor — codes bit-exact, 5/5 random problems;
2. T=I == E13's `reround_tensor` — codes bit-exact, 3/3;
3. on correlated synthetics the Kronecker sweep cuts the surrogate
   objective tr(T dW S dW^T) by ~35% vs input-only, 10/10 seeds.
So the algorithm does exactly what the math says. The failure below is
in the METRIC (our measured T), not the solver.

## Coverage

96/96 re-roundable Q2_K tensors have BOTH a full S and a full T Gram
(attn_gate 18, attn_k 6, attn_q 6, ffn_gate 24, ffn_up 24, ssm_out 18)
— the input-only fallback path was never needed on this model
(0 fallback tensors). Not re-rounded, disclosed: 36 ssm_alpha/beta
Q2_K tensors (16 x 1024 — below the campaign's min-dim, no Grams,
identical to E13/E22 handling) and the llama-quantize-promoted tensors
(ffn_down/attn_output -> Q3_K, attn_qkv/attn_v -> Q4_K; the known Q2_K
codec gap). T damping 1e-3 relative (the E20 PSD gate), S damping 0.01
(E13 convention); single pre-registered kron config — no damping sweep,
no post-hoc selection (F7 compliance).

## Results — bare re-round, three-way (BF16-truth scoring, as E22)

Full-corpus wikitext-2-test PPL (580 chunks x 512), KLD/top-1 vs
`data/bf16ref08b-40.logits` (40 chunks). Bare and fc-stack rows
reproduce E22's published numbers exactly (provenance gate passed).

| config (all 436 MB, identical bytes)   | PPL     | KLD    | top-1  |
|---|---|---|---|
| Q2_K bare (llama-quantize)             | 33.0877 | 0.4902 | 66.42% |
| **+ RR input-only (E13 sweep, free)**  | **28.0902** | **0.3234** | **72.69%** |
| + RR Kronecker S(x)T (YAQA-lite, free) | 29.4803 | 0.3708 | 72.00% |

Paired per-chunk deltas (mean +- sem of the difference; NLL/KLD in
nats/token; negative = first config better):

| comparison | d mean-NLL (580 ch) | d KLD (40 ch) | d top-1 |
|---|---|---|---|
| RR-input vs bare | -0.1637 +- 0.0036 (t=-44.9; better on 557/580) | -0.1668 +- 0.0055 (t=-30.3; 40/40) | +6.26pt +- 0.45 (t=+14.1) |
| RR-kron vs bare  | -0.1154 +- 0.0042 (t=-27.7; 509/580) | -0.1193 +- 0.0081 (t=-14.7; 40/40) | +5.58pt +- 0.50 (t=+11.2) |
| **RR-kron vs RR-input** | **+0.0483 +- 0.0031 (t=+15.5; 147/580)** | **+0.0475 +- 0.0058 (t=+8.2; 2/40)** | -0.69pt +- 0.42 (t=-1.7, unresolved) |

Named results:

1. **E13 transfers to a second architecture at full strength.** The 27B
   fade was a codec-coverage story, not a scale law: at 0.8B on the
   hybrid arch the free re-round recovers -15% PPL / -34% KLD / +6.3pt
   top-1 at identical bytes, resolved at 30-45 paired sigma.
2. **YAQA-lite is anti-helpful with our CURRENT T estimates.**
   (Reworded 2026-08-04 per adversarial-round2-experiments F6: the
   old sentence claimed an estimator-CLASS failure — "single-pass
   empirical K-FAC factors are anti-helpful" — from an estimator-
   COVERAGE failure; this T was known-defective before the run
   (delta-net gradient cut biasing 18 layers and everything below
   them, 16k tokens for 4096-dim factors), so estimator class vs
   broken capture are perfectly confounded here. The discriminating
   run is the 0.6B clean-gram rerun — ABLATION-PLAN T1.12.) The exact
   Kronecker LDLQ provably reduces the surrogate metric (verified
   numerically), yet the end scores get WORSE than T=I: THIS measured
   T is a worse guide than no row metric at all. With a misestimated T
   the row feedback actively relocates error into wrong rows — an
   approximation of the metric is not a monotone approximation of the
   outcome.

Why T is plausibly misestimated here (each checkable, none checked
tonight): (a) 16384 tokens for up to 4096-dim T — eigenstructure at the
noise floor (E20's own gate flagged tiny late-layer K/V grads);
(b) the qwen35 capture carries the delta-net GRADIENT CUT — gradients
ignore flow through the token-mixing of higher linear layers, biasing
exactly the 18 attn_gate/ssm_out layers and everything below them;
(c) YAQA proper sketches the true end-to-end Hessian factor with
far larger sample budgets and iterative refinement — E[gg^T] from one
32-window pass is the cheapest possible instantiation; (d) damping
1e-3 was fixed a priori, no sensitivity checked.

## Results — + fc-r128q8 correction on the best re-round

Best bare re-round = input-only. Fresh one-sided fc-r128q8 extracted
against the re-rounded base (same recipe as E22's adapter: uniform
r128, Gram whitening, Q8 factors — 150 tensors, byte-identical 86.9 MB;
capture 0.49). Rival = the E22 stack (adapter extracted against the
un-re-rounded base), re-run tonight for pairing.

| config (523 MB total) | PPL | KLD | top-1 |
|---|---|---|---|
| **Q2_K + fc-r128q8 (E22 stack)** | **23.0310** | **0.1685** | **79.40%** |
| RR-input + fc-r128q8 (fresh)     | 23.8982 | 0.1916 | 78.73% |

Paired: d mean-NLL +0.0370 +- 0.0021 (t = +17.6, RR-stack better on
only 133/580 chunks); d KLD +0.0231 +- 0.0032 (t = +7.3, 5/40);
d top-1 -0.68pt +- 0.40 (t = -1.7, unresolved). Q3_K_M (the rung) sits
at 23.28/0.167 for reference; the E22 stack still ties it, the RR stack
does not.

3. **Third paired-resolved confirmation of the co-design law: greedy
   sequential optimization loses to joint shaping.** Re-rounding
   minimizes standalone error, spends grid resolution on directions the
   corrector would own, and leaves a less-correctable residual — so the
   re-round that is hugely positive BARE (-0.164 nats) turns NEGATIVE
   under the corrector (+0.037 nats). Same story as 0.6B (RR+fc 26.80
   vs alt2+fc 25.27), now with paired statistics. Deployment guidance
   unchanged: re-round for zero-byte serving, alternate (E11) when a
   corrector will be attached — do not stack RR under fc.

## Follow-ups (not run tonight)

- T-damping sweep / T-shrinkage toward I (does the harm vanish
  smoothly? cheapest probe of the noise hypothesis).
- Bigger grad capture (more windows) + delta-net adjoint to remove the
  gradient cut, then rerun — separates (a)/(b) from (c).
- ProjQ x Kronecker: deflate T by the corrector row-space if the RR+fc
  composition is ever revisited.
- 0.6B rerun (196 exact Grams, no gradient cut, classic arch): if kron
  still loses there, the single-pass-K-FAC explanation (c) dominates.

## Artifacts

- `e13b_yaqa.py` — the tool (also adds a `--codes-dir` provenance
  fingerprint, the B8 repair: mismatched base/flags now fail loudly).
- `paired_stats.py` — recovers exact per-chunk series from
  llama-perplexity's cumulative streams (plain-PPL `[k]ppl` and
  KLD-mode running rows) and reports paired dmean +- sem + t.
  Round-2 review notes (adversarial-round2-experiments F10, tool
  attacked and cleared): (i) the cumulative-differencing recovery is
  exact ONLY for equal evaluated tokens per chunk — true for this
  fixed-n_ctx pipeline, wrong for variable-length chunking; assert
  before reuse. (ii) On small-n heavy-tailed KLD (n=20) the t-test is
  fragile — report the sign test on chunk-win counts beside t (the
  data is already printed).
- `q35-08b-Q2K-rr-input.gguf`, `q35-08b-Q2K-rr-kron.gguf` (436 MB
  each, byte-compatible Q2_K), `fc-rrinput-r128q8.gguf` (86.9 MB).
- Logs: `ppl-*.log` / `kld-*.log` (bare, rr-input, rr-kron, bare-fc,
  rrinput-fc); builds: `build-*.log`; extraction: `extract-fc.log`.
- Repro: sweep `python3 e13b_yaqa.py <BF16> <Q2_K> data/gram08b
  [--t-gram-dir data/gradgram08b] --codes-dir <dir> -o <out>`; evals =
  llama-perplexity `-c 512 -ngl 99` on wiki.test.raw /
  `--kl-divergence-base data/bf16ref08b-40.logits --kl-divergence`.
