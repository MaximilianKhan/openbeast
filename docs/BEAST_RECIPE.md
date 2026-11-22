# beast-recipe: OpenBeast's own serving recipes for the Sparks

`beast-recipe` takes any Hugging Face model (or a local folder) and a Spark
count, and answers one question end to end: **can we serve this, on this many
Sparks, and what does it take?** It then generates the recipe (a profile and a
launch config) and wires the steps that apply it (stage, up, check, adopt,
rollback) to the tools OpenBeast already has, with a dry run by default
everywhere. It does not depend on Mia's recipes. When the only engine for a
model is a Mia image (DeepSeek today), it says so and hands over to that
image's unit.

Built 2026-11-22 from the design in
[OWN_RECIPES_INVESTIGATION-2026-11-22.md](reviews/OWN_RECIPES_INVESTIGATION-2026-11-22.md)
(section 5). v1 is built and tested off-hardware: every acting command prints
what it would do, and real hardware runs are deferred to a pair-down window
(see [What is deferred to hardware day](#what-is-deferred-to-hardware-day)).

Related: [EXL3_PLAYBOOK.md](EXL3_PLAYBOOK.md) (the case taxonomy),
[SPARK_HYDRA.md](SPARK_HYDRA.md) (D1 Mia recipes, D2 our launcher, Hy4),
[ENGINES.md](ENGINES.md) (`engine.sh`), [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md)
(what comes after a recipe serves: the qualification campaign),
[WEIGHTS_DOWNLOAD.md](WEIGHTS_DOWNLOAD.md).

---

## In one screen

```bash
# 1. a model + a Spark count -> recipes/<name>/recipe.env + SHA256SUMS, and the plan
scripts/beast-recipe.sh init https://huggingface.co/ORG/REPO/tree/<sha> --sparks 2 [--name NAME] [--engine ID]

# 2. the plan: fit per Spark, engine choice and why, parallelism, drafter, KV,
#    the VERDICT, the gap list with effort, and every command end to end
scripts/beast-recipe.sh plan NAME [--sparks 1,2,3,4] [--json] [--write]

# 3. apply (each prints what it would do; --run acts)
scripts/beast-recipe.sh engine NAME   # the engine on every Spark: check, then the stage commands
scripts/beast-recipe.sh stage  NAME   # weights (+ drafter, aux) on every Spark, verified
scripts/beast-recipe.sh up     NAME   # render the profile + a systemd user unit; start it (refuses while another model serves)
scripts/beast-recipe.sh check  NAME --bench   # conformance + decode tok/s at 1 and 4 streams
scripts/beast-recipe.sh adopt  NAME   # make it selectable: beast-spark NAME
beast-spark NAME                       # the switch (stops the model that serves, starts this, waits, openbeast.conf)
scripts/beast-recipe.sh rollback      # back to the selection before the last switch (beast-spark <previous>)

scripts/beast-recipe.sh list           # every recipe and its verdict
scripts/beast-recipe.sh show NAME      # every value and where it came from;  show --keys: the schema
```

The worked examples, all committed under `recipes/`:

| Recipe | Model | Sparks | Engine | Verdict |
|---|---|---:|---|---|
| `hy4-flash-nvfp4-vllm-tp2` | Hy4-flash NVFP4 (atakans @ 30f201ac) | 2 | vllm-hy4 | **SERVABLE-NOW** (it is the served config; golden-tested) |
| `hy4-flash-exl3-tensorfold-tp2` | Hy4-flash EXL3 4bpw (LibertAIDAI @ 811866b0) | 2 | tf-native-1.3.4 | **SERVABLE-WITH-WORK** (32-93 engineering days, mostly upstream's two-Spark family) |
| `glm53-flash-exl3-native-tp2` | GLM-5.3-Flash EXL3 4bpw TR3 (Ablit, local) | 2 | tf-native-1.2.10 | **SERVABLE-NOW** (our launcher, no Mia image; first D2 serve) |
| `deepseek-v41-flash-exl3-2.9-tp2` | DeepSeek-V4.1-Flash EXL3 2.9 bpw (Mia EAQ) | 2 | tf-fork-dsv41 | **SERVABLE-NOW** (Mia's fork image, as today) |
| `deepseek-v41-flash-exl3-4.0` | DeepSeek-V4.1-Flash EXL3 4.0 bpw (to quantize) | 2 | tf-fork-dsv41 | **NEEDS-MORE-HARDWARE: 4 Sparks** |
| `qwen38-27b-exl3-4.0-1x` | Qwen3.8-27B EXL3 4.0 bpw (turboderp @ 387b8445) | 1 | tf-native-1.3.4 | **SERVABLE-WITH-WORK** (stage 1.3.4; installed 1.2.10 is SERVABLE-NOW) |
| `minimax-m31-flash-exl3-3.06-tp2` | MiniMax-M3.1-Flash EXL3 3.06 bpw (Mia EAQ @ c33e7a66) | 2 | tf-native-1.2.10 | **SERVABLE-NOW** (a new architecture on the pair, ~171 GB to fetch) |

Every number in those recipes was read by `init` from the Hub on 2026-11-22
(sizes, layers, heads, experts, quantization fields, licences, the LFS
sha256 of every weight file), or is labelled with where else it came from.

---

## Concepts

### What a recipe is

A recipe is everything between "a model folder on disk" and "an
OpenAI-compatible endpoint that passes conformance on N Sparks" (the
investigation's section 1 names the ten parts: weights pin, engine pin,
patches, image, per-rank inputs, auxiliary weights, drafter, launch config,
template and parsers, acceptance). In beast-recipe a recipe is **data plus
vendored files, never a fork of the pipeline**: the pipeline is shared, the
recipe is what differs per model.

```
recipes/<name>/
  recipe.env                     KEY=VALUE, parsed, never sourced; every value carries its provenance
  SHA256SUMS                     the Hub's LFS sha256 of every weight file (init writes it): offline verification
  beast-recipe-<name>.service    the systemd user unit (D2 launchers; rendered by up --write-profile)
  plan.json                      optional: plan --write
  runs/                          check --bench records, capabilities snapshots (not committed)
scripts/backends/models/<PROFILE>.env   the D2 profile the recipe serves through (rendered, committed)
scripts/backends/models/<PROFILE>.lock  its lock (model-fetch.sh writes it on the head; commit it)
```

**The grammar** is spark.env's and the profiles' (`obprofile.py`), but
stricter: one `KEY=VALUE` a line, one layer of matching quotes, a trailing
`# comment` after an unquoted value. An unknown key, a repeated key, a
malformed value or an `export` line is an error, never a silent skip. Every
value is validated by kind (repo id, 40-hex sha, number, size, JSON object,
absolute path, word), so a checkpoint's config can never write a line into a
recipe that init did not mean to write (a test injects newlines and quotes).
`beast-recipe show --keys` prints the schema.

**Provenance.** The trailing comment of every value says where it came from:

| Tag | Meaning |
|---|---|
| `READ` | read from the checkpoint's own files by `init` (config.json, the quantization config, the safetensors headers, the template, the model card front matter) |
| `HUB` | the Hub API (the resolved commit) |
| `DATA` | `scripts/backends/data/recipe-engines.json` |
| `MEASURED` / `VERIFIED` / `CLAIMED` / `ESTIMATED` | as in the investigation: measured by us, read on a primary source, stated by a README or card, our arithmetic |
| `DEFAULT` | beast-recipe's default |
| `VERIFY` | check on hardware before trusting it (a window size, a parser) |
| `USER` | a choice made by whoever wrote the recipe |

### The pipeline

```
 init ──► recipe.env ──► plan ──► engine ──► stage ──► up ──► check ──► adopt ──► beast-spark NAME
  │                       │        │          │         │       │         │            │
  model_inspect.py        │   engine.sh   model-fetch  D2 /   conform-  beast-spark  the switch,
  model_intake.py         │   docker      weights-     hy4 /  ance.sh   registry     rollback on
  weights_download.py     │   image ls    download     Mia    tokps.py  spark-       failure
  hfapi.py                │               rsync        unit             families
                          └── recipe-engines.json (engines, families, ranks, gaps, drafters, sizing)
```

Nothing is duplicated: `init` is `model_inspect.inspect` plus
`model_intake.quant_format` plus `weights_download.parse_url`; staging is
`model-fetch.sh` / `spark-cluster.sh sync-weights` (D2), `spark-hy4-stage.sh`
(the vLLM Hy4 kit), or `weights-download.sh` + `rsync` + `sha256sum -c`
(everything else); serving is D2 (`spark-cluster.sh start`), the Hy4 kit's
unit, or a Mia recipe's unit; acceptance is `conformance.sh` and the
`tokps.py` stream probe; the switch is `beast-spark`.

### The launchers

| Launcher | Used for | `up` starts | `adopt` |
|---|---|---|---|
| `d2-tensorfold` | native TensorFold (1.2.10 installed, 1.3.4 upstream) | `beast-recipe-<name>.service`: a oneshot user unit around `spark-cluster.sh start --profile <PROFILE> --engine tensorfold` | registry entry: `beast-spark <name>` |
| `d2-vllm` | a generic vLLM (NGC image from spark.env) | the same unit with `--engine vllm` | registry entry |
| `hy4-vllm` | Hy4-flash NVFP4 | `hy4-vllm-inference.service` (the kit in `scripts/backends/hy4-vllm/`) | already selectable: `beast-spark hy4` |
| `mia-unit:<unit>` | a Mia recipe image (DeepSeek's dsv41 fork, the GLM Python image) | that unit | already selectable: `beast-spark ds` / `glm` |

Every unit beast-recipe renders has **no `[Install]` section**: it cannot be
enabled and never starts at boot, exactly like `hy4-vllm-inference`. The boot
selection stays DeepSeek or GLM.

---

## The verdicts

| Verdict | Meaning |
|---|---|
| **SERVABLE-NOW** | an engine we already have (installed, or its image on every Spark) serves this model_type in this format at a rank count that fits on the Sparks given. What is left is steps (fetch, profile, start, check), no engineering |
| **SERVABLE-WITH-WORK** | it fits on the Sparks given, but an engine must be staged, built or extended first. The gap list says what, and how long |
| **NEEDS-MORE-HARDWARE** | no engine path fits on the Sparks given; one does on more. The plan says how many and why, size by size |
| **BLOCKED** | no engine in the matrix reads it at any size, or it does not fit even on 8 Sparks |

How a verdict is reached (`recipe.py evaluate_engine`): for every engine in
the matrix, the family for the checkpoint's `model_type`, then the format,
then the EXL3 variant (bits, codebook, scope), then the rank counts the
family allows (served today, or only "with work": a generator without a
server, a TP split that does not exist yet, a fork branch to build). For each
allowed rank count, the fit (below) and the gaps. The best rank count at or
under the target gives the engine's verdict; the recipe's `ENGINE` (or the
best engine, for `ENGINE=auto`) gives the recipe's. When a pinned engine is
worse than another, the plan says so ("a better path exists").

**Steps are not work.** These gaps are always listed but never change a
verdict: `glue` (profile, launch values, template, conformance), `d2-first-serve`
(D2 has never served on the pair), `tuning`, `drafter`, `perf-patches`,
`parsers`, `licence-review`. Everything else is work (`engine-stage`,
`image-build`, `vllm-image`, `launcher`, `quantize`, `exl3-layout`,
`server-wrap`, `family-tp`, `family`, `topology`, and a family's own extra
gaps).

---

## The fit maths

Per Spark, one rank each (investigation section 4.1):

```
W (weights a rank) = checkpoint GiB / TP x (1 + 0.008 x (TP - 1))      0.008 = the replicated share, calibrated on DeepSeek
rule:   W <= 92 GiB fits, 92-100 tight, > 100 does not fit             (data/spark-families.json "pair", the intake rule)
budget: W + KV pool + engine reserve + OS (6 GiB on the head) <= 121.69 GiB MemTotal, with >= 6 GiB headroom
vLLM:   also W + KV + reserve <= --gpu-memory-utilization x MemTotal
```

The verdict for a size is the worse of the rule and the budget. KV and reserve
come from the recipe, else the family's measured launch values in the data
file (DeepSeek 1.8 GiB / 12.2 GiB; GLM 10 / 9.5; Hy4 vLLM 6 / 5), else an
estimate from `KV_BYTES_PER_TOKEN x CONTEXT x PARALLEL`. A checkpoint part
that streams from disk does not count: Hy4 EXL3's resident size is the 85.38
GiB TensorFold 1.3.2 loads on one GB10, not its 203 GiB of files (Engram).

**Other bit widths.** For an EXL3 recipe the plan projects 3/4/5/6 bpw: the
family's sizing rule when the data has one (DeepSeek `14 + 63.3 x e`, GLM
`11.6 + 38 x e`, both from measured packs), else linear in bpw. A test checks
the maths against every row of the investigation's fit table (DeepSeek 2.9 at
TP2 98.9 GiB, at 4.0 bpw 135 / 91 / 68 at TP 2 / 3 / 4; GLM 82.4 at TP2, 5-bit
102 at TP2 and 68 at TP3; Hy4 NVFP4 77.3 at TP2).

---

## The subcommands

Every command takes `--help` and `--json`. Every acting command (`engine`,
`stage`, `up`, `down`, `check`, `adopt`, `rollback`) prints what it would do
(the default, or `--dry-run`) and acts only with `--run`. `--cluster FILE`
names the cluster (default `scripts/backends/cluster.env`: its
`CLUSTER_NODES`; without it, `local spark-2` for two Sparks, `local` for one,
placeholders past two).

### `init <hf-url | org/repo[@sha] | local-dir> [--sparks N] [--name NAME] [--engine ID] [--force] [--accept-nc]`

Reads metadata only (config.json, generation and tokenizer config, the
quantization config, the safetensors index and each shard's header by a Range
request, the model card's front matter), never the weights. Writes
`recipes/<name>/recipe.env` with every field and its provenance, and
`SHA256SUMS` from the Hub's LFS hashes, then prints the plan.

- URLs take every form `weights-download.sh` takes
  (`https://huggingface.co/ORG/REPO`, `/tree/<rev>`); a sub-folder or a file
  is refused. An unpinned repo resolves `main` to its commit, pins **that**,
  and warns.
- Format: `exl3`, `modelopt-nvfp4`, `modelopt-fp8` / `fp8-block`, `bf16`,
  `mlx-q4g64`, `gguf` (TensorFold's capability vocabulary, from
  `model_intake.quant_format`). BPW: EXL3 `bits` (or `avg_bits`), NVFP4 ~4.5,
  FP8 8, BF16 16.
- Drafter: an in-checkpoint drafter first (`dfly_*` keys: Hy4's DFly2;
  `dspark_*`: DeepSeek's DSpark), then a published DFlash2 for the family with
  a permissive licence (data file), then the model's MTP head, else `none`.
- Licence: the model card's `license:` (and `license_name` for `other`). A
  non-commercial or no-derivatives licence is flagged, and `stage --run`
  refuses it without `--accept-nc`.
- Online: `HF_ENDPOINT`, `HF_TOKEN_FILE`, `OFFLINE` as for `model-inspect.sh`
  (the token never reaches argv, and never another host). Offline: init a
  local directory.

### `plan <recipe> [--sparks 1,2,3,4] [--write]`

The fit per Spark count, the engines that read the model and their verdicts
(best first), the parallelism (TP, expert parallel, the allowed rank counts),
the drafter options with licences, the KV dtype and pool, the licences, the
verdict, the gap list with effort, and the exact command sequence for every
phase. `--sparks 4` retargets; `--sparks 1,2,3,4` only widens the table.
Reads only local files: it works offline anywhere.

### `engine <recipe> [--run [--stage]]`

Native TensorFold: `tensorfold --version` on every Spark, and when the version
is not installed, the offline stage (`scripts/engine.sh stage tensorfold
--version V DIR` on a connected box, `engine.sh install tensorfold --version V
--from DIR` on each Spark; `engine.sh rollback tensorfold` returns to the old
pin) plus a `tensorfold capabilities --json` snapshot to update the matrix
from. Image engines: `docker image inspect IMAGE` on every Spark; a missing
image is pulled or built and carried with `spark-cluster.sh sync-image` (or
`save-image` for an offline carry). `--run` runs the checks only; `--run
--stage` also runs the install and build steps.

### `stage <recipe> [--run] [--only weights,verify,mirror,drafter,quantize,aux,rank-inputs] [--accept-nc]`

- **D2 recipes**: `model-fetch.sh --profile <PROFILE>` on the head (the pinned
  revision and the drafter into `MODELS_DIR`, verified against the Hub, locked),
  then `spark-cluster.sh sync-weights --profile <PROFILE>` to every worker over
  the fabric, verified against the head's lock. A **local** checkpoint
  (`WEIGHTS_STATUS=local`) is verified read-only against `SHA256SUMS` on every
  Spark first, then locked by `model-fetch.sh` on the head (commit the lock), and
  verified against it on every worker.
- **The Hy4 kit**: `spark-hy4-stage.sh --run --only weights,verify,mirror` and
  `--check`.
- **Everything else**: `weights-download.sh <url> <WEIGHTS_DIR>`, `sha256sum -c`
  against the vendored `SHA256SUMS`, `rsync -a --partial` to every peer over the
  QSFP link (never `--delete`), `sha256sum -c` there (the sums go on stdin). A
  local pack without sums is checked with `spark-model-intake.sh` (family, index
  signature, pack fingerprint).
- Every download, copy and checksum runs in `systemd-run --user --scope -p
  MemoryMax=8G -p MemoryHigh=6G`, so its page cache is reclaimed inside the
  scope and never taken from a serving model's `MemAvailable`.
- A `to-quantize` recipe prints the exllamav3 `convert.py` command and the
  intake check that must pass on its output.
- **The kept Hy4 EXL3 pack** (`LibertAIDAI-Hy4-flash-EXL3-4bpw`) is read, never
  written: no download, mirror or delete step is ever generated for a path
  that names it (the same rule as `hy4_protected`).

### `up <recipe> [--run] [--write-profile] [--force] [--key-file F] [--wait S]` and `down <recipe> [--run]`

`up` acts only on a SERVABLE-NOW recipe (`--force` overrides). For D2 it
renders the profile `scripts/backends/models/<PROFILE>.env` (validated by
`obprofile.py`'s own rules: a local SOURCE must be absolute, a Hub SOURCE
carries its 40-hex REVISION, the drafter its own) and the unit
`recipes/<name>/beast-recipe-<name>.service`; `--write-profile` writes both
(commit them: every Spark launches the profile from its own checkout, at the
same commit). `up --run` refuses while any inference unit is active ("one
model at a time on the pair: switch through beast-spark"), installs the unit,
starts it and waits for the served id on `/v1/models`. `down --run` stops the
unit.

### `check <recipe> [--run] [--bench] [--url URL] [--key-file F]`

Refuses unless `/v1/models` lists the recipe's served id, then runs
`conformance.sh --concurrency 4` (the hard gate: chat, stream, tools, tool
results, parallel calls, limits). `--bench`: one warm-up, three single-stream
runs (median) and four streams at once (aggregate completion tokens over the
wall), with `tokps.py`'s `one_run` (temperature 0, a fixed seed, a fixed
prompt rotation), written to `recipes/<name>/runs/bench-*.json`. When the
recipe names a baseline (`BASELINE_FROM`, `BASELINE_TOKS_1/4`) it prints both;
re-measure the baseline with the same command before deciding, since its
published numbers may come from another probe. The key is read from the file,
sent as a header, and never printed.

### `adopt <recipe> [--run] [--write-family]` and `rollback [--run]`

`adopt --run` writes `~/.config/openbeast/recipes.d/<name>.env` (UNIT, SERVED,
BACKEND, WAIT_S; parsed by `beast-spark`, never sourced): after that,
`beast-spark <name>` (or `beast-spark recipe <name>`) switches to it the way
`beast-spark hy4` does: it stops the model that serves (DeepSeek, GLM, Hy4 or
another recipe; their boot enablement stays), starts the recipe's unit, waits
for its served id, sets `INFERENCE_BACKEND` and `INFERENCE_MODEL` in
openbeast.conf, restarts the command center, and on any failure stops it and
brings the previous model back. A successful switch records the previous
selection in `recipes.d/.previous`; `beast-recipe rollback --run` runs
`beast-spark <previous>`. `ds`, `glm`, `up` and `down` stop an adopted recipe
the way they stop Hy4, and point openbeast.conf back at the selected model.
`--write-family` adds the recipe to `data/spark-families.json` so
`spark-model-intake.sh` knows it (commit it). Hy4 and the Mia recipes are
already selectable (`beast-spark hy4 / ds / glm`): adopt says so and writes
nothing.

### `list`, `show <recipe>`, `show --keys`

---

## The engine and family matrix

`scripts/backends/data/recipe-engines.json`, data only. Every engine names its
source and the date it was read.

| Engine | Kind | Status (2026-11-22) | Launcher | Families (format: ranks) |
|---|---|---|---|---|
| `tf-native-1.2.10` | native TensorFold | installed on both Sparks | d2-tensorfold | glm5_next (EXL3 TR3 4-bit only, NVFP4, mixed: 2-8), minimax_m3_vl (EXL3: 2), qwen3_5 (EXL3: 1; MLX: 1-2), qwen4_exp (MLX/ModelOpt: 1-2), qwen3_5_moe, nemotron_h (1). Refuses deepseek_v41, hy_v4_flash |
| `tf-native-1.3.4` | native TensorFold | available upstream (signed release; not on the Sparks) | d2-tensorfold | glm5_next (EXL3 mcg at any whole 1-8 bits: 2-8), minimax_m3_vl (2), qwen3_5 (EXL3 mul1 + mcg: 1), **hy_v4_flash (EXL3: 1, generator only; 2 with work)**, deepseek_v41 (source build only) |
| `tf-fork-dsv41` | TensorFold Zig fork + Mia's 54 patches | image on both Sparks | mia-unit:deepseek-v41-inference | deepseek_v41 (EXL3 mul1: 2; 4 with the tp4 branch) |
| `tf-mia-glm-0.6.0` | TensorFold 0.6.0 Python + Mia's patches | image on both Sparks | mia-unit:spark-hydra-inference | glm5_next (EXL3 TR3: 2, 3) |
| `vllm-hy4` | vLLM hy4-flash + atakann's files | image on both Sparks | hy4-vllm | hy_v4_flash (NVFP4: 2, expert parallel) |
| `vllm-ngc` | NVIDIA vLLM 26.05 (spark.env) | not on the Sparks | d2-vllm | any architecture in `data/vllm.json` (not EXL3/MLX/GGUF); TP must divide the attention (and KV) heads |
| `exllamav3` | ExLlamaV3 1.6.7 | not installed; no launcher | none | one host only: hy_v4_flash (reference attention), glm5_next, qwen3_5, deepseek_v41 |

Also in the file: `hardware.spark` (MemTotal, the 92/100 rule, the replicated
share, the OS and memguard numbers), `hardware.topologies` (1 to 8 Sparks),
`gaps` (the investigation's ranking with effort in days and who does it),
`drafters` per model_type (repo, 40-hex revision, licence), and `sizing`
(the bpw rules and resident sizes).

**How to update it** (an engine moves weekly: 1.2.7 to 1.2.10 took two days):

1. On a Spark, after `engine.sh install`: `tensorfold capabilities --json` (and
   `tensorfold info <model dir>` for a refusal). `beast-recipe engine NAME
   --run --stage` writes the snapshot to `recipes/<name>/runs/`.
2. Edit the engine's `families` (formats, `exl3` variant fields, `ranks`,
   `rank_reason`), `refuses`, `status`, `installed_on` and `checked` (the
   command and the date). A family that only loads (a generator) gets
   `"serves": "generator-only"`; ranks that need engineering go under
   `with_work` with gap ids.
3. `uv run ... pytest tests/test_beast_recipe.py`: the consistency test checks
   every engine and gap reference; the worked-example tests show which verdicts
   moved.
4. Re-run `beast-recipe list` and update this page's tables.

---

## The worked examples

All output below is real: `scripts/beast-recipe.sh plan <name>` on
2026-11-22, on the committed recipes (`list` printed the summary table at the
top). Re-run them after any change to the matrix.

### 1. Hy4-flash NVFP4 on 2 Sparks via vLLM: the served config, as a recipe

`recipes/hy4-flash-nvfp4-vllm-tp2` was made by `init
https://huggingface.co/atakans/Hy4-flash-NVFP4/tree/30f201ac --engine vllm-hy4`.
Init's `SHA256SUMS` is byte-for-byte the hand-vendored
`hy4-vllm/SHA256SUMS.hub-lfs` (a test checks it). The serving section is
`hy4-vllm/config.sh` as data: TP2 + expert parallel, DFly 5,
`max-model-len` 262144, `max-num-seqs` 4, batched 8192, `gpu-memory-utilization`
0.85, KV 6G, the `hy_v4` parsers. **The golden test**
(`test_golden_hy4_vllm_launch_matches_config_sh`) sources `config.sh`, calls
`hy4_serve_args`, and asserts that the flags `recipe.py` renders from this file
are the same list, in the same order, plus the same repo, revision, image, base
digest, served name, port, model folder and GPU fraction. Launcher `hy4-vllm`:
`up` hands over to the kit's unit, `adopt` to `beast-spark hy4`.

```text
VERDICT   SERVABLE-NOW at TP2
          an engine we already have serves it on this many Sparks and it fits: what is left is steps (fetch, profile, start, check), no engineering

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       153.3   does not fit  153.3 + 6.0 + 5.0 + 6.0 = 170.3   -48.7     no   (weights + KV + reserve 164.3 GiB > --gpu-memory-utilization 0.85 x MemTotal)
  2       77.3    fits          77.3 + 6.0 + 5.0 + 6.0 = 94.3    27.4      yes
  3       51.9    fits          51.9 + 6.0 + 5.0 + 6.0 = 68.9    52.8      no
  4       39.3    fits          39.3 + 6.0 + 5.0 + 6.0 = 56.3    65.4      no
```

### 2. Hy4-flash EXL3 4bpw on 2 Sparks via TensorFold: can it beat vLLM?

Max: "a TensorFold recipe for Hy4-flash to see if we can get much better
performance than vLLM". The full plan:

```text
beast-recipe plan hy4-flash-exl3-tensorfold-tp2   (Hy4-flash EXL3 4bpw on native TensorFold, TP2 (plan: beat vLLM))
  model     LibertAIDAI/Hy4-flash-EXL3-4bpw@811866b0   HYV4FlashForCausalLM / hy_v4_flash
  format    exl3 4 bpw, codebook mul1, scope hy4flash-routed-k4   85.4 GiB resident (RESIDENT_BYTES (CLAIMED: TensorFold 1.3.2 CHANGELOG, 85.38 GiB loaded on one GB10; ~118 GiB of Engram streams from disk))
  moe       512 experts, 16 active; 32 attention heads
  target    2 Sparks   engine tf-native-1.3.4 (pinned)   launcher d2-tensorfold

VERDICT   SERVABLE-WITH-WORK at TP2
          it fits on this many Sparks, but an engine must be staged, built or extended first (the gap list says what, and how long)
          upstream: a two-Spark native hy_v4_flash family is in the works upstream (TensorFold#3797 open; #3801 in slices): when it lands, this becomes an engine-stage away
          caveat: never installed or run on our Sparks; D2 has never served on the pair

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       85.4    fits          85.4 + 6.0 + 9.5 + 6.0 = 106.9   14.8      with work
  2       43.0    fits          43.0 + 6.0 + 9.5 + 6.0 = 64.5    57.2      with work
  3       28.9    fits          28.9 + 6.0 + 9.5 + 6.0 = 50.4    71.3      no
  4       21.9    fits          21.9 + 6.0 + 9.5 + 6.0 = 43.4    78.3      no
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 64.0 GiB -> 1: 64.0, 2: 32.3, 3: 21.7, 4: 16.4   (linear in bpw)
    4.0 bpw: 85.4 GiB -> 1: 85.4, 2: 43.0, 3: 28.9, 4: 21.9   (linear in bpw)
    5.0 bpw: 106.7 GiB -> 1: 106.7, 2: 53.8, 3: 36.1, 4: 27.3   (linear in bpw)
    6.0 bpw: 128.1 GiB -> 1: 128.1, 2: 64.5, 3: 43.4, 4: 32.8   (linear in bpw)

Engines  (who reads it, at which sizes; best first)
    exllamav3          not-installed SERVABLE-WITH-WORK at TP1; work: image-build, launcher
  * tf-native-1.3.4    available     SERVABLE-WITH-WORK at TP2; work: engine-stage, family-tp, server-wrap, hy4-generator-qual, hy4-exl3-reader, hy4-dfly, hy4-parsers
    tf-native-1.2.10   installed     no: refuses hy_v4_flash: no registered Zig family serves model_type hy_v4_flash (tensorfold info on the LibertAIDAI pack, spark-1, 1.2.10, 2026-11-19, VERIFIED)
    tf-fork-dsv41      installed     no: no family for model_type hy_v4_flash
    tf-mia-glm-0.6.0   installed     no: no family for model_type hy_v4_flash
    vllm-hy4           installed     no: reads hy_v4_flash only as modelopt-nvfp4; this is exl3
    vllm-ngc           not-installed no: exl3 weights: vLLM cannot load them
  ranks: 1.3.2: Hy4-flash EXL3 4bpw 'loads all 42 layers on one GB10 (85.38 GiB, 29 GiB left)' via the slab upload mode; tf-hy4-cuda-generate runs the text model greedily: a generator, not a server

Parallelism  TP2, PP 1, one rank per Spark; this engine runs it at 1, 2
Drafter      in-checkpoint:dfly
             option: in-checkpoint: DFly2 (0.85B, BF16) inside the checkpoint (model.dfly_layers.*); vLLM --speculative-config method dfly
KV           bf16, 6.0 GiB a rank   (ESTIMATED: Hy4's MLA latent KV is small; values to be measured)
Licences     weights=other (tencent-hy-license)
Nodes        local spark-2   (default (the pair: spark-2 is the worker's ssh alias on the QSFP link))
Baseline     hy4-flash-nvfp4-vllm-tp2: 31.4 tok/s at 1 stream, 77.8 aggregate at 4 (beat both; re-measure the baseline with `beast-recipe check --bench` so both use one probe)

Gaps  (blocking work: 32.0-93.0 engineering days, ESTIMATED)
  WORK  [engine-stage] stage a signed native TensorFold release on every Spark (engine.sh stage on a connected box, install --from on each Spark; rollback keeps the old pin)
        effort: 0.5 day (ESTIMATED); install between models; who: us
  WORK  [server-wrap] the engine has a generator for this family but no server path (tensorfold serve does not register it)
        effort: 1-2 weeks if the generator's forward is reusable (ESTIMATED); who: upstream TensorFold, or us
  WORK  [family-tp] a tensor-parallel (multi-Spark) split for a family the engine runs on one GPU: heads/experts/vocab split, per-rank inputs, NCCL or RoCE collectives, drafter hooks
        effort: 4-12 weeks for a strong Zig/CUDA engineer; 0 if upstream lands it first; who: upstream TensorFold or a fork author; us only if the model is decisive
  WORK  [hy4-generator-qual] qualify the one-GB10 generator first: tf-hy4-cuda-generate on the LibertAIDAI pack on ONE Spark (the pair down), greedy output against the vLLM build on the same prompts; its single-stream decode is the floor a TP2 serve must beat
        effort: one 0.5-day pair-down window; who: us, on hardware
  WORK  [hy4-exl3-reader] the pack's layout is scope hy4flash-routed-k4, codebook mul1, serving_reader_qualified false: the family's reader must accept exactly that (or the pack is re-quantized to what it reads)
        effort: part of the family work; intake checks it; who: upstream / us
  WORK  [hy4-dfly] DFly2 drafting (in the checkpoint, dfly_* config) inside the TensorFold family: without it decode runs ~15 tok/s plain (CLAIMED) against vLLM's 31.4 with DFly 5
        effort: 1-3 weeks inside the family work, or copy drafts as a stopgap; who: upstream / us
  WORK  [hy4-parsers] hy_v4 tool-call and reasoning parsing in TensorFold's server (vLLM has hy_v4 parsers; conformance is the gate)
        effort: 1-5 days; who: us
  step  [d2-first-serve] D2 (spark-cluster.sh + spark-node.sh) has never served on the pair: the first native serve qualifies it
        effort: one pair-down window of 0.5-1 day; who: us, on hardware
  step  [glue] recipe glue for an architecture the engine already has: profile, launch values, fabric, template, conformance
        effort: 0.5-1 day per model; the first one 1-2 days; who: us, today
  step  [licence-review] a custom or restricted licence: read it before staging or redistributing anything derived
        effort: hours (Max decides); who: Max; weights=other (tencent-hy-license)
  step  [tuning] memory and launch tuning: KV dtype and pool, MEMORY_RESERVE_GIB, context, streams, MIN_AVAIL, from the engine's start line and MemAvailable under load on every rank
        effort: 0.5 day per model; who: us
  step  [hy4-engram-local] Engram tables (~118 GiB of the 203 GiB pack) stay on each Spark's local NVMe and are streamed, never over NFS: both Sparks already hold the pack
        effort: none (staged on both Sparks); who: -

End to end  (each command prints what it would do; add --run to act)
  engine:  beast-recipe engine hy4-flash-exl3-tensorfold-tp2
      [local] ~/.local/bin/tensorfold --version   # want tensorfold-native 1.3.4
      [spark-2] ~/.local/bin/tensorfold --version   # want tensorfold-native 1.3.4
      [connected box] scripts/engine.sh stage tensorfold --version 1.3.4 ~/carry/tensorfold-1.3.4   # signed archive + MANIFEST, for the offline Sparks
      [local] scripts/engine.sh install tensorfold --version 1.3.4 --from ~/carry/tensorfold-1.3.4   # online: drop --from; rollback: scripts/engine.sh rollback tensorfold
      [spark-2] scripts/engine.sh install tensorfold --version 1.3.4 --from ~/carry/tensorfold-1.3.4   # online: drop --from; rollback: scripts/engine.sh rollback tensorfold
      [local] ~/.local/bin/tensorfold capabilities --json > recipes/hy4-flash-exl3-tensorfold-tp2/runs/capabilities-1.3.4.json   # then update scripts/backends/data/recipe-engines.json from it
  stage:  beast-recipe stage hy4-flash-exl3-tensorfold-tp2
      [local] (verify) cd /home/max/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw && systemd-run --user --scope -p MemoryMax=8G -p MemoryHigh=6G sha256sum -c --quiet -   # < recipes/hy4-flash-exl3-tensorfold-tp2/SHA256SUMS; read-only: the Hub's LFS hashes, before the lock trusts these bytes
      [spark-2] (verify) cd /home/max/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw && systemd-run --user --scope -p MemoryMax=8G -p MemoryHigh=6G sha256sum -c --quiet -   # < recipes/hy4-flash-exl3-tensorfold-tp2/SHA256SUMS from the head; read-only
      [local] (weights) scripts/backends/model-fetch.sh --profile recipe-hy4-flash-exl3-tensorfold-tp2   # a local SOURCE: records models/recipe-hy4-flash-exl3-tensorfold-tp2.lock on the first run (commit it), verifies against it after; reads the folder, never writes it
      [spark-2] (verify) cd ~/openbeast && scripts/backends/model-fetch.sh --profile recipe-hy4-flash-exl3-tensorfold-tp2 --verify   # the worker's copy against the committed lock
      [local] (mirror) # a worker that lacks the copy: scripts/backends/spark-cluster.sh sync-weights --profile recipe-hy4-flash-exl3-tensorfold-tp2 (never onto a kept pack)
      [local] (aux) # AUX: Engram tables (~118 GiB) inside the pack, streamed from each Spark local NVMe, never over NFS
  up:  beast-recipe up hy4-flash-exl3-tensorfold-tp2
      [local] beast-recipe up hy4-flash-exl3-tensorfold-tp2 --write-profile   # renders scripts/backends/models/recipe-hy4-flash-exl3-tensorfold-tp2.env; commit it (every Spark launches the profile from its own checkout, at the same commit)
      [local] scripts/backends/spark-cluster.sh check   # 2 node(s), fabric, same checkout
      [local] scripts/backends/spark-cluster.sh start --profile recipe-hy4-flash-exl3-tensorfold-tp2 --engine tensorfold --dry-run   # the exact rank commands
      [local] install -m 0644 recipes/hy4-flash-exl3-tensorfold-tp2/beast-recipe-hy4-flash-exl3-tensorfold-tp2.service ~/.config/systemd/user/beast-recipe-hy4-flash-exl3-tensorfold-tp2.service && systemctl --user daemon-reload
      [local] systemctl --user start beast-recipe-hy4-flash-exl3-tensorfold-tp2.service   # through beast-spark hy4-flash-exl3-tensorfold-tp2 on the pair (adopt first)
  check:  beast-recipe check hy4-flash-exl3-tensorfold-tp2
      scripts/backends/conformance.sh --url http://127.0.0.1:8000 --model Hy4-flash-EXL3-4bpw --backend tensorfold --key-file ~/.config/openbeast/tensorfold-api-key --concurrency 4
      beast-recipe check hy4-flash-exl3-tensorfold-tp2 --run --bench   # decode tok/s at 1 stream (median of 3) and 4 streams aggregate, same probe for every recipe (scripts/tokps.py one_run)
  adopt:  beast-recipe adopt hy4-flash-exl3-tensorfold-tp2
      beast-recipe adopt hy4-flash-exl3-tensorfold-tp2 --run   # writes ~/.config/openbeast/recipes.d/hy4-flash-exl3-tensorfold-tp2.env (beast-spark's registry)
      beast-spark hy4-flash-exl3-tensorfold-tp2   # the switch: stops the model that serves, starts beast-recipe-hy4-flash-exl3-tensorfold-tp2.service, waits for Hy4-flash-EXL3-4bpw, INFERENCE_BACKEND/INFERENCE_MODEL in openbeast.conf, restarts the command center
  rollback:  beast-recipe rollback
      beast-recipe rollback --run   # beast-spark <the previous selection> (ds, glm, hy4 or a recipe)
```

**Reading it.**

- **It fits easily.** Resident, the pack is 85.38 GiB (TensorFold 1.3.2
  loads all 42 layers on one GB10; the other ~118 GiB of the 203 GiB pack is
  Engram, streamed from local disk). At TP2 that is 43 GiB a rank, against
  77.3 for the NVFP4 build under vLLM: room for a much bigger KV pool or more
  streams. Memory is not the problem.
- **The engine is.** Upstream 1.3.2+ has a hy_v4_flash generator on one GB10
  (`tf-hy4-cuda-generate`, greedy), not a server, and no two-Spark split.
  The work, in order, with what each buys:
  1. `engine-stage`: stage 1.3.4 on both Sparks (0.5 day; `engine.sh rollback`
     undoes it). Nothing serves native TensorFold today, so it disturbs nothing.
  2. `hy4-generator-qual`: one 0.5-day pair-down window. Run
     `tf-hy4-cuda-generate` on spark-2 against the kept pack; compare its greedy
     output with the vLLM build on the same prompts, and time its single-stream
     decode. That number is the floor: if one GB10 already decodes near 31 tok/s
     without drafting, TP2 with DFly has a real chance; if it is near 15, the
     drafter and the TP split must carry everything.
  3. `server-wrap` (1-2 weeks) and `family-tp` (4-12 weeks): the serving path
     and the two-Spark split. Both are upstream's work in flight (TensorFold#3797,
     #3801 "in slices"). If upstream lands them, they cost us an engine stage.
     If we write them, this is a Zig/CUDA project, done only if the measurement
     in step 2 says the prize is worth it.
  4. `hy4-exl3-reader`: the pack says `scope hy4flash-routed-k4`, `codebook
     mul1`, `serving_reader_qualified false`. The family's reader must accept
     exactly that layout, or the pack is re-quantized to what it reads.
  5. `hy4-dfly` (1-3 weeks): DFly2 drafting inside the family. vLLM's 31.4
     tok/s is with DFly 5; plain decode is ~15 (CLAIMED). Copy drafts are the
     stopgap.
  6. `hy4-parsers` (1-5 days): hy_v4 tool and reasoning parsing in TensorFold's
     server; conformance is the gate.
- **How to measure against the vLLM baseline** (31.4 tok/s single stream, 77.8
  aggregate at 4, `docs/SPARK_HYDRA.md`):
  1. Re-measure the baseline with the same probe first: `beast-spark hy4`, then
     `beast-recipe check hy4-flash-nvfp4-vllm-tp2 --run --bench` (1 stream
     median of 3, 4 streams aggregate, temperature 0, `tokps.py` prompts). The
     published 31.4 / 77.8 came from 512-token code prompts through the gate,
     so they are not the same probe.
  2. The candidate under the same command: `beast-recipe check
     hy4-flash-exl3-tensorfold-tp2 --run --bench`, same window (262144), same
     streams (4), thinking at the same level.
  3. Conformance must pass on both, and the v5 arm H_T2 must not regress
     (60/71 on vLLM; `MODEL_CAMPAIGN.md`). The win condition: faster at 1 **and**
     at 4 streams, at equal conformance and v5 score.
- The licence is the Tencent Hy licence (`other`): `licence-review` is listed
  (we already serve the NVFP4 build of the same weights).

### 3. GLM-5.3-Flash EXL3 4bpw on native TensorFold 1.2.10, our own launcher

Candidate 1 of the investigation. No Mia image: D2 serves the profile
`recipe-glm53-flash-exl3-native-tp2` (rendered and committed), on the Ablit
pack both Sparks already hold. The profile's lock carries the existing GLM
profile's file hashes. The drafter is re-pinned to
`incoai/GLM-5.3-Flash-DFlash2@6cc6c481`, the 2026-11-10 Apache-2.0 release
with the same `model.safetensors` blob as the CC BY-NC-ND pin `bf582e4e`.

```text
VERDICT   SERVABLE-NOW at TP2
          an engine we already have serves it on this many Sparks and it fits: what is left is steps (fetch, profile, start, check), no engineering
          caveat: D2 has never served on the pair (SPARK_HYDRA D2 'VERIFY ON HARDWARE')

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       163.6   does not fit  163.6 + 10.0 + 9.5 + 6.0 = 189.1   -67.4     no
  2       82.4    fits          82.4 + 10.0 + 9.5 + 6.0 = 107.9   13.7      yes
  3       55.4    fits          55.4 + 10.0 + 9.5 + 6.0 = 80.9    40.8      yes
  4       41.9    fits          41.9 + 10.0 + 9.5 + 6.0 = 67.4    54.3      yes
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 125.6 GiB -> 1: 125.6, 2: 63.3, 3: 42.5, 4: 32.2   (sizing rule)
    4.0 bpw: 163.6 GiB -> 1: 163.6, 2: 82.5, 3: 55.4, 4: 41.9   (sizing rule)
    5.0 bpw: 201.6 GiB -> 1: 201.6, 2: 101.6, 3: 68.3, 4: 51.6   (sizing rule)
    6.0 bpw: 239.6 GiB -> 1: 239.6, 2: 120.8, 3: 81.1, 4: 61.3   (sizing rule)

Engines  (who reads it, at which sizes; best first)
  * tf-native-1.2.10   installed     SERVABLE-NOW at TP2
    tf-mia-glm-0.6.0   installed     SERVABLE-NOW at TP2
    tf-native-1.3.4    available     SERVABLE-WITH-WORK at TP2; work: engine-stage
    tf-fork-dsv41      installed     no: no family for model_type glm5_next
    vllm-hy4           installed     no: no family for model_type glm5_next
    vllm-ngc           not-installed no: exl3 weights: vLLM cannot load them
    exllamav3          not-installed BLOCKED: allowed at 1, but it does not fit at any of them
  ranks: TF_GLM_RANK / TF_GLM_WORLD place 2 to 16 ranks (WORLD 1 is for a thin test model only)

Gaps  (blocking work: 0-0 engineering days, ESTIMATED)
  step  [d2-first-serve] D2 (spark-cluster.sh + spark-node.sh) has never served on the pair: the first native serve qualifies it
        effort: one pair-down window of 0.5-1 day; who: us, on hardware
  step  [glue] recipe glue for an architecture the engine already has: profile, launch values, fabric, template, conformance
        effort: 0.5-1 day per model; the first one 1-2 days; who: us, today
  step  [tuning] memory and launch tuning: KV dtype and pool, MEMORY_RESERVE_GIB, context, streams, MIN_AVAIL, from the engine's start line and MemAvailable under load on every rank
        effort: 0.5 day per model; who: us
```

The rendered profile and unit:

```text
# Rendered by beast-recipe from recipes/glm53-flash-exl3-native-tp2/recipe.env.
# Edit the recipe, not this file: beast-recipe up --write-profile renders it again.
BACKEND=tensorfold
SOURCE=/home/max/openbeast-models/glm53-flash-ablit-exl3
SERVED_MODEL_NAME=glm-5.3-flash-ablit
TENSOR_PARALLEL_SIZE=2
MAX_MODEL_LEN=245760
TENSORFOLD_PARALLEL=4
DRAFTER_SOURCE=incoai/GLM-5.3-Flash-DFlash2
DRAFTER_REVISION=6cc6c481eee930a5139619ed919f78726371adbc
```

### 4. DeepSeek-V4.1-Flash at 2.9 bpw (the default) and at 4.0 bpw

At 2.9 bpw, the pack that serves today: SERVABLE-NOW on Mia's fork image
(the only engine that serves `deepseek_v41` on a GB10), handed over to
`beast-spark ds`.

```text
VERDICT   SERVABLE-NOW at TP2
          an engine we already have serves it on this many Sparks and it fits: what is left is steps (fetch, profile, start, check), no engineering
          upstream: TP4: branch deepseek-v41-zig-tp4 @ 5940208 (input tools) and Mia's 4-Spark recipe; a 4-ring or a switch

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       196.1   does not fit  196.1 + 1.8 + 12.2 + 6.0 = 216.1   -94.4     no
  2       98.9    tight         98.9 + 1.8 + 12.2 + 6.0 = 118.9   2.8       yes
  3       66.4    fits          66.4 + 1.8 + 12.2 + 6.0 = 86.4    35.3      no
  4       50.2    fits          50.2 + 1.8 + 12.2 + 6.0 = 70.2    51.5      with work
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 203.9 GiB -> 1: 203.9, 2: 102.8, 3: 69.1, 4: 52.2   (sizing rule)
    4.0 bpw: 267.2 GiB -> 1: 267.2, 2: 134.7, 3: 90.5, 4: 68.4   (sizing rule)
    5.0 bpw: 330.5 GiB -> 1: 330.5, 2: 166.6, 3: 111.9, 4: 84.6   (sizing rule)
    6.0 bpw: 393.8 GiB -> 1: 393.8, 2: 198.5, 3: 133.4, 4: 100.8   (sizing rule)
```

At 4.0 bpw (`recipes/deepseek-v41-flash-exl3-4.0`, hand-written: no such pack
is published), the answer the investigation gave, now computed:

```text
VERDICT   NEEDS-MORE-HARDWARE at TP4
          no engine path fits on this many Sparks; one does on more (the plan says how many and why)
          - 1 Spark: 267.2 GiB a rank (does not fit), and tf-fork-dsv41 does not run this family at 1 rank
          - 2 Sparks: 134.7 GiB of weights a rank (does not fit; headroom -33.0 GiB)
          - 3 Sparks: 90.5 GiB a rank (fits), and tf-fork-dsv41 does not run this family at 3 ranks
          - 4 Sparks: 68.4 GiB a rank, fits (SERVABLE-WITH-WORK)
          upstream: TP4: branch deepseek-v41-zig-tp4 @ 5940208 (input tools) and Mia's 4-Spark recipe; a 4-ring or a switch

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       267.2   does not fit  267.2 + 1.8 + 12.2 + 6.0 = 287.2   -165.5    no
  2       134.7   does not fit  134.7 + 1.8 + 12.2 + 6.0 = 154.7   -33.0     yes
  3       90.5    fits          90.5 + 1.8 + 12.2 + 6.0 = 110.5   11.2      no
  4       68.4    fits          68.4 + 1.8 + 12.2 + 6.0 = 88.4    33.3      with work
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 203.9 GiB -> 1: 203.9, 2: 102.8, 3: 69.1, 4: 52.2   (sizing rule)
    4.0 bpw: 267.2 GiB -> 1: 267.2, 2: 134.7, 3: 90.5, 4: 68.4   (sizing rule)
    5.0 bpw: 330.5 GiB -> 1: 330.5, 2: 166.6, 3: 111.9, 4: 84.6   (sizing rule)
    6.0 bpw: 393.8 GiB -> 1: 393.8, 2: 198.5, 3: 133.4, 4: 100.8   (sizing rule)

Engines  (who reads it, at which sizes; best first)
    tf-native-1.3.4    available     NEEDS-MORE-HARDWARE at TP4; work: engine-stage, quantize, image-build
  * tf-fork-dsv41      installed     NEEDS-MORE-HARDWARE at TP4; work: quantize, image-build, topology
    tf-native-1.2.10   installed     no: refuses deepseek_v41: no registered Zig family serves model_type deepseek_v41 (tensorfold info, spark-1, 1.2.10, 2026-11-19, VERIFIED)
    tf-mia-glm-0.6.0   installed     no: no family for model_type deepseek_v41
    vllm-hy4           installed     no: no family for model_type deepseek_v41
    vllm-ngc           not-installed no: exl3 weights: vLLM cannot load them
    exllamav3          not-installed BLOCKED: allowed at 1, but it does not fit at any of them
  ranks: this image is the TP2 build; the fork's rank cache and plan take WORLD 1, 2 or 4 and 64 heads do not divide by 3 (DeepSeek recipe docs/zig-engine.md, VERIFIED)

Gaps  (blocking work: 2.5-6 engineering days, ESTIMATED)
  WORK  [image-build] build the recipe image from pinned parts (base by digest + vendored files, content-hashed tag) and carry it to every Spark
        effort: 0.5-1 day (the Hy4 precedent); who: us
  WORK  [topology] cabling and fabric for more than 2 Sparks: the 3-triangle / 4-ring is not in spark-cluster.sh check yet (NCCL_IB_SUBNET_AWARE_ROUTING, LAN rendezvous)
        effort: 1-2 days + the hardware; who: us, on hardware
  WORK  [quantize] our own EXL3 quantization (exllamav3 convert.py) in the exact layout the engine's reader requires
        effort: 1-3 days per 300-550B MoE on one Spark, mostly wall time; a quantizing Spark serves nothing; who: us, with a free Spark; from deepseek-ai/DeepSeek-V4.1-Flash-1017@1bae0e10 (FP8 + MXFP4, ~476 GB)
  step  [glue] recipe glue for an architecture the engine already has: profile, launch values, fabric, template, conformance
        effort: 0.5-1 day per model; the first one 1-2 days; who: us, today
  step  [tuning] memory and launch tuning: KV dtype and pool, MEMORY_RESERVE_GIB, context, streams, MIN_AVAIL, from the engine's start line and MemAvailable under load on every rank
        effort: 0.5 day per model; who: us
```

**Four Sparks, not three**: 64 attention heads and the fork's WORLD 1/2/4 rule
skip 3, even though 90.5 GiB a rank would fit at TP3. On four, the plan's work
is quantizing the pack (1-3 days on one Spark), building the tp4 image, and
cabling the 4-ring. The LibertAI 3.5-bpw pack already exists for four
(119 GiB a rank at TP2, 60 at TP4).

### 5. Qwen3.8-27B EXL3 4.0 bpw on ONE Spark, TensorFold 1.3.4

```text
VERDICT   SERVABLE-WITH-WORK at TP1
          it fits on this many Sparks, but an engine must be staged, built or extended first (the gap list says what, and how long)
          note: a better path exists: tf-native-1.2.10 is SERVABLE-NOW at TP1
          caveat: never installed or run on our Sparks; D2 has never served on the pair

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       15.2    fits          15.2 + 16.0 + 6.5 + 6.0 = 43.7    78.0      yes
  2       7.7     fits          7.7 + 16.0 + 6.5 + 6.0 = 36.2    85.5      no
  3       5.2     fits          5.2 + 16.0 + 6.5 + 6.0 = 33.7    88.0      no
  4       3.9     fits          3.9 + 16.0 + 6.5 + 6.0 = 32.4    89.3      no
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 11.4 GiB -> 1: 11.4, 2: 5.8, 3: 3.9, 4: 2.9   (linear in bpw)
    4.0 bpw: 15.2 GiB -> 1: 15.2, 2: 7.7, 3: 5.2, 4: 3.9   (linear in bpw)
    5.0 bpw: 19.0 GiB -> 1: 19.0, 2: 9.6, 3: 6.4, 4: 4.9   (linear in bpw)
    6.0 bpw: 22.8 GiB -> 1: 22.8, 2: 11.5, 3: 7.7, 4: 5.8   (linear in bpw)

Engines  (who reads it, at which sizes; best first)
    tf-native-1.2.10   installed     SERVABLE-NOW at TP1
  * tf-native-1.3.4    available     SERVABLE-WITH-WORK at TP1; work: engine-stage
    exllamav3          not-installed SERVABLE-WITH-WORK at TP1; work: image-build, launcher
    tf-fork-dsv41      installed     no: no family for model_type qwen3_5
    tf-mia-glm-0.6.0   installed     no: no family for model_type qwen3_5
    vllm-hy4           installed     no: no family for model_type qwen3_5
    vllm-ngc           not-installed no: exl3 weights: vLLM cannot load them
  ranks: EXL3 on ONE GPU
```

Pinned to 1.3.4 on purpose (Max), and the plan says the honest thing: the
installed 1.2.10 already reads this `mul1` pack at TP1, so it is servable now
on 1.2.10. On the pair, one Spark means the pair's model is down (or a third
Spark). The drafter is `z-lab/Qwen3.8-27B-DFlash2@50307d4c` (Apache-2.0).

### 6. MiniMax-M3.1-Flash EXL3 3.06 bpw on 2 Sparks

```text
VERDICT   SERVABLE-NOW at TP2
          an engine we already have serves it on this many Sparks and it fits: what is left is steps (fetch, profile, start, check), no engineering
          caveat: D2 has never served on the pair (SPARK_HYDRA D2 'VERIFY ON HARDWARE')

Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom)
  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows
  1       159.2   does not fit  159.2 + 8.0 + 9.5 + 6.0 = 182.7   -61.0     no
  2       80.3    fits          80.3 + 8.0 + 9.5 + 6.0 = 103.8   17.9      yes
  3       53.9    fits          53.9 + 8.0 + 9.5 + 6.0 = 77.4    44.3      no
  4       40.8    fits          40.8 + 8.0 + 9.5 + 6.0 = 64.3    57.4      no
  other bpw (projected checkpoint GiB -> per rank):
    3.0 bpw: 156.1 GiB -> 1: 156.1, 2: 78.7, 3: 52.9, 4: 40.0   (linear in bpw)
    4.0 bpw: 208.2 GiB -> 1: 208.2, 2: 104.9, 3: 70.5, 4: 53.3   (linear in bpw)
    5.0 bpw: 260.2 GiB -> 1: 260.2, 2: 131.1, 3: 88.1, 4: 66.6   (linear in bpw)
    6.0 bpw: 312.2 GiB -> 1: 312.2, 2: 157.4, 3: 105.7, 4: 79.9   (linear in bpw)
```

Candidate 3 of the investigation, and the first new architecture on the pair
built entirely by OpenBeast. The real pack is 159.2 GiB (init read it; the
investigation estimated ~152): 80.3 GiB a rank, exactly two ranks. Its card has
no `license:` field (`LICENCES=weights=unknown`, VERIFY before staging); the
repo ships custom code (`auto_map`), which native TensorFold does not run.
Init suggested the `hermes` tool parser from the template; the recipe sets
`minimax_m2` (MiniMax's own `<minimax:tool_call>` format) as VERIFY, and
conformance decides.

---

## Hardware guidance

From the investigation (section 4), as the plan applies it:

- **1 Spark** holds up to ~92 GiB of weights with useful KV room: anything up to
  ~30B at 8 bpw, ~100-120B at 4-6 bpw, ~177B (Qwen3.8-Flash-Next) at 3-4 bpw.
  On the pair today a TP1 model means the pair's model is down.
- **2 Sparks** (today): every Mia recipe, Hy4, and every recipe above.
- **3 Sparks**: a cabled triangle. Helps GLM (TP3 is measured; 5-6 bit GLM
  fits) and vLLM models whose heads divide by 3. Buys memory, not speed. Does
  **not** buy a higher-quant DeepSeek.
- **4 Sparks**: a cabled ring (GLM 1104 measured it) or a switch. Buys
  DeepSeek above 2.9 bpw (3.5 exists; 4-5 to quantize), and GLM FP8.
- **5-8**: a 200/400 GbE RoCE switch.
- **A Spark beside the RTX 5090**: not worth it (a TP group runs at its
  smallest member). The rig and the pair stay separate nodes in one route.

`spark-cluster.sh check` does not know the 3-triangle or the 4-ring yet (gap
`topology`). Everything past 2 Sparks is VERIFY ON HARDWARE.

## Licences

The recipe's `LICENCES` line comes from the model card (`init`) and the plan
lists `licence-review` for anything `other`, unknown, non-commercial or
no-derivatives. `stage --run` refuses a non-commercial or no-derivatives
artefact without `--accept-nc`. What the investigation found (section 6):
Mia's TensorFold recipes, their patches, TensorFold itself, the dsv41 fork,
ExLlamaV3 and the DeepSeek and GLM weights are Apache-2.0 or MIT; the GLM
DFlash2 drafter is Apache-2.0 from `6cc6c481` (the recipes pin it); Hy4's
weights carry the Tencent Hy licence; Mia's **vLLM** GLM recipe is AGPL-3.0
and nothing may be copied from it. A recipe that derives from a Mia recipe
(copies a script or a patch) keeps Mia's NOTICE and CREDITS and says what
changed; a recipe that only pins a Mia image redistributes nothing.

## Offline

- `plan`, `show`, `list` and every dry run read only the checkout: offline
  anywhere, on the Sparks' system python3 (3.9+), the rig or the Mac.
- `init` reads a local directory offline; against the Hub it is read-only and
  honours `OFFLINE=true` (refuses), `HF_ENDPOINT` (a mirror), `HF_TOKEN_FILE`.
- Weights: `SHA256SUMS` is vendored with the recipe, so `stage` verifies a
  carried copy (a USB SSD, a mirror) with no network. D2 profiles verify
  against their committed lock.
- Engines: `engine.sh stage tensorfold --version V DIR` on a connected box,
  carry the directory, `engine.sh install ... --from DIR` on each Spark (signed
  and hashed). Images: `spark-cluster.sh save-image IMAGE FILE` and `docker
  load` on the closed side.
- Everything between the Sparks runs over the QSFP link.

## What is deferred to hardware day

v1 is built and tested off-hardware; these are the first things to run on the
pair (each in a pair-down window, after the live evals):

1. `beast-recipe up glm53-flash-exl3-native-tp2 --run` through `adopt` +
   `beast-spark glm53-flash-exl3-native-tp2`: **the first D2 serve on the
   pair**. Expect to tune `CONTEXT`, `KV_POOL_GIB` and `MEMORY_RESERVE_GIB` from
   the start line. The profile's lock is the existing GLM profile's, so
   `model-fetch.sh` verifies the folder against it rather than trusting it.
2. `beast-recipe engine hy4-flash-exl3-tensorfold-tp2 --run --stage`: stage
   1.3.4, snapshot `capabilities --json`, update the matrix.
3. The Hy4 generator qualification (example 2, step 2) and the vLLM baseline
   re-measured with `check --bench`.
4. `stage` then `up` for MiniMax and Qwen (downloads of ~171 GB and ~16 GB).
5. Not built in v1: a generic vLLM pair launcher with a per-recipe image (D2's
   vLLM path takes one `VLLM_IMAGE` per host), a launcher for a new Mia-recipe
   configuration (DeepSeek TP4), the TabbyAPI/ExLlamaV3 launcher, image builds
   from a recipe's own `image/` directory (the Hy4 kit does its own), and
   `check` against the eval campaign (`MODEL_CAMPAIGN.md` stays the
   qualification).

## Files

| File | What |
|---|---|
| `scripts/beast-recipe.sh` | the entry point (wraps `scripts/backends/beast-recipe.sh`) |
| `scripts/backends/pylib/recipe.py` | the core (stdlib, python 3.9+) |
| `scripts/backends/data/recipe-engines.json` | the engine and family matrix, gaps, drafters, sizing |
| `recipes/<name>/` | the recipes |
| `scripts/backends/models/recipe-*.env` / `.lock` | the D2 profiles the recipes render |
| `scripts/backends/spark-beast.sh` | `beast-spark <recipe>`, the registry in `~/.config/openbeast/recipes.d/` |
| `tests/test_beast_recipe.py` | the grammar, the fit maths, engine selection, the verdicts, the golden Hy4 launch, init through the stub Hub, dry runs and `--run` against stubs |
| `tests/test_spark_beast.sh` | the recipe switch, its rollback on failure, the registry's hygiene |
