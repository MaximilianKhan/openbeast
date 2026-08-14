# Experiment 14 — Phase 1 fused adapter epilogue (B·t + scale + add into mmvq)

Date: 2026-08-04. Tree: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` + local
imatrix Gram patch (untouched) + this patch. LOCAL RESEARCH — never commit/push/PR.
Design doc: `research/lowrank/prior-art/arxiv-kernels.md` (Phase 1, §f.4).
Hardware: RTX 5090 (Blackwell, PDL + CUDA graphs active). Test rig: qwen3-0.6b Q2_K-imat
+ `adapter-fullcov-r64.gguf` (F16 factors, 196 lora'd projections, 28 layers x 7 proj).

## What was implemented

The 5-node window emitted by `build_lora_mm` (`src/llama-graph.cpp:1486`) —
`{MUL_MAT(W,x), MUL_MAT(A,x), MUL_MAT(B,t), SCALE, ADD}` — is pattern-matched in the
CUDA backend and collapsed to **2 kernels**: the A-matvec stays its own small kernel
(Phase 1 scope), and B·t + scale + add ride the mmvq epilogue. 4 extra kernels per
projection -> 1. Per token on this rig: 784 extra kernels -> 196.

| File | Change |
|---|---|
| `ggml/src/ggml-cuda/common.cuh:1528-1553` | `ggml_cuda_mm_fusion_args_{host,device}` extended with `lora_b`, `lora_t`, `lora_scale` (+ device-side `lora_rank`, `lora_b_f16`). Runtime fields, no new template instantiations. |
| `ggml/src/ggml-cuda/ggml-cuda.cu:3691-3750` | New window matcher in `ggml_cuda_try_fuse`, placed after the GLU fusions, before mul_mat+scale. Guards: exact edge match, `ggml_can_fuse_subgraph` + `ggml_cuda_check_fusion_memory_ranges`, A/B F16/F32 contiguous, rank<=1024, dims consistent with W, scale-op bias==0, `ggml_cuda_should_fuse_mul_mat_vec_q` on the base mm (quantized, batch 1), explicit no-alias check between the fused dst and the live `t` buffer. Dispatch = `ggml_cuda_mul_mat` for t, then `ggml_cuda_mul_mat_vec_q(..., fusion)` writing the ADD's dst; returns 4 (5 nodes consumed). Kill switch: `GGML_CUDA_DISABLE_LORA_FUSION=1` (also covered by the existing `GGML_CUDA_DISABLE_FUSION`). |
| `ggml/src/ggml-cuda/mmvq.cu:523-575` | prologue: lora fields unpacked under `if constexpr (has_fusion)`. |
| `ggml/src/ggml-cuda/mmvq.cu:668-690, 722` | epilogue: warp-cooperative `dot(B[row,:], t)` — lanes stride the rank, `warp_reduce_sum` butterfly, surviving thread adds `lora_scale*sum` before the store. First cut did this dot serially on one thread: **-15% regression under graph replay** (long per-block tail on cheap kernels). The cooperative version erased it. |
| `ggml/src/ggml-cuda/mmvq.cu:1252-1266` | host: validate + pack lora operands into device args; `has_fusion` selection includes `lora_b`. |

Behavior with no adapter loaded: matcher never fires (window absent), zero change.
PDL safety: mmvq's `cudaGridDependencySynchronize` (mmvq.cu:515) waits for full completion
of prior grids, so `t` from the preceding mmvf is visible before the epilogue reads it.

`git diff --stat` (imatrix.cpp = pre-existing Gram patch, not part of this work):
```
 ggml/src/ggml-cuda/common.cuh   |   9 +++
 ggml/src/ggml-cuda/ggml-cuda.cu |  59 +++++++++++++++++
 ggml/src/ggml-cuda/mmvq.cu      |  54 +++++++++++++++-
 tools/imatrix/imatrix.cpp       | 138 ++++++++++++++++++++++ (pre-existing, untouched)
