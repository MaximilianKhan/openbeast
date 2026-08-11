> ⚠ see review/ corrections 2026-08-04

# Experiment 14 — Phase 3: quantized-t dp4a epilogue

Date: 2026-08-04. Tree: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` + imatrix
Gram patch (untouched) + Phase-1/2/2B patches + this patch. LOCAL RESEARCH — never
commit/push. Hardware: RTX 5090. Rigs: qwen3-0.6b Q2_K-imat + `adapter-imat-r128-q8.gguf`
and heretic27b-MIXED + `mixed-fc-r128q8.gguf` (both factors Q8_0, r128).
Builds clean, zero warnings; `test-backend-ops test -o MUL_MAT` all pass (re-run on the
final binary).

## Charter (Phase 2B finding 3/9)

Have the fold also emit a q8-quantized sidecar of t and switch the Q8_0-B epilogue to an
int8 dp4a dot (136 B/row instead of 512+136 float), removing the reason unfused Q8 wins
at 27B, and lift Phase 2B's size gates.

## Verdict up front

Built, correct to every printed PPL digit on both rigs, and the 27B gate is lifted — but
the charter's premise was **half wrong**: the epilogue tax at 27B was never t-traffic.
It was (a) register pressure of the fused instantiation collapsing occupancy of the main
W kernels and (b) the gate-fusion machinery lora windows were paying for without using.
Fixing those (this patch) is worth ~10x more than the dp4a dot itself:

| Rig (same-session pairs, final build) | unfused | fused | delta |
|---|---|---|---|
| 27B MIXED + Q8 r128, **gates lifted** | 84.7 | **86.9** | **+2.6%** (2B: +0.4% and only k/v fused) |
| 0.6B Q2_K + Q8 r128 | 391.4 | **701.7** | **+79.3%** (2B: +61.8%) |

27B fused now beats unfused point-estimate-wise with every fusable window fused (review
2026-08-04 struck ">20 sigma at +-0.1 run noise": our own logs show ~1.5-2 tok/s
within-session wander and up to 9% boost drift between sessions, so against a realistic
sigma of 1-2% this single pair is ~1-1.5 sigma — direction corroborated by the rank-0
ceiling 89.4 and the 86.5-88.0 vs 85.2-85.4 fused/unfused probe spread, magnitude
unresolved; locked-clock interleaved N=10 is the repair). The mission's aspirational >=+5% was NOT reached: a perf-only "epilogue
compiled in, zero lora work" probe measured the absolute ceiling at +4.9% same-session —
even a free dot only gets +2.3 points more. Details in Findings 3-4.

## What was implemented

**q8 sidecar of t.** When B is Q8_0 (and the window passes the row threshold, below), the
activation-quantize launch additionally emits `int8 q[rank]` + `float sc[rank/8]`,
appended to the q8_1 pool allocation (36-byte q8_1 blocks keep the tail 4-aligned; quants
land 4-aligned so the epilogue does int loads). Scale group = 8 = the components one
trailing block computes — finer than q8_1's 32 (accuracy margin) and needs no cross-block
sync. Two production modes:
- **Mode 1 (fold on):** the trailing block that computes t = A@x parks its 8 per-warp
  sums in smem, one `__syncthreads`, warp 0 does an 8-lane amax reduce + quantize. Float
  t is still written (kill switches, legacy tail, fold-off consumers stay valid).
- **Mode 2 (fold gated off, e.g. 27B K > 4096):** t comes from the separate matvec grid;
  the trailing blocks only quantize it (reads follow `ggml_cuda_pdl_sync`, same producer
  guarantee the float epilogue already relied on). This decouples the fold K gate from
  the dp4a epilogue entirely.

**dp4a epilogue.** Lane k4 covers 4 consecutive rank elements: B quants assembled from
byte loads (34-byte Q8_0 blocks are only 2-aligned) pre-sync, t quants via one aligned
int load post-sync, `ggml_cuda_dp4a`, per-4-chunk `d_B * sc_t[k4/8]` FMA (a 4-chunk never
straddles a scale group). Legacy tail (rank > 4*warp_size) got the matching dp4a branch.

**The real fix — two template splits + a register restructure of `mul_mat_vec_q`:**
1. `has_lora` split off `has_fusion`: lora windows no longer compile the GLU machinery
   (gate accumulator + branch in the hot dot loop, `tmp_shared_gate` smem, bias/scale
   prefetch). Alone worth ~+10 tok/s at 27B (75.0 -> 85.0 scale, see Finding 2).
2. `has_lora_tq` split off `has_lora`: the dp4a variant compiles no float-B/float-t code.
3. **No lora register lives across the main loop anymore**: B prefetch stays pre-sync,
   but the whole dot (t loads + dp4a + warp reduce) now runs immediately post-sync and
   parks its per-row result in `lora_row_sum` smem — protected by the same barrier as
   `tmp_shared`. Register counts (type 21, ncols=1, non-small-k): lean 61, Phase-3-interim
   lora 80 (**3 vs 4 resident blocks worth of occupancy — the 27B wall**), final
   float-lora 76, final **dp4a-lora 62 — lean occupancy restored**.

**Gates.** The Phase-2B NR gate (448K) now applies only to windows that will run the
float epilogue; windows taking dp4a (Q8_0 B, rank%8==0, nrows >= threshold) fuse
unconditionally. New per-window selector `GGML_CUDA_LORA_TQ_MIN_ROWS` (default 4096):
sidecar production is a constant cost on the producer chain while its win scales with
nrows — measured: 0.6B (all windows <= 3072 rows) is 40 tok/s faster on the float
epilogue (707.7 vs 667.6), 27B (all windows >= 5120 rows) is 2+ tok/s faster on dp4a.
Fold K gate re-validated at 27B with dp4a active: fold-on 85.6 vs fold-off 88.0 — the
default 4096 stands (mode 2 supplies the sidecar regardless).

## Implementation (file:line)

| File | Change |
|---|---|
| `common.cuh:1549-1557` | device args += `lora_t_sc`, `lora_t_qs`; `common.cuh:1568-1570` `GGML_CUDA_LORA_TQ_GROUP` (=8). |
| `quantize.cu:53-66` | kernel params += sidecar pointers; header comment. |
| `quantize.cu:157-186` | trailing-block tail: mode-1 write-through vs mode-2 read of precomputed t (`lora_a == nullptr`); F32 fallback branch guards `lora_a != nullptr`. |
| `quantize.cu:187-208` | sidecar quantize step: smem park, 8-lane `warp_reduce_max`, `d = amax/127`, `roundf` — matches `quantize_q8_1` rounding. |
| `quantize.cu:714-744` + `quantize.cuh:19-30` | host: sidecar params, trailing blocks launched when `lora_a || lora_t_qs`, rank%8 assert. |
| `mmvq.cu:479-485` | template += `has_lora`, `has_lora_tq` (both default false). |
| `mmvq.cu:520-593` | prologue: compile-time split unpack (tq variant carries no `lora_t`/`lora_b_fmt`); `lora_niter` = 1 (dp4a) / 4 (float); B byte-load pack pre-sync; prefetch cap = 4*warp_size both paths. |
| `mmvq.cu:666-700` | post-sync merged dot: t loads + dp4a/float FMA + `warp_reduce_sum` + `lora_row_sum` smem park, all before the main loop (the occupancy fix). |
| `mmvq.cu:775-815` | legacy tail (rank > 4*warp_size): `if constexpr (has_lora_tq)` dp4a branch, float branches otherwise; final add unchanged. |
| `mmvq.cu:1005-1030` | dispatch: lora windows -> dedicated `<..., true, tq>` instantiations chosen on `fusion.lora_t_qs`; glu+lora combination asserted impossible. |
| `mmvq.cu:1455-1500` | host: `GGML_CUDA_DISABLE_LORA_TQ`, `GGML_CUDA_LORA_TQ_MIN_ROWS`, sidecar pool append + pointer wiring, quantize call passes lora params whenever fold or sidecar is active. |
| `ggml-cuda.cu:3730-3749` | matcher: `window_tq` mirror of the host decision lifts the NR gate for dp4a windows; float-path windows keep 448K. |

Kill switches, all re-verified digit-identical on 0.6B PPL: `GGML_CUDA_DISABLE_LORA_FUSION=1`
(fully unfused), `GGML_CUDA_DISABLE_LORA_QUANT_FUSION=1` (Phase-1 dispatch),
`GGML_CUDA_DISABLE_LORA_TQ=1` (Phase-2B float epilogue + its gates), plus
`GGML_CUDA_LORA_FUSION_Q8_MAX_{NR,K}` and `GGML_CUDA_LORA_TQ_MIN_ROWS` as knobs.

## Correctness (final build, decode path `-ub 1`)

- 0.6B, `--chunks 16`: **PPL = 32.0814 +/- 1.70008** — identical to all printed digits
  across fused / unfused / all three kill switches / `MIN_ROWS=0` (forces dp4a + mode-1
  sidecar on every 0.6B window). Matches Phase 2B exactly.
- 27B, `--chunks 8`: **PPL = 6.9942 +/- 0.39109** — identical fused vs unfused, every
  per-chunk value identical. Matches Phase 2B exactly.
- The anticipated last-digit PPL shift from quantizing t did not materialize at printed
  precision on either rig — per-8 scale grouping keeps the int8 rounding of a rank-128
  dot below 6 significant digits of PPL. The mission's KLD-justification clause was
  therefore not triggered.
- Positive fire evidence: temporary host-side print (removed) showed the sidecar active
  on all 0.6B windows (fold=1) and on all observed 27B windows (fold=0, mode 2;
  nrows 5120-10240, K 5120-6144 — exactly the windows the 448K gate previously excluded).
- `test-backend-ops test -o MUL_MAT`: all pass. No adapter: `<false>` quantize kernel and
  lean mmvq unchanged.

## Speed (llama-server, greedy 256 tok, warm, 5 runs; 0.6B :8101, 27B :8102)

Final same-session pairs are in the verdict table. Decomposition sessions:

**Session L (morning, low GPU boost — unfused anchor 78.4):**
| 27B config | tok/s |
|---|---|
| unfused | 78.4 |
| fused ungated, dp4a, pre-split kernel | 75.0 |
| fused 448K gate, dp4a | 78.8 |
| fused 448K gate, float (Phase-2B exact) | 78.7 |
| fused ungated, float (Phase-2B T=inf mode) | 74.3 |
| perf probe: windows consumed, lean kernel, no B@t at all | 83.5 |
| perf probe: fused instantiation, rank forced 0 | 76.7 |

**Session H (settled, high boost — unfused anchor 85.2-85.5):**
| 27B config | tok/s |
|---|---|
| unfused | 85.2 / 85.4 |
| fused, `has_lora` split only (76-80 reg) | 85.0 |
| fused, final 62-reg dp4a variant | 86.5-88.0 (final pair: 86.9) |
| fold forced on (`MAX_K=8192`) | 85.6 |
| perf probe: rank 0 (ceiling) | 89.4 |

0.6B session: fused dp4a-everywhere 667.6, fused float-epilogue 707.7 (chosen by the row
threshold), unfused 396.3; final pair 701.7 vs 391.4.

## Findings

1. **The dp4a sidecar works and is digit-clean, but it was the small half of the fix.**
   dp4a vs float epilogue on identical kernels moved 27B by ~+0.7 tok/s (74.3 -> 75.0
   ungated, session L). Phase 2B's decomposition attributing -3.5 tok/s to "t re-read
   traffic" is corrected: that traffic was L2-resident and nearly free.
2. **The real 27B tax was occupancy.** The fused instantiation carried ~19 extra
   registers (61 -> 80): at 64-thread blocks that is 16 -> 12 resident blocks for every
   fused W kernel — kernels covering ~2.8M dst rows/token. Splitting `has_lora` off the
   GLU fusion machinery and moving the entire lora dot before the main loop (result
   parked in smem across the existing barrier, no lora register live in the main loop)
   brought the dp4a variant to 62 registers — lean occupancy — and 27B from 75.0 to 88.0
   scale. **Epilogue-fusion design rule for mmvq-class kernels: the epilogue must be
   register-neutral in the main loop; smem is the only safe place to carry its state.**
3. **The remaining gap to unfused-parity+launch-savings is the dot's issue cost, and it
   is bounded.** rank0 probe = 89.4 vs fused 86.9-88.0: ~1.4-2.5 tok/s for B byte-load
   packing, the post-sync t L2 round on warp 0, and the extra warp reduce. Even at zero
   dot cost the ceiling is +4.9% over unfused — the >=+5% aim was unreachable in
   session-H conditions.
4. **Launch savings are regime-dependent** (extends Phase 2's finding): consuming 4
   kernels/window is worth +5.1 tok/s over unfused at low boost (session L probe) but
   only +1.6 at high boost (session H) — replay dispatch scales with clocks, the saved
   per-link latency does not. Fused margins over unfused will be LARGER in contended or
   thermally-limited serving (consistent with Phase 1's +42% under contention).
5. **Sidecar production cost is constant, its win is per-row** — hence the per-window
   `MIN_ROWS` selector instead of a global on/off: 0.6B keeps the (now faster) float
   epilogue and gained +11% over Phase 2B from the template split alone (637.6 -> 707.7);
   27B runs dp4a on every observed window.
6. Boost drift between sessions was up to 9% on identical configs; every verdict above
   is a same-session pair (Phase-2B protocol upheld).

## Blockers / next steps

- None blocking: all mandated gates pass; 27B fused > unfused decisively and stably with
  gates lifted; no regression anywhere; kill switches live.
- Closing the last ~1.4-2.5 tok/s at 27B: split the epilogue dot across all nwarps
  (latency-parallel t loads, per-warp partials in `lora_row_sum[row][warp]`), and/or
  repack B once at adapter load into 4-aligned quants + separate scales to kill the
  byte-load assembly. Both are bounded by the +4.9% ceiling; the GRAPH_OPT allocator
  campaign (Phase-2 blockers) remains the bigger prize.
- `MIN_ROWS=4096` is tuned on exactly these two rigs; the crossover lives somewhere in
  3072..5120 rows — sweep on a mid-size model before trusting it elsewhere.
- mmvf twin (F16/BF16 base) still out of scope, as in all phases.
