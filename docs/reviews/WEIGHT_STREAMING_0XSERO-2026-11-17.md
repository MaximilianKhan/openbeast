# 0xSero's weight streaming, revisited for the 5090 (2026-11-17)

**Recommendation (for Max to decide): trial one thing, skip the rest, and
first run a free check on our own engine.**

- **Trial**: Qwen3.8-Flash-Next on 0xSero's SGLang + EXL3 offload image,
  through a new gated adapter and the engine gate.
- **Skip**: his GLM-5.3-Flash and DeepSeek-V4.1-Flash stacks on the rig.
- **Do first, at no cost**: check whether llama.cpp v1.0.0 already keeps
  Flash-Next's 27 GiB n-gram table off host RAM. If it does, our own fastest
  form fits the daily 75% scope.

Nothing was installed, run or changed for this review.

> **Correction, 2026-11-18.** The gate maths below uses the pre-measurement
> estimate of 55-70 tok/s for llama.cpp's cache profile. It was measured the
> same day at **76.84 tok/s** decode on the rig
> ([WEIGHT_STREAMING.md](../WEIGHT_STREAMING.md#flash-next-on-beast-measured-november-17-2026)),
> so the engine gate's 1.5× bar is now about **115 tok/s** (1.5 × 76.84 =
> 115.3), not 82-105. 0xSero's 87.4 tok/s is 1.14× that, and **fails the
> gate** even if our box matched his rented host. The trial below would need
> a gain the published runs do not show (his 103.5 on a PRO 6000 with a
> 48 GB cache is also under the bar). The review is kept as written; read
> its "Speed vs the 1.5× gate" row and the trial recommendation with this
> in mind.

Subject: 0xSero (GitHub `0xSero`, org `sybil-solutions`, X `@0xSero`), public
work up to 2026-11-17. It follows the 2026-11-15 decision report (the "5090
Weight Streaming" artifact, cited in [WEIGHT_STREAMING.md](../WEIGHT_STREAMING.md)
"The research behind this"). That report covered his October stacks as
"reference design, built for 8-channel servers, GLM image sm_86 only". Labels
below:

- **MEASURED (who, hardware)**: someone ran it. None of these runs are ours.
- **CLAIMED**: a post or README states it, with no run record.
- **ESTIMATED**: our own arithmetic.
- **UNVERIFIED**: needs the rig.

## What is new since 2026-11-01

There is **no new weight-streaming technique** from him after 2026-11-01.
His November work moved to datacenter recipes (4x RTX PRO 6000, 4x H200),
Inkling-2-Small, the Local AI app and a measurement skill. His last offload
code commits are from 2026-10-16 to 10-18. The November items that touch
streaming are below.

| Date | Item | What it is | Matters for the rig? |
|---|---|---|---|
| 11-01 | `qwen38-flash-next-3090-usb-offload`, config LS ([repo](https://github.com/sybil-solutions/qwen38-flash-next-3090-usb-offload); [X post](https://x.com/0xSero/status/2117042619662701016)) | Strata v0.1.41 plus 35 patches. A 3090 over USB holds the hot experts; a 16 GB Mac mini M4 computes the cold ones on Metal, from an LRU of wired RAM slots plus NVMe. MEASURED (0xSero): 33.8 tok/s decode, 1,048 tok/s prefill at 33K, ABBA-paired, with KL quality gates | No. It needs Metal and a USB-attached GPU. The method is worth copying (below) |
| 11-03 / 11-08 | New lab runs of the October offload image ([3090 run](https://github.com/sybil-solutions/local-ai-registry/blob/main/lab/runs/rtx-3090-24gb.qwen3.8-flash-next.sglang-exl3-offload.256k.20261103T113021.json), [PRO 6000 run](https://github.com/sybil-solutions/local-ai-registry/blob/main/lab/runs/rtx-pro-6000-blackwell-96gb.qwen3.8-flash-next.sglang-exl3-offload-multiarch.256k.20261108T025951.json)) | 3090 with MTP (NEXTN, 2 steps): **65.6 tok/s**, 2,180 tok/s prefill, 56 GB RAM. RTX PRO 6000 Max-Q (sm_120, like the 5090) with a 48 GB cache: **103.5 tok/s**, 6,396 prefill. Six gates pass on each (load, chat, reasoning, tools, 213K context, speed) | **Yes.** MTP works together with offload, and the sm_120 build runs on Blackwell |
| 11-12 | `engram_disk_v3` in the `vllm-moet-dsv41` image ([local-ai-images#77](https://github.com/sybil-solutions/local-ai-images/pull/77)) | DeepSeek-V4.1's Engram tables read from a read-only mmap of the checkpoint on NVMe, with a worker pool, instead of ~330 GB of shm. Bit-exact CLAIMED | No: 4x RTX PRO 6000. DeepSeek stays on the Sparks |
| 11-09 | `0xSero/GLM-5.3-Flash-DSpine` ([HF](https://huggingface.co/0xSero/GLM-5.3-Flash-DSpine)) | A speculative drafter for GLM-5.3-Flash. His own card says it is slower than DFlash2. CC BY-NC-ND | No |
| 11-16 | `inference-optimisation-skill` ([repo](https://github.com/sybil-solutions/inference-optimisation-skill), MIT, ~950 stars) | A procedure: compute the ceiling first, change one thing at a time, interleaved A-B-B-A controls, a ledger of every run (failures too), a long-context needle gate, then a pinned clean build | **Yes, as process.** Our gate lacks the interleaving and the needle gate |

Searched and **not** relevant: `lego-offload` (11-06; it keeps private data
away from cloud agents, it does not move weights), `omarchy-local-ai`
(desktop app), his Substack (last post 2026-09-08). He opened no PRs or
issues against llama.cpp, vLLM, ExLlamaV3 or TensorFold since 2026-10-15
(`gh search prs/issues --author 0xSero`). The X posts the search found
(GLM on 16 GB DDR4, "30% faster", the RTX 30-series Flash-Next posts) are
dated 2026-09-29 to 10-16 when decoded from their status ids.

## The technique that applies to us (October, re-examined)

The 5090-relevant piece is `sglang-exl3-offload-multiarch`. It is
trellis-serve branch `rtx30-offload` @ `2e274225` (2026-10-16), in the image
`ghcr.io/sybil-solutions/sglang-exl3-flashnext-multiarch@sha256:fc147f4d…`.
It is built for sm_86, sm_89 and **sm_120**, and the build fails unless every
`.so` carries SASS for all three.

What streams, and how (read from the code at `2e274225`):

| Tier | What | How |
|---|---|---|
| VRAM | attention, shared/dense weights, KV (fp8), a global expert slot pool | **CLOCK** replacement with fused admission. On a miss, the GEMM reads the expert **zero-copy from pinned host RAM** and writes it into the victim slot in the same pass. No separate upload step (`kernels/offload_runtime.py`) |
| CPU | a share of each layer's decode misses | An AVX2 pool (physical cores − 2) computes it straight from the same pinned records, **GPU-exact fp16**, in parallel with the GPU's zero-copy share. Only the GPU's share crosses PCIe (`offload/cpu_tier.py`). JIT-built with `-march=native` on first start |
| Prefill | every expert | **Layer-ahead staging**: layer l's missing experts are copied on a dedicated stream into double buffers while layer l−1 computes. Staged experts are **not admitted**, so a prompt does not flush the decode working set |
| NVMe | the 18–33 GB n-gram (PLE) table | Rows stay in `ngram_embedding.safetensors`. A CLOCK RAM row cache (2 GB in the recipe) sits in front, filled by coalesced O_DIRECT reads; the gather overlaps layer 0 (`offload/ngram_nvme.py`) |
| Format | EXL3 3.05 bpw (trellis) | About 28% fewer expert bytes per miss than our IQ4_XS (4.25 bpw): ESTIMATED ~0.74 vs 1.03 GB per token before cache hits |

The 5090 run: recipe `qwen3.8-flash-next.sglang.256k`, lab run
2026-10-16 on a rented vast.ai 5090 in Hong Kong. The host CPU and RAM are
not recorded. MEASURED (0xSero):

- **87.4 tok/s** decode, **4,555 tok/s** prefill;
- 262K context;
- **55 GB host RAM**, 86 GB disk;
- one request at a time (`--max-running-requests 1`);
- stock `turboderp/Qwen3.8-Flash-Next-exl3` 3.05bpw_h5_ng5 @ `69e33439`.

## Compared with what OpenBeast ships

| | llama.cpp v1.0.0, Flash-Next + cache (ours, shipped, not yet run) | trellis-serve offload (0xSero) |
|---|---|---|
| Decode, Flash-Next | **55–70 ESTIMATED** (upstream #29887: 67.8 MEASURED, EPYC 7742 + PCIe 4, `-cmoe`, 19 GB cache) | **87.4 MEASURED** (rented 5090, unknown host); 103.5 on a PRO 6000 with a 48 GB cache |
| Prefill | 714 tok/s MEASURED (us, old pin, static). The cache serves only batches of ≤ 32 tokens | **4,555 MEASURED** (layer-ahead staging) |
| Host RAM | ~85 GiB planned (40 CPU layers + **27.6 GiB of tables**) | 55 GB (the n-gram table on NVMe) |
| Miss path | upload, or CPU compute via `--moe-cache-cpu-share` (upstream: rarely helps when pinned) | zero-copy GEMM plus a parallel exact CPU share |
| MTP with offload | not covered (#31497 keeps the cache off the draft); MTP is off in our profile | works (3090: 65.6 tok/s with NEXTN) |
| Weights | GGUF; the uncensored orcarouter IQ4_XS is already on the rig | EXL3. **An uncensored build from the same orcarouter base exists**: `Lygodactylus/Qwen3.8-Flash-Next-Uncensored-exl3-3bpw` @ `fe57295c`, 68 GB, n-gram at 3 bpw, quality "not measured" (its card) |
| Engines and licence | ours, pinned by commit, conformance-tested | single author; branch not merged to `main`; MIT code over SGLang (Apache-2.0) and ExLlamaV3 (MIT). The `local-ai-images` repo that builds the image has **no licence file** |

Two things are genuinely new for us, compared with the 11-15 report:

1. **The policy blocker is gone for Flash-Next.** An uncensored EXL3 of the
   same orcarouter checkpoint exists. A second option is
   `alesha-pro/Huihui-Qwen3.8-Flash-Next-abliterated-exl3-3bit-hq_h6_ng6`
   (92 GB, a different abliteration).
2. **The fit is better than ours.** 55 GB of host RAM sits well inside the
   75% daily scope, with no scope raise.

For the other models, nothing changes:

- **GLM-5.3-Flash.** His `glm53-flash-offload` image is **sm_86 only**.
  - CLAIMED (images README): `fast` mode needs ~218 GiB RAM and `exact`
    ~111 GiB, which is over big mode's 109.8 GiB scope with the stack.
  - `nvme` mode runs in a 55 GiB cap at 19.5 tok/s, MEASURED (0xSero, 3090,
    v4.6). That is not better than big mode's 20–30 ESTIMATE.
  - It would need an sm_120 rebuild that we would own.
- **DeepSeek-V4.1-Flash.** `dsv41-flash-offload` pins ~200 GiB of RAM and is
  sm_86 only. It stays on the Sparks.

## A free finding in our own pin: the n-gram table may already be lazy

0xSero frees ~27 GB of RAM by streaming the n-gram table from NVMe. Our
pinned llama.cpp appears to do the same by default. Read at `b1b770d9`:

- `src/models/qwen4exp.cpp:216` creates `per_layer_tok_embd` with
  `TENSOR_READ_LAZY`.
- `--lazy-mode` defaults to `auto`: lazy for tensors over 4 GiB
  (`llama-model-loader.cpp:1189`).
- `init_mappings` (`:1506`) maps lazy tensors **even when `--load-mode` is
  not mmap**. So `--load-mode none` still pins the experts and reads the
  PLE rows on demand.

[WEIGHT_STREAMING.md](../WEIGHT_STREAMING.md) budgets "27.63 GiB of tables
(token embeddings + PLE, always in host RAM)". That budget may be wrong on
v1.0.0. Two caveats:

- the lazy rows come through the page cache, which the cgroup charges
  (reclaimable);
- the table is one tensor of 51B parameters, ~25 GiB at ~4.25 bpw
  (ESTIMATED).

If the table is lazy, `-cmoe` plus a ~19 GB cache (upstream's 67.8 tok/s
form) needs about 61 + 4 + 3 ≈ 68 GiB. That fits the **75% scope**, so the
"raise the scope" decision in runbook step 3 would not be needed. All of
this is UNVERIFIED until it runs.

## Feasibility on beast

| Question | Answer |
|---|---|
| Hardware fit | VRAM: `--mem-fraction-static 0.88` of 32 GB, as in the measured run. RAM: 55 GB. NVMe: ~86 GB, plus O_DIRECT reads of the n-gram file, so `WEIGHTS_DIR` must be on NVMe. CPU: AVX2 on a 9950X3D is fine. The unknowns are the same as in the runbook: DIMM speed, PCIe link width, NVMe generation |
| Speed vs the 1.5× gate | For llama.cpp at 55–70 tok/s, the gate needs **82–105**. 87.4 came from an unknown, probably server-class host. ESTIMATED ratio on our dual-channel box: **1.2–1.5×**. A pass is possible, not likely |
| Licence | Code: MIT, Apache-2.0, MIT. Weights: `qwen-community-1.0`, the same as our Flash-Next GGUF. The image recipe repo has no licence: fine to pull and run, unclear to redistribute (beast-ark) |
| Maturity | One author. Unmerged branch, 15 stars. Validated twice (vast 5090, owner PRO 6000) by his own six-gate lab, plus 20+ runs on 30/40/50-series cards. No outside reports |
| Offline install | Yes. Image by digest (16.1 GB compressed, one amd64 manifest plus a BuildKit attestation), `docker save` for staging, `HF_HUB_OFFLINE=1` in its env, weights mounted read-only. The `-march=native` CPU-tier module JIT-compiles on first start, so persist its cache on a volume. UNVERIFIED: that the image's compiler works offline |
| Pinning and verification | Image digest `fc147f4d…`; trellis-serve commit `2e274225` (the Dockerfile checks `rev-parse`); weights `Lygodactylus@fe57295c` with per-file LFS sha256. `vllm.sh`'s docker pin, stage and install code fits almost unchanged |
| Memory scope | **A docker container runs outside `openbeast-stack`'s cgroup.** The adapter must pass `--memory`/`--memory-swap` itself |

## Integration path (if Max approves the trial)

**Step 0: the free llama.cpp check** (about 1 h, during runbook step 3; no
new engine).

1. `./start.sh -d serve-qwen38-flash-next-unc-iq4xs-moecache.sh`, then
   `grep 'lazy read enabled' .run/stack.log`. Expect `per_layer_token_embd`.
2. Record `systemctl --user show openbeast-stack -p MemoryCurrent` and the
   process RSS. Compare them with the ~85 GiB plan.
3. If the table is lazy, try `-cmoe` with
   `moe-cache-tune.py --max-mib 20000` in the 75% scope. Make it the shipped
   form if it measures 60 tok/s or more. Correct WEIGHT_STREAMING.md's table
   either way.
4. Add MTP to the Flash-Next cache profile as a separate arm: 0xSero's 3090
   evidence says MTP and offload combine.

**Step 1: an adapter, `scripts/engines/sglang-exl3.sh`** (about one day; no
GPU needed).

- Clone `vllm.sh`'s docker functions: `install` (pull by digest, refuse a
  mismatch), `stage` (`docker save` plus MANIFEST), `activate`
  (re-pin an image id).
- Pin `ENGINE_PIN_SGLANG_EXL3=v0.7.0-multiarch@sha256:fc147f4d…`.
- `gate_binding` returns the image digest, the profile and the weights
  repo@rev. `profile_check` calls `ob_engine_gate_ok`.
- `launch` runs `docker run --gpus all --ipc=host --shm-size 16g` with:
  - `--memory 64g --memory-swap 64g`;
  - weights mounted `:ro`, plus a persistent JIT-cache volume;
  - the engine JSON's env and args, with `--port` taken from `INFERENCE_URL`;
  - the API key passed without argv. UNVERIFIED: how SGLang 0.5.20 takes a
    key other than `--api-key`.
- `hydra_kind` is `openai`; `ready` is `/health`.
- Outside the file:
  - `obprofile.py` must accept `BACKEND=sglang-exl3`;
  - add a profile `scripts/backends/models/qwen38-flash-next-unc-exl3-3bpw.env`
    (Lygodactylus @ `fe57295c`);
  - add a `tests/test_engines.sh` case.

**Step 2: install and conform** (about 2 h on the rig).

1. Run `scripts/engine.sh install sglang-exl3 --digest sha256:fc147f4d…`, or
   stage it and install `--from` for an offline rig. Then
   `model-fetch.sh --profile qwen38-flash-next-unc-exl3-3bpw`.
2. First boot under `OPENBEAST_ENGINE_GATE=measuring`: watch the JIT build
   and the load time.
3. Run `scripts/backends/conformance.sh` for tools (`qwen3_coder`) and the
   reasoning split (`qwen3`).
4. Run our refusal prompts.
5. Check a 200K needle.
6. Confirm the ng3 n-gram table loads in the NVMe tier (UNVERIFIED).

**Step 3: the gate needs a rule change first.** This is a design change, and
Max decides it.

- `engine-gate.sh` refuses a mismatched GGUF only when the binding has
  `gguf=`. EXL3 against GGUF can never be "same weights".
- Proposal: bind `source=orcarouter/Qwen3.8-Flash-Next-Uncensored`, require
  the serve script to declare the same source, and admit only if **both**
  hold:
  - decode ≥ 1.5× the tuned llama.cpp cache profile, A-B-B-A interleaved,
    the median of six;
  - no regression on a paired held-out eval slice plus the needle and
    refusal checks. A 3 bpw quant must not win on bytes alone.
- Record prefill too. The gate ignores it, and prefill is where this design
  is strongest (4.5K against ~0.7K tok/s).

**Step 4: decide.**

- On PASS: `engine.sh use sglang-exl3 --profile …`, then conformance again.
- On FAIL: write the numbers into RESULTS.md, `docker image rm` the image,
  and drop the pin.
- Rollback at any time: `scripts/engine.sh use llama`.

**What must be measured on the rig**:

- decode short and at ~8K context; prefill at 2.5K, 8K and 33K;
- VRAM free, container RSS and page cache;
- the CPU-tier thread count (default 14 of 16 cores) and its effect on the
  rest of the stack;
- first-start JIT time; cold load time;
- n-gram row-cache stalls (its stats);
- the PCIe link under load.

## Risks

- **Bus factor of one.** The branch is not merged to `main`. If he moves on,
  we own an SGLang 0.5.20 fork.
- The only 5090 number is from an **unknown rented host**. Our 40 GB/s
  dual-channel box could land well below 87.4.
- **Quality is not like-for-like**: 3.05 bpw with ng3 against IQ4_XS. Hence
  the eval slice in the gate.
- **One request at a time.** WebUI and OpenCode queue behind each other,
  which is also true of today's `-np 1` Flash-Next profile.
- A docker engine sits **outside the memory scope**. A `--memory` cap is
  mandatory, and oomd still sees the RAM.
- The JIT build on first start needs a compiler in the image. If it is
  missing, the first start fails offline.

## When to look again

Look again in any of these cases:

- he publishes an sm_120 GLM image;
- `rtx30-offload` merges into trellis-serve `main` with a tag;
- llama.cpp's cache stops at 60 tok/s or less on the rig, which makes 1.5×
  plausible.

## Not determined

- Whether llama.cpp's lazy PLE holds on our GGUF, and what the cgroup charges
  for it.
- Whether Lygodactylus's ng3 table and its MTP layers load in trellis-serve.
- SGLang 0.5.20's API-key handling without argv.
- The vast host's CPU and RAM channel count.
- Whether the attestation verifies (`gh attestation verify
  oci://…@sha256:fc147f4d… --owner sybil-solutions`; not run).

## Sources (read 2026-11-17)

- GitHub API, repo lists and commits since 2026-11-01:
  `0xSero/*` and `sybil-solutions/*`.
  - [trellis-serve](https://github.com/sybil-solutions/trellis-serve)
    `rtx30-offload` @ `2e274225`: `offload_runtime.py`, `cpu_tier.py`,
    `ngram_nvme.py`, `gpu_cache_moe.py`.
  - [local-ai-images](https://github.com/sybil-solutions/local-ai-images):
    README and `sglang-exl3-flashnext-multiarch/Dockerfile`.
  - [local-ai-registry](https://github.com/sybil-solutions/local-ai-registry):
    `registry/engines/sglang-exl3-offload-multiarch.json`,
    `registry/recipes/nvidia/rtx-5090-32gb/qwen3.8-flash-next.sglang.256k.json`
    (#296, 2026-10-16), the lab runs linked above.
  - [inference-optimisation-skill](https://github.com/sybil-solutions/inference-optimisation-skill);
    [glm-flash-lite](https://github.com/sybil-solutions/glm-flash-lite);
    [dsv41-flash-offload](https://github.com/sybil-solutions/dsv41-flash-offload).
- ghcr.io manifest of `sglang-exl3-flashnext-multiarch@sha256:fc147f4d…`
  (anonymous pull token): amd64 plus an attestation, 87 layers, 16.1 GB.
- Hugging Face API:
  - [Lygodactylus/…-Uncensored-exl3-3bpw](https://huggingface.co/Lygodactylus/Qwen3.8-Flash-Next-Uncensored-exl3-3bpw)
    @ `fe57295c` (card and file sizes);
  - `turboderp/Qwen3.8-Flash-Next-exl3` refs
    (`3.05bpw_h5_ng5` = `69e33439`);
  - `alesha-pro/Huihui-…-exl3-3bit-hq_h6_ng6`;
  - [0xSero/GLM-5.3-Flash-DSpine](https://huggingface.co/0xSero/GLM-5.3-Flash-DSpine).
- llama.cpp @ `b1b770d9`: `common/arg.cpp` (`--lazy-mode`, `--load-mode`),
  `src/llama-model-loader.cpp` (`lazy_read`, `init_mappings`),
  `src/models/qwen4exp.cpp`.
- X (dates decoded from status ids):
  [2026-11-01](https://x.com/0xSero/status/2117042619662701016),
  [2026-10-16](https://x.com/0xSero/status/2111073079199924513),
  [2026-10-10](https://x.com/0xSero/status/2108584415622291726),
  [2026-10-08](https://x.com/0xSero/status/2108187090957815895),
  [2026-09-29](https://x.com/0xSero/status/2104865431659892934).
  Substack [archive](https://0xseroai.substack.com) (latest post 2026-09-08).
- Not 0xSero, seen in passing: Shuo Yang, NVFP4 Flash-Next on one 5090 at
  68.3 tok/s with 63 GB RAM, CLAIMED ([X](https://x.com/Andy_ShuoYang/status/2095579848723050842),
  2026-09-03).
- OpenBeast: [WEIGHT_STREAMING.md](../WEIGHT_STREAMING.md),
  [ENGINES.md](../ENGINES.md), [MODELS.md](../MODELS.md) (the 2026-09-11
  Flash-Next measurements), `scripts/engine-gate.sh`, `scripts/lib/engine.sh`,
  `scripts/engines/vllm.sh`, the two streaming serve scripts.
