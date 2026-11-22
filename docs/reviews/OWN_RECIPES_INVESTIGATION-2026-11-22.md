# Own recipes: can OpenBeast build and apply its own EXL3 serving recipes?

Investigation, 2026-11-22. The question (Max): can OpenBeast create and apply
serving recipes, on the same stack Mia's AI Lab (MiaAI-Lab) uses, for every
model that could run on our 2× DGX Spark pair, or on other Spark
configurations (1 Spark, 3+ Sparks, a Spark beside the RTX 5090 rig)?

No live system was changed. spark-1 was serving Hy4 for a live eval, so the
Sparks were read with `ls`, `cat`, `grep`, `git log/remote`, `docker image ls`,
`free`, `df` and `tensorfold --version / capabilities --json` (under
`nice -n 15`) only. No unit, container, model request or download was started.

**Labels.** **VERIFIED**: read on a primary source (a file on a Spark, this
repo, the upstream repo or page itself), with the command or URL given.
**CLAIMED**: stated by a recipe, model card, README or secondary source and
not reproduced by us. **ESTIMATED**: our arithmetic or judgement.

Related: [EXL3_PLAYBOOK.md](../EXL3_PLAYBOOK.md) (the case taxonomy this
report builds on), [SPARK_HYDRA.md](../SPARK_HYDRA.md) (D1 recipes, D2 native,
Hy4), [ENGINES.md](../ENGINES.md), [MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md),
[HY4_FLASH_ENGINES-2026-11-19.md](HY4_FLASH_ENGINES-2026-11-19.md).

---

## Verdict

**Can we, today?** Partly, and more than we thought (confidence: high).

- **Yes, today, for every architecture that native TensorFold already has a
  family for.** OpenBeast owns the whole pipeline: it inspects the model and
  says whether it fits, pins and verifies the weights, launches native
  TensorFold or vLLM on 1-8 Sparks (D2), runs conformance and runs the
  campaign. Hy4 is the proof that a non-Mia recipe works: we built it, and it
  is serving on spark-1 right now. On our installed native 1.2.10 the EXL3
  families are GLM-5.3-Flash (TP2-16), MiniMax-M3.1-Flash (TP2 only) and
  Qwen3.8 dense (TP1). Upstream 1.3.4 adds more (VERIFIED, CHANGELOG):
  - GLM EXL3 at any whole bit width from 1 to 8 (1.3.3);
  - `--tp` from 1 to 64 (1.3.2);
  - Hy4-flash EXL3 loading on one GB10 (1.3.2);
  - a DeepSeek-V4.1 CUDA family in the source tree. Release binaries do not
    serve it yet.
- **No, not yet, for an EXL3 architecture with no engine family.** Writing a
  TensorFold family is the one part that takes weeks: about 4-12 for a strong
  Zig/CUDA engineer (ESTIMATED). Everything else in a recipe is configuration
  and tooling we already have, or can build in days.
- **The missing piece is glue, not capability.** There is no single
  `beast-recipe` pipeline, and D2 has never served on the pair. About **12
  engineering days** plus three hardware windows would let OpenBeast build,
  apply and qualify its own recipe for any model that an engine we can carry
  offline already reads (ESTIMATED).
- **Hardware.** A higher-quant DeepSeek needs **4 Sparks, not 3**: the only
  DeepSeek engine takes 1, 2 or 4 ranks (VERIFIED). A third Spark helps GLM
  (TP3 is measured) and adds memory, not speed.
- **Licences are not a blocker.** Mia's recipes and patches, TensorFold,
  ExLlamaV3 and the base weights are Apache-2.0 or MIT. The GLM DFlash2
  drafter was **relicensed to Apache-2.0 on 2026-11-10** with byte-identical
  weights (VERIFIED, §6). Our pin `bf582e4e` predates that, so we should
  re-pin to `6cc6c481`.

**Recommended next step.** After the Hy4 eval, take one pair-down window
(half a day) to serve the GLM checkpoint we already have through D2 on native
TensorFold, with no Mia image (candidate 1, §5.5). Build `beast-recipe init` /
`plan` off the Sparks in parallel. Then stage native 1.3.4 offline and run
candidates 2 (Qwen3.8-27B on one Spark) and 3 (MiniMax-M3.1-Flash EXL3,
TP2).

---

## 1. What a "recipe" is in our stack, concretely

A recipe is everything between "a model folder on disk" and "an
OpenAI-compatible endpoint that passes conformance on N Sparks". Reading the
three Mia recipes on spark-1 (VERIFIED: `~/openbeast-recipes/deepseek-prep/runtime`
@ `c7f2b85`, `~/openbeast-recipes/glm53` @ `33b50fd`, `~/openbeast-recipes/glm53-1104`
@ `82c442f`) and our own Hy4 recipe (`scripts/backends/hy4-vllm/`), every recipe
has the same ten parts. Which of them is model-specific is the whole question.

