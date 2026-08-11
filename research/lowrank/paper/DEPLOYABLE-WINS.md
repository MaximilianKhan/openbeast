# The deployable-everywhere wins (paper §1 narrative + PR-bundle pitch)
# Saved 2026-08-04 per Max. Claims calibrated to the reviewed record.

## Win 1 — frozen-grid re-rounding: quality for free, which becomes
## memory saved

What it changes: nothing about file size, VRAM, or speed — it re-chooses
which already-available code each weight gets, using one calibration
pass (activation Grams). Same bytes, smarter bytes. **The win is free
ON the calibration distribution** — deployment requires broad/mixed
calibration + a held-out gate (see the held-out finding below; caveat
integrated into the headline claim 2026-08-04 per
adversarial-round2-experiments F13).

Measured (paired, BF16-truth, two architectures): 0.8B Q2_K KLD 0.490 ->
0.323, top-1 66.4 -> 72.7% (t = 30-45); 0.6B PPL 43.3 -> 35.5. That is
~50% of the quality gap to the NEXT quant tier, at zero bytes (0.8B
KLD (0.490−0.323)/(0.490−0.167) = 51.6%, PPL 51%; 0.6B PPL 45% — the
old "~40%" was an underclaim never recomputed after the numbers firmed
up; fixed per adversarial-round2-experiments F13). HONEST BOUND: it
does not reach the next tier ("Q2 bytes at Q3-ish feel", NOT "Q2 at Q4
performance"); gains are largest at 2-3-bit tiers and shrink at Q4+
(27B: inside error bars pending codec coverage).

How it becomes memory: users who needed the next tier's quality (and
VRAM) can sometimes stay a tier lower — on 8-16 GB consumer cards that
is a model class fitting or not.

