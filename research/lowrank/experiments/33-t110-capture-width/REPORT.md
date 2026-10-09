# E33 / T1.10 — capture-vs-width regression (CPU only, 2026-09-11)

**Question.** Does whitened-energy capture of a rank-r correction scale as
c·(r/d) (paper §4.2's two-point observation: diag r64 0.37 @ d=1024 vs
0.07 @ d=5120), and is c a constant?

**Data.** 1,146 tensors × 10 ranks (r ∈ {16…256}) × 3 whiteners
{none, diag-imatrix, full-Gram} = 34,380 primary points on FOUR models,
all recomputed from on-disk BF16/Q8_0-provenance pairs with the extractor's
exact whitening conventions (`collect_points.py`): Qwen3-0.6B (d=1024,
196 t), Qwen3.5-0.8B (d=1024, 150 t), heretic-27B Qwen3.6 (d=5120, 400 t),
Qwen3.8-27B (d=5120, 400 t). Base axis: 19 configs total (138,200 rows,
`points.csv`); 1,620 log-derived points (`points_log.csv`, E24/E32/E34)
agree with the recomputed spectra per tensor to ≤0.01 (`log_crosscheck.txt`
— this also proves the E32 logs are full-Gram whitened). Pipeline
validation: reproduces §4.2's 0.37 (0.3706, E04 imatrix), E10's 0.60
(0.596), and — on the BF16-provenance E27 pair — §4.2's 0.07 (0.074).
Randomized-SVD numerators at 27B are ≤2 % low at r≤128, ≤5 % at r=256 vs
exact SVDs (`verify-rsvd-h27b-Q2K.txt`); small models use exact SVDs.

**Width leverage is honest but thin:** the two small models share
d_model=1024 and the two 27B models share d_model=5120, so at a fixed
tensor kind the across-model width axis is ONE step (1024→5120; ffn_down
3072/3584→17408). Within-model kind variation (d_in 1024…17408) adds
leverage but is confounded with kind. Muon/optimizer provenance is NOT a
covariate: E30 measured a null (experiments/30-muon-spectrum/PREREG.md).

## Findings (fit.py, 1000 cluster-bootstrap resamples over tensors)

1. **The linear rule capture = c·(r/d) is the worst of six forms in every
   whitener** (ΔAIC ≈ +12,000 diag / +24,000 fc vs the best; held-out
   27B prediction off by 1.5–3.3×). Capture is concave in r: within-model
   rank exponents b_r = 0.48–0.75 (diag), 0.28–0.60 (fc) — doubling rank
   never doubles capture. Best form by AIC everywhere: the two-exponent
   power law **capture ≈ a · r^{b_r} · d_in^{b_d}**:
   - diag: b_r = 0.61 [0.60, 0.61], b_d = −0.67 [−0.70, −0.64]
   - fc:   b_r = 0.44 [0.44, 0.45], b_d = −0.59 [−0.61, −0.57]
   - none: b_r = 0.85 [0.84, 0.85], b_d = −0.71 [−0.73, −0.69]
   r and d do NOT enter only through r/d (b_r + b_d ≠ 0: diag −0.06
   [−0.09, −0.04], fc −0.15 [−0.16, −0.13]); a single-variable fit on r/d
   gives b = 0.64 (diag) / 0.51 (fc) with CIs ±0.02. The saturating form
   r/(r+k·d) is the best 1-parameter form but still loses to the power laws.
   The "constant head + isotropic bulk" form fits h = 0 — no width-invariant
   outlier head is visible in these spectra.
2. **At fixed kind and rank the width slope is ≈ −1 for diag only at the
   d_model-input kinds**: ffn_gate −1.13 [−1.17, −1.09], ffn_up −0.95,
   attn_q −0.90, attn_qkv −0.77, attn_gate −0.84; GQA k/v (d_out=1024 at
   27B) −0.50/−0.58; ffn_down −0.78. Full-Gram whitening halves the
   penalty: −0.5 to −0.8 (means over both 27B models). d_geo = √(d_in·d_out)
   is the better width for diag (AIC 12,530 vs 19,092); d_in for fc.