| # | Part | DeepSeek-V4.1 (Mia, Zig fork) | GLM-5.3 v1.8 (Mia, Python 0.6.0) | GLM-5.3 "1104" (Mia, native Zig) | Hy4 (ours, vLLM) | Model-specific? |
|---|---|---|---|---|---|---|
| 1 | **Weights pin** | `Mia-AiLab/…EAQ-v1-2.9-bpw` @ `45ee5d0f` (EAQ v1.1 `aee640e2` opt-in) in `scripts/config.sh` | `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold` + revision | same family, `MODEL_ID`/`MODEL_REVISION` case table in `scripts/config.sh` | `atakans/Hy4-flash-NVFP4` @ `30f201ac`, vendored `SHA256SUMS.hub-lfs` + `FILES.hub` | yes, but only data |
| 2 | **Engine pin** | `bertholomus/TensorFold` branch `deepseek-v41-zig` @ `fbbc971` (`TF_REF`), plus branch `deepseek-v41-zig-tp4` @ `5940208` for the input tools | `ashhart/TensorFold` `v0.6.0` (Python) | `ashhart/TensorFold` @ `f350cd3e` (TensorFold 1.1.9; `config.sh` now defaults `TF_REF=81de26f6`) | `vllm/vllm-openai@sha256:5e2c1bad…` (tag `hy4-flash`) | the pin is data; **whether the pinned engine has the architecture is the hard part** |
| 3 | **Patches** | 54 files in `zig/patches/` (+ `zig/patches-inputs/`), applied by `git apply --check && git apply` in filename order inside `zig/Dockerfile` | 82 files in `patches/`, applied by `scripts/prepare.sh` to the installed Python package | 15 files in `patches/engine/` as of `82c442f` (README: 121 at its release; the series was squashed/rebased) | six Python files dropped into the image (`image/`, from `atakann/dgx-spark-recipes` @ `e764efa`) | yes: performance, parsers, GB10 fixes, sometimes the architecture itself |
| 4 | **Image build** | `zig/Dockerfile` on `nvcr.io/nvidia/pytorch:26.07-py3`: Zig 0.17.0 by sha256, `zig build -Dsm=121 -Doptimize=ReleaseFast`, labels `tf.engine=zig tf.ref=… tf.patches=<hash>`, tag `tensorfold-dsv41:exl3-tp2-<hash of patches+Dockerfile+pins>` | prebuilt `ghcr.io/miaai-lab/…:v0.6.0-31557ed1cef6` by digest (or a local build) | `docker/Dockerfile`: `nvidia/cuda:13.3.1-devel-ubuntu24.04`, Zig 0.17.0, xgrammar v0.2.8, libjpeg-turbo/libwebp codecs, optional video and NCCL stages | `spark-hy4-stage.sh`: base by digest + six files, tag suffix = hash of `image/` | no: the pattern is generic |
| 5 | **Per-rank inputs** | rank files `rank{0,1}of2-<index hash>.bin` (~106 GB each) from `tf-dsv41-rank-cache MODEL DIR R 2` (no GPU); Engram token map from `make_token_map.py`; four RoPE tables + routing bias from the kernel kit (`bertholomus/deepseek-v4.1-tensorfold-tp2-2xgb10` @ `abe07c7`) | none: each rank reads its host's snapshot (`WORKER_WEIGHTS=copy`) | none: rank 0's snapshot, workers over NFS or a local copy | none: vLLM loads the snapshot | **DeepSeek-family only.** Rank files and Engram are not a general recipe feature |
| 6 | **Auxiliary weights** | Engram tables = shards 47 and 48 of `deepseek-ai/DeepSeek-V4.1-Flash-1017` @ `1bae0e10` (~203 GB, unquantized, read from local disk by AIO, ~70 random 4 KiB rows a decode round) | DFlash2 drafter `incoai/GLM-5.3-Flash-DFlash2` @ `bf582e4e` (2.34 GB) | the same DFlash2 drafter (or the checkpoint's MTP head, or none) | DFly2 drafter and Engram tables are inside the checkpoint | yes |
| 7 | **Drafter** | DSpark draft blocks **inside the pack** (layers 37-39, quantized at 4 bits) | DFlash2 (external) | `DRAFTER=dflash2 \| mtp \| none`, `DRAFTER_BITS=4\|0` | DFly2, 5 tokens (`--speculative-config`) | yes; three of four come with the model |
| 8 | **Launch config** | `TP=2` (`TF_TP_WORLD=2`), `CONTEXT=524288`, `PARALLEL=4`, KV compressed FP4 (~1.8 GiB at the full window), `TENSORFOLD_MEMORY_RESERVE_GIB=12.2`, `MIN_AVAIL_GIB=112`, RDMA rails + GID index from `scripts/nodes.sh`, memguard | `TP=2`, `KV=fp8`, `DENSE=q4`, `KV_POOL_GIB=10`, `MEMORY_RESERVE_GIB=17.5` (our `local.sh`), `COMM=roce` | `KV=fp8` on shared pages, `DENSE=q8`, `HEAD=q8`, `PARALLEL=8`, `CONTEXT=1048576`, `MEMORY_RESERVE_GIB=9.5`/`6.5`, RoCE all-gather | TP2 + `--enable-expert-parallel`, `--kv-cache-memory-bytes 6G`, cgroup cap 112 GiB | the knobs are generic, the values are measured per model |
| 9 | **Chat template / parsers** | `model/chat_template.jinja` shipped by the recipe (the release ships `encoding/encoding.py`, which the server cannot run); DSML tool-call parser patches `0064`, `0065`, `0067` | checkpoint's template; tool-call patch `0036` | `templates/glm-5.3-flash-vision.jinja`; `TF_TOOL_CALL_GUARD` patches `0010`/`0011` | vLLM's Hy4 parsers | yes, and the most common silent failure |
| 10 | **Acceptance** | `tools/smoke.py`, `validate.py`, `exact.py` (byte-identical replies across images), `gpu_validate.sh`, `bench.py` | smoke in `start.sh` | sparkDash, `docs/speed/RESULTS.md` | ours: `conformance.sh`, `verify-chat.py`, the campaign | no: OpenBeast's is generic |

All rows VERIFIED by reading the files named, except the patch count of
`glm53-1104` at release (CLAIMED, its README).

**The minimum for a new model**, therefore: (1) weights in a layout some
engine build reads; (2) an engine build that has the **architecture family**
for that layout at the rank count that fits; (3) an image or binary of that
build on every Spark; (4) per-rank inputs, only if that family needs them;
(5) a drafter, optional; (6) launch values that fit memory; (7) a chat
template and tool/reasoning parsers that pass conformance. Everything except
(2) is configuration or tooling. **(2) is the only part that can take weeks.**

---

## 2. What OpenBeast can already do without Mia

| Capability | Where | State |
|---|---|---|
| Read any model folder or Hub repo (config, quantization, tensor index, shard headers, template) and say which engine reads it, at which TP, and whether it fits | `scripts/backends/model-inspect.sh` → `pylib/model_inspect.py` (1,096 lines: `quantization`, `weights`, `kv_per_token`, `fit` for N = 1..8 Sparks with replicated-tensor slack, `tensorfold_support`, `vllm_support`, `suggest_tool_parser`, `suggest_reasoning_parser`, `choose_tp`, `draft_profile`) | VERIFIED in code; used for every profile we have |
| A verdict per folder against the families we serve (DROP-IN / NEEDS A PROFILE / SAME FAMILY / NOT SERVABLE / INCOMPLETE / NOT A MODEL) | `spark-model-intake.sh` → `pylib/model_intake.py` + `data/spark-families.json` (families `deepseek-v41`, `glm53`, `hy4-vllm`; engines `native-1.2.10`, `vllm-hy4`) | VERIFIED; run on the real folders 2026-11-17 (EXL3_PLAYBOOK §4) |
| A pinned profile + lock for any model, fetch by revision, verify by hash | `--write-profile`, `obprofile.py`, `model-fetch.sh --verify`, `models/*.env` + `*.lock` (5 profiles today) | VERIFIED |
| Launch any profile on 1-8 Sparks through the native engine or vLLM, keyed, with fabric checks | D2: `tensorfold/spark-node.sh`, `vllm/spark-node.sh`, `spark-cluster.sh check / sync-weights / sync-image / start / stop` | built and tested hermetically; **never served on the pair** (SPARK_HYDRA D2 "VERIFY ON HARDWARE"). N ≥ 3 tested against stubs only |
| Install / pin / roll back engines, offline carry | `scripts/engine.sh`, `tensorfold/install-native.sh` (ed25519 + sha256) | VERIFIED; native 1.2.10 on both Sparks (`tensorfold --version`, 2026-11-22) |
| **Build our own recipe image from pinned upstream parts** | Hy4: `spark-hy4-stage.sh` (base by digest, vendored files with `CREDITS.md` sha256s, content-hashed tag, `docker save \| load` to spark-2, memory-scoped downloads, stall watchdog) | VERIFIED: `openbeast/hy4-vllm:e764efa-b486c74ef8b6` (`6477587cdc52`, 26.5 GB) exists on **both** Sparks and spark-1 is serving Hy4 from it today. This is a non-Mia recipe that works |
| Build a Mia recipe image ourselves from its Dockerfile | `~/openbeast-recipes/deepseek-prep/native-build/` + `native-build.log` | VERIFIED: `tensorfold-dsv41:exl3-tp2-8a98ae503d85` (`d048841d851f`) was built on spark-1 (`naming to docker.io/library/tensorfold-dsv41:exl3-tp2-8a98ae503d85 done`) |
| Swap packs within a family, verify, roll back, A/B bench | `spark-deepseek-pack.sh` (DeepSeek), GLM by hand (EXL3_PLAYBOOK §5.6) | VERIFIED for DeepSeek (v1 ↔ v2 round trips, 2026-11-17) |
| Model switching and boot selection | `beast-spark glm / ds / hy4 / down / up` | VERIFIED |
| Conformance (chat, streaming, reasoning split, structured + parallel tool calls, tool results, limits) | `conformance.sh` → `pylib/conformance.py` | VERIFIED on DeepSeek, GLM |
| Qualification | the campaign (L0, failure audit, arms, board rows) | VERIFIED on GLM and DeepSeek |

So OpenBeast already owns parts 1, 4 (for vLLM and for building Mia's
Dockerfiles), 6 and 7 (as data), 8 (the fit math, not the tuned values) and 10
of §1, and the whole pipeline around them. What it does not own is part 2 for
any EXL3 architecture outside native TensorFold's registry, and part 3 (the
performance patch stacks), which is where Mia's lab and its contributors put
weeks of work per model.

---

## 3. What is missing for a NEW EXL3 architecture

### 3.1 What native TensorFold 1.2.10 reads today (VERIFIED)

`tensorfold capabilities --json` on spark-1, 2026-11-22 (`tensorfold-native
1.2.10`, backend `cuda`, kernel SMs 80/86/120/121), CUDA families and formats:

| model_type | formats (CUDA) | EXL3? | ranks (our registry) |
|---|---|---|---|
| `glm5_next` (GLM-5.3-Flash) | modelopt-nvfp4, modelopt-mixed, **exl3** | yes, TR3 layout (4-bit `mcg`, routed experts only) | 2-16 (`TF_GLM_WORLD`) |
| `minimax_m3_vl` (MiniMax-M3.1-Flash) | **exl3** | yes | 2 only |
| `qwen3_5` (Qwen3.8 dense, e.g. 27B) | mlx-q4g64, **exl3** | yes, **one GPU only** (two ranks need MLX) | 1 (EXL3), 1-2 (MLX) |
| `qwen4_exp` (Qwen3.8-Flash-Next, 177B MoE) | mlx-q4g32, modelopt-mixed | **no** (the 0.x container list had EXL3 on one GPU) | 1-2 |
| `qwen3_5_moe` (Qwen3.6 MoE) | mlx-q4g64, modelopt-mixed, modelopt-nvfp4 | no | 1 |
| `nemotron_h`, `nemotron_h_omni` | mlx-q4g64, modelopt-nvfp4, modelopt-ggml | no | 1 |
| `gemma4`, `gemma4_unified`, `spark2_5` | mlx-q2..q8 | no | — |
| `qwen4`, `qwen4_text` | bf16, mlx, modelopt-fp8/nvfp4 | no | — |
| `kolibri1`, `fish_omni`, `dory`, `dots_ocr`, `lfm2_moe`, `lfm2_vl`, `qwen3` | fp8-block / nvfp4 / bf16 / mlx | no | — |

