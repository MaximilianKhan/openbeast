> ⚠ see review/ corrections 2026-08-04

# beast-rank — low-rank decomposition for VRAM reduction in llama.cpp

**Why this work exists (Max, 2026-08-03):** current GPU economics gave us
a world that is **compute-rich but memory-poor**. Consumer cards like the
RTX 3090 Ti and 5090 ship formidable processors strapped to VRAM pools
too small for frontier models. But if we shrink a frontier model to fit
(or nearly fit) that VRAM, the onboard compute can trivially absorb the
overhead of reconstructing what compression removed — low-rank
corrections or whatever structure we land on — in a fused kernel. The
compute exists and idles; memory is the scarce resource. This campaign
optimizes for the spare-memory meta. (Our own measurements sharpen the
thesis: batch-1 decode is memory-bandwidth-bound, so smaller weights are
not just feasible — they're FASTER: 27B Q6 61 tok/s vs Q2_K 99.7 on the
5090. The trade was never compute-for-memory; it is accuracy-for-memory,
with compute as the free resource that buys accuracy back.)

**Status:** investigation opened 2026-08-03. Target: a working CUDA-only
prototype and, if the numbers hold, an upstream llama.cpp PR.
**This directory is the lab.** Everything we learn lives here, in shape to
become a paper at the end (see [`paper/OUTLINE.md`](paper/OUTLINE.md)).

## The mission

Serve a model whose weights don't fit in VRAM, by storing factorized /
decomposed weights and reconstructing the effect of the full weights on the
fly in CUDA. Pipeline:

```
GGUF ──transform──▶ decomposed format ──serve──▶ llama.cpp CUDA ops
                                                  reconstruct per-layer,
                                                  step by step, during
                                                  inference
```

Goal: **2×–10× VRAM reduction**, trading GPU compute time for memory.
First pass targets NVIDIA/CUDA only.

## The core math (and one inversion of the premise)

A weight matrix `W (m×n)` replaced by a rank-`r` factorization `W ≈ A·B`
(`A: m×r`, `B: r×n`) needs `r(m+n)` numbers instead of `m·n`.
Compression ratio:

```
C = m·n / (r·(m+n))          # square d×d: C = d / 2r
```

For a d=5120 layer, 2× compression means r=1280; 10× means r=256.

**The trick that makes serving cheap: never materialize W.** Compute
`y = (x·A)·B` — two thin GEMMs. FLOPs go from `2mn·B` to `2r(m+n)·B`,
which is FEWER FLOPs whenever compression > 1. And token generation is
memory-bandwidth-bound: the factorized form reads fewer bytes per token, so
single-stream decode should get FASTER, not slower.

**So the real cost is not compute — it is accuracy.** That inverts the
mission premise ("trade time for memory") and sharpens it: the research
question is *how much rank can we remove before the model degrades, and what
do we add back to compensate?* Known hard fact from the literature: LLM
weight matrices are NOT low-rank — singular values decay slowly, and naive
truncated SVD at 2× compression wrecks perplexity. Anyone claiming 2–10×
from plain SVD is selling something. The plausible routes there:

1. **Activation-aware decomposition** (ASVD, SVD-LLM): whiten by input
   activation statistics before truncating — minimize error where inputs
   actually live, not isotropically. Buys real ratio at low damage.
2. **Quantized base + low-rank residual** (CALDERA, EoRA, SVDQuant's
   outlier-absorbing rotation): `W ≈ Q + A·B` where `Q` is an aggressive
   quantization (2-bit K-quant / IQ2) and `A·B` is a low-rank correction of
   the quantization ERROR (which IS approximately low-rank, unlike W
   itself). This is the leading candidate: it composes with llama.cpp's
   existing quant machinery instead of fighting it, and the target ratios
   (Q6→IQ2+r≈64 residual ≈ 2.5–3× with modest quality loss) are consistent
   with published results.
3. **Per-layer rank allocation**: spectra differ wildly by layer/projection
   (attention `o_proj` vs MLP `down_proj` etc.) — a global rank is wrong;
   allocate rank by measured sensitivity (Fisher / activation energy).

## The implementation cheat code we must validate first

llama.cpp ALREADY serves `y = W·x + s·B·(A·x)` — that is exactly the LoRA
adapter path (`llm_build_lora_mm`), with GGUF adapter files, CUDA support,
hot-load, everything. So the **v0 prototype requires ZERO llama.cpp
changes**:

1. Quantize base model aggressively (e.g. IQ2_XS / Q2_K).
2. Compute per-tensor residual `R = W_f16 − dequant(W_q)`.
3. Truncated (activation-weighted) SVD of `R` → export `A,B` as a LoRA
   adapter GGUF.
