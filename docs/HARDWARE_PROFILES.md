# Hardware profiles — plan + current state

**Goal (Max, 2026-07-07):** OpenBeast should meet users on *their* hardware.
Our reference box is a single RTX 5090 (32 GB) on Arch — every shipped
context length is measured there — but a user with 2x 3090 Ti, a 4090, an
AMD card, or an Intel Arc should get a recommended configuration instead of
an OOM crash and a shrug.

## What exists today (Phase 0 — shipped)

`scripts/lib/hardware.sh` detects vendor (NVIDIA / AMD / Intel / none), GPU
count, and VRAM, and prints an *advisory* recommendation. `bootstrap.sh`
shows it during preflight. Nothing changes launch behavior yet — the 5090
profile remains the default assumption, exactly as before. Set
`OPENBEAST_ASSUME_5090=1` to silence the advice (CI/headless).

Current advisory tiers (single NVIDIA GPU):

| VRAM | Class | Recommendation | Status |
|---|---|---|---|
| ≥ 30 GB | 5090 / A6000 | Shipped defaults as-is | **Measured** (reference) |
| 22–30 GB | 3090 / 4090 | Default model, `-c 131072` (128K) start, or Q4 quant; keep ~2 GB headroom | Unmeasured, conservative |
| 15–22 GB | 16 GB-class | Q4/Q3 of a ~14B model, or partial offload (`-ngl`) | Unmeasured |
| 11–15 GB | 1080 Ti / 2080 Ti / 3060 12GB | Smallest supported tier; expect small quants + short contexts | Floor tier, unmeasured |
| **< 11 GB** | — | **BLOCKED at bootstrap** | **Below the floor** |
| Multi-GPU | e.g. 2x 3090 Ti | `--tensor-split`; hand-tune `-c` for now | Phase 2 |

**The 11 GB floor (Max, 2026-07-09 — enforced by `ob_vram_floor_check` in
`scripts/lib/hardware.sh`, called from bootstrap):** OpenBeast is an
opinionated distribution — "max intelligence, no compromise." Below the
1080 Ti / 2080 Ti class (11 GB), every shipped model needs quants and
contexts so degraded that the result is not the product we test or stand
behind. Running llama.cpp on less is *possible* — it just isn't OpenBeast,
and we won't pretend to support it. 11 GB+ cards are cheap and plentiful
secondhand; that investment is on the user. Bootstrap hard-fails under the
floor; `OPENBEAST_FORCE_VRAM=1` proceeds unsupported, and we test nothing
there.

AMD: llama.cpp builds with `-DGGML_HIP=ON` (ROCm ≥ 6); serve scripts work
unchanged once `llama-server` is HIP-built. Intel Arc: `-DGGML_SYCL=ON`
(oneAPI). Both are wired into bootstrap's build step as of Phase 1.

## Phase 1 — vendor-aware build (shipped 2026-07-07)

As built: `scripts/lib/conf.sh` resolves a `GPU_BACKEND` key (env
`OPENBEAST_GPU_BACKEND` → `openbeast.conf` → default `auto`), and
`scripts/lib/hardware.sh` maps it to a concrete backend
(`ob_resolve_backend`: auto + nvidia→cuda, amd→hip, intel→sycl, none→cpu;
an explicit value wins over detection). The backend → cmake-flags mapping
(`ob_cmake_flags`) and the toolchain preflight (`ob_backend_preflight`:
nvcc / hipcc+rocminfo / icpx) live in that one lib, and **both**
`bootstrap.sh` and `scripts/update.sh --llama` build through it — they
cannot drift. After a successful build, bootstrap persists the resolved
`GPU_BACKEND=` into `openbeast.conf` so update never guesses differently
than bootstrap did. Policy: the CUDA path is byte-for-byte the original
reference build (`-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=<detected>`);
hip and sycl builds print a clear "UNTESTED by OpenBeast — reference
profile is CUDA/5090" warning; cpu warns about the 10-50x slowdown. HIP
auto-detects the `AMDGPU_TARGETS` gfx target via `rocminfo` when available.

## Phase 2 — measured profiles per tier

The 5090 numbers came from `scripts/measure-vram.sh` sweeps. The same
harness generalizes:

- A `profiles/` dir with one file per (GPU-class, model) pair: max measured
  context, headroom, and the serve arguments. The 32 GB tables in
  `docs/REFERENCE.md` seed the first profile.
- `serve.sh` resolves `-c` from the active profile instead of the hardcoded
  per-script values; per-script values become the 5090 profile.
- Community-contributed profiles (a 4090 owner runs `measure-vram.sh`, gets
  a PR-able profile file) — this is the path to real coverage we can't
  measure ourselves.
- Multi-GPU: measure `--tensor-split` on 2-card rigs; VRAM sums but KV
  locality doesn't, so measured > assumed.

## Phase 3 — pick-your-model bootstrap

`bootstrap.sh` currently downloads the 27B Q5 default unconditionally
(~21 GB). With profiles in place it should offer the tier-appropriate
default (e.g. 24 GB → Q4_K_M) with the 5090 default as the ≥30 GB choice.
Depends on Phase 2 measurements; guessing quants without measurements just
moves the OOM.

## Design constraints

- **Advice before automation.** Until a tier is measured, we print clearly
  labeled starting points — we don't silently launch configs we haven't
  validated (that's how the 416K→350K crash saga happened *on the box we
  own*).
- **The reference profile never degrades.** 5090 behavior stays byte-for-
  byte what the eval suite validated.
- **Detection is best-effort and non-fatal.** A weird driver must never
  break bootstrap; worst case the advice block is wrong and the user reads
  `docs/REFERENCE.md`.