**Refused:** `deepseek_v41` and `hy_v4_flash` ("no registered Zig family
serves model_type …; the 0.6 line may: tensorfold@0.6", VERIFIED 2026-11-17/19).
The same capabilities file lists `glm_moe_dsa`, `kimi_k3`, `kimi_k31`, `llama`,
`lfm3`, `step5_vl` under the **Metal** backend's switch scopes only, not CUDA
(VERIFIED, `profiles.registered_switches[].scopes`): those families exist in
the codebase for Apple silicon and are not served on a GB10.

So exactly **three EXL3 architectures** are a profile away on native 1.2.10,
and one of them (Qwen3.8 dense) only on one Spark.

### 3.1b Upstream TensorFold since 1.2.10 (what an upgrade buys)

Upstream is `ashhart/TensorFold`, Apache-2.0. Its install script pins `V=1.3.4`.
The Python engine is frozen at `v0.6.6`, and the Zig native engine is the main
line (VERIFIED by fetch, 2026-11-22: <https://github.com/ashhart/TensorFold>,
`CONTRIBUTING.md`, `docs/supported-models.md`, `CHANGELOG.md`). Entries that
matter for recipes:

| Version | Change | Recipe impact |
|---|---|---|
| 1.3.2 | `--tp` takes a world size from 1 to 64 instead of 1/2/4/8, each family still admitting only its own. `--pp` is new but takes 1 only | TP3 is a family question now, not a server limit. Pipeline parallel does not exist yet |
| 1.3.2 | Hy4-flash EXL3 4bpw "loads all 42 layers on one GB10 (85.38 GiB, 29 GiB left)", via the slab upload mode; `tf-hy4-cuda-generate` runs the text model greedily | the one-Spark Hy4 EXL3 path is moving upstream. It is a generator, not a server: no TP2 family yet |
| 1.3.2 | the native binary hands a checkpoint to the Python engine 0.6.6 "only when 0.6.6 serves its model type": DeepSeek V4, Gemma 4, GLM5-Next, Nemotron-H, Qwen3.5, Qwen3.5-MoE, Qwen3.8 Flash Next, Qwen4 | a container fallback exists for those types (internet at start unless prebuilt, per D2) |
| 1.3.3 | "GLM-5.3-Flash on CUDA: EXL3 `mcg` routed experts load at any whole-bit width from 1 to 8, mixed by projection within a layer, where only 4-bit was admitted" (#2094, thanks to @MiaAI-Lab) | **5- and 6-bit GLM packs become servable natively.** This is GLM 1104's patch `0001` upstreamed |
| 1.3.3 | `TF_ROCE=1` RoCE all-gather | Mia's GLM speed path, upstream |
| 1.3.3 | Qwen3.5 EXL3 with the `mcg` codebook admitted beside `mul1` | more Qwen3.8 dense packs load |
| 1.3.x | "DeepSeek-V4.1 on CUDA (`dsv41-cuda`, a source build)" with drafts, grammars and a copy drafter; `supported-models.md`: "DeepSeek checkpoints are not served yet by any release binary" | DeepSeek is coming upstream. A source build of upstream `dsv41-cuda` is a possible non-fork DeepSeek recipe later (UNVERIFIED on GB10 TP2) |
| 1.2.1-1.2.3 | the DeepSeek-V4.1 release checkpoint reader (FP8 + MXFP4 + Engram pack tool); GLM TP3; Flash Next `--tp 2` | |

`supported-models.md` also records GLM-5.3-Flash runs at TP2 on two GB10s
and at TP3 on three GB10s "over direct links" (VERIFIED by fetch).

**How a new architecture is added upstream** (VERIFIED, `CONTRIBUTING.md`):
1. Open an issue first.
2. Put the model operators and weight handling in a *family*. Caches,
   verification and per-stream state stay in the core, and a family must not
   add a second scheduler.
3. Register every new GPU kernel with `docs/kernel-contract.md`, and post
   each backend's `SUMMARY` line in the PR.
4. Pass the exactness gates: drafted output equals plain output token for
   token, a resumed run equals a fresh one, and concurrent replies equal solo
   ones.

That is a well-defined contribution path, but it is an engine project. The
fork route is the alternative: BertholomusAI's `bertholomus/TensorFold` keeps
"one branch per model family": `deepseek-v41-zig-tp2`/`-tp4`, `glm-dsa-tp4`,
`mistral-large-4-tp4`, `beam-1.0-tp2`, `ling-3.1-tp2` (VERIFIED by fetch). Those
branches are ready-made engine pins for a fork-image recipe (§5), each UNVERIFIED
on our pair.

### 3.2 The gaps, ranked by effort

| Rank | Gap | What it takes | Effort (ESTIMATED) | Who can do it |
|---|---|---|---|---|
| 1 (smallest) | **Recipe glue for an architecture the engine already has**: profile, launch values, fabric, template, conformance | `model-inspect --write-profile`, D2 start, conformance, a unit. D2 has never served on the pair, so the first run also qualifies D2 | 0.5-1 day per model; the first one 1-2 days | us, today |
| 2 | **Memory/launch tuning**: KV dtype and pool, `MEMORY_RESERVE_GIB`, context, streams, `MIN_AVAIL` | measure the engine's start line and `MemAvailable` under load on both ranks; the rules in §4.1 give the starting point | 0.5 day per model | us |
| 3 | **Drafter** for a model that does not ship one | (a) none: always works, costs 1.5-3× decode; (b) the model's own MTP head (`DRAFTER=mtp` in GLM 1104); (c) n-gram / prompt-lookup "copy drafts" where the engine has them; (d) a published DFlash/DFlash2 drafter for that model (z-lab publishes some, e.g. `z-lab/Qwen3.8-27B-DFlash2` in `data/tensorfold.json`); (e) train one ourselves (§3.4) | (a)-(d): hours; (e): days to weeks of GPU time | us for (a)-(d) |
| 4 | **Our own EXL3 quantization** of a model whose layout the engine already reads | exllamav3 convert on the BF16/FP8 source, with the exact codebook/bits/scope the engine's reader requires (GLM: TR3 = 4-bit `mcg`, routed experts only) | 1-3 days per model on one Spark, mostly wall time (§3.3) | us, with a free Spark |
| 5 | **Performance patches** (RoCE all-gather, prompt chunking, graphs, KV paging/sharding, spill) | port or write engine patches; Mia's stacks are 15-121 patches deep | weeks per model to reach Mia's numbers; zero to get a first correct serve | Mia's lab, upstream, contributors; us only selectively |
| 6 | **Tool/reasoning parser fixes** for a new chat format | an engine patch (DSML parser patches for DeepSeek are the precedent) or a template change | 1-5 days | us, when conformance names it |
| 7 | **A new EXL3 layout** for a supported family (other bits, other codebook, other scope) | engine reader work. GLM 1104's patch `0001` ("EXL3 mcg experts at any whole bit width mix") is exactly this for GLM, and it is upstream since native 1.3.3 | 1-2 weeks | upstream / Mia / us with Zig+CUDA skill |
| 8 | **Tensor-parallel split + per-rank inputs for a new family** (rank files, Engram) | only if the family's engine needs a pre-split file; DeepSeek's `tf-dsv41-rank-cache` and `plan.zig` are the template | part of (9) | whoever writes the family |
| 9 (largest) | **A new architecture family in the Zig engine** (attention variant, MoE routing, norms, RoPE, KV layout, EXL3 expert kernels, TP split, drafter hooks) | BertholomusAI's `dsv41` family is the precedent: a fork branch, a kernel kit with AOT Triton kernels, a rank-cache tool, then 54 recipe patches on top | 4-12 weeks for a strong Zig/CUDA engineer; upstream ships families faster | upstream TensorFold or a fork author; us only if the model is decisive |

**Engram** is not a gap to close in general: it is an architectural feature of
DeepSeek-V4.1 (n-gram tables, shards 47-48) and Hy4-flash (64B of tables in the
checkpoint), implemented inside those families' engines. A recipe only stages
its tables on local disk on every rank (DeepSeek: ~203 GB a Spark; random
4 KiB reads, so never over NFS: VERIFIED, `how-it-works.md` step 7). **Rank
files** are likewise a DeepSeek-family loader detail (VERIFIED, `zig-engine.md`
"Why two engine checkouts"): GLM and the native families read the snapshot
directly.

### 3.3 Quantizing EXL3 ourselves

- **Tool.** ExLlamaV3 (`turboderp-org/exllamav3`, MIT, latest 1.6.7 of
  2026-11-13; VERIFIED by fetch of the releases page and `doc/convert.md`).
  - Command: `python convert.py -i <hf_dir> -o <out> -w <work> -b <bpw>`,
    resumable with `-w <work> -r`. It checkpoints every 120 s.
  - Per-part widths: `-hb` lm_head bits (default 6), `-mb` MTP bits
    (default 4), `-eb` embedding bits.
  - `-hq` promotes attention and shared experts.
  - `--recipe` takes a YAML per-tensor bit map, `-cd` takes custom
    calibration data.
  - Codebooks: `mul1` (the default), `mcg` and `3inst`.
  - `-d` adds GPUs: device 0 does the bulk.
  - Calibration is built in: "Hessians are computed on the fly"; the default
    is 250 rows × 2048 columns.
- **The architecture must be in exllamav3 too.** The README lists, among
  others (VERIFIED by fetch):
  - DeepSeek V3/V4; 1.6.6 added `DeepseekV41ForCausalLM`;
  - GLM 4.x, 5.2 and 5.3-Flash;
  - Qwen 3, 3-Next, 3.5 MoE and 3.8-Flash-Next;
  - Hy3 (**not Hy4**), MiniMax-M2, Nemotron-H, Kimi Linear, Ling 3.x,
    GPT-OSS and Mistral-4.

  Mia's DeepSeek EAQ pack shows what this means when an architecture is new:
  it was "converted with exllamav3 v1.6.1 plus the EAQ patch set … and a port
  of the DeepSeek-V4.1 architecture", which they checked against the fork's
  forward on layers 0-3 (VERIFIED, the pack's README on spark-1, "Conversion").
- **The layout must match the serving engine's reader**, not just "be EXL3".
  - GLM: TR3, meaning `bits` 4 (any whole bit from 1.3.3), codebook `mcg`,
    scope `glm53_routed_experts_only`, BF16 elsewhere.
  - DeepSeek: `mul1`, `out_scales=always`, and the reference tensor index.

  A quant made with default flags can therefore fail to load. Intake catches
  this before any swap.
- **Memory and time on a GB10 (ESTIMATED; NOT documented upstream).**
  - The README only says "a few minutes for smaller models, up to a few hours
    for 70B+ on a single high-end consumer GPU". Upstream gives no numbers
    for 100-700B MoEs.
  - The converter works module by module, so the GPU holds one layer plus its
    Hessians. A GB10's 121 GiB is ample for any layer of these models.
  - The source checkpoint has to be on disk: GLM FP8 ~306 GiB, DeepSeek
    FP8+FP4 ~476 GB.
  - The work directory holds a full copy of the output, plus the output
    itself.
  - Our guess at the time: **1-3 days per 300-550 B MoE on one Spark.** GB10
    compute is a fraction of a desktop Blackwell, and every expert of every
    layer gets its own Hessian.
  - A quantizing Spark serves nothing, so this wants a third Spark or a
    window with the pair down.
- **Quality.** Mia measured KL and top-1 against the source for its pack.
  Our own quant would need the same check, and then the campaign.

### 3.4 Drafters

| Option | Where it comes from | Cost | Notes |
|---|---|---|---|
| **In-checkpoint drafter** | DeepSeek-V4.1-Flash: DSpark blocks, `dspark_target_layer_ids [37,38,39]` in the base model's config (VERIFIED by fetch, `deepseek-ai/DeepSeek-V4.1-Flash` `config.json`). Hy4-flash: DFly2, 0.85 B, 5 layers (VERIFIED by fetch, the model card). GLM-5.3-Flash: one MTP layer | none | the 2026 norm; licence = the model's |
| **MTP head** | GLM 1104 `DRAFTER=mtp`; TensorFold 1.3.4 "dense MTP for Qwen3.5-family targets" | none | slower than DFlash2 on GLM (Mia's choice of default) |
| **Copy / n-gram drafts** | TensorFold copy drafts (GLM `0013`, DeepSeek `TF_DSV41_COPY_DRAFTER=1`); vLLM `ngram`, `ngram_gpu`, suffix decoding | none | strong on code and edits, weak on prose |
| **Published DFlash/DFlash2 drafter** | `incoai/GLM-5.3-Flash-DFlash2`, `z-lab/Qwen3.8-27B-DFlash2` (named in `data/tensorfold.json`) | a download | DFlash2 needs the full target vocabulary (vLLM Speculators docs) |
| **Train our own** | NVIDIA NeMo AutoModel `TrainDFlash2Recipe` (regenerate responses with the target, capture hidden states online); vLLM Speculators' DFlash2 trainer; DSpark recipes in NeMo AutoModel and SGLang (VERIFIED/CLAIMED by fetch, §7) | days to weeks of GPU time, which upstream does not document; it needs the target running at BF16/FP8 to make hidden states, which is beyond one Spark for a 300 B+ model | the only route to a drafter under our own terms for a model that has none |
| **None** | always | 1.5-3× slower decode (Hy4: ~15 tok/s plain against 17-38 with DFly, CLAIMED) | correct by construction; the right first serve |

Verdict: a new recipe starts with the drafter built into the checkpoint, or
with none, and then measures. Training a drafter is a late optimisation that
only a decisive model justifies.

---

## 4. Other configurations

### 4.1 The memory model (per Spark)

Each Spark has **121.69 GiB** of unified memory (`MemTotal` 127,598,412 kB,
VERIFIED). One rank per Spark. A rank's budget:

```
MemTotal 121.7 GiB
  = weights per rank            W = checkpoint bytes / TP × (1 + r·(TP−1)),  r ≈ 0.008 (DeepSeek, calibrated) .. 0.03 (model_inspect default)
  + KV / pool                   model-specific: DeepSeek ~1.8 GiB at 1M (compressed FP4 latents); GLM FP8 pages 10-12.5 GiB; Hy4 6 GiB
  + engine reserve / workspace  activations, graphs, prompt chunks: 6.5-17.5 GiB (Mia's values; DeepSeek 12.2 "do not lower")
  + drafter                     inside W when it ships in the checkpoint; DFlash2 ≈ 1-2.3 GiB
  + OS + command center         head: ~6 GiB idle (MEASURED 2026-11-17); worker: ~2-3 GiB
  + headroom                    memguard kills a rank under 2.5 GiB for 4 s; Mia's recipes keep 3-12 GiB at the worst point
```

The intake rule that falls out (VERIFIED, `data/spark-families.json` `pair`):
**W ≤ 92 GiB fits, 92-100 GiB tight, > 100 GiB does not fit.** On 2026-11-22,
while Hy4 serves, `free -g` shows 8 GiB available on spark-1 and 11 GiB on
spark-2 (VERIFIED).

Checkpoint bytes at another bpw scale roughly with bpw, because routed experts
are 90-97 % of an MoE checkpoint: `bytes(b) ≈ bytes(b0) × b / b0`, slightly
pessimistic because the BF16/high-bit parts do not grow (ESTIMATED).

### 4.2 Fit table

Per-rank weights W in GiB; ✔ fits (≤ 92), ~ tight (92-100), ✘ does not fit
(> 100), — the engine has no such rank count. "Engine" is the build that
would serve it. Sizes MEASURED where marked, otherwise ESTIMATED by bpw
scaling.

How the sizes were made (ESTIMATED unless marked):

- **DeepSeek-V4.1-Flash-1017** (`deepseek_v41`: 40 layers, 384 routed experts
  of 2,304, 6 active, hidden 5,120, 64 heads; VERIFIED from the pack's
  `config.json`). Routed experts are 40 × 384 × 3 × 5,120 × 2,304 ≈ 543.6 B
  parameters. Mia's EAQ pack is 196.1 GiB (MEASURED) with experts at 3 bits
  (2 in layers 18-22), so about 14 GiB is the rest (attention 5-bit, head
  6-bit, vision, DSpark blocks). At an expert width e: **14 + 63.3 × e GiB**.
  Engram's ~203 GB of tables stay on disk and are not counted.
- **GLM-5.3-Flash** (`glm5_next`: 45 layers, 288 routed experts of 2,048, 8
  active, hidden 4,096, MLA `kv_lora_rank` 512; VERIFIED from the Ablit
  checkpoint's `config.json`). Routed experts ≈ 326 B parameters, 152 GiB at
  4 bits; the TR3 pack is 163.6 GiB (MEASURED), so ~11.6 GiB is BF16. At e
  bits: **11.6 + 38 × e GiB**. Z.ai's block-FP8 release is ~306 GiB (CLAIMED,
  GLM 1104 README).
- **Hy4-flash**: NVFP4 153.3 GiB of weights (MEASURED from the Hub listing,
  164.65 GB); EXL3 4 bpw pack 203 GiB of which ~118 GiB are Engram tables
  that ExLlamaV3 streams from disk (CLAIMED, HY4 review).
- **Qwen3.8-Flash-Next** (`qwen4_exp`, 177 B total, 512 experts, 10 active:
  CLAIMED, `docs/MODELS.md`): ~88 GiB at 4 bpw, ~66 at 3, ~110 at 5 (bpw
  arithmetic plus embedding tables, ±10 %).
- **Qwen3.8-27B dense** (`qwen3_5`): ~15 GiB at 4 bpw, ~21 at 6, ~28 at 8.
- Per-rank W = total / TP × (1 + 0.008 × (TP − 1)).

| Model @ quant | Checkpoint GiB | 1 Spark | 2 Sparks | 3 Sparks | 4 Sparks | Engine that would serve it |
|---|---:|---|---|---|---|---|
| DeepSeek-V4.1-Flash @ 2.9 (EAQ, **MEASURED**) | 196.1 | ✘ 196 | ~ 98.9 (serves today) | — (66) | ✔ 50 | dsv41 Zig fork: WORLD 1, 2 or 4 only |
| DeepSeek-V4.1-Flash @ 3.5 (LibertAI pack, exists) | ~236 | ✘ | ✘ 119 | — (80) | ✔ 60 | dsv41 TP4 (bertholomus 4×GB10 / Mia 4-Spark recipe) |
| DeepSeek-V4.1-Flash @ 4.0 | ~267 | ✘ | ✘ 135 | — (91) | ✔ 68 | the same, **if** someone quantizes it |
| DeepSeek-V4.1-Flash @ 5.0 | ~330 | ✘ | ✘ 167 | — (112) | ✔ 85 | the same |
| DeepSeek-V4.1-Flash @ 6.0 | ~394 | ✘ | ✘ | — | ✘ 101 | 8 Sparks only (no TP8 in the fork) |
| GLM-5.3-Flash @ 4 TR3 (**MEASURED**) | 163.6 | ✘ 164 | ✔ 82.4 (serves) | ✔ 55 (TP3 measured, Python v1.5) | ✔ 42 (TP4 ring measured, native 1104) | Mia GLM recipes; native 1.2.10 (2-16 ranks) |
| GLM-5.3-Flash @ 5 | ~202 | ✘ | ✘ 102 | ✔ 68 | ✔ 52 | native ≥ 1.3.3 (any whole-bit `mcg` experts; was GLM 1104 patch `0001`) — we run 1.2.10 |
| GLM-5.3-Flash @ 6 | ~240 | ✘ | ✘ 121 | ✔ 81 | ✔ 61 | the same |
| GLM-5.3-Flash @ FP8 (Z.ai release) | ~306 | ✘ | ✘ | ✘ 104 | ✔ 78 (CLAIMED ~81 a rank) | GLM 1104 `FP8=1` (patch `0107`), 4 Sparks only |
| MiniMax-M3.1-Flash @ 3.06 (Mia pack; ~428 B CLAIMED) | ~152 | ✘ | ✔ ~77 | — | — | native `minimax_m3_vl`, exactly 2 ranks |
| MiniMax-M3.1-Flash @ 3.5 (Mia pack) | ~174 | ✘ | ✔ ~88 (KV room small) | — | — | same |
| Hy4-flash @ NVFP4 (**serving**) | 153.3 | ✘ | ✔ 77.3 | ✔ 52 (vLLM TP must divide heads) | ✔ 39 | vLLM TP2 + EP (ours) |
| Hy4-flash @ EXL3 4 bpw | 203 (85 trunk) | ~ 85 trunk, Engram on disk; upstream 1.3.2 loads it on one GB10 (generator, no server yet) | ✘ no 2-host EXL3 engine | ✘ | ✘ | ExLlamaV3 one host only |
| Qwen3.8-Flash-Next @ 4 | ~88 | ✔ 88 (KV room small) | ✔ 44 | ✔ 30 | ✔ 23 | native 1.2.10 has **no** EXL3 for `qwen4_exp`; MLX 4-bit at TP1-2 yes |
| Qwen3.8-Flash-Next @ 3 | ~66 | ✔ 66 | ✔ 33 | | | same |
| Qwen3.8-27B dense @ 4-8 | 15-28 | ✔ | ✔ (MLX only at TP2) | | | native 1.2.10 EXL3 at TP1 |

Two things the table says that matter for buying hardware:

1. **A third Spark does not buy a higher-quant DeepSeek.** The memory would
   hold a 4.0-bpw DeepSeek at TP3 (~91 GiB a rank, tight), but the only engine
   that serves `deepseek_v41` (the dsv41 Zig fork) takes WORLD 1, 2 or 4
   (VERIFIED, DeepSeek recipe `docs/zig-engine.md`: `tf-dsv41-rank-cache` "WORLD
   1, 2 or 4"). Upstream TensorFold (>= 1.2.6) plans 3 ranks as 24/24/16 heads by
   output groups, but serving still refuses three ranks (#2270; corrected
   2026-11-22 in the beast-recipe review). A higher-quant
   DeepSeek needs **four** Sparks. At four, a 3.5-bpw pack already exists and
   is claimed at 77.5 tok/s one-stream (drowzeys' kit README on spark-1:
   "LibertAI EXL3 3.5bpw (B) | 4× GB10 Tony TensorFold TP4 | 77.5", CLAIMED),
   against 48-50 tok/s for the 2.9 pack at TP2 in the same table. A 4-5 bpw
   pack would have to be quantized (§3.3); it fits TP4 at 68-85 GiB a rank.
2. **A third Spark does help GLM** (TP3 is measured, and 5-6 bit GLM fits) and
   any vLLM model whose head count divides by 3. For TP3 GLM prefill, Mia's
   own numbers are within 5 % of TP2's (12k / 50k / 149k: 6.2 / 25.7 / 84.2 s
   at TP3 against 6.5 / 26.8 / 87.1 s at TP2; CLAIMED, GLM v1.8 README "3
   Sparks"): a third Spark buys **memory** (bigger quant, longer pools, more
   streams), not speed.

### 4.3 1 Spark

A single Spark holds **≤ ~92 GiB of weights** with useful KV room (one
rank: no replication slack, but the same OS, engine reserve and, on spark-1,
the command center). That is, roughly:

| Total params (MoE counts every expert) | bpw that fits one Spark (ESTIMATED) |
|---|---|
| ≤ 30 B (Qwen3.8-27B) | anything up to 8 bpw, with large KV |
| ~ 100-120 B | 4-6 bpw |
| ~ 177 B (Qwen3.8-Flash-Next) | 3-4 bpw (4 is tight on KV) |
| ~ 230-250 B | ≤ 3 bpw |
| ≥ 340 B (GLM-5.3-Flash, DeepSeek) | does not fit at a usable bpw |

What runs on one Spark today, our own recipe: native 1.2.10 EXL3 for
`qwen3_5` (TP1), plus the MLX/NVFP4 families. The constraint is not memory but
**scheduling**: both Sparks are taken by the pair's model (§1.1 of the
playbook), so a TP1 model means the pair's model is down, or a third Spark.
ExLlamaV3 itself also serves EXL3 on one Spark (TabbyAPI), for architectures
exllamav3 supports; that is the case for single-host Hy4 EXL3 experiments.

### 4.4 2 Sparks (today)

One QSFP cable; NVIDIA Sync addresses both PCIe twins of the port, and
NCCL / the engines' RoCE paths use both as two rails (one twin is one PCIe
Gen5 x4, ~112 Gb/s of the port's 200: CLAIMED, DeepSeek recipe
`docs/requirements.md`). Every Mia recipe and Hy4 run here. Nothing to buy.

### 4.5 3 and 4 Sparks

**Physical topologies** (VERIFIED, NVIDIA playbook "Connect Multiple
Sparks", <https://build.nvidia.com/spark/connect-three-sparks/overview.md>
and <https://build.nvidia.com/spark/multi-sparks-through-switch/overview>,
by fetch):
- NVIDIA documents **2 to 4 Sparks**. They connect with direct QSFP cables or,
  "optionally, a switch with one 200 Gbit/s Ethernet link per device".
- The playbook needs DGX OS from April 2026 or later. NVIDIA Sync's cluster
  assistant offers a 3-node ring or a switch layout (CLAIMED).

Each Spark has two QSFP ports, so the cabled layouts are:

| Sparks | Switchless | Measured where |
|---|---|---|
| 3 | a triangle: each Spark cabled to both others, one subnet per cable | GLM TP3, Mia's Python recipe v1.5 (`docs/TP3_QUALIFICATION_20261003.md` on spark-1: "three DGX Sparks/GB10 connected as a direct CX7 triangle, TP3/NCCL"; CLAIMED by its author) |
| 4 | a ring: each Spark cabled to its two neighbours; the Spark opposite is reached through a neighbour | GLM 1104 `start-tp4-switchless.sh`, "the one run on this kit": 77.2 tok/s one stream, 172.7 total at eight (CLAIMED, 1104 README "4 Sparks") |
| 4-8 | a 200 GbE RoCE switch | community: an 8× Spark TP8 build on a MikroTik CRS812 with 400G-DD to 2×200G / 4×100G breakouts (CLAIMED, NVIDIA forum threads in §7). The CRS812 needs MTU, PFC and ECN set by CLI. No NVIDIA-named switch was found |

**A correction to our own docs.** SPARK_HYDRA §A2 says that past 3 Sparks
you need a switch because "no cabled topology reaches past 3". The 1104 ring
shows that 4 Sparks work switchless. SPARK_HYDRA §A2 and `spark-cluster.sh
check` should learn the ring, which needs `NCCL_IB_SUBNET_AWARE_ROUTING` and a
LAN-side rendezvous, the way `start-tp3.sh` and `tp4.sh` do it.

**Engines at N ≥ 3:**

| Engine | TP3 | TP4 | PP |
|---|---|---|---|
| dsv41 Zig fork (DeepSeek) | **no** (WORLD 1, 2, 4) | yes (`deepseek-v41-zig-tp4`; Mia's 4-Spark recipe) | no |
| native TensorFold ≥ 1.3.2 | per family: GLM yes (measured upstream and by Mia); MiniMax no (exactly 2); Flash Next 1-2 | GLM yes | `--pp 1` only |
| Mia's GLM recipes | yes (v1.8 `start-tp3.sh`: heads, experts and vocab split "in whole units, the remainder to the lowest ranks") | yes (1104, ring or switch) | no |
| vLLM | only if the attention heads divide by 3: 64-head models (GLM, DeepSeek, MiniMax) do not. Community advice for 3 Sparks is PP=3, DP, or TP2 plus a spare (VERIFIED by fetch, NVIDIA forum "Three-node Spark clusters without a switch"; PP=3 Qwen3.5-397B ≈ 17.9 tok/s, about one Spark's speed) | yes | yes (speculative decoding not composable with PP up to vLLM 0.15.0) |

**What Max would buy.**
- **For a higher-quant DeepSeek:** two more Sparks (four in all) and two
  more QSFP cables for the ring, about 2 × the Spark price plus the cables.
  No switch is needed for four. This gets the existing 3.5-bpw pack today, and
  4-5 bpw once quantized (§4.2).
- **For GLM headroom or a 5-6 bit GLM:** one more Spark and two cables (the
  triangle).
- **Beyond four**, or to mix pairs and quads freely: a 200/400 GbE RoCE
  switch (the community uses the CRS812) plus breakout cables.

All cabling claims beyond our pair are VERIFY ON HARDWARE.

### 4.6 Spark + RTX 5090

**Not worth building (ESTIMATED, high confidence).** Tensor parallel splits
every layer evenly, so a TP group runs at the size of its smallest member:
a 32 GB RTX 5090 rank caps every Spark rank at ~28 GiB of weights, which
wastes 75 % of each Spark. The rig is also x86_64 + sm_120 against the Sparks'
aarch64 + sm_121: the same engine source compiles for both (native TensorFold
lists kernel SMs 80/86/120/121, VERIFIED), but every rank would need its own
build, and the rig has no ConnectX-7 link to the fabric, so its traffic would
cross the LAN at a fraction of 200 GbE. Uneven **pipeline** split is the only
sensible heterogeneous scheme, and llama.cpp's RPC backend is the only engine
in our matrix that does it; it does not read EXL3. The right shape is the
hydra one we already have: the rig and the pair as separate nodes in one
route, each serving the model it fits ([BEAST_HYDRA.md](../BEAST_HYDRA.md)).

---

## 5. A recipe builder for OpenBeast

### 5.1 Shape

One command family, `beast-recipe`, over one directory per recipe. A recipe
is **data plus vendored files**, never a fork of the pipeline: the pipeline is
ours and shared, the recipe is what differs per model. That is the lesson of
Hy4 (`scripts/backends/hy4-vllm/`: a `config.sh`, vendored image files with
`CREDITS.md` sha256s, a content-hashed tag) and of Mia's repos (pins in
`config.sh`, patches applied in filename order, a tag that hashes the patches
and the Dockerfile).

```
recipes/<name>/
  recipe.env          # parsed, never sourced (like spark.env): MODEL_ID, MODEL_REVISION, FAMILY, ENGINE=native|fork|vllm,
                      # ENGINE_PIN (native version | git URL@sha + Dockerfile | base image digest), TP, CONTEXT, PARALLEL,
                      # KV, KV_POOL_GIB, MEMORY_RESERVE_GIB, MIN_AVAIL_GIB, DRAFTER=<repo@sha>|mtp|none, SERVED_NAME,
                      # TOOL_PARSER, REASONING_PARSER, AUX=<repo@sha:files> (Engram etc.), RANK_INPUTS=<command>|none
  SHA256SUMS          # weights + drafter + aux, vendored so verification needs no network
  image/              # vendored files or patches (fork/vllm engines), with CREDITS.md / NOTICE (licence of each file)
  template.jinja      # only when the checkpoint's own template cannot run
  fit.json            # generated by `plan`: per-N fit, the chosen N and why
  runs/               # conformance, bench, canary records (gitignored on the Spark, summarized in the commit)
```

### 5.2 Commands

| Command | Does | Built from | Exists? |
|---|---|---|---|
| `beast-recipe init <repo@sha \| DIR> [--name N]` | inspect (config, quant, index, template, drafter/aux detection), intake verdict, write `recipe.env` with every unresolved value marked `# VERIFY` | `model_inspect.py`, `model_intake.py`, `--write-profile` | **mostly**: profiles exist; the recipe schema and aux/drafter detection do not |
| `beast-recipe plan N --sparks 1,2,3,4` | the fit table of §4.2 for this model at its bpw (and projected bpws), engine choice with the reason (registry: which engine reads this model_type+format at which ranks), KV/pool/reserve starting values | `model_inspect.fit`, `spark-families.json` engines | **mostly**: fit exists; the engine chooser across native / fork / vLLM and the bpw projection are new |
| `beast-recipe engine N` | resolve the engine: `native` → `engine.sh install tensorfold --version V`; `fork` → build `ENGINE_PIN`'s Dockerfile with `image/` patches, tag by content hash, `docker save \| load` to every worker; `vllm` → base by digest + vendored files (Hy4's path) | `engine.sh`, `spark-hy4-stage.sh`, `spark-cluster.sh sync-image` | **partly**: three working instances, not one generic builder |
| `beast-recipe stage N` | fetch or adopt weights by revision, verify, mirror to workers (`--link-dest`, memory-scoped), HF-cache staging, drafter + aux, then `RANK_INPUTS` if the family needs them | `model-fetch.sh`, `spark-deepseek-pack.sh` staging, Hy4's capped download + stall watchdog | **partly** (per family today) |
| `beast-recipe up N [--dry-run]` | render a user unit `beast-recipe@N` (ranks N-1..0, memguard, `MIN_AVAIL` gate, rollback hook), start, wait for `/v1/models` | D2 `spark-node.sh` / `spark-cluster.sh`, Hy4 `start.sh`, `memguard.sh` | **partly**: D2 has no unit and has never served on the pair |
| `beast-recipe check N [--bench]` | canary (`verify-chat.py --model`), `conformance.sh`, `bench.py` | all three exist; `verify-chat.py` hard-codes the DeepSeek name | **yes**, one flag to add |
| `beast-recipe adopt N` | `beast-spark` learns the selection; `use-model.sh` writes `INFERENCE_MODEL`/`SLOTS`; a `benchmark_all.py` MODELS entry; the campaign | `beast-spark`, `use-model.sh`, MODEL_CAMPAIGN §0 | **partly**: beast-spark knows glm/ds/hy4 by name |
| `beast-recipe rollback` | back to the previous selection | `beast-spark`, pack tool's `local.sh.prev` pattern | **partly** |

### 5.3 Effort (ESTIMATED, engineering days, testable off the Sparks unless marked)

| Piece | Days |
|---|---:|
| Recipe schema + parser + `init` (aux/drafter detection, `# VERIFY` markers) | 1.5 |
| `plan`: engine chooser over the registry + bpw projection + launch starting values | 1.5 |
| `engine`: one builder for native / fork-Dockerfile / vLLM-overlay, content-hash tags, carry to workers, offline bundle | 2 |
| `stage`: generic mirror + HF staging + aux + `RANK_INPUTS` hook (from the pack tool and Hy4) | 1.5 |
| `up`: unit template, memguard, `MIN_AVAIL`, rollback; `beast-spark` generic selection | 2 |
| `check` / `adopt` glue (`verify-chat.py --model`, use-model, MODELS entry) | 0.5 |
| Hermetic tests in the style of `tests/test_spark_hy4.sh` and `test_spark_deepseek_pack.sh` | 2 |
| Docs (a RECIPES.md runbook; EXL3_PLAYBOOK and SPARK_HYDRA pointers) | 1 |
| **Total, code** | **~12** |
| Hardware windows (pair's model down): D2 first serve, TP1 first serve, the first new model | 3 windows of 0.5-1 day |

### 5.4 Risks

- **D2 has never served on the pair.** Every native-engine recipe depends on it.
  Qualify it first, with weights we already have (candidate 1).
- **Unified memory freezes instead of failing** when over-committed (DeepSeek
  recipe `docs/memory-and-safety.md`; SPARK_HYDRA G4). The builder must refuse
  to lower reserves below the family's measured value and must start every
  new recipe under memguard with a `MIN_AVAIL` gate.
- **A correct serve is not a fast serve.** Without Mia's patch stacks a model
  may serve at a fraction of its recipe speed (GLM's 1104 README attributes its
  numbers to 105-121 patches). Bench before adopting; the campaign decides.
- **Layout traps.** An EXL3 checkpoint loads only if its exact layout matches
  the reader (TR3 for GLM; the rank-1 cache trap for DeepSeek, EXL3_PLAYBOOK
  §(a1)). Intake catches these; the builder must not bypass intake.
- **Parsers.** Tool-call formats break conformance silently (DeepSeek needed
  four DSML parser patches). Conformance is a hard gate.
- **Licences** (§6): a recipe that pulls a non-commercial or no-derivatives artefact must say so (the GLM DFlash2 drafter was one until its 2026-11-10 relicence), and nothing may be copied from Mia's AGPL vLLM recipe.
- **Engine churn**: native TensorFold moves weekly (1.2.7 → 1.2.10 in two
  days). Pin by version and signature; re-run `capabilities --json` into the
  registry on every update.

### 5.5 The first three candidates

1. **GLM-5.3-Flash EXL3 on native TensorFold 1.2.10, TP2, through D2**
   (`models/glm53-flash-ablit-exl3-tensorfold.env` and its lock already exist;
   the weights are on both Sparks). Proves an own recipe on the pair with **no
   Mia image**, qualifies D2, and its replies can be compared with Mia's
   recipe on the same checkpoint. Risk: native's GLM path may be slower than
   either Mia recipe; that is a measurement, not a blocker. Effort: 0.5-1 day
   on the pair.
2. **Qwen3.8-27B EXL3 on ONE Spark** (`turboderp/Qwen3.8-27B-exl3`, a tested
   checkpoint for `qwen3_5` in `data/tensorfold.json`; ~15-28 GiB). Proves TP1,
   a model new to the Sparks, an own drafter choice (`z-lab/Qwen3.8-27B-DFlash2`,
   licence to check before use, or none), and the whole init → adopt path on a
   small download. Effort: 1 day.
3. **MiniMax-M3.1-Flash EXL3, TP2** (`minimax_m3_vl`: EXL3 only, 2 ranks only,
   VERIFIED in `capabilities`). The first **new architecture** on the pair
   built entirely by OpenBeast, on an engine family that already exists.
   Size: about 428 B total, 23 B active, 60 layers, 64 heads with 4 KV heads,
   128 experts with 4 active (CLAIMED, an architecture gallery). Mia publishes
   EXL3 packs at 3.06 and 3.5 bpw (`Mia-AiLab` on the Hub, VERIFIED by fetch
   of the org page). By bpw arithmetic that is ~152 GiB and ~174 GiB, or
   ~77 and ~88 GiB a rank at TP2: both fit, and the 3.06 pack is
   comfortable (ESTIMATED; intake decides on the real folder). The pack must
   match the `minimax_m3_vl` reader's layout, which intake checks. It has
   never run here. Effort: 1-2 days once the weights are on both Sparks
   (~165-190 GB each).

Not a first candidate: a higher-quant DeepSeek. It needs four Sparks and the
dsv41 fork's TP4 line (Mia's 4-Spark recipe), so it is a hardware purchase plus
a fork-engine recipe, best done once 1-3 have proven the builder.

---

## 6. Licensing and redistribution

Not legal advice; this is what the licence files say, read on spark-1 and
the sources named. The short form: **the recipes, patches and engines are
Apache-2.0 / MIT and may be derived from, with attribution; the DFlash2
drafter is the one restricted piece; a few checkpoints carry their own
terms.**

| Piece | Licence | Source | May we derive / redistribute? |
|---|---|---|---|
| Mia's DeepSeek 2-Spark recipe (`MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks`) | **Apache-2.0** ("This project's own code, scripts and documentation are licensed under the Apache License, Version 2.0 … If you redistribute this project or a modified version of it, keep this NOTICE file and state what you changed (Apache-2.0, section 4)") | `runtime/LICENSE`, `runtime/NOTICE` (VERIFIED) | **yes**: copy, modify, redistribute; keep `NOTICE` and `CREDITS.md`, mark changes, keep each patch's `From:` author |
| Its patches (`zig/patches`, by Mia's lab and ~10 named contributors) | Apache-2.0 contributions; "the code it modifies, and quotes as diff context, stays under its authors' license" (Apache-2.0) | `NOTICE`, `CREDITS.md` (VERIFIED) | yes, with the per-patch credits carried as they are |
| Mia's GLM recipes v1.8 and 1104 | **Apache-2.0**; patches modify TensorFold, whose pre-0.6.0 code keeps its MIT notice (to be kept with any patched tree) | `glm53/LICENSE`, `NOTICE`; `glm53-1104/LICENSE`, `NOTICE.md` (VERIFIED) | yes, same conditions; 1104's `NOTICE.md`: "If you redistribute it or a modified version, keep this notice and the notices below, and state what you changed" |
| b12x "RoCEnante" transport (in GLM patch `0006`, upstream `roce.zig`) | Apache-2.0 | GLM `CREDITS.md` (VERIFIED) | yes, with attribution |
| TensorFold (`ashhart/TensorFold`) | **Apache-2.0 from 0.6.0**; ≤ 0.5.0 MIT, and pre-0.6.0 code keeps its MIT notice | the recipes' `NOTICE` quoting TensorFold's (VERIFIED); upstream repo page and `CONTRIBUTING.md` (VERIFIED by fetch, 2026-11-22) | yes |
| BertholomusAI's fork (`bertholomus/TensorFold`, `deepseek-v41-zig`, `-tp4`) and kernel kit (`deepseek-v4.1-tensorfold-tp2-2xgb10`) | Apache-2.0; the kit's parts: Triton MIT, PyTorch attention BSD-3-Clause, CUTLASS BSD-3-Clause, EXL3 format MIT | DeepSeek recipe `NOTICE` (VERIFIED) | yes, with the kit's `KIT-LICENSES.md` |
| ExLlamaV3 / EXL3 format | MIT | recipes' `CREDITS.md` (VERIFIED as quoted); upstream `LICENSE` "Copyright (c) 2025 Turboderp", MIT (VERIFIED by fetch) | yes |
| atakann's Hy4 recipe + six vLLM files | MIT; the six files Apache-2.0 (vLLM) | SPARK_HYDRA Hy4 pins; `scripts/backends/hy4-vllm/image/CREDITS.md` | yes; we already vendor them with credits |
| **DFlash2 drafter `incoai/GLM-5.3-Flash-DFlash2`** | at our pin `bf582e4e`: **CC BY-NC-ND 4.0**. At `6cc6c481` (2026-11-10, "Release under Apache 2.0"): **Apache-2.0**, with the **same** `model.safetensors` blob | the README front matter on spark-1 (VERIFIED); Hub API commits and tree (VERIFIED, below) | **re-pin to `6cc6c481` and it is Apache-2.0**: derive, redistribute, quantize. Until we re-pin, our docs cite the NC-ND revision |
| DFlash architecture / code (`z-lab/dflash`) | MIT | GLM `CREDITS.md` (VERIFIED as quoted) | yes: training **our own** DFlash-style drafter with that code gives a drafter under our terms (subject to the target model's licence) |
| `brandonmusic/GLM-5.3-Flash-tr3-4bpw` (and its Mia mirror) | **ShapleyMcg License 1.0**: attribution required, and "grants no rights to the person known as '0xSero'" | GLM `CREDITS.md` (VERIFIED as quoted) | use with the attribution notice; not our default (our GLM is the Ablit/Mia pack) |
| Mia's GLM EXL3 pack | Apache-2.0 | GLM `CREDITS.md` (VERIFIED as quoted) | yes |
| Mia's DeepSeek EAQ packs | MIT | DeepSeek `NOTICE` (VERIFIED as quoted) | yes |
| DeepSeek-V4.1-Flash-1017 weights, Engram tables | MIT | DeepSeek `NOTICE` (VERIFIED as quoted) | yes |
| GLM-5.3-Flash weights | MIT ("its license, on the model card, governs any use"; 1104 `CREDITS.md` says MIT) | GLM `CREDITS.md` (VERIFIED as quoted) | yes |

**Mia's GitHub org as a whole** (`MiaAI-Lab`, 136 repos; VERIFIED by fetch)
has no org-wide licence statement and no statement on derivative use beyond
each repo's licence (NOT FOUND). One repo differs:
`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks`, the **vLLM** GLM recipe, is
**AGPL-3.0**, with contributions before 2026-09-07 under MIT (VERIFIED by
fetch). A derivative we offer over a network would be bound by AGPL's
source-offer clause. **Do not copy from that repo**, except its pre-2026-09-07
MIT parts with care. The TensorFold-based recipes we use (all three on
spark-1) are Apache-2.0. Mia's other recipe repos fetched (the DeepSeek-V4
DSpark vLLM recipe) are MIT for scripts (VERIFIED by fetch).

**DFlash2 drafter: relicensed (VERIFIED, Hub API, 2026-11-22).** The
commits of `incoai/GLM-5.3-Flash-DFlash2` are:

| Commit | Date | Title |
|---|---|---|
| `7d74cdd8` | 2026-08-27 | "Release" |
| `dc77ff1c` | 2026-08-28 | "Checkpoint update" |
| `bf582e4e` | 2026-08-31 | "Checkpoint update" (**our pin**, `license: cc-by-nc-nd-4.0` in its README on spark-1) |
| `6cc6c481` | 2026-11-10 | "**Release under Apache 2.0**" (`cardData.license: apache-2.0`) |

At `6cc6c481`, `model.safetensors` has LFS oid
`b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac`, the same
blob we hold at `bf582e4e` (SPARK_HYDRA C4). The weights are byte-identical,
and the licence of the current revision is Apache-2.0. **Action:** re-pin
`DFLASH2_REVISION` to `6cc6c481…` in our GLM `local.sh` and in the docs
(SPARK_HYDRA C4, EXL3_PLAYBOOK §5.1, `spark-families.json`). No download is
needed: only the small files and the ref change. Then drop the
"non-commercial" warning for that revision. Mia's recipes still pin `bf582e4e`
and say CC BY-NC-ND. Telling them is a courtesy worth doing.

What this means for the builder:

- A recipe that **derives from a Mia recipe** (copies its scripts or patches)
  carries the Mia `NOTICE`/`CREDITS.md`, says what changed, and keeps every
  patch's author line. That is what Apache-2.0 §4 asks; no permission is
  needed. Asking anyway, and upstreaming fixes, is the courteous route and
  keeps us on good terms with the people whose weeks of work we use.
- A recipe that **only pins** a Mia image or repo (as D1 does today) redistributes
  nothing and needs nothing beyond attribution in our docs.
- `recipe.env` gets a `LICENCES=` line, and `beast-recipe init` refuses to
  stage a non-commercial or no-derivatives artefact without `--accept-nc`.

---

## 7. Sources and commands

**On the Sparks (read-only, 2026-11-22)**, `ssh spark-1.tail4109f9.ts.net`, and
spark-2 through it:

- `ls ~/openbeast-recipes/*`; `git -C … log -1`; `git -C … remote -v`
  - DeepSeek: `MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks` @ `c7f2b851`
  - GLM: @ `33b50fd`
  - GLM 1104: `MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104` @ `82c442f`
- Recipe files read with `cat`, `head` or `grep`:
  - `LICENSE`, `NOTICE*`, `CREDITS.md`
  - DeepSeek `docs/{how-it-works,zig-engine,requirements,PORT-RECIPE}.md`,
    `zig/Dockerfile`, `ls zig/patches | wc -l` (54)
  - GLM `scripts/config.sh`, `README.md` "3 Sparks",
    `docs/TP3_QUALIFICATION_20261003.md`, `ls patches | wc -l` (82)
  - 1104 `README.md` (incl. "4 Sparks"), `docker/Dockerfile` (ARG/FROM
    lines), `scripts/config.sh`, `start-tp4-switchless.sh` header,
    `ls patches/engine | wc -l` (15)
- `~/openbeast-recipes/deepseek-prep/native-build.log` (the local image build)
- `config.json` of the DeepSeek -v2 pack and of the GLM Ablit pack;
  `README.md` of the DeepSeek -v2 pack ("Conversion")
- the README front matter of the DFlash2 snapshot `bf582e4e`
- drowzeys' kit `~/Documents/keys-DeepSeek-V4.1-Flash-1017-Abliterated/README.md`
  (pack and engine table, speed table)
- `docker image ls`; `~/.local/bin/tensorfold --version` (1.2.10 on both);
  `nice -n 15 tensorfold capabilities --json` (families, profiles,
  kernel_sms); `free -g`; `df -h /`

**This repo (`origin/main` @ `c4d7a8f0`):**
- docs: `docs/EXL3_PLAYBOOK.md`, `docs/SPARK_HYDRA.md` (DeepSeek deployment,
  Hy4, A2, C4, D2, D3), `docs/MODEL_CAMPAIGN.md` §0, `docs/MODELS.md`
  (Qwen3.8-Flash-Next, DeepSeek fit),
  `docs/reviews/HY4_FLASH_ENGINES-2026-11-19.md`
- data: `scripts/backends/data/spark-families.json`, `data/tensorfold.json`
- code: `scripts/backends/pylib/model_inspect.py` (`kv_per_token`, `tp_slack`,
  `fit`)

**Web (fetched 2026-11-22):**
- TensorFold:
  - <https://github.com/ashhart/TensorFold>
  - <https://raw.githubusercontent.com/ashhart/TensorFold/HEAD/CHANGELOG.md> (also read directly with `curl … | grep`: 1.3.3 mcg widths, 1.3.2 `--tp`/`--pp`, Hy4 slab, the 0.6.6 hand-off)
  - `…/HEAD/CONTRIBUTING.md`, `…/HEAD/docs/supported-models.md`, `…/HEAD/RUNBOOK.md`
  - <https://github.com/bertholomus/TensorFold>
- ExLlamaV3:
  - <https://github.com/turboderp-org/exllamav3/releases>
  - <https://raw.githubusercontent.com/turboderp-org/exllamav3/master/README.md>
  - `…/master/doc/convert.md`, `…/master/LICENSE`
- DFlash2 licence: `curl https://huggingface.co/api/models/incoai/GLM-5.3-Flash-DFlash2`, `…/commits/main`, `…/tree/6cc6c481…` (direct)
- Drafters:
  - DFlash: <https://arxiv.org/abs/2602.06036>, <https://github.com/z-lab/dflash>
  - NeMo DFlash2: <https://docs.nvidia.com/nemo/automodel/latest/nemo-automodel/nemo_automodel/recipes/speculative_decoding/train_dflash2.md>
  - vLLM Speculators: <https://docs.vllm.ai/projects/speculators/en/latest/user_guide/algorithms/dflash2/>
  - vLLM speculative decoding: <https://docs.vllm.ai/en/latest/features/speculative_decoding/>
  - DSpark: <https://arxiv.org/abs/2607.05147> (CLAIMED)
- Model cards:
  - <https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash>
  - <https://huggingface.co/zai-org/GLM-5.3-Flash>
  - <https://huggingface.co/Qwen/Qwen3.8-Flash-Next>
  - <https://huggingface.co/tencent/Hy4-flash>
  - MiniMax-M3.1-Flash: <https://sebastianraschka.com/llm-architecture-gallery/models/minimax-m3-1-flash-428b/> (CLAIMED)
- Mia's AI Lab:
  - <https://github.com/MiaAI-Lab>, <https://huggingface.co/Mia-AiLab>
  - <https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks> (AGPL-3.0)
  - <https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold>
  - <https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks>
  - <https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark>
- NVIDIA:
  - <https://build.nvidia.com/spark/connect-three-sparks/overview.md>
  - <https://build.nvidia.com/spark/multi-sparks-through-switch/overview>
  - <https://build.nvidia.com/spark/vllm-cluster/advanced> (CLAIMED)
- NVIDIA forum (community, CLAIMED unless marked):
  - <https://forums.developer.nvidia.com/t/three-node-spark-clusters-without-a-switch-are-now-supported-in-spark-vllm-docker-and-sparkrun/365296> (read by fetch)
  - …/connectx-7-200gbe-via-mikrotik-crs812-qsfp-dd-400g-2xqsfp56-200g-breakout/357162
  - …/8x-dgx-spark-cluster-build-report-crs812-400dd-4x100g-breakouts-nemotron-3-ultra-at-tp-8/373146
  - …/4-node-dgx-spark-cluster-without-a-switch/368726
  - …/who-has-tested-tensorfold/384789

The upstream pages were read through a fetch tool that summarises them. The
quotes above come from those summaries, except the CHANGELOG lines and the Hub
API results, which were read directly. Treat the exact wording of the other
quotes as CLAIMED until someone re-reads the page.