Why it deploys everywhere: output is a byte-compatible standard GGUF —
runs unmodified on every llama.cpp version and every downstream app
(Ollama, LM Studio, ...). Zero adoption friction. Tool shape:
`gguf-refine model.gguf` (calibrate ~minutes, emit better file). We
are not aware of an equivalent tool class — stated as awareness, not
proof: the 2026-08-04 tracker sweep left no query/hit artifact, and
round-1 sweeps missed tracker-resident prior art twice
(#21037/#23476/#23575); retain a sweep record before claiming novelty
in print (softened 2026-08-04 per adversarial-round2-experiments
F13). Millions of deployed K-quant files carry this headroom today.

## Win 2 — the kernel patch-set: compute saved, directly

(a) Allocator/GRAPH_OPT concurrency fix: **+19.6% decode** (794 -> 950
tok/s measured, correctness + generation-identity gates green) for any
CUDA llama.cpp user with graph opt — lower latency, ~20% fewer GPUs at
constant load, less energy per token. Unrelated to compression;
benefits everyone. (b) Adapter-fusion trilogy: LoRA serving tax -45% ->
-20% (up to +79% at small models) — cheaper personalized/multi-tenant
adapter serving ecosystem-wide. (c) Two transferable kernel idioms
(prefetch-before-grid-sync; register-neutral epilogues).

## Who improves, concretely

Hobbyist: a card runs a model class it couldn't. Scale operator: same
traffic, fewer GPUs. Quant publishers: catalogs silently better at
identical sizes. Fine-tune products: adapters nearly free. Adoption
cost everywhere: none (existing files, existing binaries).

## Route to the world

Upstream PR bundle (review-scrubbed): re-rounder tool + Gram-capture
imatrix extension; allocator fix; fusion trilogy; confirmation comments
on #23476/#23575 and #21037. OpenBeast ships all of it first.

## ⚠️ HELD-OUT FINDING (2026-08-04, R7 first pass) — MATERIAL CAVEAT

On the disjoint code corpus, wikitext-calibrated re-rounding is WORSE
than bare (PPL 3.470 vs 3.083) while the fc correction generalizes
(2.649 < bare). The free lever ANTI-GENERALIZES under narrow
calibration: re-rounding is a pure fit to the calibration covariance
with no anchor beyond the grid. Claim revised: "free lever ON the
calibration distribution; deployment requires broad/mixed calibration +
held-out validation." Mitigation under test: mixed-corpus calibration.
The gguf-refine tool ships with a held-out gate or not at all.

## 🟡 MITIGATION INDICATED (confounded — see round-2 review)
## (retitled from "✅ RESOLUTION: coverage, not intrinsic" 2026-08-04
## per adversarial-round2-experiments F1 — the resolution as recorded
## was confounded and proved less than it claimed)

True-interleaved mixed calibration (2:1 wiki:code) re-round beats bare
on BOTH corpora: wiki 33.09→29.84, code 3.083→2.785 (both-corpus
direction ~5-8σ unpaired — qualitatively solid). (First "mixed" test
was invalid — sequential chunking never reached the appended code; bug
caught and documented; construction now scripted in
experiments/22-qwen35-08b/build_mixed_calib.sh and documented in
experiments/22-qwen35-08b/HELDOUT-METHOD.md.)

What the record does NOT support, per the round-2 review (F1):

- **Confounded comparison**: mixed-vs-wiki-only changed THREE things
  together — corpus composition, calibration budget (gram08b count
  20480 tokens/40 chunks vs gram08b-mixed 24576/48), and chunk
  sampling. The published −10%-vs−15% tradeoff is against the
  40-chunk baseline; the equal-budget comparison (mixed2 29.84 vs
  mixed1 27.54) implies a larger on-distribution cost (~8%).
- **Calibration-SAMPLING variance alone is worth 0.55 PPL (~2%)**:
  the buggy first "mixed" run (48 sequential chunks, 100% wiki) is an
  accidental control — same corpus, different chunking/budget — and
  scored wiki-RR 27.54 vs the published wiki-only 28.09
  (heldout-results.txt line 6). First and only measurement of
  review-F12a's unbounded axis; LARGER than several published 0.8B
  margins (e.g. alternation-vs-fc, 22.79 vs 23.03). Formalizing this
  bound is ABLATION-PLAN T1.15.
- **Interpolation, not extrapolation**: after mixing, BOTH eval
  corpora are inside calibration coverage. Whether mixed-calibrated
  re-rounding still anti-generalizes on a third, uncalibrated corpus
  — the actual deployment question — was never tested. Third-corpus
  test queued: ABLATION-PLAN T1.11.
- **Half-measured deployable variant**: RR-mixed2 has PPL only — no
  KLD, no top-1, on either corpus. Queued: ABLATION-PLAN T1.14.

Claim as currently supportable: **re-rounding improves what it is
calibrated on** — the "calibrate broadly, improve broadly" slogan is
an extrapolation hypothesis pending T1.11. Tool requirement unchanged:
mixed-calibration default + held-out validation gate, or the tool does
not ship. heldout-results.txt remains the generalization table seed,
now with its confounds on the label.

## T1.11/T1.14 (pre-registered, 2026-08-04 evening): the generalization
## map completes

Third corpus (technical prose, in NEITHER calibration; caveat: sourced
from our own prior-art docs, LLM-authored): bare 57.56, RR-wiki 54.40
(-5.5%), RR-mixed2 55.32 (-3.9%) — BOTH re-rounded variants improve on
truly-unseen text. Refined claim: re-rounding TRANSFERS within a
distribution neighborhood; it damages far-off distributions (code)
unless covered. Mixed2 on full metrics: improves BOTH corpora vs bare
(wiki KLD 0.490->0.372, code 0.318->0.219 +2.5pt top-1), trading some
near-fit (vs RR-wiki's 0.323 wiki) for far-coverage. Tool guidance
unchanged: mixed calibration default + held-out gate; now with the
distance nuance documented. 27B free-lever paired-resolved same evening
(T1.16: -10.4% KLD, t=-3.70, zero bytes).
