# 3. Results I: the free lever

## 3.1 What re-rounding buys at zero bytes

The re-rounder changes nothing about file size, VRAM, or serving speed: it re-chooses which already-available code each weight gets, using one calibration pass. We report it at three scales, in order of scale.

**Qwen3-0.6B (Q2_K-imat, legacy Q8_0-referenced scoring).** The first measurement, and the largest effect [source: research/lowrank/experiments/13-rerounder/README.md]:

| config (296 MB, identical bytes) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K-imat bare (llama-quantize) | 43.33 | 0.766 | 58.2% |
| + re-round (free) | **35.54** | **0.556** | **64.0%** |

An 18% perplexity improvement at identical bytes on an unmodified serving stack, resolved at ~20 sigma on PPL and ~19 sigma on KLD even unpaired [source: research/lowrank/review/adversarial-stats.md §F15].

**Qwen3.5-0.8B (Q2_K, BF16-truth scoring, paired per-chunk statistics — the protocol-v2 flagship measurement).** A second, hybrid-attention architecture, scored against the true BF16 reference with the paired-statistics repair applied throughout [source: research/lowrank/experiments/24-yaqa-lite/README.md]:

| config (436 MB, identical bytes) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K bare | 33.09 | 0.4902 | 66.4% |
| + re-round, input-only (free) | **28.09** | **0.3234** | **72.7%** |

Paired per-chunk deltas versus bare: mean ΔNLL −0.1637 ± 0.0036 nats/token (t = −44.9; better on 557/580 chunks), ΔKLD −0.1668 ± 0.0055 (t = −30.3; better on 40/40 chunks), Δtop-1 +6.26 ± 0.45 points (t = +14.1). This is −15% PPL and −34% KLD for zero bytes, and it establishes that the 27B fade below was not a scale law (finding 1 of E24): the free lever transfers across architectures at full strength where its codec coverage is complete.

**Qwen3.6-27B (Q2_K-imat; paired, n = 100; legacy-provenance pair).** At production scale the lever is smaller and still resolved. On 100 chunks against BF16 truth logits [source: research/lowrank/experiments/27-bf16-rederivation/results-100ch-paired.txt]:

| config (10.86 GB, identical bytes) | PPL | KLD | top-1 |
|---|---|---|---|
| Q2_K-imat bare | 7.735 ± 0.126 | 0.1603 ± 0.0032 | 83.48% |
| + re-round (free) | 7.696 ± 0.124 | **0.1390 ± 0.0029** | **84.55%** |

Paired per-chunk: ΔKLD −0.0213 ± 0.0030 (t = −7.02; better on 77/100 chunks), Δtop-1 +1.07 points (t = +4.72), ΔNLL −0.005 ± 0.005 (t = −0.98, a tie). That is −13.3% KL divergence and one point of top-1 agreement for zero bytes; perplexity does not move. Two labels travel with the row. The pair is a requantization of a Q6_K file rather than a single step from BF16, so it is not tabled beside the single-step rows of §4.3 — both sides share that provenance, so the difference is clean. And the sweep covers only part of the model: inside a nominal "Q2_K" 27B file, llama-quantize promotes ffn_down and attn_output to Q3_K and attn_qkv/attn_v to Q4_K, and our sweep implements only the Q2_K codec. The unswept mass is most of the model's sensitive tensors. The first reading of this row, at 20 chunks and unpaired, was inside its error bars (0.78σ); the 40-chunk paired reading gave t = −3.70 and the 100-chunk reading is the one reported. Q3_K/Q4_K codecs are open work (§6.3); returns should be smaller at higher bit-widths.

In summary: at small scale the lever recovers roughly half of the quality gap to the next tier at zero bytes (0.8B: 51.6% of the KLD gap, 51% of the PPL gap; 0.6B: 45% of the PPL gap); at 27B, with partial codec coverage, it recovers 13% of the base's KL divergence. It is not "Q2 at Q4 performance," and it is not demonstrated at tiers whose codecs we have not written [source: research/lowrank/paper/DEPLOYABLE-WINS.md]. The consumer reading: quality bought at zero bytes is *memory* — a user who needed the next tier's quality can sometimes stay a tier lower, which on 8-16 GB cards is a model class fitting or not.

**The held-out arc: transfer depends on distance from the calibration corpus.** The first held-out pass evaluated the wikitext-calibrated 0.8B re-round on a disjoint code corpus and found it *worse than bare*: PPL 3.470 versus 3.083 (bare), while the fc correction generalizes (2.649). Mechanism: re-rounding is a pure fit to the calibration covariance with no anchor beyond the grid — there is no bias term, no structure prior, nothing pulling toward the untouched solution off-distribution. A mixed 2:1 wikitext:code calibration (true-interleaved; a first sequentially-concatenated attempt never reached the code text and is documented as a bug) rescues both corpora: wiki 33.09→29.84 and code 3.083→2.785, at a modest on-distribution cost versus narrow calibration (−10% versus −15% wiki PPL against the 40-chunk baseline). Two pre-registered follow-ups complete the map [source: research/lowrank/paper/DEPLOYABLE-WINS.md; experiments/22-qwen35-08b/HELDOUT-METHOD.md]. On a third corpus in neither calibration set (technical prose), both re-rounded variants *improve* on the untouched model: bare 57.56, wikitext-calibrated 54.40 (−5.5%), mixed-calibrated 55.32 (−3.9%). And the mixed variant, scored on full metrics, improves both calibration-covered corpora against bare (wikitext KLD 0.490 → 0.372; code KLD 0.318 → 0.219 with +2.5 points top-1), giving up some on-distribution fit (the wikitext-only artifact reaches 0.323). The reading we hold: re-rounding transfers within a neighborhood of its calibration distribution and can damage a distant one unless that one is covered. Three caveats remain. The mixed-versus-narrow comparison changed corpus composition, calibration budget (+20%) and chunk sampling together; an accidental control bounds calibration-sampling variance alone at 0.55 PPL (~2%), which is larger than several 0.8B margins in this paper; and the third corpus was assembled from our own literature notes, which are machine-drafted prose, so it is one sample of "unseen text," not a benchmark. The full mix-ratio grid and the 27B held-out test of the re-rounder have not been run. Every deployment sentence in this paper therefore conditions the free lever on calibration breadth and a held-out gate.

