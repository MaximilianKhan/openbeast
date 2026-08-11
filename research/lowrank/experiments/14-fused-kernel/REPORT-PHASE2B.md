# Experiment 14 — Phase 2b: Q8_0 adapter factors in the fused lora paths

Date: 2026-08-04. Tree: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` + imatrix
Gram patch (untouched) + Phase-1 + Phase-2 patches + this patch. LOCAL RESEARCH — never
commit/push. Hardware: RTX 5090. Rigs: qwen3-0.6b Q2_K-imat + `adapter-imat-r128-q8.gguf`
and heretic27b-MIXED + `mixed-fc-r128q8.gguf` (both factors Q8_0, r128).
Builds clean, zero warnings; `test-backend-ops test -o MUL_MAT` all pass.

## Approach

Both fused paths accept GGML_TYPE_Q8_0 A/B now (blocks of 32: fp16 scale d + 32 int8, 34
bytes). Dequant-to-float-in-register + FMA, as scoped: dp4a is inapplicable in both spots
because the other operand is float (x in the fold, t in the epilogue). The boolean
`lora_{a,b}_f16` flags became a 3-way fmt code (`GGML_CUDA_LORA_FMT_{F32,F16,Q8_0}`).

Key layout constraint discovered up front: `block_q8_0` is 34 bytes, so blocks are only
2-byte aligned — `char4`/`int` loads of `qs` are illegal in general. All Q8 quant loads
are byte loads (coalesced across the warp; the fp16 scale is a uniform or 8-way-shared
load).

- **Fold (A path, quantize.cu)**: mirrors the F16 vec4 prefetch structure. Per rank
  warp, an iteration covers 4 Q8_0 blocks = 128 elements: 8 lanes per block, 4 elements
  per lane (`d` + 4 bytes prefetched into registers BEFORE `ggml_cuda_pdl_sync`, npref=8
  iterations => K <= 1024 fully covered), then float4 x loads + FMA after the sync.
  Tail loop for larger K; scalar per-lane-per-block fallback when x is not 16B-aligned.
- **Epilogue (B path, mmvq.cu)**: in the pre-sync prefetch (rank <= 4*warp_size), lane
  k = m*warp_size+tid.x means the whole warp sits in ONE Q8_0 block per iteration — one
  broadcast `d` load + one per-lane byte, dequant into the same `lora_bv[]` registers the
  F16 path uses. The legacy large-rank tail got the matching Q8 branch.
- **Host/matcher**: validation accepts Q8_0 (+ rank/K % 32 asserts); matcher guard in
  `ggml_cuda_try_fuse` accepts Q8_0 factors.

## The 27B surprise, and the two size gates

First full build was digit-correct everywhere and +62% on the 0.6B — but **-6.6% on the
27B flagship** (75.1 vs 80.4 unfused). Root cause is not the Q8 kernels (zero ptxas
spills, regs 80-96, 0.6B flies): **the unfused competition is far stronger for Q8 than
for F16.** Unfused Q8 B@t runs as a tiny int8 dp4a mmvq (often upstream-fused with
scale+add into one kernel), and the big W kernels stay in the lean non-fusion
instantiation. F16 factors lose unfused (mmvf B@t, 2x traffic, no dp4a) — that is why
F16 fusion still wins at 27B (66.0 vs 63.9 measured same-session) while Q8 fusion lost.
Decomposition at 27B (same session): epilogue -3.5 tok/s (per-dst-row tax: t re-read of
rank*4B per row-block + reduce overhead, ~2.8M dst rows/token at 27B), fold -1.8 tok/s
(post-sync tail beyond the 1024-element prefetch: 34 iterations/lane at K=5376, 6 loads
each). Both scale with model size; launch savings do not.

Fix: two per-window empirical gates, Q8-only (F16 behavior untouched):

1. **Epilogue gate**: fuse only when `nrows_dst * rank <= GGML_CUDA_LORA_FUSION_Q8_MAX_NR`
   (default 448K). 0.6B max window 393K -> everything fuses; 27B only k/v (262K) fuse.
2. **Fold gate**: fold A only when `K <= GGML_CUDA_LORA_FUSION_Q8_MAX_K` (default 4096).
   Beyond that the window still fuses the epilogue but dispatches A as its own matvec
   (Phase-1 style). 27B k/v (K=5376) hit this: fold+epilogue 79.3 vs epilogue-only 79.9.
   0.6B ffn_down (K=3072) folds: forcing MAX_K=1024 there cost 17 tok/s (621 vs 638).

Threshold sweep at 27B (same session): T=inf 75.1, T=720K 77.7, T=448K 79.3 (fold on) /
79.9 (fold gated), T=0 79.4 = unfused rebaseline 79.4 (the earlier 80.4 was GPU boost
drift; all gate decisions were made against same-session baselines).

## Implementation (file:line)

| File | Change |
|---|---|
| `common.cuh:1535-1562` | host struct comments; device `lora_b_f16` -> `int lora_b_fmt`; `enum ggml_cuda_lora_fmt` {F32=0, F16=1, Q8_0=2}. |
| `quantize.cu:56-75` | kernel param `lora_a_f16` -> `lora_a_fmt`; separate `x16` (x alignment) from `vec4` (x+A alignment). |
| `quantize.cu:106-140` | Q8_0 A prefetch fold: 4 blocks/iter, 8 lanes/block, `d`+bytes pre-sync, float4 x + FMA post-sync, tail loop for K > 1024. |
| `quantize.cu:148-153` | scalar Q8_0 fallback (unaligned x): lane = element-in-block, loop over blocks. |
| `quantize.cu:686-702` + `quantize.cuh:19-22` | host: `int lora_a_fmt` param; `ne00 % QK8_0` assert. |
| `mmvq.cu:524-566` | prologue: `lora_b_fmt`; Q8_0 branch in the pre-sync B prefetch (whole warp in one block per iteration: broadcast `d`, per-lane byte). |
| `mmvq.cu:744-762` | legacy large-rank tail: Q8_0 dequant branch. |
| `mmvq.cu:1263-1270` | `ggml_cuda_lora_fmt_from_type` helper. |
| `mmvq.cu:1325-1345, 1370-1372` | host validation accepts Q8_0 for lora_a/lora_b (+ %32 asserts); fmt routed into the quantize launch. |
| `ggml-cuda.cu:3717-3737` | matcher accepts Q8_0 factors; **epilogue size gate** `GGML_CUDA_LORA_FUSION_Q8_MAX_NR` (default 448K = nrows*rank). |
| `ggml-cuda.cu:3753-3762` | **fold K gate** `GGML_CUDA_LORA_FUSION_Q8_MAX_K` (default 4096). |

Kill switches unchanged and re-verified: `GGML_CUDA_DISABLE_LORA_FUSION=1` (fully
unfused), `GGML_CUDA_DISABLE_LORA_QUANT_FUSION=1` (Phase-1 dispatch), plus the two new
gates double as research knobs.

## Correctness (final build, decode path `-ub 1`)

- 0.6B Q8 r128, `--chunks 16`: **PPL 32.0814 +/- 1.70008 — identical to all printed
  digits** fused vs `DISABLE_LORA_FUSION=1` (also identical under Phase-1-only mode).
- 27B MIXED + mixed-fc-r128q8, `--chunks 8`: **PPL 6.9942 +/- 0.39109 — identical to all
  printed digits** fused vs disabled, every per-chunk value identical too.
- `test-backend-ops test -o MUL_MAT`: all pass. No adapter: matcher cannot fire.

## Speed (llama-server :8100, greedy 256 tok, warm, 5 runs, same-session pairs)

| Rig | unfused | fused (final defaults) | delta |
|---|---|---|---|
| 27B MIXED + Q8 r128 | 80.6 | **80.9** | +0.4% (was -6.6% pre-gates) |
| 0.6B Q2_K + Q8 r128 | 394.1 | **637.6** | **+61.8%** |
| 27B MIXED + F16 r64 (regression check) | 63.9 | 66.0 | +3.3% (unchanged behavior) |

The prompt's ~82.3/82.4 prior-session 27B numbers were fused==unfused because the old
matcher silently rejected Q8 factors — the window never fired. This session's spread
(79.3-81.0 across hours) is GPU boost drift; each verdict above is a same-session pair.

## Findings

1. **Q8_0 in-kernel dequant works and is cheap where fusion wins at all.** At 0.6B the
   Q8 fused path lands within noise of the F16 result while unfused Q8 (394) is only
   slightly above unfused F16 — +62% net.
2. **"Fusion always wins" is false once the unfused alternative is dp4a-optimal.** For
   Q8 factors the per-row epilogue tax (t re-reads + reduces, ~nrows*rank*4B of L2
   traffic per projection) and the fold's post-sync tail grow with model size, while the
   launch savings are fixed — at 27B scale they cross. The gates encode that crossover;
   both are env-tunable for other rigs.
3. The proper large-model fix is a **quantized-t dp4a epilogue**: have the fold emit a
   q8_1 sidecar of t (it already holds t in registers pre-store; amax reduce is one more
   butterfly), then the epilogue does int8 dot at 136B/row instead of float at 512+136.
   That removes the only reason unfused Q8 wins at 27B. Needs a t-sidecar buffer and is
   Phase-3 scope.
4. Byte loads for Q8 quants are mandatory (34-byte blocks, 2-byte alignment); they cost
   nothing at 0.6B and are not the 27B bottleneck (zero spills, traffic-neutral).

## Blockers / next steps

- None blocking: all mandated gates pass, no regression anywhere, kill switches live.
- Phase-3 candidates, in payoff order for Q8 at 27B: quantized-t epilogue (finding 3),
  Phase-0 q/k/v packing, GRAPH_OPT allocator work (see REPORT-PHASE2.md blockers).
- The two gate defaults are tuned on exactly two rigs (0.6B, 27B on RTX 5090); re-sweep
  `GGML_CUDA_LORA_FUSION_Q8_MAX_{NR,K}` before trusting them on other hardware.
