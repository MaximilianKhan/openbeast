# Experiment 14 — Phase 2: fold t = A·x into the activation-quantize launch

Date: 2026-08-04. Tree: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` + imatrix
Gram patch (untouched) + Phase-1 patch + this patch. LOCAL RESEARCH — never commit/push.
Hardware: RTX 5090. Rig: qwen3-0.6b Q2_K-imat, F16 adapters, 196 lora'd projections.
Builds clean (`cmake --build build --config Release -j24 --target llama-server`), zero
warnings; `test-backend-ops test -o MUL_MAT` passes.

## Approach chosen (and why)

**Primary path from the design doc — fold t = A·x into `quantize_q8_1` — with one
structural change: dedicated trailing thread blocks instead of atomicAdd partials.**
The doc's sketch (each quantize chunk atomicAdds partial t) requires a zeroed t
workspace, i.e. a memset node per projection — reintroducing exactly the graph nodes
Phase 2 exists to remove. Instead the quantize grid grows by `ceil(rank/8)` blocks;
each block's eight 32-lane groups each compute one full `t_j = dot(A[j,:], x)` (x is
L2-hot because the quantize blocks read it concurrently). No atomics, no zero-init, no
new nodes; per projection 3 kernels -> 2, and the A->mmvq dependency edge collapses
into the pre-existing quantize->mmvq edge (PDL-covered, same-stream ordering).

**Rejected fallback (batched/strided GEMM over all A-matvecs):** K is not uniform
across projection types (q/k/v/gate/up K=n_embd, attn_output K=n_head·head_dim,
ffn_down K=n_ff), so cuBLAS strided-batched doesn't apply without padding; a grouped
kernel still leaves one launch plus a sync barrier per graph window and does not
compose with the per-projection window matcher Phase 1 built.

**The unplanned second half — epilogue prefetch — turned out to be the real payoff**
(see Findings): Phase 2's A-fold alone recovered ~0 tok/s because the bottleneck
Phase 1 attributed to "196 A-matvec launches" is actually tail-load latency, not
launches.

## Implementation (file:line)

| File | Change |
|---|---|
| `quantize.cu:53-148` | `quantize_q8_1` templated on `has_lora`. Trailing blocks (`blockIdx.x >= nblocks_quant`) compute t: one 32-lane group per rank component; F16 A rides a vec4 path (float4 x + 2x half2 A per lane); A loads are issued **before** `ggml_cuda_pdl_sync()` (A is static weights, x is not) so they overlap the wait for the producer grid — 8 prefetch iterations cover K<=1024 fully, larger K tail-loops. Scalar and F32 fallbacks kept. `<false>` instantiation byte-equivalent to the old kernel. |
| `quantize.cu:604-631` + `quantize.cuh:19-26` | `quantize_row_q8_1_cuda` gains defaulted trailing params (`lora_a`, `lora_a_f16`, `lora_t`, `lora_rank`); lora path launches `block_num_x + ceil(rank/8)` blocks, batch-1 only (asserted). |
| `common.cuh:1536-1541` | host fusion args gain `lora_a`; device args unchanged (quantize takes plain params). |
| `mmvq.cu:1300-1320, 1352-1358` | host: validate `lora_a` (F16/F32, contiguous, shapes, single contiguous x row) and route it into the quantize launch; `fusion->lora_a` set means no separate A kernel is dispatched. |
| `ggml-cuda.cu:3738-3756` | matcher: `fold_a` guard (x contiguous single row, t is [r,1]); on failure falls back to Phase-1 dispatch (separate `ggml_cuda_mul_mat` for t). Kill switch `GGML_CUDA_DISABLE_LORA_QUANT_FUSION=1` = Phase-1 behavior; `GGML_CUDA_DISABLE_LORA_FUSION=1` = fully unfused. |
| `mmvq.cu:511-556, 617-634, 668-689, 719-744` | **epilogue redesign** (applies to Phase-1 mode too): B-row + t loads move out of the kernel tail. B (static) is prefetched into registers *before* `ggml_cuda_pdl_sync` (mmvq.cu:558), t right after it; warp-per-row assignment (`threadIdx.y` owns row `row0+threadIdx.y`) so the `small_k` variant's 4-row blocks compute their four dots in parallel instead of 4 serial load+reduce rounds in the tail; per-row sums land in `lora_row_sum[]` smem before the existing `__syncthreads`, and the tail is a single smem read + FMA. `lora_max_iter=4` => ranks <= 4*warp_size prefetched; larger ranks keep the old tail loop (`mmvq.cu:723-741`). Per-lane m-loop preserves the old FP reduction order (PPL unchanged). |
| `ggml-cuda.cu:3995-4062` | upstream bug fix: when `GGML_CUDA_GRAPH_OPT=1` events are invalid, the graph previously stayed *interleaved* (original order never restored) — losing in-stream fusion on top of losing concurrency (measured 552 tok/s, worse than no GRAPH_OPT). Original order is now restored regardless of validity. |
| `ggml-cuda.cu:4352-4412` | groundwork: attn_norm fan-out 6 (lora doubles q/k/v consumers) is now accepted, pairing each A-matvec seed into its base mul_mat's branch so a whole lora window maps to one stream. Events are still rejected by `is_valid` (see Blockers) so behavior today is unchanged-but-safe. |

## Correctness

- `llama-perplexity ... --chunks 16 -ub 1` (decode path): **PPL = 31.7562 +/- 1.68117 —
  identical to all printed digits** across Phase-2 fused, Phase-1
  (`DISABLE_LORA_QUANT_FUSION=1`), fully unfused (`DISABLE_LORA_FUSION=1`), and
  `GGML_CUDA_GRAPH_OPT=1`. Matches the mandated Phase-1 reference 31.7562.
- Mandated batch command (`--chunks 16`, no `-ub`): 31.8277 +/- 1.68469, same digits as
  Phase 1 (prefill never enters mmvq; patch is behavior-neutral there by design).
- r16 and r128 fused vs unfused: digit-identical PPL (75.3042 / 94.1999).
- No adapter: matcher can't fire; `quantize_q8_1<false>` is the old kernel; PPL 47.0308
  = baseline; server output sane; `test-backend-ops test -o MUL_MAT` all pass.
- Fold verified firing on all 196 windows (temporary counter, removed).

## Speed (llama-server :8099, greedy 256 tok, warm, 5 runs)

Two sessions (GPU boost drifted between them; ratios are the stable quantity).
Session A = quiet GPU, directly comparable to the Phase-1 report's conditions.

| Config | Session A | Session B | vs no-adapter |
|---|---|---|---|
| no adapter | 857 | 780 | 100% |
| no adapter, `GGML_CUDA_GRAPH_OPT=1` | 1018 | 911 | +19% over base |
| r64 unfused | 470 | 470 | 55-60% |
| r64 Phase-1 dispatch, new epilogue | — | ~583 | 75% |
| r64 Phase-1 as shipped (old tail epilogue) | 476 | — | 55% |
| **r64 Phase 2 (fold + prefetch epilogue)** | **685** | **626** | **80%** |
| r16 Phase 2 | 640 | 630 | 81% |
| r128 Phase 2 | 638 | 620 | 79% |
| r64 Phase 2, `GRAPH_OPT=1` | — | 646 | 83% |

**Phase-1's 476 -> 685 (+44%) at r64 under Phase-1's own measurement conditions.** The
adapter tax collapsed from -45% to -20%. Under GPU contention the unfused gap is far
larger (Phase 1 measured +42% there); not re-measured.

## Findings

1. **The "0.68 ms of A-matvec launches" hypothesis is falsified.** Removing all 196 A
   kernels (fold firing on every window, kernel count == no-adapter) moved r64 decode
   by ~0: 476 -> 476. Launch overhead under Blackwell graph replay was nearly free;
   eager-vs-graphs is now only ~15 tok/s. The constant was **tail-load serialization**:
   Phase-1's epilogue loaded B[row,:] and t *after* the main dot loop, adding a full
   HBM/L2 latency round to every mmvq's critical path — and on the `small_k` variant
   (K=1024 rows: 140/196 projections, 4 rows per block) it did **four serial
   load+reduce rounds** in the tail of warp 0.
2. **The mysterious rank-64 anomaly** (r64 474 while r16/r32/r128 all ~525, three
   different r64 adapters, eager or graphs, PDL on or off) was this same tail
   serialization interacting with trip count; sliced-rank adapters mapped a fast/slow
   split at {16,32,128} vs {48..96}. A perf-only epilogue-disable knob proved the entire
   effect lived in the B·t tail (685 tok/s for r64 AND r128 with the dot skipped). The
   prefetch redesign erased the anomaly: all ranks now within 2%.
3. **Prefetch before the PDL grid sync is the key kernel idiom**: static operands (B
   rows in mmvq, A rows in the quantize t-blocks) can legally load while
   `cudaGridDependencySynchronize` waits for the producer — turning exposed latency
   into overlap. mmvq: 640 -> 663; quantize fold: fold cost 0.29 -> 0.15 ms/tok.
4. Remaining decomposition at r64 (session A): no-adapter 1.166 ms/tok; +0.153 ms fold
   (t-block critical path: post-sync x loads + reduce, x196); +0.141 ms epilogue
   (~0.7 us per projection each). Extra DRAM traffic (A+B ~77 MB/tok) accounts for only
   ~0.045 ms — the rest is per-link latency on a ~200-deep serial kernel chain. This is
   the same wall the megakernel literature (No Bubbles, MPK) prices at ~1.3-2 us/node.
5. **`GGML_CUDA_GRAPH_OPT=1` is a bigger lever than everything above for the base
   model** (+19%) and defines the next campaign (see Blockers). Bug found+fixed on the
   way: invalid concurrent events left the graph interleaved, silently breaking window
   fusion (552 tok/s regression mode).

## Blockers / next steps

- **Target 700+ not fully reached on-tree: 685 at r64** (session A; 80% of no-adapter).
  The projection assumed the 0.68 ms was launch overhead removable at zero cost; ~0.45
  ms of it was actually tail latency (now recovered) and ~0.29 ms is genuine
  producer-consumer latency of computing t on the critical path, of which ~0.15 ms
  remains. Closing the rest needs chain-shortening, not more fusion.
- **GRAPH_OPT composition is the identified next step.** Pair-seeding (this patch) maps
  each whole lora window to one stream, but `is_valid` correctly rejects the events:
  ggml-alloc's global free-list reuses one branch's freed buffer (Kcur's base) in
  another branch, a real cross-stream WAR hazard. An end-aligned interleave was tried
  and REVERTED: it silently corrupted generation because `is_valid` exempts tensors
  aliasing the join buffer from overlap checking. The proper fix is allocator-level
  (pin region intermediates until the join, or per-stream free-lists) — upstream's own
  TODO. Prize: no-adapter went 857 -> 1018 with 3-stream attn; lora q/k/v windows
  (6 of 7 fused kernels per attn layer) should scale similarly, plausibly 685 -> ~800.
- Phase-0 packing (q/k/v share x: 3 quantize launches -> 1, A factors concatenated)
  attacks the same chain length and needs no allocator work.
- rank > 128 uses the legacy tail epilogue (correct, slower); rank <= 8·warp_size could
  be covered by bumping `lora_max_iter` at some register cost — unmeasured.
- mmvf twin (F16/BF16 base) still out of scope, as in Phase 1.
- Session-B numbers ran at lower GPU boost; use ratios, not absolutes, across sessions.
