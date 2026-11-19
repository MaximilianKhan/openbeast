# EXL3 playbook: hosting another EXL3 model on spark-hydra, offline

This page is for a model you have in mind and want to try on the DGX Spark
pair (spark-1 + spark-2) through our TensorFold builds, with no internet. The
goal is drag and drop: copy a model folder onto spark-1 from a drive, run one
command, and either it is served or it is refused with a reason. That works
today for one case (another DeepSeek-V4.1 pack). For the other cases this page
gives the procedure, the memory and disk arithmetic, and the design for making
them one command too.

Labels: **MEASURED** (where and when), **CLAIMED** (by whom), **UNVERIFIED**
(reasoned, never run). Everything about the Sparks was read on 2026-11-17 with
read-only commands (`ls`, `cat`, `docker image ls/inspect`, `tensorfold
--version/capabilities/models/info`, `free`, `df`, `/proc/meminfo`). No unit,
container or model was started or stopped.

Related pages: [SPARK_HYDRA.md](SPARK_HYDRA.md) (the pair's runbook,
including [DeepSeek packs](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack)),
[DGX_SPARK_PLAN.md §14](DGX_SPARK_PLAN.md#14-onboarding-a-model-youve-never-seen)
(onboarding a model through OpenBeast's own launchers),
[MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md) (the qualification every Spark model
goes through), [ENGINES.md](ENGINES.md), [BEAST_ISLAND.md](BEAST_ISLAND.md)
(running offline).

## The answer in short

| Case | What it is | Path today | Effort |
|---|---|---|---|
| **(a1)** DeepSeek pack | another EXL3 quant of DeepSeek-V4.1-Flash-1017 with the same tensor index | drag and drop: `spark-model-intake.sh DIR`, then `beastds` (DeepSeek selected) and `spark-deepseek-pack.sh use DIR` (verify, rollback, A/B bench built in) | about 20 min unattended, the first time (rank files) |
| **(a2)** GLM pack | another GLM-5.3-Flash EXL3 checkpoint in Mia's TR3 layout (4 bits, `mcg`, routed experts only), same tensor index | by hand, about 10 commands ([A GLM pack, by hand](#a-glm-pack-by-hand)); the recipe already serves any staged `MODEL_ID@MODEL_REVISION` | 30 min of commands plus a 2-6 min restart; a `spark-glm-pack.sh` would make it one command (about a day) |
| **(a3)** same architecture, other layout | e.g. Mia's official EAQ v1.1 pack (27 BF16 linears), another quantizer's GLM EXL3, a GLM EXL3 that is not 4-bit TR3 | not a drop-in. Intake says "SAME FAMILY, NOT A DROP-IN" and shows the index difference. Some are a recipe setting away (EAQ v1.1: `EAQ_V1_1=1`), others need engine work | hours (a setting and a pack-tool exception) to weeks (a reader for a new layout) |
| **(b)** an architecture a build already supports | native TensorFold 1.2.7 reads EXL3 for `glm5_next`, `minimax_m3_vl` (2 ranks) and `qwen3_5` (one GPU) | a profile and OpenBeast's own launcher ([D2](SPARK_HYDRA.md#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)): `model-inspect.sh --write-profile`, `model-fetch.sh --verify --trust-first-use`, `spark-cluster.sh start` | a day for the first one, because D2 has never served on this pair. VERIFY ON HARDWARE |
| **(c)** an architecture no build supports | e.g. a new model_type, or DeepSeek on native 1.2.7 | an engine family must exist first: a newer TensorFold release, a Mia recipe, or a port | weeks for a port. Days if upstream ships it and we stage it offline |

What makes this hard is memory and engines, not copying. The pair serves one
model at a time, on both GPUs. A rank can hold about **100 GiB of weights**,
which puts the ceiling at about **198 GiB of checkpoint at TP2**. DeepSeek at
2.9 bpw is already at 98.9 GiB per rank. Each family is served by its own
engine build, and an EXL3 checkpoint is only servable when that build has a
reader for its exact layout.

## 1. What the pair has

### 1.1 One model at a time

Each Spark has 121.69 GiB of unified memory (`MemTotal` 127,598,412 kB on both,
MEASURED). Both GPUs belong to the live model: rank 0 on spark-1 and rank 1 on
spark-2. A second model, even a small one, cannot run beside it: on 2026-11-17
spark-1 had 6-13 GiB `MemAvailable` while DeepSeek served (MEASURED,
`free -g` and `/proc/meminfo`). Every new model is therefore a **swap**. The
pair stops what it serves, loads the new model, verifies it, and rolls back if
anything fails. The tools below never swap without saying so.

### 1.2 The engine builds

| Build | Where | Serves | EXL3 |
|---|---|---|---|
| `tensorfold-dsv41:exl3-tp2-8a98ae503d85` (image ID `d048841d851f`, 24.9 GB) | both Sparks, built locally from [Mia's two-Spark recipe at `c7f2b85`](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/tree/c7f2b85100f0a7b099c92a4e21825e30d93c9c51) | `deepseek_v41` only. Its engine is bertholomus/TensorFold's Zig fork at `fbbc971` plus the recipe's `zig/patches` (image labels `tf.engine=zig`, `tf.ref=fbbc9713…`, `tf.patches=8a98ae503d85`) | yes: Mia's EAQ packs (MEASURED), and per the recipe the EAQ v1.1 mixed pack with `EAQ_V1_1=1` (CLAIMED, recipe `docs/configuration.md`) |
| `tensorfold-glm53:v0.6.0` (image ID `ead0174052ac`, 24.6 GB; the same image as `ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold`, tag `v0.6.0-31557ed1cef6`) | both Sparks, from Mia's GLM recipe v1.8 at `33b50fd` | `glm5_next` through TensorFold v0.6.0 (Python) plus 82 GLM patches | yes: the TR3 layout only (bits 4, `mcg` codebook, scope `glm53_routed_experts_only`) |
| native TensorFold 1.2.7 (`~/.local/bin/tensorfold`, signed) | both Sparks, installed, not serving | the families in [§6](#6-native-tensorfold-127-what-it-can-and-cannot-do) | `glm5_next`, `minimax_m3_vl`, `qwen3_5` |

### 1.3 How a recipe finds its weights

Both recipes read a Hugging Face cache layout,
`$HF_CACHE/hub/models--ORG--NAME/snapshots/REV/`. They pick a snapshot by
`MODEL_ID` and `MODEL_REVISION` in the recipe's `scripts/local.sh`:

| | DeepSeek recipe | GLM recipe |
|---|---|---|
| recipe | `~/openbeast-recipes/deepseek-prep/runtime` @ `c7f2b85` | `~/openbeast-recipes/glm53` @ `33b50fd` |
| `HF_CACHE` | `~/openbeast-models/deepseek-hf` (both hosts) | `~/.cache/huggingface` (both hosts) |
| unit | `deepseek-v41-inference` | `spark-hydra-inference` (`PREPARE=0`) |
| served name | `DeepSeek-V4.1-Flash-1017` | `GLM-5.3-Flash-EXL3` |
| per-rank weights | built rank files, about 106 GB each (`tensorfold-dsv41/REV8/` on spark-1, `worker-rank-cache-REV8/` copied to spark-2) | read straight from the snapshot on each host (`WORKER_WEIGHTS=copy`) |
| auxiliary | Engram tables: base shards 47 and 48 of `deepseek-ai/DeepSeek-V4.1-Flash-1017` @ `1bae0e10`, staged on both hosts and shared by every pack | DFlash2 drafter `incoai/GLM-5.3-Flash-DFlash2` @ `bf582e4e` in the cache on both hosts |
| API key | yes (`~/.config/openbeast/tensorfold-api-key`) | none (v0.6.0); loopback only, beast-gate in front |

A model folder in `~/Documents` becomes servable when it is hard-linked into
that layout on **both** hosts. Hard links cost no disk, but only within one
file system: everything here is on `/` (MEASURED: `df`).

## 2. The taxonomy, case by case

### (a1) A DeepSeek-V4.1 pack: drag and drop today

A **pack** is any EXL3 quantization of DeepSeek-V4.1-Flash-1017 whose
`model.safetensors.index.json` has exactly the reference key set and whose
`config.json` has the same architectures. The engine names its rank files
after the tensor index, so a pack with a different index is a different
model to the recipe.

```bash
# On spark-1
~/openbeast/scripts/backends/spark-model-intake.sh ~/Documents/<pack-folder>        # VERDICT: DROP-IN deepseek-v41
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --dry-run   # the plan and the local.sh block, nothing changes
beastds                                                            # the real swap needs DeepSeek selected and serving
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --bench     # swap, verify, A/B bench; rolls back on failure
```

What `use` does (read from the script): it mirrors the folder to spark-2 with
`--link-dest` against the serving pack, so shared shards become hard links.
It stages the folder on both hosts as an HF snapshot, rewrites the managed
block in `local.sh` (`MODEL_ID`, `MODEL_REVISION`, a per-revision
`WORKER_CACHE_DIR`, `MODEL_SRC`) and stops the unit. It builds both rank
files on the head and copies rank 1 to spark-2, then starts the unit and
runs `verify-chat.py` (17 × 23 = 391) and `conformance.sh`. On any failure it
restores `local.sh.prev`, re-prepares the previous pack and restarts it. The
first run on 2026-11-17 took about 15 min of rank-file building and a 40 s load
(MEASURED, run record in
[SPARK_HYDRA.md](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack)). The
served name does not change, so the gate, the command center and clients need
nothing.

**The rank-1 cache trap.** Two packs with one index both produce
`rank1of2-f4b9c7e9bd4f6316d1b4.bin`. MEASURED: both
`worker-rank-cache/` (stock) and `worker-rank-cache-04c13f12/` (-v2) hold a
file of that name, 106,120,890,008 bytes each. A shared directory would serve
the old pack's rank 1 as "ready". `spark-deepseek-pack.sh` keeps one
directory per revision. Never point two packs at one `WORKER_CACHE_DIR` by
hand.

**Overlays are not packs.** `~/Documents/keys-DeepSeek-V4.1-Flash-1017-Abliterated`
is drowzeys' Keys abliteration kit: `wo_b` sidecars and scripts, no
`config.json`. Intake calls it "NOT A MODEL: an overlay or patch kit". The
recipe applies the matching sidecar itself with `ABLIT=1`. That path is gated
and online in the recipe's own flow (CLAIMED, recipe `docs/abliterated.md`),
and `spark-deepseek-pack.sh` refuses `ABLIT=1` setups (`test "$ABLIT" = 0` in
`spark-deepseek-prepare-local.sh`). To serve an abliterated pack offline, apply
the kit to a copy of a pack on a box that is not serving, with the kit's own
`apply_mia29_wo_b_graft.py`, then bring the result as a new pack. Run intake
on it first: the kit's README says its sidecar changes 26 `wo_b` tensors in
layers 10-35 and keeps the key set (CLAIMED). UNVERIFIED on our pair.

### (a2) A GLM-5.3-Flash pack: by hand today

The GLM recipe serves whatever `MODEL_ID` / `MODEL_REVISION` say, from the
local cache, with `PREPARE=0` (read from `start.sh`: the `snapshot()` check
wants `config.json` in the snapshot on each host, and nothing is downloaded).
So a GLM pack needs no new code, only the staging that `spark-deepseek-pack.sh`
automates for DeepSeek. GLM has no rank files and no rank-cache trap: each
rank reads its own host's snapshot.

A GLM drop-in must be TR3 EXL3: `quant_method exl3`, `bits 4`, `codebook
mcg`, `scope glm53_routed_experts_only`. Its tensor index must match the
Ablit checkpoint's 150,226 keys. Anything else is (a3). The procedure is in
[§5.6](#a-glm-pack-by-hand).

### (a3) Same architecture, another layout

Intake reports these as **SAME FAMILY, NOT A DROP-IN**, with the index
difference grouped by key suffix. What the difference means:

- **Different suffixes for a few tensors** (`.weight` where the reference has
  `.trellis/.suh/.svh/.mul1`): some linears are stored unquantized. This is
  Mia's official EAQ v1.1 pack at `aee640e2`, whose 27 indexer and compressor
  linears are BF16 (CLAIMED, recipe `docs/requirements.md`). The image's
  mixed-pack loader reads it with `EAQ_V1_1=1` (patch `0024`). The -v2 folder
  (DeepSeek-V4.1-Flash (Mia EXL3), the pair's default since 2026-11-18) is *named* EAQ v1.1, but its index and config are byte-identical
  to v1's. MEASURED: both have index sha256 `525efee7…` over 192,495 keys.
  Teaching `spark-deepseek-pack.sh` the official v1.1 means accepting exactly
  that difference and setting `EAQ_V1_1=1` in its managed block. That is
  hours of work, but UNVERIFIED: we do not have that pack.
- **A different quantization variant** (GLM EXL3 at 3 bits, another codebook,
  other experts quantized): the engine refuses it at start (CLAIMED,
  `data/tensorfold.json`'s note on `families/glm5_next`). Only engine work
  helps, by Mia's lab or upstream.
- **Rank-sliced EXL3** (`experts.N.proj.rank0.trellis`): a runner-specific
  copy. TensorFold reads the plain names, so ask for the unsliced original.
- **Same weights, more or fewer files** (a pack re-sharded into 50 files
  instead of 39): the key set is the same, so intake says DROP-IN. The pack
  tool's local revision changes, because it hashes `size name` per shard.

### (b) A new architecture that a build already supports

Case (b) is a model_type that no family of ours serves, read by an engine we
have. For EXL3 that means native TensorFold 1.2.7 and three families
(MEASURED, `tensorfold capabilities --json` on spark-1):

| model_type | native formats | ranks | notes |
|---|---|---|---|
| `glm5_next` | `modelopt-nvfp4`, `modelopt-mixed`, `exl3` | 2 up to 16 (`TF_GLM_WORLD`) | EXL3 in the TR3 layout only (CLAIMED for native from the 0.x list) |
| `minimax_m3_vl` | `exl3` only | 2 only (`TF_MINIMAX_*`) | MiniMax-M3.1-Flash family per [SPARK_HYDRA.md D2](SPARK_HYDRA.md#d2-native-tensorfold-through-spark-nodesh--spark-clustersh). Never run here |
| `qwen3_5` (Qwen3.8 dense) | `mlx-q4g64`, `exl3` | EXL3 on one GPU; two ranks need the MLX checkpoint | `turboderp/Qwen3.8-27B-exl3` is a tested checkpoint (CLAIMED, `data/tensorfold.json`) |

The vendored container list (`data/tensorfold.json`, TensorFold 0.x at
`9cd52ab`) also reads EXL3 for `qwen4_exp` (Flash Next, one GPU). It only
helps through a container image that already holds its dependencies
(`TENSORFOLD_IMAGE_PREBUILT=true`), and we have none, so it is not an offline
path today. The GLM recipe image carries TensorFold v0.6.0, which may serve
other families. That is UNVERIFIED and not worth relying on: its `start.sh`
is GLM-specific.

The path is OpenBeast's own launcher, D2. It takes a profile, a lock, and one
rank per Spark. The commands are in [§5.7](#57-case-b-a-native-tensorfold-profile).
D2 has never served on this pair: SPARK_HYDRA lists GLM on native 1.2.7 as
VERIFY ON HARDWARE. The first case-(b) model therefore also qualifies D2.
The cheapest first run is the GLM checkpoint we already have, through
`glm53-flash-ablit-exl3-tensorfold.env`, because its outputs can be compared
with the recipe's.

### (c) An architecture no build supports

Intake says **NOT SERVABLE HERE** and names what refused it: no family claims
the model_type, the family does not read this format, or the engine refuses
outright. For example, native 1.2.7 answers `tensorfold info` on our
DeepSeek folder with "no registered Zig family serves model_type
deepseek_v41; the 0.6 line may: tensorfold@0.6" (MEASURED). Someone has to
write the family, and then we need its build offline:

| Who | What we would receive | Offline intake of the engine |
|---|---|---|
| **Upstream TensorFold** (a newer native release) | a signed archive. Check its `capabilities --json` first | online box: `scripts/engine.sh stage tensorfold --version V --platform linux-aarch64 ./carry`; Spark: `scripts/engine.sh install tensorfold --from ./carry` ([D3](SPARK_HYDRA.md#d3-the-engine-plane-scriptsenginesh)). Native 1.2.9 exists (`docs/ENGINES.md`); its families are UNVERIFIED |
| **Mia's lab** (a recipe repo, as for DeepSeek and GLM) | a git repo plus an image built from a pinned engine fork and patches | `git bundle` of the repo plus `docker save` of the built image (the build itself downloads Zig, the base image and git sources, so build online and carry the image). See [§5.1](#51-what-to-bring-on-the-drive) |
| **Us** (a port) | a family in the Zig engine or patches on a recipe | weeks; Mia's DeepSeek port is the template (`docs/PORT-RECIPE.md` in the recipe). Not recommended unless the model is decisive |

Other ways out of (c): a format that another engine reads, such as an NVFP4
quant under vLLM (`model-inspect.sh` says which), or the same model in
another family's layout.

## 3. Fit and feasibility

### 3.1 The per-rank budget

| Item | DeepSeek (MEASURED / CLAIMED) | GLM (MEASURED / CLAIMED) |
|---|---|---|
| Spark memory | 121.69 GiB | 121.69 GiB |
| Weights per rank | 98.9 GiB (rank file 106,120,890,008 B) | 82.4 GiB (175.6 GB / 2 × 1.008) |
| KV | compressed FP4: about 0.5 GiB at 262K tokens, 1.8 GiB at the full window; `POOL` adds 0.9 GiB per rank per 1M positions (CLAIMED, recipe docs) | fixed pool `KV_POOL_GIB=10` (live `local.sh`) |
| Engine reserve | `TENSORFOLD_MEMORY_RESERVE_GIB` 12.2 (recipe: do not lower it); start refuses below `MIN_AVAIL_GIB` 112 on either Spark | `MEMORY_RESERVE_GIB=17.5` (live) |
| Head left beside the command center | about 6 GiB idle (MEASURED, 2026-11-17); `memguard` kills a rank under 2.5 GiB for 4 s | 9.7-9.9 GiB (MEASURED, 2026-11-15) |

From this, intake's rule (in `data/spark-families.json`, `pair`):

- **per-rank weights = checkpoint safetensors bytes / TP × 1.008.** The 0.8 %
  is replicated tensors. It is calibrated on DeepSeek: 210,601,036,966 B of
  checkpoint gave a 106,120,890,008 B rank-1 file.
- **≤ 92 GiB per rank: fits** with room for KV and the command center.
- **92-100 GiB: tight.** It runs, but the head keeps only a few GiB (DeepSeek
  is here).
- **> 100 GiB: does not fit.** Use more Sparks or a smaller quant.

So the pair's ceiling is about **198 GiB (213 GB) of checkpoint at TP2**, and
one Spark's is about 92-100 GiB at TP1. Do not lower an engine's memory
reserve to make weights fit: on unified memory, exhaustion freezes the Spark
instead of raising an error (recipe `docs/memory-and-safety.md`; SPARK_HYDRA
G4).

### 3.2 Estimating a checkpoint before you have it

`bytes ≈ total parameters × average bpw / 8`, plus the unquantized parts:
embeddings, norms, a vision tower, anything kept in BF16. Use **total**
parameters for an MoE: every expert lives in memory, while active parameters
only set the speed. EXL3 cards state an average bpw. Mia's EAQ packs mix
widths: routed experts at 3 bits (2 in layers 18-22), attention at 5, the head
at 6, draft blocks at 4 (CLAIMED, recipe `docs/requirements.md`).

### 3.3 Worked examples

**DeepSeek-V4.1-Flash EAQ 2.9 bpw (MEASURED).** 210.6 GB (196.1 GiB) in 39
shards → 98.9 GiB per rank at TP2. Tight, and it serves. TP1 would need
196 GiB: no.

**The same model at 3.5 bpw (estimate).** drowzeys' kit README names a
LibertAI EXL3 3.5 bpw pack (CLAIMED). Scaling by bpw, 196.1 × 3.5 / 2.9 ≈
237 GiB of checkpoint → about 119 GiB per rank. **Does not fit the pair.** The
same README gates that pack on 4 Sparks, which agrees. Intake would say
"a deepseek-v41 drop-in by layout, but 119 GiB of weights per rank … is over
the 100 GiB".

**The same model at 2.8 bpw (estimate).** The Jayleton EXL3 2.8 bpw pack
(CLAIMED, same README, "TensorFold Zig TP2") scales to about 189 GiB → about
95 GiB per rank. That is tight but fits, *if* its index matches (UNVERIFIED).
Intake decides that in seconds once the folder is here.

**GLM-5.3-Flash TR3 4 bpw (MEASURED).** 175.6 GB (163.6 GiB) in 83 shards →
82.4 GiB per rank at TP2, plus a 10 GiB KV pool. Fits.

**A 27B dense model at 4 bpw (estimate).** About 27e9 × 4 / 8 = 13.5 GB plus
embeddings, about 15 GB. It fits one Spark many times over. It still has to
swap out the live model, because both GPUs are taken (§1.1). Running it on
one Spark beside a pair model is not possible while the pair model takes
about 99 GiB on each.

**KV for a new model.** `model-inspect.sh DIR` prints KV bytes per token
(MLA latent, or 2 × layers × KV heads × head dim × 2 B). With W GiB of weights
per rank, the KV room per rank is about `100 - W` GiB minus the engine's
pool and workspace. For example, 17 GiB of room at 200 KiB per token is
about 89K tokens. That is an estimate. The engine's own start line ("N
streams at once, … at a W-token window") is the measurement.

### 3.4 Disk

On 2026-11-17, `/` had 2.4 TB free on spark-1 and 2.5 TB on spark-2 (MEASURED).

| Per new pack | spark-1 | spark-2 |
|---|---|---|
| DeepSeek | the folder (shards shared with a staged pack are hard links) + rank 0 (`tensorfold-dsv41/REV8`, about 106 GB) + rank 1 built here (about 106 GB) | the mirrored folder (`--link-dest`) + rank 1 (about 106 GB) |
| GLM | the folder (the cache snapshot is hard links) | the mirrored folder |
| native profile (b) | the folder (+ hard links for a recipe-style cache, if any) | the folder |

That leaves room for about 6 more DeepSeek packs before cleanup. Nothing
deletes old rank files automatically. Remove a pack only when no
`local.sh` / `local.sh.prev` names it.

## 4. The intake preflight: `spark-model-intake.sh`

`scripts/backends/spark-model-intake.sh` is read-only and offline. It reads
`config.json`, `quantization_config.json`, the safetensors index, each shard's
header (through `pylib/model_inspect.py`), the tokenizer and template files
and `SHA256SUMS`. It checks them against `data/spark-families.json`: the
families we serve, each with its recipe, image, unit, served name and the
sha256 of its reference tensor key set, plus native 1.2.7's families and
formats. It never reads weights unless you pass `--checksums`, never writes,
and never touches a unit, docker or the GPU.

```bash
scripts/backends/spark-model-intake.sh DIR [DIR ...]      # one verdict per folder
scripts/backends/spark-model-intake.sh DIR --json         # every field
scripts/backends/spark-model-intake.sh DIR --checksums    # also verify SHA256SUMS (reads the weights: see below)
```

| Verdict | Meaning | Next |
|---|---|---|
| `DROP-IN <family>` | same model_type, the family's quantization fields, the reference tensor index, and it fits | the family's swap (printed) |
| `NEEDS A PROFILE` | no family of ours, but an engine we have reads this model_type and format, at a rank count that fits | `model-inspect.sh --write-profile`, then D2 (§5.7) |
| `SAME FAMILY, NOT A DROP-IN <family>` | our architecture, another index or quantization variant | case (a3) |
| `NOT SERVABLE HERE` | no engine reads it, or it does not fit at an allowed rank count | case (c), or a smaller quant |
| `INCOMPLETE` | shards the index names are missing or empty, or a broken symlink | fix the copy |
| `NOT A MODEL` | no `config.json`: a recipe repo or an overlay kit, named as such | bring it as what it is |

It exits 0 only when every folder is a drop-in that fits, 1 otherwise, and 2
on bad usage. It also warns about:

- a missing tokenizer or chat template;
- `SHA256SUMS` entries that are absent or shards it does not cover;
- a tight fit;
- a pack that is already known (by its local revision), or one with a known
  pack's local revision but other content: the family's `pack_fingerprints`
  (the same content fingerprint `spark-deepseek-pack.sh` checks before staging,
  sha256 over each shard's name, size, first and last MiB) tells them apart;
- a folder on a different file system from the family's cache, which would
  mean copies instead of hard links.

**`--checksums` on a serving Spark.** Reading 200 GB fills the page cache. The
DeepSeek recipe's own docs record a 5.8 GB copy that held `MemAvailable` under
memguard's floor long enough to stop a rank. Intake drops each file's pages
after hashing it (`posix_fadvise DONTNEED`), but run it on the drive before
the copy, or with the pair down.

### Intake on the real folders (MEASURED, spark-1, 2026-11-17)

The tool and its data were copied to `/tmp/ob-intake`, run with `nice -n 15`,
and deleted. Six folders took 2.2 s of wall time, reading headers only.

| Folder | Verdict | Detail |
|---|---|---|
| `~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw` | DROP-IN deepseek-v41 | 210.6 GB in 39 shards, 192,495 keys, index `525efee7…`, local rev `5d70e783` (known: the stock pack); TP2 98.9 GiB/rank tight; SHA256SUMS 64 entries; disk head +212 GB, spark-2 +317 GB |
| `…-EAQ-v1-2.9-bpw-v2` | DROP-IN deepseek-v41 | same index; local rev `04c13f12` (known: DeepSeek-V4.1-Flash (Mia EXL3), THE DEFAULT pack, and DeepSeek the pair's default model since 2026-11-18; GLM held the default for part of that day); warns that SHA256SUMS lists 1 file not in the folder (`.gitattributes`, which v1 has and v2 lacks) |
| `~/Documents/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit` | DROP-IN glm53 | EXL3 4 bpw, `mcg`, `glm53_routed_experts_only`; 175.6 GB in 83 shards, 150,226 keys, index `60b13aad…`, local rev `b2c37cff`; TP2 82.4 GiB/rank fits; no SHA256SUMS |
| `~/openbeast-models/glm53-flash-ablit-exl3` | DROP-IN glm53 | identical to the above (the same files) |
| `~/Documents/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold` | NOT A MODEL | "a recipe repository (start.sh, scripts/), not a checkpoint" |
| `~/Documents/keys-DeepSeek-V4.1-Flash-1017-Abliterated` | NOT A MODEL | "an overlay or patch kit (scripts + safetensors, no config.json)" |

The local revisions agree with `spark-deepseek-pack.sh`'s (`size name` lines
over `model-*.safetensors`, sorted, sha1): `04c13f12…` is the REV the pack
script staged the -v2 pack under. A REV is only a size listing, so the
families file also records each known pack's content fingerprint
(`pack_fingerprints`, read-only from spark-1 on 2026-11-18: -v2 `5314a93d…`,
stock `4456161f…`, GLM `8fc4c5d5…`). The hermetic tests check both functions
(REV and fingerprint) against the pack script's, on any OS.

## 5. Offline intake, step by step

### 5.1 What to bring on the drive

| Item | Why | How to make it on a connected box |
|---|---|---|
| The checkpoint folder: every `model-*.safetensors`, `model.safetensors.index.json`, `config.json`, `quantization_config.json`, `generation_config.json` | the model | `hf download ORG/NAME --revision <40-hex sha> --local-dir NAME`. Record the SHA: it becomes the snapshot REV and the provenance |
| tokenizer (`tokenizer.json`, `tokenizer_config.json`) and chat template (`chat_template.jinja`) | tools and thinking need the template; the recipes read the checkpoint's own | same download. DeepSeek's recipe also ships `model/chat_template.jinja` |
| any auxiliary weights | some families need more than the checkpoint | DeepSeek: Engram shards 47 and 48 of the base repo (already on both Sparks; carry them only for a new pair). GLM: the DFlash2 drafter (already on both) |
| drafters | speculative decoding | GLM: `incoai/GLM-5.3-Flash-DFlash2` @ `bf582e4e` (CC BY-NC-ND). DeepSeek: DSpark blocks are inside the pack |
| `SHA256SUMS` over the folder | proves the copy | `sha256sum model-* *.json *.jinja > SHA256SUMS.drive` on the source box. Keep the upstream `SHA256SUMS` too, but the -v2 pack shows it can be stale |
| for case (b)/(c): the engine | the Sparks cannot build images offline | `docker save IMAGE \| zstd -T0 > IMAGE.tar.zst` (or `scripts/backends/spark-cluster.sh save-image IMAGE FILE`); a native release via `scripts/engine.sh stage tensorfold --version V --platform linux-aarch64 ./carry` |
| for a new recipe: the repo | its scripts and patches | `git -C RECIPE bundle create RECIPE.bundle --all`; on the Spark `git clone RECIPE.bundle ~/openbeast-recipes/NAME && git -C … checkout <pinned commit>` |
| this repo at a commit with the intake tool | `spark-model-intake.sh` and its data | `git bundle` ([SPARK_HYDRA H2](SPARK_HYDRA.md#h2-the-repo)) |
| the model card and license | the candidates checklist (§8) | save the README |

### 5.2 Check the drive before it touches a Spark

On the Mac or another box, from the drive:

```bash
cd /Volumes/<drive>/<folder> && shasum -a 256 -c SHA256SUMS.drive        # every line OK
~/openbeast/scripts/backends/spark-model-intake.sh /Volumes/<drive>/<folder>   # the verdict, before any copy
```

Intake runs anywhere with python3 and a checkout. On the Mac it shows "free
here" for the drive, not the Spark. The memory verdict is the pair's either
way. If the verdict is NOT SERVABLE HERE or NOT A MODEL, stop here.

### 5.3 Copy onto spark-1 without starving the live model

A 200 GB copy on a serving Spark builds page cache and dirty pages. The
DeepSeek recipe's memguard kills a rank when `MemAvailable` stays under
2.5 GiB for 4 s (CLAIMED, recipe `docs/memory-and-safety.md`, issue #117).
Pick one approach:

1. **The pair down** (safest): `beast-spark down` (PR #212, installed on
   spark-1 as `~/.local/bin/beast-spark`), copy, then `beast-spark up`.
2. **A memory-capped copy** while it serves. The user manager has the memory
   controller delegated (MEASURED: `cgroup.controllers` = `cpu memory pids`
   on spark-1), so page cache charged to the copy is capped:

   ```bash
   systemd-run --user --scope -p MemoryMax=4G -p MemoryHigh=3G \
     rsync -a --partial --info=progress2 /media/$USER/<drive>/<folder>/ ~/Documents/<folder>/
   ```

   This is UNVERIFIED on the pair: watch `grep MemAvailable /proc/meminfo` on
   spark-1 during the first minute, and stop the copy if it falls under
   4 GiB.

Keep the folder name as the model's identity (`ORG-NAME[-variant]`) and put
it under `~/Documents`, which is the backup convention: the pack tools mirror
it to spark-2 at the same path.

### 5.4 Intake on spark-1

```bash
~/openbeast/scripts/backends/spark-model-intake.sh ~/Documents/<folder>
```

(spark-1's `~/openbeast` was at `d3727185` on 2026-11-17, which predates
this tool. Pull or bundle a commit that has it first.) Follow the verdict.

### 5.5 Case (a1): a DeepSeek pack

```bash
beastds                                         # DeepSeek selected and serving first (it is the default; a no-op when it serves)
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh list
./spark-deepseek-pack.sh use ~/Documents/<folder> --id ORG/NAME --dry-run
./spark-deepseek-pack.sh use ~/Documents/<folder> --id ORG/NAME --bench
```

`--id ORG/NAME` names the cache entry. Without it the ID is
`local/<folder>`. **The pack tool swaps DeepSeek's pack only while DeepSeek
is the model.** If GLM is serving, `use` refuses (`--dry-run` still works)
and tells you to run `beast-spark ds` first; `--stop-glm` stops GLM
explicitly for one swap, after which the boot selection and the command
center still point at GLM until `beast-spark ds`. Never run
`activate-stock.sh` while GLM is active: it does not stop GLM.

**`beast-spark ds` always re-selects its configured pack**, the `-v2` pack by
default, so a pack adopted here is replaced at the next GLM → DeepSeek round
trip. To keep it, name it in `~/.config/openbeast/beast-spark.env` on spark-1
(parsed, never sourced):

```bash
DS_PACK=~/Documents/<folder>
DS_ID=ORG/NAME          # the ID `./spark-deepseek-pack.sh list` shows
```
Logs go to `runtime/runs/packs/REV8-<time>/` (`prepare.log`, `canary.log`,
`conformance.log`, `bench.log`). Rollback is automatic on failure. By hand
it is `./spark-deepseek-pack.sh use <previous folder>` or `use stock`.

<a id="a-glm-pack-by-hand"></a>

### 5.6 Case (a2): a GLM pack, by hand

All on spark-1, with the folder in `~/Documents` and intake saying
`DROP-IN glm53`. The steps mirror what `spark-deepseek-pack.sh` does.

```bash
NEW=~/Documents/<folder>
SERVING=~/openbeast-models/glm53-flash-ablit-exl3
ID=local/$(basename "$NEW")                                     # or ORG/NAME
REV=$(~/openbeast/scripts/backends/spark-model-intake.sh "$NEW" --json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["local_rev"])')

# 1. mirror to spark-2 at the same path (shards shared with the serving checkpoint become hard links)
rsync -aH --partial --info=progress2 --link-dest="$SERVING" "$NEW/" "spark-2:$NEW/"

# 2. stage on both hosts as an HF-cache snapshot, by hard link
C=".cache/huggingface/hub/models--${ID//\//--}"
stage="set -e; mkdir -p ~/$C/snapshots/$REV ~/$C/refs; [ -s ~/$C/refs/main ] || printf %s $REV > ~/$C/refs/main; cp -al --update=none '$NEW/.' ~/$C/snapshots/$REV/"
bash -c "$stage" && ssh -n spark-2 "$stage"
ssh -n spark-2 "test -f ~/$C/snapshots/$REV/config.json" && echo "spark-2 staged"
stat -c '%h %n' ~/$C/snapshots/$REV/config.json                 # links >= 2: the same bytes as the folder

# 3. select it in the recipe (back up first; the explicit MODEL_ID wins over ABLIT=1)
cd ~/openbeast-recipes/glm53
cp -p scripts/local.sh scripts/local.sh.bak-$(date +%Y%m%d%H%M)
printf '%s\n' "MODEL_ID=$ID" "MODEL_REVISION=$REV" "THINKING=0" >> scripts/local.sh
DRY_RUN=1 PREPARE=0 ./start.sh 2>&1 | tail -25                   # checks the snapshot here, prints both ranks' commands; stops nothing

# 4. serve it
systemctl --user restart spark-hydra-inference                   # if GLM is the live model (2-6 min)
# or, from DeepSeek: beast-spark glm   (restore-glm.sh: GLM's openbeast.conf lines, enable GLM, restart the command center)

# 5. verify (the GLM server has no API key; it listens on loopback)
curl -s http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"GLM-5.3-Flash-EXL3","temperature":0,"max_tokens":64,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"What is seventeen times twenty-three? Answer with the decimal digits only."}]}'
cd ~/openbeast && scripts/backends/conformance.sh --url http://127.0.0.1:8000 --model GLM-5.3-Flash-EXL3 \
  --backend tensorfold --out ~/openbeast-recipes/glm53/conformance-${REV:0:8}

# 6. optional A/B (the DeepSeek recipe's bench is a generic OpenAI client)
python3 -I ~/openbeast-recipes/deepseek-prep/runtime/tools/bench.py --url http://127.0.0.1:8000 --model GLM-5.3-Flash-EXL3 \
  --suite prefill,decode,multi --reps 2 --salt packs --label "$ID@${REV:0:8}" --out ~/openbeast-recipes/glm53/runs-${REV:0:8}.json

# rollback: put the backup back and restart
cd ~/openbeast-recipes/glm53 && cp -p scripts/local.sh.bak-<time> scripts/local.sh && systemctl --user restart spark-hydra-inference
```

Notes, all read from the recipe's `config.sh` / `start.sh`:

- `THINKING=0` keeps the server default the Ablit checkpoint gets. `config.sh`
  turns thinking off by default only when `MODEL_ID` is the Ablit ID.
  OpenBeast sends `enable_thinking` per request anyway (`INFERENCE_THINKING`).
- `need_hf_token` applies only to the Ablit ID, so a local ID never asks for
  a token.
- `SERVED_NAME` stays `GLM-5.3-Flash-EXL3`, so the gate, `openbeast.conf`
  (`rollback/openbeast.conf.glm`) and clients need nothing.
- `DRY_RUN=1` validates the serve arguments by running the image once
  (`start.sh` step 2's argument check, lines 213-217 at `33b50fd`). It is short, but it is a container start.

The whole procedure is UNVERIFIED as a sequence: no second GLM pack has been
served. Each step is the GLM half of a step that ran for DeepSeek, or a
recipe check read from its source.

### 5.7 Case (b): a native TensorFold profile

```bash
cd ~/openbeast
scripts/backends/model-inspect.sh ~/Documents/<folder> --write-profile <name> --backend tensorfold
$EDITOR scripts/backends/models/<name>.env        # SOURCE=/home/max/Documents/<folder> (absolute), SERVED_MODEL_NAME,
                                                  # TENSOR_PARALLEL_SIZE (intake's TP), MAX_MODEL_LEN; resolve every "# VERIFY"
python3 scripts/backends/pylib/obprofile.py check <name>
scripts/backends/model-fetch.sh --profile <name> --verify --trust-first-use   # records models/<name>.lock (reads every byte: §4's caution)
# spark-2: the same folder (rsync as in 5.6 step 1) and the same profile + lock in its checkout, then --verify there
scripts/backends/spark-cluster.sh check
scripts/backends/spark-cluster.sh start --profile <name> --dry-run            # read every command
beast-spark down                                                              # D2 uses :8000 and the rendezvous ports too
scripts/backends/spark-cluster.sh start --profile <name>
scripts/backends/conformance.sh --url http://127.0.0.1:8000 --model <served name> --backend tensorfold \
  --key-file ~/.config/openbeast/tensorfold-api-key                           # the key spark.env's TENSORFOLD_API_KEY_FILE names
scripts/backends/use-model.sh --profile <name>                                # INFERENCE_MODEL in openbeast.conf, after a passing report
systemctl --user restart openbeast
# rollback: scripts/backends/spark-cluster.sh stop; then beast-spark up (the enabled unit comes back)
```

A one-GPU model (TP1) runs on spark-1 alone with `spark-node.sh --profile
<name> --rank 0`. It still needs the pair's model down, because rank 0 of the
live model is on spark-1. Nothing in D2 is a systemd unit: a reboot does not
bring it back, and `beast-spark` knows only GLM and DeepSeek ([§7](#7-what-to-build-next)).

### 5.8 Wiring: command center, opencode, evals

- **Same served name** (a1, a2): nothing. `INFERENCE_MODEL` stays, and
  enrolled clients keep `openbeast-rig/<name>`.
- **A new served name** (b): `scripts/backends/use-model.sh --profile <name>`
  (or `--model <id> --report <conformance>/latest.json`) records
  `INFERENCE_MODEL`. Set `INFERENCE_SLOTS` to the engine's streams. Restart
  `openbeast`. On each client, run `openbeast-client use spark-hydra`, which
  refreshes the model in opencode. Open WebUI lists `/v1/models` by itself.
- **Evals**: a `benchmark_all.py` MODELS entry ([MODEL_CAMPAIGN §0.4](MODEL_CAMPAIGN.md#0-onboard-the-model)),
  then the campaign.

### 5.9 Rollback, in one place

| You did | Undo |
|---|---|
| a DeepSeek pack | automatic on failure; else `spark-deepseek-pack.sh use <previous folder>` or `use stock` |
| a GLM pack | restore `scripts/local.sh.bak-<time>`, `systemctl --user restart spark-hydra-inference` |
| switched models | `beast-spark glm` / `beast-spark ds` (the switch path; `restore-glm.sh` / `activate-stock.sh` are its internals) |
| a native profile | `spark-cluster.sh stop`, then `beast-spark up` |
| `INFERENCE_MODEL` | `use-model.sh --model <old id> --report <its conformance report>`, or `restore-glm.sh` for GLM's lines |

Staged snapshots and rank files stay on disk, which is why a rollback takes
seconds to minutes, not a re-copy.

## 6. Native TensorFold 1.2.7: what it can and cannot do

All MEASURED on spark-1, 2026-11-17, read-only.

**It can:**

- report its build: `tensorfold-native 1.2.7`, `engine: zig`, chip
  `nvidia-sm121`, backend `cuda`;
- read EXL3 for `glm5_next`, `minimax_m3_vl` and `qwen3_5` (the table in
  §(b)), and MLX, ModelOpt, FP8-block and BF16 for 17 families in all;
- run multi-node with `--tp 1|2|4|8 --rank --master --master-port`, with NCCL
  and RDMA verbs found (`capabilities` lists `libnccl.so.2` and
  `libibverbs.so.1` as found);
- require a key with `--api-key-file` (and `--max-inflight`,
  `--requests-per-second` limits);
- produce structured output and required tool calls (`libtfgrammar.so`
  found);
- list the HF-cache models it recognizes: `tensorfold models` names only
  `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit` in `~/.cache/huggingface`.

**It cannot:**

- serve DeepSeek-V4.1: "no registered Zig family serves model_type
  deepseek_v41; the 0.6 line may: tensorfold@0.6". Its `capabilities`
  registers `TF_DSV41_*` switches, but no `deepseek_v41` family. DeepSeek runs
  only inside the recipe image's forked engine;
- size an EXL3 checkpoint correctly in `info`: for the GLM TR3 folder it
  prints `format: full precision` and a single-device "memory floor" of
  165.65 GiB (weights 163.65 GiB). Use intake or `model-inspect.sh` for
  fit, not `info`;
- read JPEG, WebP or PDF inputs for `--vision`: `libturbojpeg`,
  `libwebpdecoder` and `libpdfium` are not found;
- fetch anything offline: `pull` and the update check need the network.
  `spark-node.sh` passes `--no-update-check`.

It has never served on this pair. Serving GLM through it (the profile
`glm53-flash-ablit-exl3-tensorfold`) is the open VERIFY ON HARDWARE item in
SPARK_HYDRA D2, and the natural first run of case (b).

## 7. What to build next

### 7.1 The design

The goal is `beast-spark use ~/Documents/<folder>`: one command for every
case-(a) and case-(b) folder. Each piece is small, because the checks already
exist.

1. **The registry** (`scripts/backends/data/spark-families.json`, shipped
   with intake). Each family carries its model_types, required quantization
   fields, reference index signature, recipe and commit, image, unit, served
   name, HF cache, swap command and auxiliary weights. Each engine carries its
   families, formats, ranks and refusals. Adding a family is a JSON entry plus
   an adapter.
2. **Per-family adapters**, one script each, all with the same verbs:
   `current`, `check DIR`, `plan DIR`, `apply DIR` (stage, select, restart,
   verify, roll back on failure) and `list`.
   - `deepseek-v41`: `spark-deepseek-pack.sh` as it is.
   - `glm53`: a new `spark-glm-pack.sh`. It is §5.6 in code: a managed
     `MODEL_ID` / `MODEL_REVISION` / `THINKING` block in the GLM `local.sh`,
     `local.sh.prev`, a mirror and stage on both hosts, a restart (or
     `restore-glm.sh` from DeepSeek), then canary + conformance, rollback and
     `--bench`.
   - `native`: writes a profile from intake's JSON, records the lock, and
     runs D2 under a third user unit, `spark-native-inference`, so the model
     survives a reboot.
3. **A generic canary.** `verify-chat.py` hard-codes
   `DeepSeek-V4.1-Flash-1017`. A `--model` argument (and no key for GLM)
   makes it every adapter's canary.
4. **The dispatcher**: `beast-spark use DIR [--dry-run] [--bench]`. It runs
   intake, refuses anything but `DROP-IN` / `NEEDS A PROFILE`, prints the plan
   and calls the family's adapter. `beast-spark` learns a third selection
   (`native:<profile>`) beside `glm` and `ds`.
5. **The official EAQ v1.1 exception**: let the DeepSeek adapter accept
   exactly the "27 linears stored as `.weight`" difference and set
   `EAQ_V1_1=1` in its managed block.

### 7.2 Effort and order

| Step | Effort | Can be tested without the Sparks? |
|---|---|---|
| `spark-glm-pack.sh` + test | about 1 day | yes, hermetically, like `test_spark_deepseek_pack.sh`; one live swap window (about 30 min, the GLM model down) to prove it |
| `verify-chat.py --model` | 1 hour | yes |
| `beast-spark use` dispatcher over intake | about half a day | yes (stub adapters) |
| D2 qualification with GLM on native 1.2.7 | half a day on the pair | no: it stops the live model |
| `native` adapter + `spark-native-inference` unit | 2-3 days | partly (unit rendering, profile drafting) |
| EAQ v1.1 exception | hours, once we have the pack | partly |

**Recommended next step: `spark-glm-pack.sh` plus the `beast-spark use`
dispatcher, about 1.5 days.** That makes case (a) one command for both
families, and it can be built and tested off the Sparks. Then qualify D2 by
serving the GLM checkpoint we already have on native 1.2.7. Only that tells
us whether case (b) is a profile away or a project, and it should happen
before a model Max cares about depends on it.

## 8. Candidates: evaluating a model before you bring it

For a model you have in mind, answer these from its model card and file
list, before downloading 200 GB:

1. **Architecture.** `config.json`'s `model_type` and `architectures`. Is it
   `deepseek_v41` or `glm5_next` (case a), a native 1.2.7 EXL3 family (case
   b), or neither (case c)? `model-inspect.sh ORG/NAME@<sha>` reads the Hub
   metadata only (online, read-only) and says the same before any download.
2. **Format and layout.** `quantization_config`: `quant_method exl3`, bits or
   average bpw, codebook, scope. For GLM, it must be TR3 (4, `mcg`,
   `glm53_routed_experts_only`). For DeepSeek, the tensor index must match
   Mia's packs. The quantizer and its version (`version` in the config) are
   clues, not proof.
3. **Size.** Total safetensors bytes. Divide by 2 and multiply by 1.008: is
   it ≤ 92 GiB (fits), 92-100 (tight), or more (no) per rank? Can a single
   Spark hold it (≤ 92 GiB)? Also count the KV it needs at the context you
   want (§3.3).
4. **Disk.** The folder on both hosts, plus rank files for DeepSeek-family
   packs (§3.4).
5. **License and terms.** Gated repos, non-commercial drafters (DFlash2 is CC
   BY-NC-ND), abliteration overlays and their responsible-use terms. Note the
   license in the commit that adds the model.
6. **Recipe availability.** Is there a recipe (Mia's lab, upstream TensorFold
   `MODELS`, a tested checkpoint in `data/tensorfold.json`) that has run this
   exact checkpoint on two Sparks? A "tested checkpoint" note is the best
   signal. Without one, budget for case (a3) or (c).
7. **Claims to check.** Speed and quality numbers on a card are CLAIMED until
   our bench and the campaign reproduce them.
8. **Qualification.** Every model that is served for real goes through
   [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md): conformance, an L0 baseline, the
   failure audit, the thinking-first arms, the report. A pack that changes
   only the quantization of a qualified model needs at least the bench A/B
   and a v5 rerun before it becomes the default (DeepSeek-V4.1-Flash (Mia
   EXL3) has its v5 row, 61/71; a v1-vs-v2 quality A/B is still open).

No specific new models are recommended here. The only other EXL3 DeepSeek
packs named on the Sparks are in drowzeys' kit README: LibertAI 3.5 bpw and
Jayleton 2.8 bpw (CLAIMED). §3.3 has their fit estimates.

## 9. Open items

- A second GLM pack has never been served. §5.6 is UNVERIFIED as a sequence.
- The memory-capped copy (§5.3) is UNVERIFIED on the pair.
- D2 / native 1.2.7 has never served on the pair (SPARK_HYDRA "Not done yet").
- Native 1.2.9's families are unknown here.
- The -v2 pack's `SHA256SUMS` is v1's and cannot verify its 24 new shards
  (SPARK_HYDRA run record). Ask Mia's lab for the real one.
- Intake's thresholds (92 / 100 GiB) come from two families. A model with a
  large KV, or a vision tower kept in memory, needs more than the weights
  rule shows. The engine's start line is the measurement.