## 3.2 The estimator result: a better metric on paper made it worse (E24)

The natural upgrade to input-only re-rounding is the Kronecker global metric tr(T dW S dWᵀ) with both measured factors — YAQA's Kronecker-factored Hessian (arXiv:2505.22988) instantiated with our captured S (input Grams) and T (output-gradient Grams). The exact nested LDLQ sweep of §2.3 provably reduces that surrogate objective (verified: ~35% surrogate reduction on correlated synthetics, codes bit-exact against brute-force Kronecker GPTQ), and setting T = I reproduces the input-only sweep bit-for-bit. The end-to-end result [source: research/lowrank/experiments/24-yaqa-lite/README.md]:

| comparison (0.8B, paired) | ΔNLL | ΔKLD | Δtop-1 |
|---|---|---|---|
| kron vs bare | −0.1154 ± 0.0042 (t = −27.7) | −0.1193 ± 0.0081 | +5.58 ± 0.50 |
| **kron vs input-only** | **+0.0483 ± 0.0031 (t = +15.5)** | +0.0475 ± 0.0058 (t = +8.2) | −0.69 ± 0.42 (unresolved) |

The richer metric with our measured T is *anti-helpful*: it gives back a third of the input-only win, at 15 paired sigma. The solver is exonerated (it does exactly what the math says); the failure is the estimator. Our T comes from a single 16,384-token pass — the cheapest possible instantiation of a K-FAC factor — carries the delta-net gradient cut (§2.1) on exactly this architecture, and its late-layer K/V eigenstructure sits at the fp32 noise floor by the capture tool's own gates. With a misestimated T, per-row Cholesky feedback actively relocates error into the wrong rows. The general lesson, which recurs at every layer of this paper (§4.6): *an approximation of the metric is not a monotone approximation of the outcome* — estimator quality gates metric quality, and one-sided or under-sampled proxies of a two-sided geometry are traps. YAQA proper spends far larger sketch budgets on exactly this estimation problem; our result measures what happens when one does not.

A pre-registered discriminating run asked whether the harm came from the gradient cut or from the estimator class. On Qwen3-0.6B, a classic architecture with no gradient cut and 196 exact gradient Grams, the same Kronecker sweep is worse than the input-only sweep *and worse than the untouched model*: PPL 50.53 / KLD 0.866 / top-1 58.0%, against 35.54 / 0.556 / 64.0% input-only and 43.33 / 0.766 / 58.2% bare. A cleaner capture made the harm larger. The model changed along with the capture, so the magnitudes are not a pure comparison, but the direction rules out capture coverage as the explanation: a single-pass Kronecker factor of this class hurts [source: research/lowrank/JOURNAL.md, 2026-08-04 T1.12].

## 3.3 The ordering rule: re-round for serving, alternate for correction

Where does the free lever sit in the co-design stack? Below the corrector, nowhere. Third paired-resolved confirmation at 0.8B: the re-round that is hugely positive bare (−0.164 nats paired) turns *negative* under a freshly-extracted fc corrector — bare+fc beats RR+fc at t = +17.6 (RR stack better on only 133/580 chunks), reproducing the 0.6B ordering (RR-then-correct 26.80 vs alternation-then-correct 25.27 PPL) and the ProjQ-deflated middle point (25.94) [source: research/lowrank/experiments/24-yaqa-lite/README.md; experiments/13-rerounder/README.md]. Mechanism: re-rounding minimizes *standalone* whitened error, spending grid resolution on directions the corrector would own, and leaves a less-correctable residual — measured as higher whitened capture of a worse-shaped residual (capture 0.78, worse KLD, on the 0.6B triad). Deployment guidance, unchanged across three architectures: re-round when shipping a bare quant (the zero-byte serving lever); alternate (§2.4) when a corrector will be attached; never stack re-rounding under a corrector.

## 3.4 The Lloyd null: further surrogate descent buys nothing (E26)

Would a servable Lloyd-style refinement beat single-pass re-rounding? The natural upgrade alternates the code sweep with a per-row generalized-least-squares refit of the format's *storable* affine scales (fp16 d/dmin; the 4-bit sub-scales stay frozen so the artifact remains byte-compatible), both halves descending the same damped whitened metric. Run to a pre-registered 8-iteration cap at 0.8B, the alternation cuts the whitened surrogate a further 2.6% below single-pass re-rounding — and the end-to-end effect is statistically zero: paired ΔNLL +0.0009 ± 0.0022 (t = +0.40), ΔKLD +0.0015 ± 0.0034 (t = +0.43), Δtop-1 −0.55 ± 0.42 points (t = −1.32), on a harness that resolves the re-round-versus-bare comparison at t = −27..−47 [source: research/lowrank/experiments/26-lloyd-gauge/README.md]. No codes fixed point was reached within budget (92/96 tensors in a ~0.1% limit cycle — the measured amendment to §2.4's Claim 3). Together with §3.2 this is the cleanest instance of the campaign's recurring lesson: single-pass re-rounding *is* the deployment recipe, because below some floor whitened-proxy descent stops predicting KL divergence. §4.6 collects every instance.
