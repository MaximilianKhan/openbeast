> ⚠ see review/ corrections 2026-08-04

# FP4 microscaling × low-rank correction — the composition question

Sweep date: 2026-08-04. Question from Max: are we converging on what NVIDIA
does with NVFP4, and does our full-covariance low-rank correction COMPOSE
with FP4 float formats? Companion files: `llamacpp-internals.md` (LoRA/NVFP4
graph path), `gguf-tooling.md` (codec support), `upstream-landscape.md`
(llama.cpp NVFP4 PR stream). Verdict at the bottom.

---

## (a) Format mechanics: NVFP4 / MXFP4, and quality vs K-quants

### The formats

**NVFP4** = E2M1 (4-bit float) elements in blocks of **16**, two-level
scaling: per-block **FP8 E4M3** scale + per-tensor **FP32** scale. 4.5
bits/value effective. The tensor-level FP32 scale remaps the distribution
into block-scalable range; the E4M3 block scale (a *float*, not power-of-two)
maps each 16-block onto the E2M1 grid. Native Blackwell tensor-core GEMM on
packed FP4/E4M3 pairs, 2–3× FP8 math throughput, ~1.8× memory reduction vs
FP8.

- [arXiv:2509.25149 — Pretraining Large Language Models with NVFP4](https://arxiv.org/abs/2509.25149)
  (NVIDIA; the format-definition + trillion-token pretraining validation
  paper, up to 120B scale).
- [NVIDIA blog — Introducing NVFP4](https://www.edge-ai-vision.com/2025/07/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/)
  (4.5 bpw accounting: one FP8 scale per 16 values + one FP32 per tensor).
- [arXiv:2505.19115 — FP4 All the Way](https://arxiv.org/abs/2505.19115) (fully
  quantized FP4 training).
- [arXiv:2512.02010 — Four Over Six: Adaptive Block Scaling for NVFP4](https://arxiv.org/abs/2512.02010)
  (NVIDIA-adjacent, Nemotron 3 Nano 30B-A3B): adaptively scales some blocks
  to *smaller* FP4 max so the representable grid is more uniform near the
  block max — hardware-compatible, closes training-loss gap to BF16. This is
  scale-search-per-block, i.e. exactly the family of lever llama.cpp
  [PR #25153](https://github.com/ggml-org/llama.cpp/pull/25153)
  (imatrix-aware NVFP4 scale search, open) is landing.

**MXFP4** (OCP Microscaling spec) = E2M1 elements in blocks of **32** with a
shared **E8M0 power-of-two** scale, no second level. The power-of-two scale
is the weak point (below).

**GGML's NVFP4** (`ggml/src/ggml-common.h:221-227` in our checkout): storage
superblock of 64 = 4×16 sub-blocks, `uint8 d[4]` UE4M3 sub-block scales +
32 bytes packed E2M1 → 36 bytes/64 = 4.5 bpw. Type enum 40, ftype 39
(`gguf-py/gguf/constants.py:4784,4840,4969`). Per-tensor FP32 scales, when
present, ride as separate side tensors (`*_s`) — vendor-shipped checkpoints
have them; `llama-quantize`-produced NVFP4 does not.

### The measured quality story vs K-quants/IQ-quants

- [arXiv:2509.23202 — Bridging the Gap Between Promise and Performance for Microscaling FP4 Quantization](https://arxiv.org/abs/2509.23202)
  (ICLR 2026; ISTA/Alistarh-lineage). **The central theory result for us:**
  (1) NVFP4's group size of 16 *provably neutralizes* traditional outlier
  mitigation (Hadamard rotations hurt or do nothing — the tiny group already
  localizes outliers); (2) MXFP4's E8M0 power-of-two scale induces large
  error on its own. Their MR-GPTQ (block-wise Hadamard + FP4-specific GPTQ)
  lifts MXFP4 to near-NVFP4 accuracy; NVFP4 with plain round-to-nearest is
  already strong. Speedups: up to 2.2× end-to-end on B200, 4× on RTX 5090.
- [arXiv:2603.08747 — Diagnosing FP4 inference: layer-wise and block-wise sensitivity of NVFP4 and MXFP4](https://arxiv.org/abs/2603.08747)
  — per-layer sensitivity heterogeneity in FP4, the same phenomenon our
  measured-sensitivity allocation (law #4) exploits.
- **Equal-bpw GGUF numbers** —
  [llama.cpp Discussion #23853](https://github.com/ggml-org/llama.cpp/discussions/23853)
  (advanced-gguf-quantizer, Qwen3.6-27B, imatrix-aware): NVFP4-based mix at
  4.80 bpw (15.28 GiB) → PPL 7.030, KLD 0.045, top-p match 91.9%; **Q4_K_M
  at 5.01 bpw (15.92 GiB) → PPL 6.937, KLD 0.022, top-p 94.3%**. Community
  consensus in-thread: *"speed remains NVFP4's only distinct advantage"* —
  Q4_K_M still wins quality near-equal-bpw. Matches our rig memory: K-quant
  MTP siblings beat the neko-legends NVFP4-MTP GGUFs at single-stream
  (Q5 27B ~141 vs ~115 tok/s) — on CUDA-dp4a paths NVFP4 doesn't even win
  batch-1 speed; its wins are Blackwell-tensor-core batch throughput and
  accuracy-per-bit vs *naive* 4-bit, not vs mature imatrix K-quants.
- [arXiv:2601.14277 — Which Quantization Should I Use? (llama.cpp unified eval)](https://arxiv.org/abs/2601.14277)
  — covers Q-legacy + K-quants only (no FP4, no IQ): K-quants dominate
  legacy formats; Pareto picks Q5_0 / Q4_K_S / Q3_K_L. Confirms no
  peer-reviewed head-to-head NVFP4-vs-K-quant eval exists yet — the #23853
  community numbers are the best available.
- Also seen: [arXiv:2602.11287 — HiFloat4 (Huawei)](https://arxiv.org/abs/2602.11287)
  and [arXiv:2604.08826 — HiFloat4 pretraining on Ascend](https://arxiv.org/abs/2604.08826)
  (spectral anisotropy framed as *the* obstacle to low-bit training — treated
  with spectral decomposition, i.e. low-rank thinking on the training side);
  [arXiv:2603.10444 — Mean Bias in FP4 training](https://arxiv.org/abs/2603.10444);
  [arXiv:2605.20402 — Decomposing MXFP4 quantization error](https://arxiv.org/abs/2605.20402)
  (reducible bias + recoverable deadzone + **irreducible floor** — a clean
  error taxonomy worth citing in the paper).

**Read-across to our campaign:** NVFP4's two-level float scaling is the
format-side version of our law #4 ("metric quality gates everything") — they
spend 0.5 bpw on better scale metrology instead of on codes. And 2509.23202's
neutralization result says the FP4 residual is *outlier-poor*: whatever
correction rides on top must feed on covariance structure, not outliers.
That is precisely the regime our fullcov whitened correction was built for
and where outlier-driven corrections (SVDQuant-style) starve.

---

## (b) SVDQuant — the exact composition of low-rank + 4-bit float

[arXiv:2411.05007 — SVDQuant: Absorbing Outliers by Low-Rank Components for 4-Bit Diffusion Models](https://arxiv.org/abs/2411.05007)
(MIT Han Lab, ICLR 2025 Spotlight) · [OpenReview](https://openreview.net/forum?id=vWR3KuiQur)
· [Nunchaku engine](https://github.com/nunchaku-ai/nunchaku)

**Composition, exactly:** W4A4 (weights *and* activations 4-bit).
1. Smoothing first: migrate activation outliers into the weights (per-channel
   scale λ, SmoothQuant-style) — consolidates all difficulty on the weight
   side.
2. SVD the *shifted* weight: keep top-r (r = 16–32) as a 16-bit low-rank
   branch `L1·L2`.
3. Quantize the residual `W − L1L2` to 4-bit; run both branches, sum.

**What the low-rank absorbs:** the migrated outliers — after smoothing, the
weight singular spectrum (in diffusion models) has a steep head; removing
the top ~32 components drops the residual's magnitude/dynamic range enough
that 4-bit rounding of the remainder is benign. It is an *outlier-eating*
rank-head, not a residual-covariance correction like ours.

**Kernel design (the part that matters for us):** a naive extra low-rank
branch adds real latency — not from FLOPs (r/d is tiny) but from re-reading
activations from DRAM. Nunchaku fuses: down-projection (`L1ᵀx`) into the
activation-quantize kernel, up-projection into the 4-bit GEMM epilogue —
both branches share one activation pass, overhead collapses to a few
percent. This is the same diagnosis as our measured LoRA decode overhead
(61→66.5 tok/s adapter tax; `upstream/` fusion sketch + `arxiv-kernels.md`)
and the same cure.

**FP4 composition shipped:** Nunchaku supports **NVFP4 on RTX 5090** —
SVDQuant's low-rank branch over an NVFP4 base, in production, with NVFP4
beating INT4 on image quality at ~3× speedup vs BF16. **So low-rank + NVFP4
composition exists as a shipped artifact — but only for diffusion models.**

**Why it does NOT transfer to LLMs as-is:**
- [arXiv:2606.01556 — TwinQuant](https://arxiv.org/abs/2606.01556): LLaMA3-8B
  weight singular spectra decay *slowly and stay flat beyond rank 256* —
  SVDQuant's steep-head assumption fails on mainstream LLMs; prior
  decompositions also minimize real-valued residual energy rather than
  post-quantization error. Their fix: learn quantization-friendly subspaces
  on Stiefel/GL manifolds + a **fused dual-component kernel** (two-stage
  low-rank pipelined on-chip, merged in a single GEMM epilogue — near-FP16
  accuracy, 1.8× vs FP16). This flat-spectrum finding is the literature's
  version of our measured **capture ≈ 5.9·(r/d)** two-point observation
  (demoted from "law", review 2026-08-04) — independently
  confirms the head-energy ceiling we hit, and why plain-SVD corrections
  scale-shrink (law #5).
- [arXiv:2606.15652 — MosaicQuant](https://arxiv.org/abs/2606.15652)
  (inlier–outlier disaggregation for unified 4-bit LLM quant) and
  [arXiv:2606.26587 — SharQ](https://arxiv.org/abs/2606.26587) (activation
  sparsity + FP4) are the LLM-side descendants.

---

## (c) NVIDIA EoRA — productization status

[arXiv:2410.21271 — EoRA: Fine-tuning-free Compensation for Compressed LLM with Eigenspace Low-Rank Approximation](https://arxiv.org/abs/2410.21271)
· [NVlabs/EoRA (ICLRW'26)](https://github.com/NVlabs/EoRA)
· [NVIDIA dev blog](https://developer.nvidia.com/blog/a-fine-tuning-free-approach-for-rapidly-recovering-llm-compression-errors-with-eora)

- **Method:** project the compression error `ΔW` into the eigenspace of the
  layer's *input activation autocorrelation*, truncate there — i.e.
  activation-weighted (≈ single-sided whitened) low-rank of the residual.
  This is our diag→fullcov family with one-sided whitening; our fullcov v2
  is the two-sided/joint refinement of the same objective. Training-free,
  minutes on small calibration sets; factors are resilient to being
  themselves quantized (their 3/4-bit-factor results ≈ our r128-**Q8**
  factor-quantization finding).
- **Productization:** integrated into
  [GPTQModel](https://github.com/modelcloud/gptqmodel) (ModelCloud) as an
  opt-in flag at quantize time; output saved as a **standard LoRA adapter**
  consumable by HF Transformers and vLLM. **No dedicated fused kernels** —
  it rides the generic LoRA decode path, i.e. it pays exactly the adapter
  tax we measured, unfused. Headline numbers: +4.53/+3.48/+11.83 points
  (ARC-C/MathQA/GSM8K) on 2:4-pruned Llama3-8B; biggest wins at 2–3-bit
  ([Towards Data Science walkthrough](https://towardsdatascience.com/boost-2-bit-llm-accuracy-with-eora/)).
- **NVFP4 + EoRA combined: not published.** GPTQModel's EoRA targets
  GPTQ-INT bases; NVIDIA's own NVFP4 accuracy-recovery route in production
  is **not low-rank** — it is:
  - PTQ recipes in TensorRT Model Optimizer / llm-compressor
    ([NVFP4 W4A4 example](https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantization_w4a4_fp4/)),
  - block-scale optimization
    ([arXiv:2606.07618 — ScaleSweep](https://arxiv.org/abs/2606.07618),
    [arXiv:2605.12245 — SOAR](https://arxiv.org/abs/2605.12245),
    llama.cpp [#25153](https://github.com/ggml-org/llama.cpp/pull/25153)),
  - and above all **QAD** —
    [arXiv:2601.20088 — Quantization-Aware Distillation for NVFP4 Inference Accuracy Recovery](https://arxiv.org/abs/2601.20088)
    (29-author Nemotron-family paper: KL-distill BF16 teacher into the NVFP4
    student, near-BF16 recovery across AceReason/Nemotron Nano/Super). When
    NVIDIA has GPU-hours, they retrain; EoRA is their training-free fallback
    lane, published but not wired to NVFP4.

---

## (d) Low-rank ON TOP of NVFP4/MXFP4 — who has published it?

**Nobody has published low-rank correction on an NVFP4/MXFP4 base for LLMs
with measured composition at 2–4-bit.** The near-misses:

- **Nunchaku SVDQuant-NVFP4** (above): the literal composition, shipped —
  diffusion models only, rank-32, outlier-driven.
- [arXiv:2601.07475 — ARCQuant: Augmented Residual Channels for NVFP4](https://arxiv.org/abs/2601.07475):
  the philosophically closest LLM work. Appends **quantized residual
  channels to the activation matrix** so error compensation happens *inside
  one unified NVFP4 GEMM* — no second branch, no format break, standard
  kernels, up to 3× vs FP16 on RTX 5090 / RTX PRO 6000, error bounds
  ≈ MXFP8, near-FP16 perplexity on LLaMA/Qwen. It is a correction that
  lives as extra columns (channel-structured, not low-rank-factored), chosen
  *because* a separate high-precision branch breaks NVFP4's unified-precision
  hardware path. Read this as: the field found the same fusion problem we
  did and dodged it by staying in-format. No comparison against
  SVDQuant/EoRA in the abstract.
- [arXiv:2604.17789 — DuQuant++](https://arxiv.org/abs/2604.17789):
  fine-grained rotation for microscaling FP4 — rotation lane, not low-rank.
- [arXiv:2606.01556 — TwinQuant](https://arxiv.org/abs/2606.01556): learnable
  subspace decomposition + fused dual kernel; base format unstated in
  abstract (INT-leaning), the closest *kernel* prior for a fused low-rank +
  4-bit LLM path.
- Predicted composition behavior at 2–4-bit float, from
  [2509.23202](https://arxiv.org/abs/2509.23202)'s neutralization theorem:
  NVFP4's 16-element blocks already absorb outliers, so the residual left
  for a correction branch is outlier-poor and covariance-structured. →
  Outlier-eating low-rank (SVDQuant-style) should underperform its INT4
  results on NVFP4; *whitened-residual* low-rank (EoRA/our fullcov) is the
  variant with food left on the table. Nobody has measured this. There is
  no NVFP4-block-size analog of our joint alternation (shape base codes
  and factors together) anywhere in the literature.

---

## (e) llama.cpp NVFP4 state + can our pipeline target an NVFP4 base?

State (details in `llamacpp-internals.md` §type-add and
`upstream-landscape.md` §3–4, all verified in our checkout
`/home/max/Documents/openbeast/llama.cpp`):

- Core type landed (`5eae9cb1d`, #19769): enum 40, ftype `MOSTLY_NVFP4`=39,
  4.5 bpw, 64-superblock (4 × 16-sub-block UE4M3 scales + packed E2M1).
- Active NVIDIA-engineer optimization stream: CUDA dp4a #20644, MMQ #21074,
  MMVQ post-scale fusion [#24481](https://github.com/ggml-org/llama.cpp/pull/24481)
  (merged 2026-07-07), [#25730](https://github.com/ggml-org/llama.cpp/pull/25730),
  [#26311](https://github.com/ggml-org/llama.cpp/pull/26311) (open); Metal
  #25419, Vulkan #25430, AVX2 #23961. Imatrix-aware scale search
  [#25153](https://github.com/ggml-org/llama.cpp/pull/25153) open — upstream
  is converging on Four-Over-Six-style scale optimization by itself.
- Our serving data: NVFP4-MTP GGUFs run today (2026-07-10 deploy), but
  K-quant siblings are *faster at batch 1* on our rig — dp4a/MMVQ NVFP4
  decode is not bandwidth-optimal the way K-quant kernels are; NVFP4's real
  win needs Blackwell tensor-core MMQ at batch.

**LoRA-adapter compatibility (corrected, important):** re-reading
`src/llama-graph.cpp:1699-1704` — the asserts fire only when an FFN tensor
has **per-tensor side scales (`*_s`) AND type NVFP4**, or side scales at
all with NVFP4. Plain NVFP4 tensors *without* side-scale tensors (what
`llama-quantize` produces) **can carry LoRA adapters through the normal
`build_lora_mm` path**. Our earlier note "rules out an NVFP4-base variant"
overstated it — it rules out *vendor checkpoints shipping `_s` tensors*
(check whether the neko-legends files carry `_s` before testing), not
self-quantized NVFP4 bases.

**Tensor codec feasibility in gguf-py:** dequant exists (708 ms/GiB-class,
`gguf-tooling.md` §1); `quantize_blocks` is **not** implemented for NVFP4
(MXFP4 *is*, pure numpy). But unlike K-quants/IQ (search-based, C++-only),
NVFP4 encode is closed-form: per-16 amax → `scale = amax/6` → cast UE4M3 →
round elements to E2M1 grid — ~40 lines of numpy mirroring the existing
MXFP4 class. That gives our pipeline full read/write on NVFP4 bases without
touching C++, and E13-style re-rounding of E2M1 codes under a fullcov
residual is *easier* than for Q2_K (no nested 6-bit scale search). A numpy
NVFP4 `quantize_blocks` is also a small free-standing upstream contribution
to gguf-py.

---

## Verdict

**Same direction as NVIDIA?** Convergent on diagnosis, divergent on lever.
NVFP4's two-level float scaling, Four-Over-Six, ScaleSweep/SOAR, and
llama.cpp #25153 are all *scale-metrology* improvements — the format-side
expression of our law #4. Their accuracy-recovery lane is QAD (retraining),
because NVIDIA is compute-rich on the *producer* side. Our lane — training-
free whitened low-rank on top of a frozen base — is exactly NVIDIA's own
EoRA lane, which they published, integrated into GPTQModel, and then left
unfused and unconnected to NVFP4. We are not chasing their taillights; we
are driving the branch they parked.

**Composition opportunity?** Real, shipped-adjacent, and unclaimed for LLMs.
Nunchaku proves low-rank-over-NVFP4 works at kernel level (diffusion);
2509.23202 predicts the NVFP4 residual is outlier-poor → *our
covariance-driven* correction is the right species for it (outlier-driven
SVDQuant is not); ARCQuant shows the field is worried enough about branch
overhead to redesign the correction as in-format channels. Expectations must
stay sober: NVFP4 is a 4.5-bpw format — a *mature-quant* regime where our
levers measurably shrink (law #5), and Q4_K_M already beats NVFP4 by ~0.023
KLD at +0.2 bpw (#23853). The prize is not "improve NVFP4 a lot"; it is
**"NVFP4 + fc ≥ Q4_K_M quality at fewer bytes, on a Blackwell-native,
batch-fast base"** — and, longer game, our 2–3-bpw joint-alternation story
retargeted onto a *hypothetical* NVFP2/E1M1-class base where no mature
competitor exists.

**Recommended experiment (in order):**
1. **NVFP4-carries-LoRA smoke** (hours): `llama-quantize` heretic-v2 to
   MOSTLY_NVFP4, attach the existing fullcov r128-Q8 adapter pipeline (new
   residual computed against the NVFP4 dequant), verify no `_s` assert,
   measure PPL20/KLD/top-1 + tok/s vs the #23853-style ledger: NVFP4 bare
   vs NVFP4+fc vs Q4_K_M at matched total bytes (~4.6 vs 5.01 bpw).
   Success bar: close ≥ half the 0.023 KLD gap to Q4_K_M.
2. **numpy NVFP4 `quantize_blocks`** (~40 lines) → unlocks step 3 and is an
   independent gguf-py upstream PR.
3. **E13 re-round + alternation on the NVFP4 codec**: joint shaping (law #3)
   with closed-form E2M1 re-rounding under the fc residual — the one
   composition nothing in the literature has: alternation *inside* a
   hardware-native FP4 format. If the 0.6B alternation gain (−15% KLD over
   best-standalone) survives even half-strength on NVFP4, that plus #25153's
   scale search makes a coherent upstream/paper story: "imatrix-aware NVFP4
   + whitened low-rank + joint re-rounding."

Non-goals from this sweep: MXFP4 base (E8M0 scale error dominates — MR-GPTQ
territory, rotation lane, not ours); replicating ARCQuant's channel
augmentation (activation-side surgery, wrong layer of the stack for GGUF);
QAD (needs training compute we spend elsewhere).
