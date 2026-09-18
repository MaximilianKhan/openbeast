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
| 24–30 GB | 3090 / 4090 | Default model, context auto-scaled by `serve.sh`; or a Q4 quant for far more context; keep ~2 GB headroom | Floor tier — unmeasured, conservative |
| **< 24 GB** | 16 GB-class, 1080 Ti / 2080 Ti / 3060 12GB, and smaller | **BLOCKED at bootstrap** | **Below the floor** |
| Multi-GPU | e.g. 2x 3090 Ti | `--tensor-split`; hand-tune `-c` for now | Phase 2 |

**The 24 GB floor (Max, 2026-09-17; it was 11 GB from 2026-07-09 — enforced
by `ob_vram_floor_check` in `scripts/lib/hardware.sh`, called from bootstrap,
and reported by `./start.sh doctor`):** OpenBeast is an opinionated
distribution — "max intelligence, no compromise." The shipped default is
~21 GB of weights; below the 3090 / 4090 class nothing we ship runs at a
context worth the name, and every configuration that would is a quant and a
window so degraded that the result is not the product we test or stand
behind. Running llama.cpp on less is *possible* — it just isn't OpenBeast,
and we won't pretend to support it. The threshold is 22 000 MiB, because a
24 GB card reports ~24.5 GB (a 3090: 24 564 MiB) and the number has to admit
the class it names. Bootstrap hard-fails under the floor;
`OPENBEAST_FORCE_VRAM=1` proceeds unsupported, and we test nothing there.

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

## Phase 2 — adaptive context (✅ SHIPPED 2026-07-09)

`serve.sh` now scales the shipped `-c` value to the detected card's KV
budget instead of assuming 32 GB — the OOM-on-smaller-cards blocker is
closed. `ob_scale_context` (`scripts/lib/hardware.sh`, unit-tested):

- **Reference-class (≥ ~31.6 GB) or unknown VRAM → the measured value is
  used unchanged.** Behavior on the 5090 is byte-identical.
- **Smaller card → scale down proportionally.** Weights are a fixed cost
  (≈ the GGUF size), and with `--kv-unified` the KV cache scales with
  context and is shared across slots, so
  `new_ctx = ref_ctx × (card_KV_budget / ref_KV_budget)`, floored to a 4K
  multiple, where `KV_budget = VRAM − weights − 2 GB headroom − ~1.5 GB
  desktop`. Conservative on purpose: it under-allocates rather than risk an
  OOM.
- **Weights don't fit → warn "use a smaller quant" and fall back to
  `-c 8192`** so the user gets a clear message, not a cryptic CUDA OOM.
- Overrides: `OPENBEAST_CONTEXT=<n>` (exact), `OPENBEAST_VRAM_MIB=<n>` (when
  detection is wrong — Intel Arc / headless AMD), `OPENBEAST_AUTO_CONTEXT=0`
  (off). `bootstrap.sh` advice reflects the auto-scaling per tier.

This is *estimation*, not per-card measurement — the safe-and-shipping
version. The measured-profile refinement is still worth doing:

- A `profiles/` dir with one file per (GPU-class, model) pair: max MEASURED
  context + headroom, seeded by the 32 GB `docs/REFERENCE.md` tables.
- Community-contributed profiles (a 4090 owner runs `measure-vram.sh` and
  PRs the result) — real coverage we can't measure ourselves; `serve.sh`
  would prefer a measured profile over the estimate when one exists.
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
