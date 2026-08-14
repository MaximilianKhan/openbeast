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

## Rebase status

Recorded here so the next session doesn't re-derive it:

- **2026-08-14** — saved and committed; rebase onto current upstream attempted.
  See [`../../../docs/TODO.md`](../../../docs/TODO.md) for the outcome and
  whether a port is still outstanding.
