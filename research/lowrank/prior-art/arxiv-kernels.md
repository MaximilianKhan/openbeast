# Prior art: fusing low-rank branches into quantized GEMM/GEMV for token generation

Research date: 2026-08-03. Scope: arXiv 2024–2026 + code repos on kernel-level fusion of
LoRA-style low-rank corrections into quantized matmul, plus the llama.cpp CUDA backend
integration map for the eventual fused upstream contribution.

Our measured baseline (see JOURNAL.md): serving y = W_q·x + s·B·(Aᵀx) through the stock
LoRA path costs **−33% decode** at rank 64, and rank 16 is *slower* than rank 64 —
launch-overhead dominated (~600 extra tiny kernels/token), not FLOP- or bandwidth-bound.
Everything below is aimed at the fused CUDA path that eliminates that overhead.

Companion docs: `llamacpp-internals.md` (LoRA serving path, GGUF adapter format),
`upstream-landscape.md` (upstream appetite, discussion #8831).

llama.cpp source read at commit `0ef6e55ed` (2026-08-03); all file/line refs below are
against that commit.

## Verdict up front

1. **The field is nearly empty where we're building.** Two true fusions exist in public:
   SVDQuant/Nunchaku (GEMM regime, diffusion — §b) and EoRA (GEMV regime, GPTQModel
   stack — §b′). Nobody has a fused quantized-GEMV + low-rank path in llama.cpp/ggml
   formats. Even MiLo, whose entire thesis is quantized+low-rank MoE, ships unfused (§d).
2. **Our −33% pathology is published, three times over**: Punica's ~40 µs/LoRA-op flat
   across ranks 8–64 at batch 1 (§c), AdaFuse's rank-independent per-projection cost
   (§b′ table), FlashSVD v1.5's 1,174-launch/token diagnosis (§a.2). Rank-16 slower
   than rank-64 is exactly what launch-bound looks like.
3. **The fix decomposes into proven pieces**: Phase 0 graph hygiene + factor packing
   (FlashSVD v1.5, vLLM), Phase 1 epilogue fusion of B·t into mmvq (SVDQuant principle 2;
   mmvq's `has_fusion` bias/gate/GLU machinery is the in-tree precedent), Phase 2 Aᵀx
   into the activation-quantize kernel (SVDQuant principle 1; llama.cpp already launches
   `quantize_q8_1` per matvec). Expected end state: **~5–10% rank-proportional
   bandwidth overhead, zero extra launches** — consistent with SVDQuant's 50%→5–10% and
   EoRA's 1.1×→1.4× recoveries.
4. **Rejects**: CUTLASS EVT (no new contraction dim in epilogue), cuBLASDx (overkill for
   a rank-64 FMA loop), megakernels (right ceiling, wrong campaign), a new GGML op
   (backend pattern-fusion is the upstream-blessed mechanism — §f.2).

---

## (a) FlashSVD — and the batch-1 follow-up, FlashSVD v1.5

### a.1 FlashSVD (AAAI 2026)

- [arXiv 2508.01506](https://arxiv.org/abs/2508.01506), "FlashSVD: Memory-Efficient Inference with Streaming for Low-Rank Models," Shao et al., Duke. Repo: [Zishan-Shao/FlashSVD](https://github.com/Zishan-Shao/FlashSVD).
- A *peak-activation-memory* paper, not a latency paper: naive execution of SVD-compressed models materializes full-size intermediates (reconstructed Q/K/V, the B×M×D_ff FFN tensor), so peak memory doesn't drop. Their kernels stream rank factors:
  - **FlashSVDAttn**: keeps only P_a = X·U_a (B×M×r) off-chip; reconstructs Q/K/V *per tile on-chip* inside a FlashAttention-style loop. HBM traffic O(BMD) → O(BMr).
  - **FlashSVDFFN V1**: cuBLAS X·U → P, then one fused streaming kernel S = φ(P·V + b)·U_o chunked over D_ff with an on-chip B_M×r accumulator; the D_ff activation never exists. (V2, a single fully-fused GEMM-act-GEMM kernel, costs up to **+60% latency** — they recommend V1. Lesson: total fusion can lose to partial fusion.)
- Regime: **prefill/GEMM, encoders** (BERT/RoBERTa, batch 64, L40S). No quantization. −70% peak activation memory, latency ≈ parity.
- Applicability to us: low directly — wrong regime — but its follow-up is a bullseye:

### a.2 FlashSVD v1.5 (May 2026) — batch-1 decode, our pathology exactly

- [arXiv 2605.08314](https://arxiv.org/abs/2605.08314), "FlashSVD v1.5: Making Low-Rank Transformers Inference Actually Fast," same group/repo.
- Diagnosis mirrors ours: batch-1 decode of factorized checkpoints is "a runtime problem, not an arithmetic problem" — naive path = **1,174 kernel launches/token, 30.8 ms/tok, launch-bubble dominated** (their Fig. 1; compare our ~600 launches, −33%).
- The fix is notably NOT deep kernel fusion:
  1. dense-KV attention (reconstruct only the new token's q/k/v; standard KV cache + FA2);
  2. **packed MLP projection** — offline-concatenate up+gate input-side factors into one wide matrix, one launch, split rank activations after;
  3. **per-layer CUDA-graph replay** — the biggest lever: 1174 → **54 launches/token**, 30.8 → **12.1 ms/tok** (ablation: eager 32.4 → whole-graph split 16.6 → per-layer graphs 9.9 ms/tok).
- Up to 2.55× decode vs HF StaticCache; 1.86–2.20× vs a strong Dense-KV+FA2 baseline. No quantization.
- Applicability: **high** — proves a large fraction of launch overhead falls to graph hygiene + factor packing *before* any custom kernel, and its ablation is the baseline a true fused kernel must beat.

## (b) SVDQuant / Nunchaku — the fusion design template

- [arXiv 2411.05007](https://arxiv.org/abs/2411.05007), "SVDQuant: Absorbing Outliers by Low-Rank Components for 4-Bit Diffusion Models," Li, Lin, …, Han (MIT). ICLR 2025 Spotlight. Engine: [nunchaku-tech/nunchaku](https://github.com/nunchaku-tech/nunchaku) (quantizer [nunchaku-tech/deepcompressor](https://github.com/nunchaku-tech/deepcompressor) — offline only, no kernels).
- Setup: W4A4 (INT4 group-64 sym + GPTQ; NVFP4 on Blackwell), **rank-32 fp16** branch absorbing weight/activation outliers.
- **The overhead result we're chasing:** naive low-rank branch = **+~50% of 4-bit-branch latency at ~1% extra FLOPs** — memory-bound: the 16-bit activation read for Aᵀx plus the 16-bit output write of B(·) rival the entire 4-bit branch's traffic. Their fusion cuts it to **5–10%** (Table 4: RTX 4090, naive INT4 212 ms → +SVDQuant 218 ms/diffusion step). Four kernels → two:
  1. **Down-proj fused into the activation-quantize kernel** — X is read from HBM once, producing both packed INT4 activations and Aᵀx (`quantize_w4a4_fuse_lora_kernel`, chaining `EpilogueLoraDown` + `EpilogueQuantize`, in `src/kernels/zgemm/`).
  2. **Up-proj fused into the W4A4 GEMM epilogue** — fp32 accumulator tile gets `+= (lora_act)·B` via fp16 HMMA (`EpilogueLoraUp`) before the single output store. Key files: `src/kernels/zgemm/gemm_w4a4.cu/.cuh`, `lora.cuh`, `epilogues.cuh`, `gemm_base.cuh`.
- Kernel-level facts worth stealing:
  - **Rank is a runtime parameter** — tiled in WARP_R=16 chunks with 2-stage double buffering, `MAX_RANK 1024`, per-16-rank fp32 scales (user LoRAs stack onto the SVD branch by rank concatenation — no recompilation).
  - Low-rank activations kept **fp32** ("no bf16 reduction in sm_89"); LoRA weights fp16.
  - **Regime: pure tensor-core GEMM** (diffusion, thousands of tokens/step). There is **no GEMV variant** of the fused path — their AWQ W4A16 GEMV (`src/kernels/awq/gemv_awq.cu`) has no LoRA fusion at all.
- Transfer assessment: **the two-way fusion principle transfers exactly; the kernel does not.** At M=1 the warp-MMA tiling is dead weight; the GEMV re-derivation keeps t in shared memory and uses plain FMA rank-loops. And the arithmetic is *more* favorable for us: at M=1 both branches are weight-traffic-bound, and LoRA weight bytes (2·K·r·2B) are small against the quantized matrix — see the design sketch below.

## (b′) EoRA — the only published fused GEMV precedent (our exact regime)

- [arXiv 2410.21271](https://arxiv.org/abs/2410.21271), "EoRA: Fine-tuning-free Compensation for Compressed LLM with Eigenspace Low-Rank Approximation," NVIDIA (NVlabs), ICLR-W 2026. Repos: [NVlabs/EoRA](https://github.com/NVlabs/EoRA) (method), shipped via [ModelCloud/GPTQModel](https://github.com/ModelCloud/GPTQModel) (adapter generation + inference integration).
- Structurally identical to our target: GPTQ 3/4-bit base + fp16 low-rank correction, W4A16, batch-1 decode.
- **Their overhead numbers (H100, batch 1, 128-token gen):** naive PyTorch low-rank path collapses the 3-bit speedup from **1.7× → 1.1×** over fp16 (sometimes *slower* than fp16) — same order as SVDQuant's +50% and our −33%. Their **fused CUDA kernel** (low-rank multiply fused into the dequant/GEMV kernel, avoiding intermediate round-trips through L2/HBM) restores **up to 1.4× vs fp16** at rank 128, 3-bit; ranks 64/128/256 evaluated. Low-rank factors themselves quantizable to 3/4-bit with minimal loss.
- Kernel source lives inside the GPTQModel kernel stack (not a standalone file) — **first code to dive when we write ours**; it is the existence proof that fused quantized-GEMV + low-rank at batch 1 recovers the quantization speedup.

### Algorithm-side papers with NO fused kernels (the field is empty on our side)

| Work | Citation | Kernel status |
|---|---|---|
| Low-Rank Correction for Quantized LLMs (MSR) | [2412.07902](https://arxiv.org/abs/2412.07902) | None — PyTorch-simulated; "we have not studied the computational costs" |
| LQER / L²QER (ICML 2024) | [2402.02446](https://arxiv.org/abs/2402.02446), [repo](https://github.com/ChengZhang-98/lqer) | None — argues kernel-friendliness, runs standard GEMMs |
| QERA | [2410.06040](https://arxiv.org/abs/2410.06040) | None (analytical framework) |
| CALDERA (NeurIPS 2024) | [2405.18886](https://arxiv.org/abs/2405.18886), [repo](https://github.com/pilancilab/caldera) | Separate kernels; low-rank factors cause **measured throughput loss vs QuIP#** — negative evidence for unfused serving |
| ZeroQuant-V2 / LoRC | 2303.08302 | None (origin of the term) |
| GLOWQ / SERQ (2026) | [2603.25385](https://arxiv.org/abs/2603.25385) / 2603.08185 | None — GLOWQ *mitigates* correction overhead by sharing bases across layer groups |
| LoRA-Inlaid (NeurIPS 2024) | [paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/747dc7c6566c74eb9a663bcd8d057c78-Paper-Conference.pdf) | Quantized-base *sharing* across LoRAs; compute stays in SGMV-style kernels |
| AdaFuse (AAAI 2026, Baidu) | [2603.11873](https://arxiv.org/abs/2603.11873) | Weight-space merge kernel. Gold profiling: at seq-1 decode a LoRA up- or down-proj each costs as much wall time as the full backbone matmul, **independent of rank 8→64** — pure launch/context overhead (our r16-slower-than-r64 finding, published). Their merge route is unavailable to us: merging fp16 low-rank into quantized weights destroys the quantization. |

## (c) Punica SGMV / S-LoRA / vLLM LoRA kernels — throughput machinery, useless at batch 1

**Headline: no serving system fuses the LoRA path into the base matmul.** All run base GEMM
+ separate shrink/expand kernels; at best the final `+=` is fused into the expand kernel.

- **Punica** ([arXiv 2310.18547](https://arxiv.org/abs/2310.18547), MLSys 2024; [punica-ai/punica](https://github.com/punica-ai/punica), `csrc/`): SGMV = segmented gather matvec; batch sorted by adapter, `blockIdx.y` = segment; LoRA op = two launches (shrink `v = xA` with split-K + grid sync since r≪d; expand `y += vB` split over d_out — the add rides the expand). Base GEMM explicitly separate. **The critical batch-1 number (their Fig. 8/9, A100, h=4096):** ~**37–42 µs per LoRA operator at batch 1, flat across ranks 8–64** — published proof that at batch 1 the kernel is launch/latency-bound and rank is free (our r16-vs-r64 inversion, again). SGMV buys multi-adapter *throughput* (12×); it does nothing for single-request decode latency.
- **S-LoRA** ([arXiv 2311.03285](https://arxiv.org/abs/2311.03285); [S-LoRA/S-LoRA](https://github.com/S-LoRA/S-LoRA)): MBGMM (Triton, prefill) / MBGMV (modified Punica BGMV, decode) + unified paging (adapters paged with KV, page = one H-row). No single-request measurements; same separate-kernel structure.
- **vLLM current** ([`vllm/lora/ops/triton_ops/`](https://github.com/vllm-project/vllm/tree/main/vllm/lora/ops/triton_ops): `lora_shrink_op.py`, `lora_expand_op.py`, FP8 + MoE variants; orchestration `vllm/lora/punica_wrapper/punica_gpu.py`): [PR #13096](https://github.com/vllm-project/vllm/pull/13096) replaced SGMV with token→adapter-mapped `lora_shrink`/`lora_expand` largely for **CUDA-graph capturability** (SGMV's data-dependent grid wasn't capturable). Expand supports `ADD_INPUTS` (accumulate into base output). QKV shrinks/expands stacked into single calls with slice offsets. Mitigation for launch overhead = CUDA graphs, never fusion; no interaction with quantized-weight kernels at all.
- **LoRAX/TGI** ([predibase/lorax](https://github.com/predibase/lorax)): vendored Punica kernels, SGMV prefill/BGMV decode. Nothing new for batch 1.

Reusable ideas: fuse the add into the expand; stack same-x adapters into one call (Phase 0);
vLLM's lesson that graph-capturable launch shapes matter (echoes §e.1's #19217).

## (d) Marlin / Machete / ExLlama / mmvq — epilogue extensibility audit

Key insight from this sweep: **at batch 1 the correction B(Aᵀx) is an N-vector — i.e.
bias-shaped** — and three of four kernels already add an N-vector in their epilogue.

- **Marlin** ([arXiv 2408.11743](https://arxiv.org/abs/2408.11743); live source `csrc/libtorch_stable/quantization/marlin/marlin_template.h` in vLLM): fp16×int4 tensor-core GEMM built for batch 1–32 (dequant-in-register via lop3, cp.async multi-stage smem pipeline, stream-K over K with lock/barrier global reduce; `m_block_size_8` fast path for M≤8; ~3.87× vs fp16 GEMM). **Two ready-made composition hooks:**
  1. **Bias path**: kernel takes `b_bias_ptr` + `has_bias`; `write_result()` does `__hadd2(res, tmp_bias)` on the last slice. A tiny pre-kernel computing `lora_bias = B(Aᵀx) (+bias)` makes the expand+add free.
  2. **fp16 `atomicAdd` output mode** (`use_atomic_add`): Marlin can accumulate into a pre-populated C — pre-seed C with the correction, let the base GEMM add on top.
  No LoRA wired through either hook today.
- **Machete** (Neural Magic, Hopper; `csrc/libtorch_stable/quantization/machete/machete_mm_kernel.cuh`): CUTLASS 3.x wgmma kernel; computes Yᵀ=WᵀXᵀ so upconverted weights stay in registers. Epilogue is a **Sm90 EVT** (`Sm90EVT<Sm90AccFetch>`, `with_C` plumbing present but unused) — aux-tensor loads are a config-level change. But it loses to Marlin at batch 1 (their own decode chart) and EVT can't host a new contraction dim (§e.4) — reference architecture only.
- **ExLlamaV2** ([turboderp-org/exllamav2](https://github.com/turboderp-org/exllamav2), `exllamav2_ext/cuda/q_gemm.cu`): SIMT small-m path; bias via *separate* launches; only a per-row multiply hook in-kernel. Not the model.
- **ExLlamaV3** ([turboderp-org/exllamav3](https://github.com/turboderp-org/exllamav3), `exllamav3_ext/quant/exl3_gemv_kernel.cuh`) — **the most relevant single-kernel design found**: cooperative launch with exactly two `grid.sync()`s fusing *input Hadamard transform → trellis-dequant GEMV → output scale + inverse Hadamard* in ONE kernel (warps split K sync-free; `ld.global.cs` evict-first weight streaming; `mma.m16n8k16` with fp16→fp32 cadence accumulate). This is literally the "pre-transform, quantized GEMV, post-transform" shape we need: pre-stage → `t = Aᵀx`, post-stage → `y += B·t`. Proof the grid-sync single-kernel variant is viable in production code.
- **llama.cpp mmvq**: see §f.3 — the `has_fusion` template (bias/gate/GLU) is the intended extension point; the gate-fusion-only-at-ncols_dst==1 restriction is the register-pressure warning for an r=64 epilogue loop.
- **bitsandbytes / AWQ**: naive M=1 GEMV (NF4 LUT) with zero hooks / fixed 64-thread GEMM, no epilogue — nothing to borrow.
- **MiLo** ([arXiv 2504.02658](https://arxiv.org/abs/2504.02658); [repo](https://github.com/Supercomputing-System-AI-Lab/MiLo)) — closest published cousin: INT3 MoE + low-rank compensators, y = W₃bit·x + UV·x, custom INT3 tensor-core kernel… and the compensator is **explicitly not fused** (separate fp16 GEMMs). Even the paper whose whole point is quantized+low-rank ships unfused. The fused quantized-GEMV+low-rank kernel does not exist in public code outside EoRA (§b′).

## (e) Kernel-launch-overhead elimination: CUDA graphs, megakernels, epilogue fusion

### e.1 CUDA graphs: necessary, not sufficient — and possibly broken under LoRA

- llama.cpp's CUDA-graph integration (NVIDIA, Aug 2024 — [blog](https://developer.nvidia.com/blog/optimizing-llama-cpp-ai-inference-with-cuda-graphs/), [#6763](https://github.com/ggml-org/llama.cpp/issues/6763)): batch-1 only, per-token graph replay with KV-pointer param patching; measured max ~1.2x on H100, less on slower GPUs. Disabled by heuristic when the captured graph keeps changing.
- **⚠ [llama.cpp #19217](https://github.com/ggml-org/llama.cpp/issues/19217): LoRA in llama-server causes infinite CUDA-graph rebuild** (graph cache keyed on node pointers; the LoRA path churns them). With adapters active, decode may silently fall back to eager launch on all ~600 extra kernels. **Action item: verify whether our −33% run had graphs active** (debug log prints the disable reason; `GGML_CUDA_DISABLE_GRAPHS` to A/B). Part of the penalty may be recoverable just by making LoRA capture-stable.
- Residual overhead *under* graphs — the key published numbers:
  - Hazy Research ["No Bubbles"](https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles): ~2.1 µs/kernel from a stream → **~1.3 µs/kernel under graph replay**; ~5 µs effective stall per kernel boundary in memory-bound decode.
  - ["Memory-Bound but Not Bandwidth-Limited" (arXiv 2605.30571)](https://arxiv.org/html/2605.30571): eager-vs-graph delta ≈ 20.6% of batch-1 step time on H100, ~3% on L4 — launch overhead dominates only on fast-memory GPUs.
  - Ada-MK ([arXiv 2605.11581](https://arxiv.org/html/2605.11581v1)): launch overhead ≈ **14.6% of decode time even in TensorRT-LLM**.
- Verdict: even a capture-stable LoRA graph leaves ~600 × ~1.3 µs ≈ **0.8 ms/token** of irreducible node overhead. Graphs fix the CPU side; only fusion fixes the count.

### e.2 Megakernels / persistent kernels — the ceiling

- **Hazy Research low-latency megakernel** ([blog](https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles), [HazyResearch/Megakernels](https://github.com/HazyResearch/Megakernels), `demos/low-latency-llama/`): entire Llama-1B forward as one persistent kernel with an on-GPU instruction interpreter (7 fused instruction types), explicit 16 KiB shared-memory paging, global-memory counters for fine-grained dependencies. Batch-1: <1 ms/forward on H100 at **78% HBM bandwidth (vs 50% baseline), 2.5x vs vLLM**. Even so: ~120 µs of 600 µs is sync+setup — megakernels keep ~20% coordination cost.
- **Mirage Persistent Kernel (MPK)** ([arXiv 2512.22219](https://arxiv.org/abs/2512.22219), [mirage-project/mirage](https://github.com/mirage-project/mirage)): compiler that lowers a PyTorch graph to an SM-level task graph inside one persistent kernel; worker SMs + scheduler warps with event counters; task-to-task transition **1–2 µs**; batch-1 up to 1.7x vs vLLM/SGLang.
- **Ada-MK** ([arXiv 2605.11581](https://arxiv.org/html/2605.11581v1)): offline-scheduled trace megakernel for consumer Ada GPUs; +23.6% over TensorRT-LLM at batch 1.
- Verdict: the ceiling, not the first step — a decode-path rewrite is a different campaign. Two transferable elements: counter-based sub-kernel dependencies (a producer kernel's tail can feed t without a launch boundary), and the measured ~1.3–2 µs/node floor that defines our win condition.

### e.3 Horizontal fusion / grouping / stream overlap

- **HFuse** ([arXiv 2007.01277](https://arxiv.org/abs/2007.01277), CGO'22, [aoli-al/HFuse](https://github.com/aoli-al/HFuse)): source-level horizontal fusion of independent kernels, 2.5–60.8%.
- Intra-layer grouping is proven multi-LoRA practice: vLLM stacks the QKV LoRA shrinks/expands into single Triton calls with slice offsets (`vllm/lora/ops/triton_ops/`); TensorRT-LLM's LoRA plugin runs a CUTLASS **grouped GEMM** over per-request problems. All shrinks at a fusion point share x → concatenate A matrices `[K × r·k]`, one GEMV; ditto expands. ~600 → ~120–180 launches with existing kernel templates. (Cross-layer grouping impossible — sequential dependency.)
- **Stream overlap of the LoRA branch: nobody does it for compute.** Closest is SGLang [PR #15512](https://github.com/sgl-project/sglang/pull/15512) overlapping LoRA *weight loading* on a side stream. A fork-join side stream is encodable inside a CUDA graph (zero extra launch cost after capture) and the branch reads only tiny A/B — near-free if the base GEMV leaves SM slots. Cheap experiment, novel if it works.

### e.4 CUTLASS EVT / cuBLASDx — wrong vehicles

- **EVT** (Epilogue Visitor Tree, ASPLOS 2024, [DOI 10.1145/3620666.3651369](https://dl.acm.org/doi/10.1145/3620666.3651369); [Colfax deep-dive](https://research.colfax-intl.com/epilogue_visitor_tree/)): epilogue nodes = aux-tensor loads, broadcasts, elementwise ops, and reductions **over existing tile dimensions only — EVT cannot introduce a new contraction dimension**. A rank-64 B·t in the epilogue would need 64 chained broadcast-FMA nodes and t computed *before* the GEMM anyway. **Reject for the B-side; reject CUTLASS generally** — llama.cpp's batch-1 path is hand-written mmvq, not CUTLASS. The right formulation is a hand-fused epilogue: 64 FMAs per output element with t staged in shared memory.
- **cuBLASDx** ([docs](https://docs.nvidia.com/cuda/cublasdx/)): device-side GEMM inside a kernel — legitimate but overkill; a rank-64 dot is a trivial FMA loop. Skip.

### e.5 2025–2026 fused-LoRA-for-inference papers — the exact problem is unclaimed

No paper fuses LoRA into a **quantized** base GEMV for batch-1 decode. Nearest:

- **AdaFuse** ([arXiv 2603.11873](https://arxiv.org/html/2603.11873v1), 2026): per-token adapter routing was 2.5x *slower* from fragmented small launches (same pathology as ours); a fused single-pass adapter-switch kernel recovered 2.4x decode latency. Validates "the overhead is launches; fusion recovers it."
- **LoRAFusion** ([arXiv 2510.00206](https://arxiv.org/abs/2510.00206), training): fuse LoRA's memory-bound pieces into neighbor ops, leave the compute-bound base GEMM alone — 1.39x kernel-level. Same principle as our Phase 1/2 split.
- **Scaling DoRA** ([arXiv 2603.22276](https://arxiv.org/pdf/2603.22276)): fused Triton adapter-chain kernels, ~4x memory-traffic cut — evidence a Triton prototype is viable before hand-CUDA.
- Also swept: CaraServe (2401.11240), EdgeLoRA (2507.01438), "LoRA Is Slower Than You Think" (2507.08833) — batching/serving papers, no GEMV fusion.

---

## (f) llama.cpp CUDA backend: where the fused op lives

Read from source at `0ef6e55ed`. This section is the integration map.

### f.1 What the unfused path actually launches (why −33%)

`build_lora_mm()` (`src/llama-graph.cpp:1486-1515`) emits, per LoRA'd projection:

```
res    = ggml_mul_mat(W, x)            // mmvq kernel + quantize_q8_1 kernel
ab_cur = ggml_mul_mat(B, ggml_mul_mat(A, x))   // 2 × mmvf kernels (A,B are F16/F32)
ab_cur = ggml_scale(ab_cur, scale)     // 1 tiny elementwise kernel
res    = ggml_add(res, ab_cur)         // 1 tiny elementwise kernel
```

That is **4 extra kernels per projection** (A-matvec, B-matvec, scale, add), on top of the
base mmvq. With ~7 projections/layer (q,k,v,o,gate,up,down) × 46–64 layers ≈ 320–450
extra nodes, and each mul_mat possibly adding its own setup work → matches the observed
~600 extra launches/token. Every one of them is O(r·K) or O(N) work — microseconds of
dispatch around ~1 µs of math. The MoE variant `build_lora_mm_id` (`llama-graph.cpp:
1517-1553`) is worse (adds reshape/repeat/get_rows plumbing when scales are present).

Note: llama.cpp already runs decode under CUDA graphs (`ggml_cuda_graph_check_compability`,
`ggml-cuda.cu:2510` — graphs are only disabled for large-batch `MUL_MAT_ID`), and it uses
programmatic dependent launch (`ggml_cuda_pdl_lc/pdl_sync` in `quantize.cu:58`,
`mmvq.cu:515`). So our −33% is *already* the graph-amortized number: what remains is
GPU-side dispatch, dependency-chain serialization, and tail latency of ~600 kernels that
each run ~1–4 µs. Fusion, not graphing, is the only remaining lever.

### f.2 How the CUDA backend does op fusion today (the pattern we must follow)

There is **no fusion IR**: the CUDA backend pattern-matches node windows of the ggml graph
at evaluation time and dispatches a fused kernel, skipping the matched nodes. All fusion
is backend-local; the graph itself stays composed of primitive ops, so CPU/Metal/Vulkan
fall back to the unfused sequence automatically. This is the upstream-blessed mechanism —
a new GGML op is NOT needed and would be the wrong shape for a PR.

Machinery, all in `ggml/src/ggml-cuda/ggml-cuda.cu`:

- `ggml_cuda_try_fuse()` (line 3158) — called per node from
  `ggml_cuda_graph_evaluate_and_capture()` (line 3883, call site 4023); returns number of
  nodes to skip. Killable via `GGML_CUDA_DISABLE_FUSION=1` (line 3160) — useful for A/B
  benchmarks.
- `ggml_cuda_can_fuse()` (line 2937) — window matchers for fixed op sequences
  (RMS_NORM+MUL(+ADD), MUL_MAT+ADD+MUL_MAT+ADD+GLU, ROPE+VIEW+SET_ROWS, …), built on the
  core-side `ggml_can_fuse_subgraph()` (checks intermediates are consumed only inside the
  window) plus `ggml_cuda_check_fusion_memory_ranges()`.
- Existing **mul_mat_vec fusion** is the direct precedent for ours:
  - Patterns matched (lines 3404–3684): `{MUL_MAT, MUL_MAT, GLU}`,
    `{MUL_MAT, ADD, MUL_MAT, ADD, GLU}`, `{MUL_MAT, MUL(scale), [ADD(bias)], …, GLU}` and
    the MUL_MAT_ID equivalents, plus `mul_mat + scale + optional bias` (line 3694+).
  - Predicate `ggml_cuda_should_fuse_mul_mat_vec_q()` (line 1783): quantized src0,
    src1 F32, dst F32, `src1->ne[1] <= MMVQ_MAX_BATCH_SIZE` (=8, `mmvq.cuh:3`), and
    **fusion only for `dst->ne[1] == 1`** — i.e. exactly our batch-1 decode regime.
    F16/BF16 twin: `ggml_cuda_should_fuse_mul_mat_vec_f()` (line 1756) → `mmvf.cu`.
  - Matched operands are packed into `ggml_cuda_mm_fusion_args_host`
    (`common.cuh:1528-1543`: x_bias, gate, gate_bias, x_scale, gate_scale, glu_op) and
    passed to `ggml_cuda_mul_mat_vec_q(..., fusion)` (`mmvq.cuh:12`).

### f.3 The mmvq kernel — exactly where the epilogue hook sits

`mul_mat_vec_q<type, ncols_dst, has_fusion, small_k>` (`mmvq.cu:480-701`):

- Launch: grid = (nrows/rows_per_cuda_block, nchannels, nsamples); block =
  (warp_size=32, nwarps) with nwarps=4 (2 for K-quants on Turing) and
  **rows_per_cuda_block=1 at ncols_dst=1** (`calc_nwarps`/`calc_rows_per_block`,
  `mmvq.cu:354-478`). One thread-block owns one output row.
- Activations arrive pre-quantized: `quantize_row_q8_1_cuda` is launched once per mul_mat
  (`mmvq.cu:1230-1237`) before the matvec — this kernel is a natural host for the Aᵀx
  fusion (it already reads all of x; `quantize_q8_1` kernel at `quantize.cu:53-101`, one
  thread per element, PDL-enabled).
- Main loop (`mmvq.cu:594-614`): strided `vec_dot_q_cuda` over quant blocks; the
  `has_fusion` template already runs a **second dot product against a second quantized
  matrix in the same loop** (the GLU gate, line 606-611) — proof the structure tolerates
  a fused second operand.
- Epilogue (`mmvq.cu:619-693`): cross-warp shared-mem reduction, `warp_reduce_sum`, then
  a single surviving thread applies `+bias`, `×scale` (NVFP4), GLU, and writes `dst`.
  Fusion operands are prefetched early (lines 556-585) to hide latency. **This epilogue,
  between the reduction and the store, is exactly where `+ s·(B[row,:]·t)` goes.**

### f.4 Integration plan (the PR shape)

New fusion pattern in `ggml_cuda_try_fuse`, matching the `build_lora_mm` window:

```
{ MUL_MAT(W,x), MUL_MAT(A,x), MUL_MAT(B,·), SCALE, ADD }   → 5 nodes → 1..2 kernels
```

(and the `+MUL` variant when a per-tensor weight scale is present, and the gate/up+GLU
super-window so LoRA-on-gate/up keeps the existing GLU fusion).

Concrete file touch list:

| File | Change |
|---|---|
| `ggml/src/ggml-cuda/common.cuh:1528` | extend `ggml_cuda_mm_fusion_args_{host,device}` with `lora_a`, `lora_b`, `lora_t` (workspace), `lora_scale`, `lora_rank` (runtime fields — no new template instantiations needed; existing fields are runtime-null-checked under `if constexpr (has_fusion)`) |
| `ggml/src/ggml-cuda/ggml-cuda.cu` | new window matcher in `ggml_cuda_try_fuse` (~line 3404 block) + predicate additions to `ggml_cuda_should_fuse_mul_mat_vec_q/_f` (rank ≤ 64·k, A/B type F16/F32, contiguous) |
| `ggml/src/ggml-cuda/mmvq.cu` | epilogue: `result += lora_scale * dot(B[row,:], t)` before line 690 store; Phase 2: extend `ggml_cuda_mul_mat_vec_q` to produce `t` (see sketch) |
| `ggml/src/ggml-cuda/mmvf.cu` | same epilogue for F16/BF16 base weights |
| `ggml/src/ggml-cuda/quantize.cu` | Phase 2 only: `quantize_q8_1` variant that also accumulates partial `Aᵀx` into `t` via atomics |
| `tests/test-backend-ops.cpp` | fusion-window test cases (existing mul_mat+glu fusion tests are the template) |

Graph-side: **no changes required** to `build_lora_mm` — the window it emits today is the
window we match. (Optional later: emit the ops adjacently for multi-adapter cases; with a
single adapter they already are.)

CUDA-graph interaction: fused dispatch happens inside
`ggml_cuda_graph_evaluate_and_capture`, i.e. *during capture*, so the fused kernel is what
lands in the CUDA graph — fusion and graphs compose for free.

---

## Kernel design sketch: fused batch-1 y = dequant-GEMV(Q,x) + s·B(Aᵀx)

Shapes (27B-class): K = n_embd ≈ 5376, N = rows(W) ∈ {K, ffn, kv}, r ∈ {16..64}.
The structural problem: `t = Aᵀx ∈ R^r` is a full-K reduction needed by *every* output
row's block — a grid-wide dependency. You cannot compute t and consume it in one flat
kernel without a grid sync. Precedents: SVDQuant's two-kernel decomposition (§b) is the
template; EoRA (§b′) is the existence proof in our exact GEMV regime; FlashSVD v1.5 (§a.2)
and the graph literature (§e.1) set the non-kernel baseline we must beat. Tiers in order
of increasing ambition:

### Phase 0 — graph hygiene + factor packing (no new kernels). Baseline to beat

1. **Verify graph mode was active in the −33% run** ([#19217](https://github.com/ggml-org/llama.cpp/issues/19217):
   LoRA can silently de-graph llama-server decode; debug log prints the disable reason).
   If graphs were off, fixing capture stability alone may recover the eager-vs-graph gap
   (~20% of step time on fast GPUs, §e.1).
2. **Pack the A-side factors**: q/k/v (and gate/up) share x — concatenate their A
   matrices offline into one `[K × r·k]` matrix → one shrink GEMV per fusion point
   (FlashSVD v1.5's packed-MLP trick; vLLM's stacked-QKV Triton LoRA ops are the same
   move). Graph-level surgery in `build_lora_mm` callers, no CUDA changes.
   ~600 → ~120–180 launches. FlashSVD v1.5 got 2.55× decode from this tier alone
   (1174 → 54 launches) — but from an eager-PyTorch baseline; ours is already
   graph-amortized, so expect less.

### Phase 1 — epilogue fusion only (t via one small kernel). ~600 → ~120 launches

1. Keep one tiny kernel per projection: `t = s · Aᵀx` (an mmvf-style matvec, r outputs;
   one block, r·K/2 bytes of A read).
2. Fold **B·t + add** into the mmvq epilogue: after `warp_reduce_sum` (`mmvq.cu:654`),
   the block's threads cooperatively load `B[row, 0:r]` (F16, r·2 bytes) and `t`
   (r·4 bytes, L2-resident after step 1), compute the r-MAC dot, add to `result` before
   the store at line 690. Removes B-matvec, scale, and add kernels → 1 extra kernel per
   projection instead of 4.

   Per-row extra traffic: r·2 B (B row) + amortized t ≈ 128 B at r=64, vs ~K/2·bpw ≈
   2.4–3.6 KB of quantized W row → **~4–5% bandwidth increase**, sub-µs extra latency.
   The A-matvec kernel remains as launch overhead: ~120/token.

   Equivalent framing from §d: at batch 1 the correction is *bias-shaped* (an N-vector),
   and mmvq's `x_bias` hook already adds one — the minimal-risk variant of Phase 1 is a
   tiny kernel producing `lora_bias = s·B(Aᵀx)` consumed via the existing bias fusion
   path (Marlin's `has_bias` hook is the same idea in vLLM-land). Full B-in-epilogue is
   still preferred: it halves the tiny kernel's work (r·K instead of r·K + N·r) and
   avoids an N-sized scratch write+read.

### Phase 2 — fold Aᵀx into the activation-quantize kernel. ~120 → 0 extra launches

llama.cpp already launches `quantize_q8_1` per mul_mat (`mmvq.cu:1236`) — it reads all of
x. Extend it (new template flag, mirroring `has_fusion`):

- each block already owns a contiguous chunk of x (one thread/element,
  `quantize.cu:61-84`);
- after quantizing, the block computes partial `t_j += Σ_chunk A[j,k]·x[k]` for
  j = 0..r−1 (chunk×r F16 loads of A, warp-reduced), then `atomicAdd` r floats into a
  zeroed `t` workspace (pool-allocated next to `src1_q8_1`, `mmvq.cu:1231`);
- same-stream ordering (and PDL's grid-dependency sync) guarantees t is complete before
  the mmvq epilogue reads it. ~21 blocks × 64 atomics — negligible contention.

This is exactly the SVDQuant/Nunchaku decomposition (§b): down-proj rides the
quantize kernel (shares the x read), up-proj rides the main GEMM's output write —
re-derived for GEMV with FMA rank-loops instead of HMMA tiles, which is precisely the
part Nunchaku never built (their W4A16 GEMV has no LoRA fusion). EoRA (§b′) built the
GEMV fusion but inside the GPTQModel stack, not llama.cpp/ggml quant formats.
**Zero extra kernel launches.** Adopt Nunchaku's runtime-rank convention (rank tiled in
16-chunks, per-slice scales, fp32 t) so one binary serves r = 16..64+ and stacked
user-LoRA + correction adapters.

### Phase 3 (optional) — true single-kernel via cooperative launch

ExLlamaV3 (§d) proves the shape in production code: cooperative launch, two
`grid.sync()`s, pre-stage → GEMV → post-stage in one kernel. Ours would be:
stage 1 = all blocks cooperatively compute t = Aᵀx (r·K work spread over the grid),
`grid.sync()`, stage 2 = normal mmvq main loop + B·t epilogue. Costs: cooperative-launch
occupancy constraints (grid must fit residently), a llama.cpp-wide precedent change, and
CUDA-graph interaction to validate. The `has_fusion` gate path (`mmvq.cu:606-611`)
separately proves the main loop tolerates a second vec_dot operand if A/B are stored
q8_0 (reusing the q8_1 activations). Not needed if Phase 2 lands — Phase 2 already hits
zero extra launches without grid sync — but it is the fallback if extending
`quantize_q8_1` is rejected upstream, and the stepping stone toward the megakernel
ceiling (§e.2).

### Expected overhead budget (r = 64, F16 factors, Q4_K base, K = N = 5376)

| Component | Extra bytes/projection | vs base row traffic |
|---|---|---|
| A read (in quantize kernel) | r·K·2 ≈ 688 KB | — |
| B read (in mmvq epilogue) | N·r·2 ≈ 688 KB | — |
| t traffic | ~N·r·4 worst-case, L2-served in practice | ≈ 0 |
| Base W read | K·N·0.56 ≈ 16 MB | — |

Extra DRAM traffic ≈ 2·r/(K·bpw/8·(1/2)) … net **≈ 8–9% at r=64 F16**, ≈ 4–5% at r=64
q8_0 factors or r=32 F16. Launch overhead: zero (Phase 2). So the −33% penalty should
collapse to **single-digit percent, bandwidth-bound and rank-proportional** — which also
restores the natural ordering (r16 faster than r64) that the launch-bound regime
inverted. Matches SVDQuant's published 50%→5–10% fusion result in the GEMM regime (§b).

Fallback behavior: unfused path (current, works everywhere) remains the automatic
fallback whenever the pattern or predicate doesn't match — same graceful degradation as
every existing llama.cpp fusion.

---

## Sources

Papers (arXiv unless noted):
- SVDQuant — 2411.05007 (ICLR'25 spotlight) · EoRA — 2410.21271 (NVIDIA, ICLR-W'26)
- FlashSVD — 2508.01506 (AAAI'26) · FlashSVD v1.5 — 2605.08314
- Punica SGMV — 2310.18547 (MLSys'24) · S-LoRA — 2311.03285 (MLSys'24)
- Marlin — 2408.11743 · MiLo — 2504.02658 · AdaFuse — 2603.11873 (AAAI'26)
- LoRAFusion — 2510.00206 · Scaling DoRA — 2603.22276 · HFuse — 2007.01277 (CGO'22)
- MPK megakernel compiler — 2512.22219 · Ada-MK — 2605.11581
- Batch-1 decode overhead study — 2605.30571 · FlashInfer — 2501.01005
- EVT — ASPLOS 2024, DOI 10.1145/3620666.3651369
- Algorithm-only line: LQER 2402.02446 · QERA 2410.06040 · CALDERA 2405.18886 ·
  MSR low-rank correction 2412.07902 · ZeroQuant-V2 2303.08302 · GLOWQ 2603.25385

Code:
- [nunchaku-tech/nunchaku](https://github.com/nunchaku-tech/nunchaku) — `src/kernels/zgemm/{gemm_w4a4.cu,lora.cuh,epilogues.cuh}`
- [NVlabs/EoRA](https://github.com/NVlabs/EoRA) + [ModelCloud/GPTQModel](https://github.com/ModelCloud/GPTQModel) (fused GEMV kernel lives here)
- [Zishan-Shao/FlashSVD](https://github.com/Zishan-Shao/FlashSVD) · [punica-ai/punica](https://github.com/punica-ai/punica) · [S-LoRA/S-LoRA](https://github.com/S-LoRA/S-LoRA)
- vLLM: `vllm/lora/ops/triton_ops/`, `csrc/libtorch_stable/quantization/{marlin,machete}/`, [PR #13096](https://github.com/vllm-project/vllm/pull/13096)
- [turboderp-org/exllamav3](https://github.com/turboderp-org/exllamav3) — `exllamav3_ext/quant/exl3_gemv_kernel.cuh` (cooperative-launch fused GEMV)
- [HazyResearch/Megakernels](https://github.com/HazyResearch/Megakernels) · [mirage-project/mirage](https://github.com/mirage-project/mirage) · [aoli-al/HFuse](https://github.com/aoli-al/HFuse)
- llama.cpp @ `0ef6e55ed`: `ggml/src/ggml-cuda/{ggml-cuda.cu,mmvq.cu,mmvf.cu,quantize.cu,common.cuh}`, `src/llama-graph.cpp`

Key threads/blogs:
- [llama.cpp #19217 — LoRA breaks CUDA-graph reuse](https://github.com/ggml-org/llama.cpp/issues/19217) (action item)
- [NVIDIA: llama.cpp CUDA graphs](https://developer.nvidia.com/blog/optimizing-llama-cpp-ai-inference-with-cuda-graphs/) · [Hazy "No Bubbles"](https://hazyresearch.stanford.edu/blog/2025-05-27-no-bubbles)
- [Colfax EVT deep-dive](https://research.colfax-intl.com/epilogue_visitor_tree/) · [Red Hat Marlin](https://developers.redhat.com/articles/2024/04/17/how-marlin-pushes-boundaries-mixed-precision-llm-inference) · [Red Hat Machete](https://developers.redhat.com/articles/2024/10/14/introducing-machete-mixed-input-gemm-kernel)
- [cuBLASDx docs](https://docs.nvidia.com/cuda/cublasdx/) · [SGLang PR #15512 (LoRA load-stream overlap)](https://github.com/sgl-project/sglang/pull/15512)
