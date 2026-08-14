# Experiment 25 - allocator-level concurrency fix for GGML_CUDA_GRAPH_OPT

Date: 2026-08-04. Tree: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` + imatrix
Gram patch + Phase-1/2/2B/3 lora-fusion patches + this patch. LOCAL RESEARCH - never
commit/push. Hardware: RTX 5090. Build dir: `build-alloc` (Release, CUDA arch 120).

## Charter (Phase-2 blocker)

`GGML_CUDA_GRAPH_OPT=1` forks attention q/k/v (and, with Phase-2 pair-seeding, whole
lora windows) onto parallel CUDA streams. On lora rigs the events were correctly
rejected by `is_valid`: ggml-alloc keeps one global free-list per chunk, so a buffer
freed inside one branch (e.g. Kcur's base mul_mat output, freed once the lora add
consumes it) is handed to a later-allocated tensor in a DIFFERENT branch. Those
branches run on different streams with no ordering between them - a real cross-stream
WAR hazard. Phase 2's end-aligned-interleave attempt was REVERTED after silent
generation corruption (`is_valid` exempts tensors aliasing the join buffer). The fix
had to be allocator-level: pin branch intermediates until the join.

## Design chosen: branch-window pinning ("no-reuse-across-branch epoch")

The smaller of the two candidates named in the Phase-2 report. Per-stream free-lists
were rejected as invasive: they thread stream identity through `ggml_dyn_tallocr`,
complicate best-fit/merge, and buy nothing beyond what pinning already guarantees for
windows this small. Three pieces:

1. **Window marker.** New `GGML_TENSOR_FLAG_BRANCH` (=32) in `ggml.h`.
   `ggml_backend_cuda_graph_optimize` - only reachable under `GGML_CUDA_GRAPH_OPT=1`,
   so the change stays env-gated - sets it on every node of a committed fork-join
   region (the exact `original_order` set, noops included). Scheduler order guarantees
   the flags exist before allocation: `graph_optimize` runs inside
   `ggml_backend_sched_split_graph`; `ggml_gallocr_alloc_graph`/`reserve_n` runs after,
   on the same interleaved node order and the same tensor pointers.

2. **Deferred frees.** `ggml_gallocr` gains a pending-free list. While the allocation
   walk is on flagged nodes, both free paths - `ggml_gallocr_free_node` and the inplace
   `ggml_gallocr_free_extra_space` - record {allocator, addr, size} instead of
   returning the block. The walk flushes at the first unflagged node (= the join;
   committed regions are contiguous by construction) and again at end-of-graph as a
   backstop. Allocations always carve disjoint free space, so with no frees returned
   inside the window all concurrently-live branch tensors have provably disjoint
   addresses. The join node and everything after may reuse window memory - safe, the
   main stream waits on all branch join-events first.

3. **Inplace guard.** `ggml_op_can_inplace` ops reuse a parent's buffer at
   n_children==1. In-window that is only safe if the parent is in the same window
   (same branch => same stream; a node depending on two branches becomes the join).
   But the LAST interleaved consumer of the fork tensor sees n_children==1 and could
   inplace-steal a buffer other branches still read on other streams - a hazard
   `is_valid` cannot see (it only checks write/write overlap between branch nodes).
   So a flagged node never inplace-reuses an unflagged parent (or a parent viewing an
   unflagged base). In-branch inplace (lora add, norms, rope) is untouched, keeping
   pinning cost to the true branch intermediates.

Cost: peak growth bounded by one window's intermediates (flush at every join).
Measured 0.6B+r64: CUDA0 compute buffer 66.01 MiB under GRAPH_OPT=0 AND =1 - the
pinned bytes vanish inside the prefill-sized reservation. With GRAPH_OPT off no flag
is ever set and the allocator behavior is bit-identical to before.

## Bonus unlock: Q8_0 pair-seeding (the 27B rig was never forming regions)

With the allocator fixed, the 27B flagship still showed ZERO stream launches. Root
cause: `mixed-fc-r128q8.gguf` has Q8_0 A factors and Phase-2's pair-seeding predicate
only accepted F16/F32 A matvecs, so each lora A became its own branch, 6 branches >
max_fan_out 3, region dropped. One-line predicate extension (same structural guards -
directly follows base mul_mat, shares src[1] - plus `GGML_TYPE_Q8_0`). After it, 112
`Launching 3 streams` per pass on attn_norm-3/-7/... - exactly the full-attention
layers of the qwen35 hybrid; delta-net layers have no matchable fork (fan-out 2/4,
wqkv+gate) and are correctly untouched.

## Implementation (file:line)

| File | Change |
|---|---|
| `ggml/include/ggml.h:656` | `GGML_TENSOR_FLAG_BRANCH = 32`. |
| `ggml/src/ggml-alloc.c:481-487` | `struct deferred_free {alloc, addr, size}`. |
| `ggml/src/ggml-alloc.c:502-508` | gallocr fields: pending array/count/cap + `defer_frees` (calloc-zeroed; freed in `ggml_gallocr_free`). |
| `ggml/src/ggml-alloc.c:612-634` | `ggml_gallocr_free_or_defer` + `ggml_gallocr_flush_deferred_frees`. |
| `ggml/src/ggml-alloc.c:656,756` | both free sites routed through the helper. |
| `ggml/src/ggml-alloc.c:676-684` | inplace guard: flagged node never reuses unflagged parent/view-base. |
| `ggml/src/ggml-alloc.c:814-818,878-879` | walk drives `defer_frees` from the node flag; flush at first unflagged node and at end of graph. |
| `ggml/src/ggml-cuda/ggml-cuda.cu:4516-4520` | committed regions set the flag on every `original_order` node. |
| `ggml/src/ggml-cuda/ggml-cuda.cu:4419-4421` | pair-seeding predicate also accepts Q8_0 lora A. |

No change to `is_valid`, the interleave, or any kernel. `ggml_dyn_tallocr` internals
(best-fit, merge, chunk growth) untouched - deferral sits entirely in the gallocr walk.

## Correctness (build-alloc, decode path `-ub 1`)

- Builds clean; zero warnings from touched files.
- `test-backend-ops test` FULL suite: **12996 OK, 2/2 backends passed, exit 0**
  (run on the allocator-fix build; re-run queued on the final binary after the
  matcher one-liner - that path is env-gated off and unreachable from the suite).
- Positive fire: `Launching 3 streams at attn_norm-*` now appears on the 0.6B ADAPTER
  rig (fan-out-6 pair-seeded windows Phase 2 saw rejected) and on the 27B rig's
  full-attn layers. Base 0.6B launches as before.
- PPL digit-match, GRAPH_OPT=1 vs =0, `--chunks 16 -ub 1 -ngl 99` (0.6B Q2_K-imat):
  - no adapter: **47.0308 +/- 2.50069** both, per-chunk line identical (Phase-2 ref 47.0308).
  - `--lora adapter-fullcov-r64.gguf`: **31.7562 +/- 1.68117** both, per-chunk
    identical (Phase-1/2 ref 31.7562).
  - 27B MIXED + mixed-fc-r128q8, `--chunks 8`: **6.9942 +/- 0.39109** both, per-chunk
    identical (Phase-3 ref 6.9942).
- 200-token greedy `llama-server` generation (temp 0, top_k 1, warm), on vs off:
  **string-identical on all three rigs** (base 1078 B, r64 974 B, 27B 908 B). This is
  the check the Phase-2 corruption escaped; mandated and passed.

## Speed (llama-server greedy, warm, alternated off/on same-session pairs)

Host caveat: Max's CPU jobs ran throughout (load 40-66 on 32 threads; validate_gram35b
finished mid-campaign). Early 256/512-tok runs were useless (2x scatter); protocol
hardened to 2x256 warmup + 4096-tok (0.6B) / 1024-tok (27B) runs x3, alternating
off,on per session. Quiet-regime (load ~40, steady) results:

| Rig | GRAPH_OPT=0 | GRAPH_OPT=1 | delta |
|---|---|---|---|
| 0.6B base (s1/s2 medians) | 763.3 / 794.1 | 849.3 / 949.7 | **+11% / +19.6%** |
| 0.6B + fullcov-r64 (s1/s2) | 697.3 / 694.4 | 780.3 / 743.1 | **+12% / +7%** |
| 27B MIXED + r128q8 (6 sessions) | 80.0-87.0 | 62.9-87.1 | **within noise** |

- 0.6B base: best pair 794 -> 950 (+19.6%) reproduces the Phase-2 prize (+19%,
  857 -> 1018) on the new allocator - now with events VALIDATED rather than lucky.
- 0.6B r64: 697 -> 780 best pair (+12%). Off-anchor 697 matches Phase-3's float
  epilogue reference (701.7-707.7), confirming the quiet regime. The Phase-2
  projection was ~+17% (685 -> ~800); +7..12% measured under residual host load -
  prize substantially collected, exact size needs a quiet host.
- 27B: 6 alternated sessions, on wins 3 / loses 2 / 1 contended outlier (62.9);
  session boost drift +/-9% (as documented in Phase 3) dominates. Verdict: no
  regression, gain unproven. Structurally expected to be small: streams cover only
  the ~1-in-4 full-attn layers of the hybrid, and 5120-10240-row mul_mats already
  fill the GPU (concurrency pays on launch-bound small kernels, per Phase-3
  finding 4). Before the predicate fix, GRAPH_OPT=1 cost a consistent ~1-2% at 27B
  with zero regions formed - that is the per-token `graph_optimize` scan on a
  contended host, visible only because nothing offset it.

## Findings

1. **The Phase-2 diagnosis was exact.** The freed-base-buffer cross-branch reuse was
   the only thing `is_valid` was rejecting on the 0.6B lora rig: pinning alone flipped
   every window to validated, streams launched, and PPL/generation stayed digit- and
   byte-identical everywhere.
2. **Pinning is free here.** Windows are small and flushed at every join; the decode
   compute buffer did not grow a single MiB on any rig.
3. **The inplace guard matters in principle.** `is_valid` tracks only branch-node
   writes; an in-window inplace of the fork buffer would be invisible to it. Cheap to
   close at the allocator, hard to debug if ever hit.
4. **The 27B "allocator blocker" was actually a matcher gap.** Q8_0 A factors (the
   Phase-3 adapter format) silently broke pair-seeding, so the flagship never even
   reached `is_valid`. Worth remembering: absence of `Launching` logs is a separate
   failure mode from invalid events.
5. **A loaded host poisons short-burst decode benchmarks far more than it poisons
   long ones.** 256-tok runs scattered 2x; 1024-4096-tok runs on the same box were
   reproducible to ~1%. And an off-then-on ordering under decaying load manufactured
   a fake +8% at 27B once - alternation caught it.

## Blockers / next steps

- 27B speed verdict needs a quiet host (no CPU jobs) - single remaining unmeasured
  gate; correctness is fully closed.
- The hybrid dilution is structural: extending the matcher to delta-net layers
  (fan-out 2/4 forks, wqkv+gate) is new campaign territory, not allocator work.
- Upstreaming candidate: the pinning mechanism is generic and env-gated off by
  default; upstream's own TODO points at exactly this. Would need the flag-bit
  reservation discussed with maintainers first (Max's call, per AGENTS.md).
- Phase-0 packing (q/k/v share x: 3 quantize launches -> 1) composes with streams and
  remains open from Phase 2.