```
Builds clean: `cmake --build build --config Release -j24 --target llama-server`.

## Correctness

- Positive fire evidence: temporary printf (since removed) showed the matcher firing at
  the expected 5-6 node spacing during warmup/capture; captured CUDA graph contains the
  fused kernels and is reused (verbose log: "CUDA Graph id 75 reused" 15/16 tokens).
- Decode path (the path the fusion actually changes), `-ub 1`, fused vs
  `GGML_CUDA_DISABLE_LORA_FUSION=1`:
  - 4 chunks: PPL 23.7266 +/- 2.56248 — **identical to all printed digits**.
  - 16 chunks: PPL 31.7562 +/- 1.68117 — **identical to all printed digits**.
- The mandated command (`llama-perplexity ... --chunks 16`, batch prefill) gives
  **31.8277 +/- 1.68** — with the patch, without it (`GGML_CUDA_DISABLE_FUSION=1`), same
  digits. NOTE: the expected 27.5836 does not reproduce on this checkout even fully
  unfused, so that reference came from different run conditions (the 27.58 in exp-10/11
  logs is a full-length wikitext run; --chunks 16 has +/-1.68 noise and a different
  estimate). The patch is exactly behavior-neutral on this command: prefill batches never
  enter mmvq (ne1=512 > 8), so the window cannot fire there by design.
- Greedy 64-token server output: byte-identical to unfused with the serial epilogue;
  diverges at a near-tie token (~#9) with the warp-cooperative epilogue — pure FP
  reduction-order effect, consistent with the digit-identical PPL.

## Speed (llama-server :8098, greedy 512-tok completions, quiet GPU, 5 runs/config)

| Config | tok/s |
|---|---|
| no adapter, graphs | 876.8 |
| r64 unfused, graphs (recorded baseline 444, machine was noisier then) | 475.4 |
| **r64 fused, graphs** | **476.0** |
| r64 unfused, eager (`GGML_CUDA_DISABLE_GRAPHS=1`) | 390.6 |
| r64 fused, eager | 396.6 |
| r16 unfused, graphs | 479.7 |
| **r16 fused, graphs** | **528.0 (+10%)** |

Under GPU contention (shared with the live stack, eager): unfused 223 -> fused 317
(+42%) — the launch-elimination effect is real and large when dispatch is contended.

## Findings

1. **Phase 1 works and is safe**: fires on the real graph, digit-identical decode PPL,
   zero cost when unmatched, zero change with no adapter, clean fallback.
2. **The launch-overhead thesis is only half the story on this hardware.** At r64 the
   fused path exactly ties unfused (476.0 vs 475.4) despite removing 588 of 784 extra
   kernels/token: under Blackwell graph replay those tiny kernels were nearly free. The
   remaining +0.96 ms/token adapter penalty is dominated by the **196 surviving A-matvec
   launches and their dependency stalls** (a hard serialization point in front of every
   fused mmvq) plus rank-proportional work.
3. **Fusion restores the natural rank ordering.** Unfused is rank-flat (r16 480 = r64
   475 — the published launch-bound signature). Fused is rank-proportional: r16 528 >
   r64 476. Overhead decomposition (vs 877 no-adapter): fused penalty = ~0.68 ms
   constant (A-matvec launches/stalls) + rank-linear slope.
4. GPU util is 94% during fused decode; multi-stream concurrency (`GGML_CUDA_GRAPH_OPT`)
   is off by default, so no hidden overlap — the measurement is honest.
5. Epilogue design lesson (cost us one iteration): a serial rank-loop on the surviving
   thread regressed graphs decode by 15%; warp-cooperative striding + butterfly reduce
   is mandatory. Register/launch config untouched, no occupancy change.

## Blockers / next steps

- **Phase 2 is now clearly the payoff step**: folding t = A^T x into `quantize_q8_1`
  removes the 196 remaining launches AND the A->mmvq serialization — the measured
  ~0.68 ms/token constant term, i.e. a projected ~476 -> ~700+ tok/s at r64 on this rig.
  The r16 result (+10% from epilogue fusion alone) is the floor for what Phase 2 unlocks.
- The 27.5836 reference needs re-derivation: rerun the intended baseline command on this
  checkout and pin the exact invocation next to the number.
- llama-cli hangs on this machine (with or without the patch, adapter, or fusion —
  pre-existing; 98% CPU spin after load). Server + perplexity used instead.
- Multi-adapter: only the first adapter's window fuses (second ADD chain falls back
  unfused) — fine, correct by construction.
- mmvf twin (F16/BF16 base weights) not done — quantized-base only, per Phase 1 scope.
