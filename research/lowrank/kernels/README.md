# beast-rank kernels — vendored patch

The low-rank VRAM campaign needed changes to llama.cpp itself: correction paths
in the CUDA MMVQ kernels, quantize-side re-rounding support, graph-level
hookup, gradient/covariance capture in `imatrix`, and a standalone
`gradmatrix` extractor. Measured result: **+79% on the 0.6B kernel trilogy**
(see [`../MASTER-TABLE.md`](../MASTER-TABLE.md) and
[`../RESULTS_ROLLUP.md`](../RESULTS_ROLLUP.md)).

**Why this directory exists.** `llama.cpp/` is gitignored by the OpenBeast repo
(`.gitignore:2`), so none of that work is captured by an OpenBeast commit — it
lived only as uncommitted files in one working tree until 2026-08-14. A re-clone
of `llama.cpp/`, a repo move, or a careless `git checkout .` would have erased
it with nothing to recover from. The patch here is the durable copy.

## What's saved

`0001-beast-rank-low-rank-VRAM-research-kernels-gradmatrix.patch` — 16 files,
1,367 insertions:

| Area | Files |
|------|-------|
| CUDA kernels | `ggml-cuda/mmvq.cu` (+267), `quantize.cu` (+180), `ggml-cuda.cu` (+138), `common.cuh` (+27) |
| ggml core | `ggml-alloc.c` (+60), `ggml.c` / `ggml.h` (+41), `ggml-cpu/*` (+3) |
| llama | `llama-graph.cpp` (+40), `llama-context.cpp` (+7) |
| tooling | `tools/imatrix/imatrix.cpp` (+205), `tools/gradmatrix/` (NEW, 422 lines) |

## Provenance

- **Developed against:** `0ef6e55edb306fcbcf73e6f1f41923cccb9cf7f8` (tag `b10254`)
- **Committed:** 2026-08-14 as `3690cab8b` on the local llama.cpp branch
  `beast-rank-kernels`
- **Built + measured with:** the 2026-08-04 build of that tree

## Reapplying after a re-clone

```bash
cd llama.cpp
git checkout -b beast-rank-kernels 0ef6e55ed        # the exact base
git am ../research/lowrank/kernels/0001-*.patch
```

To port onto a newer upstream instead, apply at the base as above and then
`git rebase origin/master` — expect conflicts, since upstream actively changes
several of these files (9 of the 14 had diverged within 180 commits of the
base). `git apply --3way` is the fallback if `git am` refuses.

## Rebase status — ✅ DONE 2026-08-14, onto b10434

The patch above is the **pre-rebase** rescue copy (base `b10254`). It has since
been carried forward 180 commits to upstream `b10434`, living on the local
llama.cpp branch `beast-rank-kernels` at `7ffa5cb9c`.

**Conflicts: 3 files, 4 hunks.** All 11 other files — including every CUDA
kernel (`mmvq.cu`, `quantize.cu`, `ggml-cuda.cu`, `common.cuh`) — auto-merged.

| File | Conflict | Resolution |
|------|----------|------------|
| `ggml/src/ggml.c` | upstream added `ggml_build_forward_order()` at the same spot as our `ggml_gradmatrix_cut_op()` | kept both — independent additions |
| `src/llama-context.cpp` (1) | upstream introduced `uint32_t res;` where we declare `gradmatrix_scale` | kept both declarations |
| `src/llama-context.cpp` (2) | upstream restructured early-returns into one accumulated `res` | **took upstream's structure and did NOT re-apply the scale here** — the final `return gradmatrix_scale*res;` already covers both branches; scaling in both places would compound to 64× |
| `tools/imatrix/imatrix.cpp` | upstream added a non-finite check where we call `gram_accumulate()` | kept both, finite check **first**, so bad activations abort before polluting the Gram matrix |

Verified beyond compiling — built clean (0 errors, all binaries including
`llama-gradmatrix`) and measured against the pre-rebase numbers on
Qwen3.8-27B Q5 + MTP n4 @ 262144:

| | Decode | Draft acceptance | VRAM |
|---|---|---|---|
| pre-rebase (b10254) | 123.7 tok/s | 196/407 = 48% | 28,852 MiB |
| **post-rebase (b10434)** | **121.8 tok/s** | **196/407 = 48%** | 28,843 MiB |

−1.5% is run-to-run noise, and the draft counts are *byte-identical* at
temperature 0 — the compute path is unchanged. This also served as the
mandatory post-rebuild tok/s check for the open upstream MTP throughput
regression (#25489, see [`../../../docs/LLAMACPP_WATCH.md`](../../../docs/LLAMACPP_WATCH.md));
no sign of it.

⚠️ **The rebased tree is NOT the active build.** It lives in
`llama.cpp/build-rebase/`; `llama.cpp/build/` (2026-08-04) is still what
`serve.sh` uses. Promoting it needs a tok/s check on the *default* model
(Heretic v2 27B MTP Q6) first — #25489 targets MTP, and only Qwen3.8 has been
checked. See `docs/TODO.md`.