4. `llama-server -m base-iq2.gguf --lora residual-correction.gguf`.
5. Measure: VRAM, perplexity, tok/s vs the Q6 baseline.

If v0 shows signal, the PR-shaped work begins: fused CUDA kernel for
`Q·x + B·(A·x)` (one pass, no adapter overhead), a standardized "decomposed
GGUF" convention, rank-allocation tooling, and upstreaming. If v0 shows no
signal, we will have learned exactly where the wall is — document it and
attack the wall, not the tooling.

## Direction of travel (start small → build up)

*(Opening plan, 2026-08-03 — SUPERSEDED by experiments/ 01–27; kept
for the record. 9B GLM died to conversion rot; "E06" became the
kernel trilogy in dir 14. Note added per coherence-audit P3.5.)*

| Stage | What | Proves |
|---|---|---|
| E01 | SVD spectra of every tensor in Qwen3-0.6B | are ANY tensors low-rank? where does energy live? |
| E02 | truncated-SVD reconstruct → perplexity | how fast does plain SVD degrade? (the null hypothesis) |
| E03 | quant-residual spectra | is quantization error low-rank? (the load-bearing claim) |
| E04 | IQ2 base + SVD-residual LoRA served in llama.cpp | v0 end-to-end: VRAM/quality/speed triangle |
| E05 | activation-aware weighting + per-layer rank | close the quality gap |
| E06 | fused CUDA kernel prototype | the actual PR payload |

Scale up (0.6B → 9B GLM → 27B) only after each stage earns it.

## Map of this directory

- [`PROTOCOL.md`](PROTOCOL.md) — **methodology v2** (post-adversarial-
  review): the standards every result must meet + the ground-up rerun
  matrix (0.8B-first).
- [`MASTER-TABLE.md`](MASTER-TABLE.md) — the PhD-grade comparison table
  (BF16-referenced, ± everywhere, provenance column) — the paper's
  Table 1 in waiting.
- [`review/`](review/) — the adversarial review (stats/claims/code);
  read before citing any verdict.
- [`beastrank.py`](beastrank.py) — **the method as a tool**: one command
  takes any GGUF + calibration text → geometry-aware base + whitened
  Q8 correction + report, stock-servable. Smoke-proven end-to-end
  (0.6B: 1.81x vs Q8 ref in ~6 min — compression ratio only; the smoke
  prints no KLD/PPL, so never quote it without a quality metric
  attached).
- [`RESULTS_ROLLUP.md`](RESULTS_ROLLUP.md) — **start here**: every
  measured configuration at both scales on one page, plus the
  campaign's headline observations (framings corrected 2026-08-04 per
  [`review/`](review/): the capture "law" is a two-point observation;
  ladder "victories" are ~1σ parity or wins-from-below).

- [`JOURNAL.md`](JOURNAL.md) — dated lab notebook, append-only. Every session
  writes what was tried, what happened, what it taught us. Failed runs are
  sacred knowledge — record them with the same care as wins.
- [`TODO.md`](TODO.md) — the investigation queue, ranked (recon-adjusted
  2026-08-11: paper-math lane first, then the re-gated GPU queue).
- [`prior-art/`](prior-art/) — one file per paper/system, with the numbers
  (compression vs perplexity/benchmarks), not vibes. **Start with
  [`recon-2026-08-11.md`](prior-art/recon-2026-08-11.md)** — the
  post-08-04 threat/lever synthesis; it supersedes every
  "first"/"unpublished" claim elsewhere in this tree (gguf-refine and
  two-sided whitening are reframed, E16 twice-narrowed; E17,
  capture-vs-width, and the E27 control methodology verified still
  ours).
- [`experiments/`](experiments/) — numbered, self-contained, each with its
  own README stating hypothesis → method → result. NAMING note (ids
  diverged from dirs mid-campaign; map added per coherence-audit
  P3.1): "E14" = fused kernel (dir `14-fused-kernel`); "E14-cond" =
  task conditioning (dir `17-task-conditioned`); "E17"/"E18" = entropy
  reconstruction (dir `18-entropy-reconstruction`). Everywhere else
  id == dir number.
- [`paper/`](paper/) — outline and references, maintained as we go, so the
  paper is an assembly job, not an excavation.

## Ground rules

- **Numbers or it didn't happen.** Every claim in notes carries a
  measurement or a citation.
- **Honest nulls.** If plain low-rank caps out at 1.3×, that result goes in
  the paper too.
- **CUDA/NVIDIA only** for now (reference rig: RTX 5090, 32 GB, Blackwell).
- **The rig's GPU is the lab bench** — the serving stack stays down during
  experiment windows (Max's standing beastdown authorization 2026-08-03).