3. **c is not a constant — and not even a per-whitener constant.**
   c = capture·d_model/r at r=64: diag 5.9 (0.6B) / 5.2 (0.8B) / **5.9
   (heretic, reproducing §4.2's ≈5.9 exactly) / 10.0 (Qwen3.8)**; fc 9.5 /
   9.3 / **22.7 / 11.5**. Base axis at 27B-heretic (Q2_K, MIXED, IQ3_XS):
   fc c = 27–30 (d_in), Qwen3.8 UD bases 15.8 flat across IQ2_S/Q2_K_XL/
   IQ3_S; re-rounded 0.8B base drops fc c from 14.0 to 9.7 (RR removes
   what fc sees). §4.2's caveat is confirmed and sharpened.
4. **[SUPERSEDED by the addendum below — the difference is the BASE
   RECIPE, not the model.] The third model's UD-quant lane does not sit
   on heretic's curve at the same width and identical architecture.** At r=128, Qwen3.8 vs heretic:
   diag HIGHER by +0.03…+0.12 at 8/10 kinds (attn_qkv 0.180 vs 0.111),
   fc LOWER by −0.08…−0.35 at 9/10 kinds (attn_qkv 0.199 vs 0.393,
   attn_o 0.311 vs 0.604); ffn_down is the only kind that agrees under
   both. Qwen3.8's fc/diag gap nearly vanishes (0.143 vs 0.125 at r64).
   Held-out prediction of one 27B model from the other three misses by
   0.58–1.6× under every form. The two 27B lanes differ in base
   provenance (our from-BF16 Q2_K with the whitening imatrix vs Unsloth's
   UD dynamic quant with its own calibration), so model-vs-base is not
   separable here (see "cannot resolve").
5. Width, not architecture, sets the small-vs-large gap: 0.6B (classic)
   and 0.8B (hybrid GDN) overlap under fc at every rank (0.583 vs 0.596 at
   r64) and within 14 % under diag.

## Verdict on §4.2

**Upgrade the direction, kill the form.** Replace "capture ≈ c·(r/d) …
fivefold fall for fivefold width" with a fitted statement: at fixed rank,
diagonal-whitened capture falls as d^{−0.8…−1.1} across the d_model-input
kinds (four models, 1024→5120; per-kind CIs above) and full-Gram capture
as d^{−0.5…−0.8}; returns to rank are sublinear (r^{0.4…0.75}), so no
r/d "rule" exists — the best pooled form is a·r^{0.6}·d^{−0.7} (diag) /
a·r^{0.44}·d^{−0.59} (fc). Keep the caveat and strengthen it: the prefactor
depends on (whitener, base, model) even at identical width and
architecture — the diag-r64 "c ≈ 5.9" is a property of the base RECIPE
(Qwen3.8 under the Unsloth UD recipe gives 10; under OUR recipe it gives
5.9 exactly — see the addendum: "model" drops out of the caveat). The design consequence in §4.2 survives unchanged.
Suggested §6 note: the observation's "three published mechanisms" are
constrained — no width-invariant head fraction is visible (h=0), so the
constant-cardinality-head story needs a direct test, not this regression.

## What the cached data CANNOT resolve (needs GPU / quantize binaries)

- `data/cache27b{,-iq2}` hold only top-128/192 whitened s² with no total
  ‖R·D‖²_F and their source pair (legacy Q6_K ref − legacy Q2_K-imat) is
  pruned: the ORIGINAL 0.07 cannot be recomputed; the BF16-provenance
  sibling (0.074) stands in.
- ~~Model-vs-base at 27B~~ — RESOLVED by the addendum (same-recipe Q2_K
  of Qwen3.8 built on CPU with llama-quantize; heretic under Unsloth's
  recipe remains unbuilt but is no longer needed for the verdict).
- A third d_model (e.g. 8B, d=4096 — the T1.17 Qwen3-8B artifact — or a
  ~70B) is required before any width exponent can be quoted with more than
  one step of leverage; the CIs above are tensor-resampling CIs, not
  model-resampling CIs.
- Capture→KLD: E28b/E29 showed KLD is insensitive to marginal capture;
  this regression says nothing about functional recovery vs width.

Files: `collect_points.py`, `fit.py`, `points.csv`, `points_log.csv`,
`aggregate_points.csv`, `log_crosscheck.txt`, `fit_summary.txt`,
`fit_results.json`, `verify-rsvd-h27b-Q2K.txt`, `fig_capture_vs_rd.png`,
`fig_capture_vs_r.png`, `fig_c_by_lane.png`, `spectra/<config>/*.npz`
(recomputed spectra incl. exact totals — the cache this campaign lacked).

## Addendum — Model-vs-base separation (2026-09-11, coordinator follow-up)

**Build.** `weights/research-staging/e33/q38-27b-bf16-Q2_K.gguf`
(11,003,735,104 bytes) — ONE `llama-quantize` step from the Qwen3.8-27B
BF16 shards with OUR 48-chunk wikitext imatrix (`imatrix-38-bf16.gguf`),
MTP pin `blk\.64\.=q5_k`, i.e. the exact E27 recipe that made
`h27bf16-Q2_K.gguf` (11.00 GB); `--dry-run` showed every quantized tensor
covered by the 496 imatrix entries. Spectra: config `q38-27b-Q2K-ours`
(400 tensors, diag + full-Gram `gram38-bf16`), CPU only, nice 19, 8
threads. Numbers: `addendum_summary.txt`, `fit_summary_ours.txt`
(pooled refit with this lane as the primary Qwen3.8 lane).

**Same-recipe comparison, heretic-Q2K vs Qwen3.8-Q2K (mean over 400
tensors each; c_model = capture·5120/r):**

| whitener | r | heretic | Qwen3.8 (ours) | c_model heretic / Qwen3.8 | Qwen3.8 UD-Q2_K_XL (ref) |
|---|---|---|---|---|---|
| diag | 64 | 0.074 | 0.073 | 5.9 / 5.9 | 0.125 (c 10.0) |
| diag | 128 | 0.121 | 0.121 | 4.9 / 4.8 | 0.179 (c 7.1) |
| fc | 64 | 0.284 | 0.290 | 22.7 / 23.2 | 0.143 (c 11.5) |
| fc | 128 | 0.368 | 0.374 | 14.7 / 15.0 | 0.216 (c 8.7) |

Per kind (10 kinds × {diag, fc} × {r64, r128} = 40 contrasts, 1000-resample
bootstrap): **0/40 differences exclude zero**; the largest |Δ| is 0.010
(fc ffn_gate/ffn_up r128, CI [−0.004, +0.024]) against UD-lane deltas of
−0.17…−0.29 (fc) and +0.06…+0.12 (diag) at the same kinds. Within-model
rank exponents are identical: diag b_r 0.75 ± 0.11 vs 0.75 ± 0.10, fc
0.40 ± 0.10 vs 0.40 ± 0.10 (UD lane: 0.57 / 0.60). Pooled refit with the
same-recipe lane primary: diag b_r 0.675 [0.668, 0.682], b_d −0.756
[−0.790, −0.728]; fc b_r 0.368 [0.363, 0.374], b_d −0.519 [−0.541,
−0.498]; held-out prediction of either 27B model from the other three
is now 0.9–1.1× under the power-law forms (fc power law: 1.02×, RMSE
log c 0.20) instead of the 0.58× / 1.44× misses of the UD lane; per-kind
diag width slopes at d_model-input kinds: attn_qkv −0.98 [−1.01, −0.94],
ffn_up −1.14, ffn_gate −1.31, ssm_out −0.94; fc −0.36…−0.58 (ffn_down
−0.85 both).

**Verdict.** The Qwen3.8-vs-heretic curve difference does NOT survive
when the base recipe is held fixed: under the same one-step Q2_K recipe
the two qwen35 27B models are indistinguishable at every kind, rank and
whitener (Δ within ±0.01, all CIs spanning zero, identical rank
exponents, identical prefactors c_model 5.9/5.9 diag-r64 and 22.7/23.2
fc-r64). The entire same-d gap reported in finding 4 was the Unsloth UD
recipe (dynamic bit allocation + a different calibration imatrix), which
raises diag-visible residual structure (+70 % at r64) and flattens
fc-visible structure (−50 %). Consequence for §4.2: the prefactor is a
property of (whitener, base RECIPE, width) — "model" and "architecture"
drop out of the caveat at this width; the width exponents quoted in
the main findings stand and tighten (diag ≈ d^{−1} at the d_model-input
kinds, fc ≈ d^{−0.4…−0.6}). This also retracts main-finding 4's "third
model off the curve" and any §4.2 sentence built on it (commit 49c2b48
should be checked for that wording).
