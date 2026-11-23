# spark-hydra: the DGX Spark launch runbook

**Live deployment, 2026-11-18.** The pair serves **DeepSeek-V4.1-Flash (Mia EXL3)** by default since 2026-11-18 (`DeepSeek-V4.1-Flash-1017`, unit `deepseek-v41-inference`, enabled for boot; 524,288-token window, four streams): Max's decision after hands-on testing, with v5 61/71, the board's #1 (65/71 under arm T2 since 2026-11-19, its default agent setup: [E2b](#e2b-deepseek-best-configuration-v5-t2); the preset `AGENT_PROFILE=deepseek-best` is on `main` since #232 and live in spark-1's `openbeast.conf` since 2026-11-19, E2b). **GLM-5.3-Flash is staged as the alternative** (`GLM-5.3-Flash-EXL3`, unit `spark-hydra-inference`, 1,048,576-token window): `beastglm` switches to it, `beastds` back (it stops GLM, re-selects the Mia EXL3 pack, [DeepSeek packs](#deepseek-packs-spark-deepseek-pack), and runs `activate-stock.sh`). Switch only through `beast-spark` (the `beast*` aliases, [Everyday shortcuts](#quickstart)); `activate-stock.sh` and `restore-glm.sh` are its internals, and `activate-stock.sh` never stops GLM itself. `beastup` with nothing selected picks DeepSeek. The pair serves these two models, one at a time; **Hy4-flash (NVFP4, vLLM TP2) is staged on both Sparks and measured** (`beasthy4`, never a boot default; v5 60/71 under arm H_T2, 5 units behind DeepSeek's T2 on the same harness, single seed; [Hy4-flash](#hy4-flash-nvfp4-vllm-tp2-measured)). The command center and beast-gate run on spark-1. The DeepSeek sections below record its deployment and describe the serving default; the GLM recipe, tuning measurements and rebuild examples describe the alternative, which the fresh-install checklist still builds first. (History: GLM served 2026-11-13 to 11-17, DeepSeek from 11-17, GLM again for part of 11-18, then DeepSeek by Max's decision.)

This page takes you from bare boxes to a serving pair, start to finish, and then
covers day-2 operations and fully offline operation. Read it top to bottom the
first time. After that, jump to the section you need, or start at the
[Quickstart](#quickstart). Historical design background is in
[`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md) (§8a: the stack on a Spark; §14:
onboarding a model; §15: why GLM-5.3-Flash); where it and this page differ,
this page is current. Engine choice is in
[`ENGINES.md`](ENGINES.md). For the local model working on OpenBeast itself,
see [`SELF_DEVELOPMENT.md`](SELF_DEVELOPMENT.md).

**How to read the labels.** **Measured** means it ran on our pair and the
result is recorded here. **VERIFY ON HARDWARE** means the code exists and is
tested against stubs, but has never run on a real Spark. Every command on this
page exists in the code on `main` (`feat/spark-scale-1-8`, where it started,
is merged into it). Flags were checked against each script's `--help` on
2026-11-15, and again for the two checklists on 2026-11-16. **Units:** GB is
10^9 bytes (disk, files, copies), GiB is 2^30 bytes (memory: MemTotal, per-rank
weights, KV pools); a 106 GB rank file is 98.8 GiB of memory.

## DeepSeek deployment

On spark-1, the deployed runtime checkout lives at `~/openbeast-recipes/deepseek-prep/runtime`. The canary, conformance records and rollback helper sit under `~/openbeast-recipes/deepseek-prep/`, and its user unit is under `~/.config/systemd/user/`. These paths are host-local deployment artifacts.

The November 17 cutover used Mia's [two-Spark recipe at c7f2b85](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/tree/c7f2b85100f0a7b099c92a4e21825e30d93c9c51). Both hosts ran the same locally built image, `tensorfold-dsv41:exl3-tp2-8a98ae503d85`, with one tensor-parallel rank per Spark over two RoCE rails. The advertised context was 524,288 tokens with four streams.

| Disk material | Verified at cutover |
|---|---|
| Mia EXL3 EAQ v1, 2.9 bpw | 39 shards at revision `45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b`. All 64 manifest entries passed on spark-1. |
| Engram data | Base revision `1bae0e1088993b9b82de08f59de5be5a34e42170`, shards 47 and 48, totaling 203,073,077,576 bytes. Verified on both hosts. |
| Prepared rank caches | About 106 GB per rank. Both were prepared on the head after stopping GLM, then rank 1 was copied to spark-2. Each host loads its own rank cache. |
| Documents backup | spark-2 received 389,659,303,295 logical bytes from spark-1. The copy was verified with rsync checksums and metadata. A Documents backup and a prepared rank cache serve different purposes. |

**Acceptance.** The gate accepted an enrolled Mac device at `:8443`, forwarding to the authenticated backend on spark-1 loopback `:8000`. In OpenCode, `openbeast-rig/DeepSeek-V4.1-Flash-1017` answered 17 × 23 with 391. The conformance run passed chat, streaming, reasoning-field extraction, structured and parallel tool calls, tool results and output-token limits. Four concurrent requests completed in 1.5 seconds. The record on spark-1 is `~/openbeast-recipes/deepseek-prep/conformance-4/latest.json`. Context overflow was skipped. Cold boot without WAN and a v5 capability score are still open.

**Local interactive baseline, November 17.** From the Mac through the authenticated tailnet gate, six short temperature-zero prose probes requested 256 completion tokens each. With thinking on (including one request using the gate default), single-stream decode was 70.1–71.7 tok/s, all reasoning tokens. With `chat_template_kwargs.enable_thinking=false`, it was 53.7–63.2 tok/s, median 60.7, all visible answer tokens. The median first streamed token arrived in 232 ms with thinking and 156 ms without. Four simultaneous thinking-off requests delivered 1,024 completion tokens in 8.74 s, or 117.1 tok/s aggregate end to end. Decode rates use tokens after the first divided by time from first to last content-bearing chunk. Use thinking for multi-step work and disable it per request for quick answers. This short-prompt test does not establish long-context performance or a quality-optimal setting.

**Switching on this pair.** Exactly one of the two inference units is enabled. Since 2026-11-18 that is DeepSeek's `deepseek-v41-inference` (the default), and GLM's `spark-hydra-inference` is disabled (the staged alternative). Switch with `beast-spark` only ([Everyday shortcuts](#quickstart)): `beastglm` runs `rollback/restore-glm.sh` (stops and disables DeepSeek, GLM's serving configuration, enables and starts GLM); `beastds` stops GLM, re-selects the Mia EXL3 pack and runs `activate-stock.sh` (starts DeepSeek, canary, conformance, enables DeepSeek, disables GLM, restarts the command center). Both helpers are internals: **`activate-stock.sh` never stops GLM**, so never run it while GLM is active. By hand, without beast-spark: `systemctl --user stop spark-hydra-inference && bash ~/openbeast-recipes/deepseek-prep/activate-stock.sh`. Then run `openbeast-client use spark-hydra` on each client to refresh its model; it probes the rig's `/api/slot` and `/v1/models` (discovery and the model list, not a completion). `openbeast-client autosync`, which `setup-client.sh` installs by default, does the refresh every 30 s.

### DeepSeek packs: spark-deepseek-pack

A *pack* is any EXL3 quantization of DeepSeek-V4.1-Flash-1017 with the same tensor layout as Mia's EAQ v1 2.9 bpw build: the same `model.safetensors.index.json` keys and `config.json` architectures. Engram's tables (base shards 47 and 48) are shared by every pack. `scripts/backends/spark-deepseek-pack.sh` swaps the served pack offline, verifies it, and rolls back on failure. **It is a pack swap, not a model switch: `use` is valid only while DeepSeek is selected and serving, so run `beastds` first.** While GLM's `spark-hydra-inference` is active, `use` refuses and names `beastds`; `--stop-glm` stops GLM explicitly for one swap (the boot selection and the command center then still point at GLM until `beastds`). `--dry-run` works either way. `spark-beast.sh install` puts it on spark-1 next to the other helpers, together with `spark-deepseek-prepare-local.sh` (the generalized form of `prepare-stock-local.sh`):

```bash
# On spark-1, from a checkout (the live ~/openbeast, or a hub clone, offline): beast-spark + both pack tools + aliases
~/openbeast/scripts/backends/spark-beast.sh install
# or from the Mac, by hand
scp scripts/backends/spark-deepseek-pack.sh scripts/backends/spark-deepseek-prepare-local.sh \
    spark-1:openbeast-recipes/deepseek-prep/

# On spark-1
beastds                                                         # DeepSeek first (a no-op when it already serves)
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh list                                   # serving pack, staged packs, previous
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --dry-run  # checks + plan, changes nothing
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --bench    # swap, verify, A/B bench
./spark-deepseek-pack.sh use stock                              # back to Mia EAQ v1 @ 45ee5d0f
```

`beastds` re-selects its configured pack every time, DeepSeek-V4.1-Flash (Mia EXL3) by default, so a pack adopted with `use` is replaced at the next GLM → DeepSeek round trip unless `~/.config/openbeast/beast-spark.env` names it (`DS_PACK=~/Documents/<pack-folder>` and `DS_ID=ORG/NAME`, the ID `list` shows; parsed, never sourced). `use` and `beast-spark` share the lock `${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock`: a second switch is refused while one runs.

To bring in a new pack, copy its folder into `~/Documents` on spark-1, for example from a drive. Offline, the script does the rest:

1. It refuses a folder whose tensor index or architecture differs from the serving pack's. Such a model is not a drop-in and needs its own recipe.
2. It mirrors the folder to spark-2 at the same path, which is the backup convention. Shards shared with the serving pack become hard links.
3. It hard-links the folder into `$HF_CACHE/hub` on both hosts as `models--ORG--NAME/snapshots/REV`. A local pack has no Hub revision, so REV is the sha1 of its shards' `size name` lines, and the same folder always gets the same REV. If a cache snapshot already has that REV, its name is reused. Otherwise the ID is `local/<folder>`, or whatever `--id ORG/NAME` sets. Two different packs can have one size listing, so the REV is never trusted alone: before anything is copied, every snapshot with that REV must match the folder's content fingerprint (sha256 over each shard's name, size, first and last MiB; cached in the folder as `.pack-fingerprint`), and staging refuses a snapshot directory that already holds a file that is not the folder's own (another inode or size) instead of silently keeping it.
4. It rewrites the managed block in `runtime/scripts/local.sh` (`MODEL_ID`, `MODEL_REVISION`, `WORKER_CACHE_DIR`, `MODEL_SRC`) and keeps the old file as `local.sh.prev`.
5. It stops the unit, then builds both rank files on the head. Each is about 106 GB and takes about 15 minutes the first time, seconds afterwards. Rank 1 goes to spark-2. The unit then restarts, which took about 40 s.
6. It waits until `/v1/models` lists `DeepSeek-V4.1-Flash-1017`, then runs `verify-chat.py` (17 × 23 = 391) and `conformance.sh`, each logged whole to a file before its verdict is read. On any failure it restores `local.sh.prev`, re-prepares the previous pack (seconds, since its files are kept) and restarts it. Ctrl-C (or TERM, HUP) during steps 4 to 6 restores `local.sh.prev` too. `use` of the pack that is already selected is a no-op only when the recipe's prepared marker matches it; otherwise it prepares it again.
7. With `--bench`, it runs `tools/bench.py` (prefill 2K/8K/32K, decode, 1/2/4 streams, thinking off) into `runtime/runs/packs/REV8.json` and compares it with the previous pack's run.

**Rank-1 cache per revision (a trap).** The engine names its rank files after the tensor *index*, not the weights. Two packs with one layout therefore both produce `rank1of2-f4b9c7e9bd4f6316d1b4.bin`. With a shared `WORKER_CACHE_DIR`, prepare would find the old pack's rank-1 file "ready" and the pair would serve mixed weights. The script therefore uses `worker-rank-cache-REV8`. The stock pack keeps its original `worker-rank-cache`.

The served name stays `DeepSeek-V4.1-Flash-1017`, so the gate, the command center and enrolled clients need no change. `beastds` (GLM → DeepSeek) stops GLM, re-selects its configured pack, then `activate-stock.sh` brings up whatever pack `local.sh` selects. Disk cost per pack is its folder, plus about 212 GB of rank files on spark-1 and about 106 GB on spark-2. Old rank files are never deleted automatically.

**Run record, 2026-11-17: EAQ v1.1 "-v2".** Max brought the folder `~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2` from a drive. It is Mia's experimental EAQ v1.1 build: 39 shards, with shards 1–10 and 35–39 identical to EAQ v1 and 11–34 rewritten. Its config, index, tokenizer, template and README are byte-identical to v1's. Its `SHA256SUMS` is v1's file carried over, so it fails on the 24 new shards and cannot verify them either way. The cache ID is `Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2` at local REV `04c13f12b83d57487912d87d0f1677b076810954`. It loaded in 40 s and passed the canary and conformance (`conformance-v2/` on spark-1). The round trip v2 → `use stock` → v2 through the script also passed both times. Bench medians, thinking off, the same session:

| Metric | EAQ v1 (45ee5d0f) | EAQ v1.1 -v2 (04c13f12) |
|---|---|---|
| Decode, 1 stream | 59.6 / 60.2 tok/s | 60.6 / 62.3 tok/s |
| 2 streams aggregate | 86.6 / 85.6 tok/s | 90.2 / 90.8 tok/s |
| 4 streams aggregate | 120.4 / 121.7 tok/s | 118.4 / 120.5 tok/s |
| Prefill 8K | 2,331 / 2,296 tok/s | 2,384 / 2,376 tok/s |
| Prefill 32K | 2,419 / 2,274 tok/s | 2,434 / 2,436 tok/s |
| Errors | 0 | 0 |

Each cell gives two runs. The differences are inside run-to-run noise. A first v1 run measured 2K prefill at 1,322 tok/s, but that was cold-server warm-up: the later v1 run gave 1,990 against v2's 2,035–2,049. The verdict is a drop-in replacement, and v2 is DeepSeek's pack: DeepSeek-V4.1-Flash (Mia EXL3), the pair's default model since 2026-11-18 (v5 61/71, 65/71 under arm T2; GLM-5.3-Flash is the staged alternative). A v1-vs-v2 quality A/B (the v5 eval on both packs) has not been run. **Every v5 board run of DeepSeek ran on -v2** (the journal's `Ready: … EAQ-v1.1-2.9-bpw-v2 @ 04c13f12` on each start from 2026-11-17 23:07; the stock pack last served before 22:59 that night), recorded as `weights_id` `dsv41f-mia-exl3-2.9bpw-eaq1.1-v2` in `evals/boards/v5/weights.json` ([RESULTS.md, Weights used](RESULTS.md#weights-used)). The Hub's `Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw` (@ `aee640e2`, "Experimental v1.1 pack") is a different build: its shard sizes and index differ from this folder's, so never cite it as these weights. A new pack that goes on the board gets its own `weights.json` entry and its own row ([MODEL_CAMPAIGN.md 7a](MODEL_CAMPAIGN.md#7a-the-board-row-and-its-weights)).

**Other EXL3 models.** For other packs and other EXL3 models brought offline, including GLM packs, new architectures and the read-only `spark-model-intake.sh` preflight, see [EXL3_PLAYBOOK.md](EXL3_PLAYBOOK.md). Whether a model can be served on the pair at all, with which engine and on how many Sparks, and the commands that apply it: [BEAST_RECIPE.md](BEAST_RECIPE.md) (`scripts/beast-recipe.sh plan`).

## Hy4-flash (NVFP4, vLLM TP2): measured

**State, 2026-11-22: staged on both Sparks, served and measured.** The
weights, the image and the unit are in place on the pair (the steps below);
`beasthy4` served it from 2026-11-21 22:29 PST for the v5 arms. Measured:
conformance PASS, the speed below, and v5 arm H_T2 60/71 (SCORE 83.4,
board row HY4-T2; [the report](reports/V5_HY4_FLASH-2026-11-22.md)) and the
ablation H_C 55/71 (SCORE 82.2, row HY4-C). The unit served without a
restart until 2026-11-22 12:11 PST, after H_C; the pair is back on DeepSeek. (State on 2026-11-19: the code on `main`, nothing on the Sparks;
staging waited for the U arms, because serving Hy4 means stopping DeepSeek,
and because the docker steps run outside any memory scope.) Hy4 is **never a boot default**: its unit cannot be
enabled, `beastup` never picks it, and a reboot comes back to the selection
(DeepSeek). It is here to be measured (v5 arm `H_T2`, a public-interest
measurement) and to be tried by hand. Why this build and not another: the
review [HY4_FLASH_ENGINES-2026-11-19](reviews/HY4_FLASH_ENGINES-2026-11-19.md).

**The EXL3 pack stays, on purpose.** `~/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw`
(218 GB, on both Sparks) is kept (Max, 2026-11-19). It cannot serve on the pair
(ExLlamaV3's TP workers are local to one host; [EXL3_PLAYBOOK.md](EXL3_PLAYBOOK.md)
case (c) worked example), but nothing deletes it: every path the Hy4 scripts
write is checked against it (`hy4_protected` in `scripts/backends/hy4-vllm/config.sh`),
no Hy4 script deletes anything (no `rm -r`, no `rsync --delete`, no image prune;
`tests/test_spark_hy4.sh` checks both), and `spark-model-intake.sh` on that
folder says NOT SERVABLE HERE with the reason.

### Measured, 2026-11-21/22

| What | Result | Conditions |
|---|---|---|
| First start | the unit started 22:29:52, vLLM ready ("Starting vLLM server") 22:39:14: 9.4 min, the first start's autotune included | 2026-11-21, spark-1 journal (`hy4-vllm-inference`) |
| Conformance | **PASS**: chat, stream, tools, tool_result, parallel, max_tokens, overflow. WARN: the reasoning arrives in `reasoning`, not `reasoning_content` | 2026-11-22 06:40 UTC; spark-1 `~/openbeast/.run/conformance/conformance-20261122T064050Z-Hy4-flash-NVFP4.json` |
| Decode, 1 stream | **31.4 tok/s** | through the gate from the Mac, 2026-11-21 ~22:40 PST; 512-token code prompts, thinking at the default "high", temperature 0; DFly 5 |
| Decode, 4 streams | **77.8 tok/s** aggregate (~20 a stream) | the same |
| v5, arm H_T2 | **60/71, SCORE 83.4** (#2 on the board); 0 lost; integrity scan clean | 2026-11-21 22:41 to 11-22 04:46 PST (6.1 h), `--jobs 4`, harness9 (f38a29c7), the Mac harness host |
| v5, arm H_C | **55/71, SCORE 82.2** (row HY4-C, the ablation); 0 lost; integrity scan clean | GLM's arm C on the same tree, 2026-11-22 04:46 to 12:10 PST (7.4 h), `--jobs 4` |

The speed rows are from a different probe than DeepSeek's table above
(prose, 256 tokens), so they are not directly comparable. The arm's wall
clock was about DeepSeek T2's (6.2 h): Hy4 takes more turns per unit with
fewer completion tokens each. The served engine's start line confirms the
recipe: `max_model_len` 262144, TP2 + expert parallel, 4 sequences,
`kv_cache_memory_bytes` 6 GiB, DFly with 5 speculative tokens,
`modelopt_fp4`, the `hy_v4` reasoning and tool parsers.


This config is also the recipe `recipes/hy4-flash-nvfp4-vllm-tp2`
([BEAST_RECIPE.md](BEAST_RECIPE.md)); a test asserts that the vLLM flags
rendered from the recipe equal `hy4_serve_args`. Its TensorFold
challenger is planned in `recipes/hy4-flash-exl3-tensorfold-tp2`.

### What it is (pins)

| Piece | Pin | Source (checked 2026-11-19) |
|---|---|---|
| Weights | `atakans/Hy4-flash-NVFP4` @ `30f201acf0b4f9ba178817bcbc04ccfa3ec473f9`: 76 files, 164,691,130,443 bytes, 14 shards (164,654,390,950 B of weights), 254,168 tensor keys (index sha256 `d3cd2ba4…`) | Hub API. `main` is one commit ahead (`922cbf11`, model card only); 30f201ac is the revision the recipe pins. Experts NVFP4 (ModelOpt 0.48.0, group 16), Engram tables FP8, the DFly2 drafter BF16; architecture `HYV4FlashForConditionalGeneration` (vision included) |
| Hub checksums | `scripts/backends/hy4-vllm/SHA256SUMS.hub-lfs` (the 20 LFS files' sha256) and `FILES.hub` (every file's size and git blob id) | vendored from the Hub tree API at 30f201ac, so verification needs no network |
| Base image | `vllm/vllm-openai@sha256:5e2c1badbca60595d6591c60feb4b8547f59da96e7a91710d40239250e721aba` (the arm64 image of tag `hy4-flash`; tag index `sha256:6b645bfb…`, 11.0 GB compressed) | Docker Hub. vLLM 77766db with the Hy4-flash pull request, FlashInfer 0.7.4 (CLAIMED by the recipe) |
| Recipe | `atakann/dgx-spark-recipes` @ `e764efa3efed3e21fe543c3fffc927eb6a00bff8`, `recipes/llm/hy4-flash-nvfp4-vllm-tp2.yaml` and `images/vllm-hy4-flash/` | GitHub. MIT; the six Python files are Apache-2.0 (vLLM). Vendored verbatim in `scripts/backends/hy4-vllm/image/` with `CREDITS.md` (each file's sha256) |
| Our image | `openbeast/hy4-vllm:e764efa-b486c74ef8b6` (the suffix is `hy4_image_hash` of `image/`) | built by `spark-hy4-stage.sh`; the test fails if `image/` changes and the tag does not |
| Upstream | [vllm-project/vllm#68380](https://github.com/vllm-project/vllm/issues/68380) (open): the same sm_121 + ModelOpt NVFP4 changes | when it lands in a release, the six files can go |

The six files: `sm120.py` sends Hy4's per-head attention sink through
FlashInfer's SM120 sparse-MLA kernel; `attention.py` and `hy_v4_attention.py`
pick that backend on compute capability 12 for the model's and the drafter's
layers; `mla_attention.py` adds the `fp8_ds_mla` KV cache on sliding-window
MLA; `model.py` loads ModelOpt NVFP4 experts; `dfly_config.py` lets DFly take
an NVFP4 target. Without them the image's Hy4 code takes FP8 only and stops on
a GPU that is not Hopper or data-center Blackwell.

### Files

| File | Where it runs | Does |
|---|---|---|
| `scripts/backends/spark-hy4-stage.sh` | spark-1, from a checkout | stages everything (default `--dry-run`), `--check`, `install` |
| `scripts/backends/hy4-vllm/config.sh` | sourced | the pins, the settings (`~/.config/openbeast/hy4-vllm.env`, `HY4_*=value`, parsed, never sourced; the environment wins), link discovery, the memory rule |
| `scripts/backends/hy4-vllm/start.sh` | spark-1 (the unit) | checks, then rank 1 on spark-2 over ssh, then rank 0 here; foreground: exits 1 when a rank dies on its own (the unit shows `failed`), 0 when stop.sh or SIGTERM ends them |
| `scripts/backends/hy4-vllm/stop.sh` | spark-1 (the unit's ExecStop) | writes the stop mark (`HY4_STOP_MARK`, `~/.cache/hy4-vllm/.stopping`), then stops and removes both containers; idempotent |
| `scripts/backends/hy4-vllm/memguard.sh` | both Sparks, beside each rank | kills the rank's container before the Spark runs out of memory |
| `scripts/backends/hy4-vllm/hy4-vllm-inference.service` | `~/.config/systemd/user/` on spark-1 | the user unit: no `[Install]` (cannot be enabled), `Restart=no`, `Conflicts=` both other inference units. A deliberate stop (`beast-spark ds` / `glm` / `up` / `down`, `systemctl --user stop`) leaves it `inactive`, not `failed`: no `systemctl --user reset-failed` needed (before 2026-11-23 it did need one). A crash still shows `failed` |
| `scripts/backends/spark-beast.sh` | `~/.local/bin/beast-spark` | `beast-spark hy4` (`beasthy4`), and `ds` / `glm` / `up` / `down` stop Hy4 |

### Stage (after the eval series; on spark-1)

```bash
cd ~/openbeast && git pull --ff-only              # a main with this section
scripts/backends/spark-hy4-stage.sh               # the plan + what is staged (writes nothing)

# 1. weights: hf download (capped scope), verify against the Hub's sizes and LFS sha256, mirror to spark-2
#    (rsync over the QSFP link, capped on both ends, never --delete), verify there. Safe while DeepSeek serves:
#    every process runs in systemd-run --user --scope -p MemoryMax=8G -p MemoryHigh=6G, so the page cache
#    it fills is reclaimed inside the scope, not taken from DeepSeek's MemAvailable (its memguard: 2.5 GiB).
#    A stall watchdog restarts a crawling hf download (seen at ~1 MB/s for hours; a restart was fast at once,
#    and hf resumes): under HY4_STALL_MB=50 MB on disk in HY4_STALL_SECS=180 s, it is killed and started
#    again, up to HY4_STALL_RESTARTS=5 times, each restart logged ("weights: stalled: ... restart 1 of 5").
scripts/backends/spark-hy4-stage.sh --run --only weights,verify,mirror

# 2. image: pull the base by digest, build, save the tar, load it on spark-2. The docker daemon is outside any
#    scope, so these refuse while an inference unit is active; run them between models (beastdown first), or
#    accept the risk with --while-serving.
scripts/backends/spark-hy4-stage.sh --run --only image,save,ship

# 3. the runtime copy (~/openbeast-recipes/hy4-vllm) and the unit; daemon-reload; NOTHING is enabled
scripts/backends/spark-hy4-stage.sh install
scripts/backends/spark-beast.sh install           # beast-spark with `hy4`, and the beasthy4 alias
scripts/backends/spark-hy4-stage.sh --check       # exit 0 only when every piece is in place on both Sparks
```

Every step is resumable and skips what is already done (a verified folder,
an image that is present, a tar whose MANIFEST names the image id, a worker
that has the same image id). What lands where:

| Where | What | Size |
|---|---|---|
| both Sparks, `~/Documents/atakans-Hy4-flash-NVFP4/` | the 76 files, `SHA256SUMS.hub-lfs`, `.hy4-verified` (revision, date, host, the sums' sha256) | 165 GB each |
| spark-1, `~/Documents/hy4-vllm-image/` | `openbeast_hy4-vllm_e764efa-b486c74ef8b6.tar` + `.sha256` + `MANIFEST` (image id, base digest and index, recipe commit) | about the image's size (UNVERIFIED: the base is 11 GB compressed) |
| both Sparks, docker | `vllm/vllm-openai@sha256:5e2c1bad…` (spark-1 only) and `openbeast/hy4-vllm:e764efa-b486c74ef8b6` | |
| both Sparks, `~/.cache/hy4-vllm/b486c74ef8b6/` | the first start's Triton / FlashInfer / vLLM compile and autotune cache (the image's `HOME=/cache`), and `memguard.log` | filled on the first start |
| spark-1 | `~/openbeast-recipes/hy4-vllm/` (start, stop, config, memguard, the Hub lists) and `~/.config/systemd/user/hy4-vllm-inference.service` | |

Disk on 2026-11-19: 2.2 TB free on spark-1, 2.3 TB on spark-2.

**Offline.** Weights from a drive: `--weights-from /media/drive/atakans-Hy4-flash-NVFP4`
(the same verification against the vendored Hub sums). The image from the
saved tar: `--image-from ~/Documents/hy4-vllm-image/<tag>.tar` (checked
against its `.sha256`), then `--only ship`. To carry it to a rebuilt pair,
copy `~/Documents/hy4-vllm-image/` and the weights folder (the Documents
backup carries both). Nothing in `start.sh` reaches beyond the link
(`HF_HUB_OFFLINE=1` in both containers).

### Bring-up

```bash
beasthy4                     # = ~/.local/bin/beast-spark hy4
beaststatus                  # the hy4 row, what :8000 serves; "the selection stays ds"
beastds                      # back to DeepSeek (or beastglm); a reboot also comes back to DeepSeek
```

`beast-spark hy4` holds the model-switch lock, stops whichever of
`deepseek-v41-inference` / `spark-hydra-inference` runs (their enablement
stays: DeepSeek stays the boot selection), starts `hy4-vllm-inference`, waits
up to 2,400 s (`SB_HY4_WAIT_S`) until `/v1/models` on `127.0.0.1:8000` lists
`Hy4-flash-NVFP4`, then sets `INFERENCE_BACKEND=vllm` (the command center's
readiness probe for vLLM: its `/health` is an empty 200, TensorFold's body
says ok) and `INFERENCE_MODEL="Hy4-flash-NVFP4"` in `~/openbeast/openbeast.conf`
(`LLAMA_API_KEY` stays: the server takes the same `tensorfold-api-key` the gate
already sends) and restarts the command center. If any of that fails, its
trap stops Hy4, puts both keys back if they were changed, and starts the model
that served before, waiting for its id. `beastds`, `beastglm`, `beastup` and
`beastdown` stop Hy4 and, while `openbeast.conf` still names Hy4, point it
back at the selected model (`INFERENCE_BACKEND=tensorfold`, its id; `beastup`
then restarts the command center to read it); `activate-stock.sh` and
`restore-glm.sh` rewrite `INFERENCE_MODEL` for their model, as before. `AGENT_PROFILE=deepseek-best` stays in
`openbeast.conf` while Hy4 serves: it is arm T2's switch set, which is also
H_T2's, so the daily agent runs Hy4 the way the arm measures it.

What `start.sh` does, in order (refusing before anything starts):

1. The weights are verified at 30f201ac on both Sparks (`.hy4-verified`), the
   image is present on both, the key file is readable.
2. Nothing listens on `:8000` (`ss`).
3. The QSFP link on each Spark: the netdev and source address of the route to
   the peer (`ip -4 route get 10.100.216.2` on the head, `… 10.100.216.1` on
   the worker, over ssh), that netdev's RoCE device first, then every other Up
   RoCE device (the second rail). Today: head `enp1s0f0np0` 10.100.216.1,
   `rocep1s0f0,roceP2p1s0f0`; worker `enp1s0f1np1` 10.100.216.2,
   `rocep1s0f1,roceP2p1s0f1` (the worker's cables sit on port f1).
4. Memory: each Spark must have `HY4_MIN_AVAIL_GIB` available, `auto` =
   ceil(0.85 x MemTotal) + 2 = **106 GiB** on a 121.7 GiB Spark (what vLLM asks
   for at start with `--gpu-memory-utilization 0.85`, plus 2), waiting up to
   180 s for a just-stopped model's memory to come back.
5. Stale `hy4-vllm` containers removed on both, then rank 1 on spark-2
   (headless), then rank 0 here, each with `--memory 112g` (the recipe's cgroup
   cap), `--shm-size 16g`, `/dev/infiniband`, `IPC_LOCK`, the weights read-only
   at `/model`, the cache at `/cache`.
6. memguard on both Sparks (`HY4_MEMGUARD_GIB` 2.5, hold 4 s, hard floor 1 GiB:
   the DeepSeek recipe's levels), each exits when its container ends.
7. Foreground: rank 0's log goes to the journal; when either rank ends, the
   other is stopped and the unit fails (`Restart=no`: an experimental start is
   not retried in a loop).

The flags, from the recipe (the same on both ranks, plus the rank's own):

```text
vllm serve /model --served-model-name Hy4-flash-NVFP4
  --tensor-parallel-size 2 --enable-expert-parallel --dtype bfloat16
  --max-model-len 262144 --max-num-seqs 4 --max-num-batched-tokens 8192
  --kv-cache-memory-bytes 6G --gpu-memory-utilization 0.85
  --speculative-config {"method":"dfly","num_speculative_tokens":5}      # HY4_SPEC; HY4_SPEC=off without the drafter
  --limit-mm-per-prompt {"image":1,"video":0}
  --reasoning-parser hy_v4 --tool-call-parser hy_v4 --enable-auto-tool-choice
  --nnodes 2 --master-addr 10.100.216.1 --master-port 29501
  rank 1: --node-rank 1 --headless          rank 0: --node-rank 0 --host 127.0.0.1 --port 8000
env (each rank its own link): NCCL_SOCKET_IFNAME / GLOO_SOCKET_IFNAME / TP_SOCKET_IFNAME = the netdev,
  NCCL_IB_HCA = both rails, NCCL_IB_GID_INDEX=3 (as the DeepSeek recipe), NCCL_IB_DISABLE=0,
  NCCL_IGNORE_CPU_AFFINITY=1, VLLM_HOST_IP = the link address, HF_HUB_OFFLINE=1, FLASHINFER_WORKSPACE_BASE=/cache,
  MAX_JOBS=4; rank 0 also VLLM_API_KEY (by name: docker copies it from start.sh's environment, never argv)
```

`scripts/backends/hy4-vllm/start.sh --print` prints both docker commands
with the real links and memory, and starts nothing (safe to run while
DeepSeek serves). The link netdevs run MTU 9000 on both Sparks (read 2026-11-19); RoCE is
unaffected by spark-guard (RDMA bypasses netfilter).

**spark-guard.** No new port. The rendezvous is vLLM's 29501 (+1), which the
guard already keeps on the fabric; vLLM's other sockets bind `VLLM_HOST_IP`,
the link address, which the guard's fabric-address rule covers. Without
`VLLM_HOST_IP` vLLM would pick the default route's address (the LAN), so
`start.sh` always sets it (`tests/test_spark_guard.sh` checks both).

### Memory and fit

| | Per Spark | Status |
|---|---|---|
| Weights | 77.3 GiB of safetensors per rank by our rule (164.65 GB / 2 x 1.008); the recipe reports ~82 GiB resident | computed; CLAIMED |
| KV cache | 6 GiB fixed (`--kv-cache-memory-bytes 6G`): 578k tokens, 262,144 a request | CLAIMED |
| Start needs | 106 GiB available on each Spark (`auto`) | our check |
| Lowest free while serving | 12.8 GiB (one user, the 6G KV cache) | CLAIMED |
| cgroup cap | 112 GiB per container | the recipe's |

Against DeepSeek's 98.8 GiB of weights a rank with ~6 GiB left on the head,
Hy4 is the roomier fit. The command center keeps running beside rank 0 on
spark-1 as it does with DeepSeek (UNVERIFIED with Hy4: the recipe's 12.8 GiB
was measured without it). Speeds (CLAIMED, the recipe, one user, thinking
off): 17-38 tok/s decode with DFly 5 (about 15 without), prefill 1,110 tok/s
at 4K and 1,461 at 32K; weights load in 104 s; the first start autotunes for
about 9 minutes (ready 9.7 minutes after the setup), later starts reuse
`~/.cache/hy4-vllm/<image hash>/`.

### Thinking

Hy4's chat template takes `reasoning_effort` (`high`, `max` or `no_think`) in
`chat_template_kwargs` and **ignores `enable_thinking`**. When a request names
no `reasoning_effort`, the template uses `high`, and vLLM's `hy_v4` reasoning
parser defaults to the same `high` (it must agree with the template, or the
whole reply would land in the reasoning field). So:

- thinking ON works with no change: the eval runner's `enable_thinking=true`
  (and the gate's `INFERENCE_THINKING=on` default) reach Hy4 as
  `reasoning_effort` high. That is what arm `H_T2` measures.
- thinking OFF does **not**: `enable_thinking=false` is ignored too, so Hy4
  still thinks. A client that wants it off sends
  `"chat_template_kwargs": {"reasoning_effort": "no_think"}`. The eval runner
  cannot (it names only `enable_thinking`, in `agents/runner.py`, an
  era-hashed file), so the N / N0 arms and the U arms' forced-act turn are not
  Hy4 arms until that changes; a server-side mapping in the template would
  not do it either, because the reasoning parser decides from the request.

### UNVERIFIED until the first real start

**Resolved by the first start and the run (2026-11-21/22):** the image
built and the recipe's flags started on this pair with our two-netdev
layout and both rails; the first start took 9.4 minutes to ready; the
start check passed and memguard did not fire through the H_T2 run (spark-1's `~/.cache/hy4-vllm/memguard.log` holds only its start line, read 2026-11-22, and the container has not restarted); tool
calls and the reasoning split pass conformance (above). Still open: the
autotune cache's reuse on a second start, the tar's size and load time,
and the low point of free memory beside the command center (not recorded).
The list as written before the first start:

- The image builds on spark-1 and the recipe's flags start on this pair, with
  this vLLM build and our two-netdev layout (the recipe ran on its author's
  pair).
- NCCL over both rails with GID index 3 and the worker on port f1 (the
  DeepSeek recipe runs the same NCCL settings, but on TensorFold).
- The first start's autotune time and that the cache under
  `~/.cache/hy4-vllm/` is reused (the image runs as root: the cache files are
  root-owned).
- The 106 GiB start check against what vLLM actually allocates; memguard's
  2.5 GiB floor against vLLM's real low point beside the command center.
- The tar's size, and `docker save | load` time over the link.
- Tool calls and the reasoning split through `hy_v4` with the gate and
  OpenCode: after the first start, `scripts/backends/conformance.sh --url
  http://127.0.0.1:8000 --model Hy4-flash-NVFP4 --backend vllm --key-file
  ~/.config/openbeast/tensorfold-api-key`.
- Whether `VLLM_HOST_IP` and `--master-addr` on the link are all vLLM needs
  for its multi-node sockets in this build.

### Run H_T2

After the U series, with Hy4 serving (`beasthy4`), from the Mac as for DS-T2:

```bash
python3 evals/campaign.py run --campaign v5-thinking-first --only H_T2 \
    --model-name "Hy4-flash (NVFP4)" --served-model Hy4-flash-NVFP4 \
    --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env OPENBEAST_SPARK_API_KEY \
    --jobs 4 --slots 4
```

`H_T2` is arm T2 exactly (same switches, the same declared 8,192 budget),
`optional`, jobs 4 / slots 4, and declares `served_model: Hy4-flash-NVFP4`:
its preflight refuses to run while the server serves anything else (a Hy4
row measured on DeepSeek would look valid). Compare its row with DS-T2
(65/71) under the same harness. Then `beastds`. **Ran 2026-11-21 22:41 to
11-22 04:46 (6.1 h; the 8-12 h estimate was high), from the harness9
worktree with `--arm-tree H_T2=<the harness9 checkout>` (the campaign's
`--tree` was a newer checkout, for H_C's definition): 60/71, board row
HY4-T2.** H_C followed in the same `campaign.py run --only H_T2,H_C`.

## Quickstart

Three paths. Every command below is from the runbook body; the linked section
has the detail. All run from the Mac unless marked "on spark-1".

**Everyday shortcuts: beast-spark.** On spark-1, `~/.local/bin/beast-spark` (from `scripts/backends/spark-beast.sh`) and its bash aliases cover the daily operations:

| Alias | Command | Does |
|---|---|---|
| `beastup` | `beast-spark up` | Stops the non-selected model's unit if it runs, starts the selected one, waits until `/v1/models` lists that model's id, then starts the command center |
| `beastdown` | `beast-spark down` | Stops the command center and both inference units. The boot selection stays as it is |
| `beastglm` | `beast-spark glm` | Switches to GLM-5.3-Flash, the staged alternative, through `rollback/restore-glm.sh`, waits for `GLM-5.3-Flash-EXL3` |
| `beastds` | `beast-spark ds` | Switches to DeepSeek-V4.1-Flash (Mia EXL3), the default (or the `DS_PACK` / `DS_ID` in `~/.config/openbeast/beast-spark.env`): stops GLM, re-selects that pack with `spark-deepseek-pack.sh use … --id …` if another pack is selected, then runs `activate-stock.sh` (canary, conformance, command center, boot selection), and waits for `DeepSeek-V4.1-Flash-1017`. If anything fails and GLM was serving when it began, it runs `restore-glm.sh` and waits for GLM |
| `beasthy4` | `beast-spark hy4` | Serves Hy4-flash (NVFP4, vLLM TP2) for now, never as the selection: stops DeepSeek or GLM (their enablement stays), starts `hy4-vllm-inference`, waits for `Hy4-flash-NVFP4`, sets `INFERENCE_BACKEND=vllm` and `INFERENCE_MODEL`, restarts the command center; the model that served before comes back on failure ([Hy4-flash](#hy4-flash-nvfp4-vllm-tp2-measured)). `beastds` / `beastglm` / `beastup` / `beastdown` stop Hy4 (and point `openbeast.conf` back at the selected model) |
| `beaststatus` | `beast-spark status` | Shows the selected model and pack, the inference units (Hy4's too), and what `:8000` serves |
| (no alias) | `beast-spark <recipe>` | Serves an **adopted beast-recipe recipe** the way `beasthy4` serves Hy4, never as the selection: `beast-recipe adopt <recipe> --run` first (SERVABLE-NOW plans only; it installs the recipe's unit, which has no `[Install]` section and so enables and starts nothing, and writes `~/.config/openbeast/recipes.d/<recipe>.env`, parsed, never sourced). The switch checks the installed `tensorfold --version` for a native recipe before it stops anything, stops the model that serves, starts `beast-recipe-<recipe>.service`, waits for its served id, sets `INFERENCE_BACKEND` and `INFERENCE_MODEL`, restarts the command center, and brings the previous model back on failure. `beast-recipe rollback --run` returns to the selection before the last switch; `beastds` / `beastglm` / `beastup` / `beastdown` / `beasthy4` also stop a recipe ([BEAST_RECIPE.md](BEAST_RECIPE.md#adopt-recipe---run---force---write-family-and-rollback---run)) |

"Selected" means the enabled inference unit, which is the one that starts at boot. DeepSeek is the default; `beastup` with nothing selected picks DeepSeek (`beast-spark ds`). GLM is the alternative. The pair serves only these two models as a boot selection (Hy4 and adopted recipes serve only on demand). The Mia EXL3 pack (its folder and cache id end in `-v2`) supersedes the stock EAQ v1 pack, which stays on disk only as a fallback (`spark-deepseek-pack.sh use stock`, while DeepSeek serves). Switches hold the lock `${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock` (shared with `spark-deepseek-pack.sh`), so a second one is refused while one runs. **Install or update** after a pull, on spark-1, offline too: `~/openbeast/scripts/backends/spark-beast.sh install` copies `beast-spark` to `~/.local/bin`, `spark-deepseek-pack.sh` and `spark-deepseek-prepare-local.sh` to `~/openbeast-recipes/deepseek-prep/`, and appends the alias block to `~/.bashrc` once (a rebuilt spark-1 needs nothing else). Once installed, `beast-spark install` does the same: the installed copy hands over to the checkout's. **Staleness:** every command (but `install`) compares the installed `~/.local/bin/beast-spark` with `~/openbeast/scripts/backends/spark-beast.sh` (one `cmp`; `SB_CHECKOUT` names another checkout) and warns when they differ, with the fix `beast-spark install` (pull the checkout first if it is the older one). The check exists only in installs made from #263 onward; an older install cannot warn about itself, so update it once from the checkout: `bash ~/openbeast/scripts/backends/spark-beast.sh install`. An unknown subcommand is named in an error (an old install once answered `hy4` with the bare usage). `beast-spark aliases` prints the six lines (`install` adds `beasthy4` to an older alias block), under `# beast-spark shortcuts`; they call `"$HOME/.local/bin/beast-spark …"`. From the Mac by hand: `scp scripts/backends/spark-beast.sh spark-1:.local/bin/beast-spark`. After a switch, clients with `openbeast-client autosync` follow within 30 s; otherwise run `openbeast-client refresh-config`.

**(a) Everyday, on the deployed pair.** On spark-1 the enabled user unit drives both ranks: `deepseek-v41-inference` for DeepSeek (the default), or `spark-hydra-inference` while GLM is selected (`beaststatus` shows which). It retries a failed launch after 60 seconds (DeepSeek) or 30 seconds (GLM). The command center has its own user unit, `openbeast`.

```bash
# On spark-1
systemctl --user status deepseek-v41-inference openbeast --no-pager   # DeepSeek, the default; spark-hydra-inference when GLM is selected
journalctl --user -u deepseek-v41-inference --since today
curl -fsS http://127.0.0.1:8000/health

# On the enrolled Mac
openbeast-client use spark-hydra
openbeast-client status
curl -fsS https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool
```

The model ID should be `DeepSeek-V4.1-Flash-1017` (the default since 2026-11-18), or `GLM-5.3-Flash-EXL3` when GLM is selected. Some optional capacity and busy fields may be null. Start, stop or restart both ranks through the head unit ([G3](#g3-restarts-only-through-systemctl---user-while-the-unit-is-enabled)).

**(b) Use it from a client.** Everything goes through beast-gate on
`https://spark-1.tail4109f9.ts.net:8443/v1`, with a per-device key. The model
itself listens on spark-1's loopback only.

1. **Browser (any tailnet device):** Open WebUI at
   `https://spark-1.tail4109f9.ts.net`, with a WebUI login
   ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)).
2. **A device key** (on spark-1; [E7](#e7-device-keys-for-beast-gate)). The key
   prints once. Copy it straight into a 0600 file on the device (on the Mac:
   `~/.config/openbeast/spark-hydra-gate.key`), never into a shared log:
   `cd ~/openbeast && ./scripts/clients.sh enroll macbook --label "Max's MacBook"`.
3. **Mac or Linux client mode** ([F2](#f2-client-mode-one-or-several-rigs)). This
   gives OpenCode on the client, with the tools acting on the client's own
   files, and switches between this pair and the `beast` rig:

   ```bash
   ./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key
   openbeast-client status                     # reads :8444/api/slot; "active rig: spark-hydra"
   openbeast-client rigs                       # every registered rig; use <rig> switches
   cd <any project> && opencode                # pick an 'openbeast-rig' (active) or 'openbeast-<rig>' model
   scripts/client-e2e.sh                       # end-to-end check from the Mac
   ```

   The opencode v2 layout is written correctly; there is no fix-up any more
   ([F3](#f3-what-the-client-writes-into-opencode)).
4. **Raw OpenAI-compatible API** (any tool that takes a base URL and a key).
   Without a key, the gate answers 401:

   ```bash
   curl -s https://spark-1.tail4109f9.ts.net:8443/v1/chat/completions \
     -H "Authorization: Bearer $(cat ~/.config/openbeast/spark-hydra-gate.key)" \
     -H 'Content-Type: application/json' \
     -d '{"model": "DeepSeek-V4.1-Flash-1017", "messages": [{"role": "user", "content": "hello"}]}'
   # while GLM (the alternative) is selected: "model": "GLM-5.3-Flash-EXL3"
   ```

**(c) First build from bare boxes (the GLM recipe first, then DeepSeek, the default, on top).** Follow
[Install everything, fresh](#install-everything-fresh): 22 ordered steps,
each with its host, the online command, the offline variant and a link to the
detail. In outline:

1. [B1](#b1-first-boot) first boot.
2. [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark) root setup, on each Spark.
3. [B3](#b3-firewall-spark-guard) spark-guard, and [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) the fabric MTU staged before the one reboot.
4. [C](#c-weights) weights, on both.
5. [D1](#d1-the-glm-recipe-the-alternative-deployment) the recipe and the `spark-hydra-inference` unit.
6. [E1](#e1-bootstrap)–[E7](#e7-device-keys-for-beast-gate) the command center, boot units, WebUI admin, tailnet publishing and device keys.
7. [F](#f-the-mac-client) the Mac client, then the optional toolchains, docs mirror, ark drive and eval harness host.
8. Step 22: DeepSeek, the default, from the backup overlay, then `beastds`.

The short form is [Rebuild from scratch](#rebuild-from-scratch-short-form).
With no internet at all, follow [H9](#h9-the-order-for-a-pair-built-entirely-offline)
instead.

**(d) Update a built pair to the latest `main`.** Follow
[G6](#g6-updating): read the diff, back up Open WebUI if its pin moves, pull
on both Sparks, `pydeps.sh install`, restart the command center, then the
per-change steps (spark-guard, MTU, `ARTIFACT_LOGIN_FROM`, the seed filter,
zig, the docs mirror). G6 also lists what is still pending on the live pair.

| Section | What it gets you |
|---|---|
| [Hy4-flash (NVFP4, vLLM TP2): measured](#hy4-flash-nvfp4-vllm-tp2-measured) | the third model, staged and measured (v5 H_T2 60/71): pins, staging, `beasthy4`, memory, thinking, what is unverified, arm H_T2 |
| [Quickstart](#quickstart) | everyday checks and restarts, using the pair from a client, the build order |
| [Install everything, fresh](#install-everything-fresh) | the ordered checklist from bare DGX OS to the full pair, online and offline, per host |
| [0. The pair at a glance](#0-the-pair-at-a-glance) | names, addresses, what runs where |
| [A. What you need](#a-what-you-need) | hardware, cabling for 1 / 2 / 3 / 4 (a cabled ring needs no switch) / 5-8 Sparks, accounts, the offline staging checklist |
| [B. Bare boxes to root setup](#b-bare-boxes-to-root-setup) | `spark-root-setup.sh`, `spark-guard.sh`, checking them from another device, fabric MTU 9000 (`spark-fabric-mtu.sh`) |
| [C. Weights](#c-weights) | fetch or copy, lock, hard-link into the recipe's HF-cache layout, carry to workers |
| [D. Inference](#d-inference) | D1 the GLM recipe (the alternative; DeepSeek's recipe is in [DeepSeek deployment](#deepseek-deployment)), D2 native TensorFold through `spark-node.sh` / `spark-cluster.sh`, D3 the engine plane |
| [E. The command center on spark-1](#e-the-command-center-on-spark-1) | bootstrap, `openbeast.conf`, boot units, WebUI admin, tailnet publishing, device keys |
| [F. The Mac client](#f-the-mac-client) | the "From the Mac" quick reference, `setup-client.sh`, several rigs (`openbeast-client rigs / use`), what opencode gets, `client-e2e.sh`, beast-apps, beast-docs, the eval harness host |
| [G. Day-2 operations](#g-day-2-operations) | health, logs, restarts, memory, watchdog, the "update everything" checklist, keys, troubleshooting |
| [H. Fully offline operation](#h-fully-offline-operation) | island and `OFFLINE=true`, what keeps working, staging every artifact |
| [Reference](#reference) | serving numbers, scaling notes, parity checks, known issues, not done yet |

---

## Install everything, fresh

This checklist builds the GLM deployment, now the staged alternative. DeepSeek-V4.1-Flash (Mia EXL3), the pair's default since 2026-11-18, is staged on top of it (the [DeepSeek deployment record](#deepseek-deployment)), and `beastds` then makes it the boot selection.

One ordered pass from bare DGX OS to the full working pair, including every
optional piece. Each step names the host, the online command, the offline
variant and the section with the detail. Commands run in a shell **on the
host named** (ssh in first, and quote remote commands: [F1](#f1-ssh-from-the-mac)).
Steps marked **sudo** need Max's password at a terminal on that box
(`ssh -t`). Updating a pair that is already built is the other checklist,
[G6](#g6-updating).

**What DGX OS already brings.** Present on both Sparks on 2026-11-16, and not
installed by OpenBeast: Ubuntu 24.04.5 aarch64, NVIDIA driver 580.178.04
(CUDA 13.0), docker-ce 29.6 with the compose and buildx plugins,
nvidia-container-toolkit 1.20, python3.12 3.12.3 with `python3-venv` and
`pip`, gcc / g++ 13.3, build-essential, cmake, git, jq, rsync, sqlite3,
smartmontools, netplan 1.1.2, nftables, ufw (installed, inactive) and the RDMA
tools (`ibdev2netdev`, `ibv_devinfo`, perftest). OpenBeast adds exactly two
system packages, in step 4: `libnccl2 2.28.9-1+cuda13.0` (held) and
`tailscale 1.104.7`. Everything else lives in `max`'s home: the Python lock
in `~/.local` (pip `--user`, 43 packages, the `hf` CLI among them), native
TensorFold in `~/.local/opt` and `~/.local/bin`, the compose images in Docker,
and the user units in `~/.config/systemd/user`. Not installed on either Spark
that day: zig, go, rust, node, clang, opencode, a beast-docs mirror, a beast-ark
drive (steps 15-18 add them). Since then (checked 2026-11-19): opencode v2.3.2
on both ([What runs where](#what-runs-where)) and a docs mirror with the
`tailscale` source on spark-1; still no zig, go, rust or ark drive.

**0. Offline only: stage everything first** (any connected box, plus one
online Spark for the aarch64 pieces). Fill a drive from the
[A4](#a4-the-artifact-checklist-what-to-stage-for-an-offline-build) list:
the .debs ([H3](#h3-debs-libnccl2-and-tailscale)), `openbeast.bundle`
([H2](#h2-the-repo)), the weights and DFlash2 ([C1](#c1-fetch-online-or-verify-a-local-copy),
[C4](#c4-the-drafter-dflash2)), the recipe as a git bundle plus its image
tarball ([H4](#h4-container-images)), native TensorFold
([H5](#h5-native-tensorfold-and-the-engine-plane)), the command-center bundle
built **on an online Spark** ([H6](#h6-the-command-center-wheels-and-compose-images)),
the test wheels ([H7](#h7-test-and-language-toolchains)), a docs mirror
tarball ([H8](#h8-beast-docs-mirror)) and an ark drive with zig, go and rust
([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)). Online, skip
this step: each later step fetches its own piece.

**1. Cable, boot, NVIDIA Sync** (both Sparks + the Mac;
[A2](#a2-cabling-and-addressing-for-1-2-3-4-and-5-8-sparks),
[B1](#b1-first-boot)). Create the user `max` (in `sudo` and `docker`),
connect the LAN, pair each Spark with NVIDIA Sync so it addresses both fabric
rails, then check on each Spark: `ibdev2netdev` shows the cabled ports `(Up)`
and `ip -4 -o addr show | grep 10.100` shows the rail addresses.

**2. The repo at one commit** (both; [B1](#b1-first-boot) step 3):

```bash
git clone https://github.com/MaximilianKhan/openbeast ~/openbeast && git -C ~/openbeast log --oneline -1   # online, main
git clone /media/usb/openbeast.bundle ~/openbeast && git -C ~/openbeast log --oneline -1                   # offline (H2)
```

The two `log` lines must print the same commit.

**3. The head's ssh key to the worker** (spark-1; [B1](#b1-first-boot) step 4):
`ssh-keygen -t ed25519` if it has none, `ssh-copy-id max@10.100.216.2`, then
`ssh max@10.100.216.2 true`. No network beyond the cable.

**4. Root setup, guard included, sudo** (each Spark;
[B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark),
[B3](#b3-firewall-spark-guard)):

```bash
cd ~/openbeast
sudo scripts/backends/spark-root-setup.sh spark-1 --guard                 # on spark-1, online
sudo scripts/backends/spark-root-setup.sh spark-2 --guard                 # on spark-2, online
scripts/backends/spark-root-setup.sh --check-debs ~/debs                  # offline: no root, sha256 vs the pins
sudo scripts/backends/spark-root-setup.sh spark-N --debs ~/debs --guard   # offline (H3)
```

The first run prints a Tailscale login URL. Offline, `tailscale up` cannot
finish until there is a route; the script says so and goes on.

**5. Fabric MTU 9000, staged, sudo** (each Spark;
[B4](#b4-fabric-mtu-9000-spark-fabric-mtu)). Fully local, so the online and
offline commands are the same:

```bash
cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage
```

Staging changes nothing live. Doing it now means the reboot in step 6 also
brings the rails up at 9000, so you reboot once instead of twice. **Measured:**
our pair has run at 9000 since 2026-11-17 (B4, `--verify` passed on both);
its speed gain is not measured yet. To build without it, skip this step;
`--stage` can run on any later day, followed by a reboot of both.

**6. Reboot both, once** (worker first, then head). This is the docker-group
gotcha from [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark),
and it activates step 5. Then check on each Spark:

```bash
systemd-run --user --wait --pipe docker info >/dev/null && echo user-manager-sees-docker
dpkg-query -W libnccl2 && loginctl show-user "$USER" -p Linger && tailscale ip -4
sudo ~/openbeast/scripts/backends/spark-guard.sh --status         # "rules: current", unit active
~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <peer rail-1 IP> <peer rail-2 IP>   # if step 5 ran: VERIFIED (all peer IPs, one call)
```

**7. Weights** (spark-1, then spark-2; [C1](#c1-fetch-online-or-verify-a-local-copy)–[C4](#c4-the-drafter-dflash2)).
On spark-1, online, put the HF token in `~/.config/openbeast/hf-token`
(0600) and run `model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold`.
Offline, copy the tree from the drive and run the same command with
`--verify`. Then, both ways: the HF-cache hard links (C2), one `rsync -aH`
to spark-2 and `--verify` there (C3), DFlash2 copied and checked with the
blob loop on both (C4). Online, DFlash2 comes from
`hf download incoai/GLM-5.3-Flash-DFlash2 --revision 6cc6c481eee930a5139619ed919f78726371adbc`
(Apache-2.0 since that 2026-11-10 revision; [C4](#c4-the-drafter-dflash2))
(the `hf` CLI is in `~/.local/bin` once step 11 has installed the Python
lock; run step 11's `bootstrap.sh` first if it is missing).

**8. The recipe and its image** (spark-1, image on both;
[D1](#d1-the-glm-recipe-the-alternative-deployment), [H4](#h4-container-images)):

```bash
# on spark-1. Online:
git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold.git ~/openbeast-recipes/glm53
git -C ~/openbeast-recipes/glm53 checkout 33b50fd
# offline: git clone /media/usb/glm53-recipe.bundle ~/openbeast-recipes/glm53 (a `git bundle create … --all` of the same repo), then the same checkout
# write ~/openbeast-recipes/glm53/scripts/local.sh exactly as in D1 (untracked; the recipe's config.sh sources it)

# the image, on BOTH Sparks. Online, the prebuilt image prepare.sh would pull, by digest:
IMG=ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c66273e2965dd40a7227e7a5243db333fe250113fb3987462ad4f12588
docker pull "$IMG" && docker tag "$IMG" tensorfold-glm53:v0.6.0
# or on spark-1 only, then over the cable: docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load
# offline: docker load -i tensorfold-glm53-v0.6.0.tar on each Spark (H4)
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0   # sha256:ead0174052ac16b0… on both
```

The recipe's own `scripts/prepare.sh` does the same image pull and can also
fetch weights and copy to the worker; read the recipe's README before running
it. On our pair, OpenBeast staged the weights (step 7) and only the image
came from the recipe. The recipe lives on spark-1 only: `start.sh` runs rank 1
on spark-2 with `docker run` over ssh.

**9. The inference unit** (spark-1; [D1](#d1-the-glm-recipe-the-alternative-deployment)).
The `sed … spark-hydra-inference.service` block in D1, then
`systemctl --user enable --now spark-hydra-inference`. Loading takes 2-6 min.
`curl -s http://127.0.0.1:8000/health` must show `"ok": true`. Online and
offline are the same: the unit runs with `PREPARE=0` and `HF_HUB_OFFLINE=1`.
Then the switch shortcuts, one command from the checkout:
`~/openbeast/scripts/backends/spark-beast.sh install` puts `beast-spark` in
`~/.local/bin`, the two DeepSeek pack tools in
`~/openbeast-recipes/deepseek-prep/` (when the DeepSeek recipe is there; run it
again after staging DeepSeek) and the five `beast*` aliases in `~/.bashrc`
([Everyday shortcuts](#quickstart)).

**10. Native TensorFold, optional** (each Spark; [D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh),
[D3](#d3-the-engine-plane-scriptsenginesh)). The challenger engine; the pair
serves without it.

```bash
cd ~/openbeast
scripts/engine.sh install tensorfold --version 1.2.10         # online: download, verify, pin (or install-native.sh --version 1.2.10; the Sparks' release)
scripts/engine.sh install tensorfold --from /media/usb/tf      # offline (H5)
cp scripts/backends/spark.env.example scripts/backends/spark.env    # then edit it per node (D2 table)
cp scripts/backends/cluster.env.example scripts/backends/cluster.env  # spark-1 only: CLUSTER_NODES="local max@10.100.216.2"
sudo scripts/backends/spark-guard.sh                           # RE-RUN after writing spark.env: it guards spark.env's master ports
```

The key file `spark.env` names in `TENSORFOLD_API_KEY_FILE` must exist (0600)
on **every** node before a D2 launch. The re-run of the guard matters: both
live Sparks went `STALE` because `spark.env` (with `VLLM_MASTER_PORT=29501`)
was written an hour after the guard ([B3](#b3-firewall-spark-guard)).

**11. The command center** (spark-1; [E1](#e1-bootstrap), [E2](#e2-openbeastconf)).
Write `openbeast.conf` first (0600). The keys spark-1 runs with, secrets
aside:

```bash
cd ~/openbeast && cp openbeast.conf.example openbeast.conf && chmod 600 openbeast.conf
# then set, each on its own line (no trailing comments: the parser keeps them):
#   INFERENCE_BACKEND=tensorfold   INFERENCE_URL=http://127.0.0.1:8000
#   INFERENCE_MODEL=GLM-5.3-Flash-EXL3   INFERENCE_SLOTS=4
#   EDGE_GATE=true   WEBUI_AUTH=true   TS_HOSTNAME=spark-1   EXTENSIONS="dashboard"
#   BEAST_CHAT=true   CHAT_OPERATORS=<you>   CHAT_LOGIN_FROM=unix
#   BEAST_ARTIFACT=true   ARTIFACT_OPERATORS=<you>   ARTIFACT_ADMINS=<you>   ARTIFACT_LOGIN_FROM=unix
#   EDGE_READ_TIMEOUT=3600          (only if an eval harness will run against the pair: F6)
#   LANG_PACKS=off   LANG_PACK_CONTEXT=off   BEAST_ESCALATE=0
#   INFERENCE_THINKING=on   BEAST_ASSIST=0   AGENT_MAX_TOKENS=16384   (GLM's v5 best, arm C: E2a)
#   (once `beastds` makes DeepSeek the selection: drop those two lines, add AGENT_PROFILE=deepseek-best: E2b)
./bootstrap.sh --preflight      # read-only: what it will do and what it cannot reach
./bootstrap.sh --no-start       # online: the Python lock, the pinned compose images (Open WebUI 0.13.3)
```

Offline: `./scripts/bundle.sh verify` and `install` the Spark-built bundle,
`echo 'OFFLINE=true' >> openbeast.conf`, then `./bootstrap.sh --no-start`
([H6](#h6-the-command-center-wheels-and-compose-images)). Leave
`EDGE_MAX_INFLIGHT` unset: the gate then admits `INFERENCE_SLOTS` (4)
generations per device. `WEBUI_RANDOM_SEED` defaults to on.

**12. Boot units** (spark-1; [E3](#e3-boot-units)): the `sed … openbeast.service`
block, `systemctl --user enable --now openbeast`. The first start installs
the watchdog and logrotate timers and runs `configure-webui.sh`, which wires
the tool servers and installs the random seed filter and, with
`INFERENCE_THINKING` set, the thinking default filter
([G6](#g6-updating), [E2a](#e2a-recommended-serving-configuration-from-v5)). Expect `healthcheck.sh` to say "All 9 services
healthy" once the model has loaded ([E4](#e4-the-healthy-state)).

**13. WebUI admin, then publish** (spark-1; [E5](#e5-webui-auth-and-the-admin-account-before-publishing),
[E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)).
Log in over an ssh tunnel, create your admin, then:

```bash
cd ~/openbeast
./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng
tailscale serve --bg --https=8447 http://127.0.0.1:3002   # the dashboard; by hand, persists (BEAST_SLOT.md)
./scripts/setup-tailscale.sh --status
```

A host that runs ntfy (`--publish-ntfy`, also `:8447`) mounts the dashboard on
another port ([BEAST_SLOT.md](BEAST_SLOT.md#the-status-dashboard-8447)).

With `ARTIFACT_LOGIN_FROM=unix` and `CHAT_LOGIN_FROM=unix` set before this
run, `:8446` and `:8445` proxy to the 0600 sockets, not to loopback TCP.
`tailscale serve` needs the box joined to the tailnet (step 4), and joining
needs a route to Tailscale's coordination server once.

**14. Device keys** (spark-1; [E7](#e7-device-keys-for-beast-gate)):
`./scripts/clients.sh enroll macbook --label "Max's MacBook"` prints the key
once. Put it straight into `~/.config/openbeast/spark-hydra-gate.key` (0600)
on the Mac. One key per device; the eval harness host gets its own.

**15. The Mac client** (the Mac; [F1](#f1-ssh-from-the-mac)–[F4](#f4-beast-apps-external-apps-through-mcp-servers)):
the ssh config with `HostKeyAlias`, then
`./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key`,
then `scripts/client-e2e.sh` (F2). Offline client install:
[BEAST_APPS.md](BEAST_APPS.md) § Offline client.

**16. zig 0.16.0 and the other language toolchains, optional** (spark-1, and
the harness host; [H7](#h7-test-and-language-toolchains)). Needed for
`language_reference` on zig and for the zig / go / rust eval variants. From an
ark drive: `ark restore zig`, `ark restore go`, `ark restore rust` (offline
and online alike). Online without a drive: the pinned zig tarball in H7.

**17. beast-docs mirror, optional** (sync on the Mac, unpack on spark-1;
[F5](#f5-beast-docs-platform-docs-mirrored), [H8](#h8-beast-docs-mirror)).
`docs.sh sync` + `pack` on a connected box, `docs.sh unpack` on spark-1. The
unpack is offline.

**18. beast-ark drive, optional** ([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)).
`./scripts/ark.sh fetch --to <drive> --profile core --platform linux-aarch64`
on a connected box; `DRIVE/ark verify DRIVE` and `DRIVE/ark restore <name>`
on the Spark, offline.

**19. pytest, optional** (any Spark; [H7](#h7-test-and-language-toolchains)).
The hash-pinned venv `~/.venvs/openbeast-test`; offline from a wheelhouse.

**20. The eval harness host, optional** (the Mac or a Linux box;
[F6](#f6-the-eval-harness-host-benchmarking-the-pair)):
`./scripts/eval-host.sh setup` (zig 0.16.0, rustc 1.100.0, go 1.23.3, the
python 3.12 eval venv; `--from` an ark drive offline), gcc / g++ / git from
the OS, its own device key, and `EDGE_READ_TIMEOUT=3600` on spark-1 (step 11).

**21. Prove it** (spark-1 and the Mac; [G1](#g1-health)):

```bash
~/openbeast/scripts/healthcheck.sh          # on spark-1: "All 9 services healthy"
~/openbeast/scripts/doctor.sh               # on spark-1: exit 0 (no FAIL row)
sudo ~/openbeast/scripts/backends/spark-guard.sh --status   # on each Spark: "rules: current"
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30   # from the Mac
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed                           # from the Mac: closed
curl -s -o /dev/null -w '%{http_code}\n' https://spark-1.tail4109f9.ts.net:8443/v1/models    # from the Mac, no key: 401
```

**22. DeepSeek, the default, on top** (spark-1, rank files to spark-2;
[DeepSeek deployment](#deepseek-deployment)). DeepSeek's runtime is not in
this repo: the checkout of Mia's recipe at `c7f2b85` with its `local.sh`
(`~/openbeast-recipes/deepseek-prep/runtime`), the helpers beast-spark calls
(`activate-stock.sh`, `rollback/`, `verify-chat.py`), the unit file
`deepseek-v41-inference.service`, the kernel kit with its `.git`, the image
`tensorfold-dsv41:exl3-tp2-8a98ae503d85` (both Sparks), the Engram base shards
and the pack folders are host-local. They come back, offline, from the backup
overlay ([SPARK_BACKUP_RESTORE.md §3.4](SPARK_BACKUP_RESTORE.md#34-bare-metal-to-serving-the-order),
steps 4, 5 and 7). With them in place:

```bash
~/openbeast/scripts/backends/spark-beast.sh install      # again, now that deepseek-prep exists: the pack tools
beastdown                                                # the rebuild refuses while any inference unit runs
~/openbeast/scripts/backends/spark-restore.sh rebuild    # dry run: the checks; then add --apply (about 35 min, offline)
beastds                                                  # selects the Mia EXL3 pack, activates DeepSeek, sets the boot selection
beaststatus                                              # selected: ds; :8000 serves DeepSeek-V4.1-Flash-1017
```

Then the agent preset ([E2b](#e2b-deepseek-best-configuration-v5-t2)) and, on
each client, `openbeast-client use spark-hydra` (or wait 30 s for autosync).
Building DeepSeek from the Hub instead (no backup) follows Mia's recipe README
online; it has not been done on this pair since the 2026-11-17 cutover.

---

## 0. The pair at a glance

### Names and addresses

| | spark-1 (head) | spark-2 (worker) |
|---|---|---|
| Role | rank 0 + API + command center | rank 1, nothing else |
| Tailnet | `spark-1` 100.66.191.118 (`spark-1.tail4109f9.ts.net`) | `spark-2` 100.110.143.15 (`spark-2.tail4109f9.ts.net`) |
| LAN (home) | 192.168.40.153 | 192.168.40.151 |
| ConnectX-7 rail 1 / rail 2 | 10.100.216.1 / 10.100.217.1 | 10.100.216.2 / 10.100.217.2 |
| Hostname / mDNS | `spark-3dc6` / `spark-3dc6.local` | `spark-9f26` / `spark-9f26.local` |
| NVIDIA Sync ssh alias | `spark-1` → `spark-3dc6.local` | `spark-2` → `spark-9f26.local` |

`spark-hydra` names the **pair**, never one box.

How to reach each box:

- **At home:** `ssh spark-1` works through NVIDIA Sync's `.local` alias.
- **Right after a reboot:** mDNS sometimes does not resolve. Use the LAN IP:
  `ssh -o HostName=192.168.40.153 spark-1`.
- **Away from home:** `.local` names never resolve. Use the tailnet names; see
  the ssh config in [F](#f1-ssh-from-the-mac).
- **spark-1 to spark-2:** use the fabric address, `ssh max@10.100.216.2`.
  Control traffic between the ranks never needs the LAN.

### What runs where

**Which model is selected.** DeepSeek-V4.1-Flash (Mia EXL3) is the default since 2026-11-18; GLM-5.3-Flash, the staged alternative, has the second table. `systemctl --user is-enabled spark-hydra-inference deepseek-v41-inference` shows the selection.

**DeepSeek-V4.1-Flash (Mia EXL3), the default (checked read-only on 2026-11-18; serving and enabled for boot since Max's decision that day):**

| Layer | Where | What |
|---|---|---|
| Inference | spark-1, `systemctl --user` unit `deepseek-v41-inference` (**enabled**, serving: the default since 2026-11-18) | `Type=simple`, `WorkingDirectory=~/openbeast-recipes/deepseek-prep/runtime`, `FOREGROUND=1 PREPARE=0 ./start.sh`, `Restart=on-failure`, `RestartSec=60s`, at most 3 starts in 30 min. The runtime is Mia's two-Spark recipe at `c7f2b85`. It starts rank 1 on spark-2 over ssh, then rank 0 |
| Containers | both | `dsv41-1017-exl3-tf` from the locally built image `tensorfold-dsv41:exl3-tp2-8a98ae503d85`, one per Spark |
| Pack selection | spark-1, `runtime/scripts/local.sh` (managed block written by `spark-deepseek-pack.sh`; previous copy `local.sh.prev`) | `MODEL_ID=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2`, `MODEL_REVISION=04c13f12…` (local REV), `MODEL_SRC=~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2`, `WORKER_CACHE_DIR=…/worker-rank-cache-04c13f12`, `CONTEXT=524288`, `HOST=127.0.0.1`, `PORT=8000` |
| API | spark-1 | `http://127.0.0.1:8000/v1`, model `DeepSeek-V4.1-Flash-1017`, bearer key in `~/.config/openbeast/tensorfold-api-key` (0600). Loopback only |
| HF cache | both | `~/openbeast-models/deepseek-hf` (packs hard-linked in as snapshots; Engram base revision `1bae0e10…`, shards 47 and 48) |
| Pack folders | both | `~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw` (stock EAQ v1, the fallback) and `…-bpw-v2` (DeepSeek-V4.1-Flash (Mia EXL3), serving). spark-2's `~/Documents` mirrors spark-1's as a backup |
| Helpers | spark-1, `~/openbeast-recipes/deepseek-prep/` | beast-spark's internals: `activate-stock.sh` (starts and verifies DeepSeek, enables it, disables GLM; it does **not** stop GLM, so `beastds` stops GLM first), `rollback/restore-glm.sh` (DeepSeek → GLM). Also `spark-deepseek-pack.sh`, `spark-deepseek-prepare-local.sh`, `verify-chat.py`, conformance records (`conformance-4/`, `conformance-v2/`) |
| Shortcuts | spark-1, `~/.local/bin/beast-spark` + aliases in `~/.bashrc` | `beastup`, `beastdown`, `beastds`, `beastglm`, `beasthy4`, `beaststatus`: **the** switch path (from `scripts/backends/spark-beast.sh`; `spark-beast.sh install` puts it and the aliases in place) |
| GLM unit | spark-1, `spark-hydra-inference` (**disabled** since 2026-11-18; enabled only while GLM is selected) | the staged alternative; see the GLM table below |
| Command center | spark-1, `openbeast` (enabled) + transient `openbeast-stack` scope | as in the GLM table. `INFERENCE_MODEL` follows the selection (`restore-glm.sh` and `activate-stock.sh` rewrite it): `DeepSeek-V4.1-Flash-1017` with the default (checked 2026-11-18), `GLM-5.3-Flash-EXL3` while GLM serves. `INFERENCE_THINKING=on`, `INFERENCE_SLOTS=4`, `EDGE_GATE=true` either way |
| opencode | both, `~/.opencode/bin/opencode` v2.3.2 | provider `spark-hydra-local` at `127.0.0.1:8000`, default model `DeepSeek-V4.1-Flash-1017` (since 2026-11-18), GLM also listed; on spark-2 through the `opencode-model-link` ssh tunnel ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md#opencode-on-the-sparks)) |

**GLM-5.3-Flash, the staged alternative (since 2026-11-18; the first deployment, 2026-11-13 to 2026-11-17, and the default for part of 2026-11-18).** Checked read-only on 2026-11-18 while it served: unit `spark-hydra-inference` (`WorkingDirectory=~/openbeast-recipes/glm53`, `FOREGROUND=1 PREPARE=0`, `Restart=on-failure`, `RestartSec=30`), container `glm53-flash-tf` from image `tensorfold-glm53:v0.6.0` on both Sparks, model id `GLM-5.3-Flash-EXL3` on `127.0.0.1:8000`, which answers without a key (v0.6.0 has no API key; a bearer header is ignored). The rest of the table is as recorded when it was set up.

| Layer | Where | What |
|---|---|---|
| Inference | spark-1, `systemctl --user` unit `spark-hydra-inference` | Runs `~/openbeast-recipes/glm53/start.sh` in the FOREGROUND. This is Mia's recipe v1.8 (`33b50fd`): TensorFold v0.6.0 plus patches, in image `tensorfold-glm53:v0.6.0` (= `ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c6…`). It starts rank 1 on spark-2 over ssh. Its settings are in `scripts/local.sh` ([D1](#d1-the-glm-recipe-the-alternative-deployment)) |
| API | spark-1 | `http://127.0.0.1:8000/v1`, model `GLM-5.3-Flash-EXL3`. **Loopback only: v0.6.0 has no API key** |
| Command center | spark-1, `systemctl --user` unit `openbeast` | `~/openbeast`, a git checkout: WebUI :3000, tools :3001, SearXNG :8888, beast-gate :8090, beast-chat, beast-artifact, the dashboard (beast-slot), the island watcher. `openbeast.conf`: [E2](#e2-openbeastconf) |
| Weights | both | `~/openbeast-models/glm53-flash-ablit-exl3` (175.7 GB, 83 shards), hard-linked into `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit/snapshots/57edefd2…`. Verified against the Hub revision (sha256, 0 mismatches) and locked in `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock` ([C](#c-weights)) |
| Drafter | both | `incoai/GLM-5.3-Flash-DFlash2 @ 6cc6c481` (**Apache-2.0**; re-pinned 2026-11-23 from `bf582e4e`, CC BY-NC-ND, which holds the byte-identical `model.safetensors` and `config.json`: only the README differs). It lives in the HF cache only, in `hf download` layout (snapshot symlinks into `blobs/`). **No OpenBeast lock covers it**; [C4](#c4-the-drafter-dflash2) shows how to check it |
| Native TensorFold 1.2.10 | both | `~/.local/bin/tensorfold` → `~/.local/opt/tensorfold-1.2.10-linux-aarch64` (signed release; read 2026-11-22; `install-native.sh`'s default and the ark catalog pin 1.3.4 since 2026-11-23, so pass `--version 1.2.10` to reinstall this one). A challenger engine, **not serving** ([D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)) |
| spark-guard | both | `openbeast-spark-guard.service` (system unit, `active (exited)`): an nftables table that keeps the engines' rendezvous ports on the fabric ([B3](#b3-firewall-spark-guard)) |

**Checkouts, 2026-11-22 (read-only).** spark-1 is on `e8e4b2fa` (#278), spark-2 on
`ab62b348` (#248); `main` is at #278 or later. They are skewed: pull both
([G6](#g6-updating) step 4) in a gap between evals, and rewrite this paragraph
once both match. The installed `~/.local/bin/beast-spark` on spark-1 also
predates #263 (it differs from the checkout's copy and cannot warn about it):
re-run `bash ~/openbeast/scripts/backends/spark-beast.sh install` after the pull.

**Checkouts, 2026-11-19 (read-only).** Both Sparks are on `f38a29c7` (#230, DeepSeek the
default), with clean working trees: the uncommitted copies described below are
gone. `main` is ahead by #231 to #233: the v5 board, an eval report, and #232, the
`deepseek-best` preset in `scripts/lib/conf.sh`, which the pull in
[E2b](#e2b-deepseek-best-configuration-v5-t2) brings. None is needed to serve.

**Checkouts, 2026-11-18.** Both Sparks are on one commit,
`d3727185` (#203, the thinking default), eight merges behind `main`. Both
also hold uncommitted copies of files that #210 later merged into `main`
(`docs/PYTHON_OFFLINE.md`, `docs/TAILSCALE_OFFLINE.md`, `skills/tailscale/`,
edits to `agents/docs/sources.toml`, `docs/BEAST_DOCS.md`,
`skills/IN_HOUSE_SKILLS.txt`, `skills/platform-docs/SKILL.md`), plus macOS
`._*` metadata files. They match `main` except one line of
`skills/platform-docs/SKILL.md`, where `main` points at `skill("tailscale")`.
A `git pull` will refuse to overwrite them: move them aside first
([SELF_DEVELOPMENT.md 11.3](SELF_DEVELOPMENT.md#113-the-loop-edit-test-commit-share-review-merge)).
#204 to #211 changed the Mac client, the docs and the pack helper, so
neither Spark needs them to serve. (On 2026-11-16 evening they
were skewed, spark-1 at `29e5241a` (#181) and spark-2 at `de931dfe` (#176);
on 2026-11-15 spark-1 was at `ecf50a2` and spark-2 at `0988641`.) Keep them
on one commit: the "same repo
on every node" rule is what `spark-cluster.sh check` enforces, and
[G6](#g6-updating) step 4 pulls both. Run this on each Spark, quoting the command
for ssh:
`ssh spark-2.tail4109f9.ts.net 'cd ~/openbeast && git pull --ff-only'`.
Restarting the command center afterwards (`systemctl --user restart openbeast`)
is what makes a new commit live. Do it at a quiet moment.

---

## A. What you need

### A1. Hardware

| Item | Per Spark | Notes |
|---|---|---|
| NVIDIA DGX Spark (GB10, 128 GB unified memory, aarch64, DGX OS = Ubuntu 24.04) | 1 | the driver is R580 = CUDA 13.0. `nvidia-smi --query-gpu=memory.*` reports `[N/A]`, and that is how `scripts/lib/hardware.sh` classifies the host as `spark` |
| Disk free | ≥ 400 GB on the head, ≥ 250 GB on a worker | the GLM checkpoint is 175.7 GB. The HF-cache hard links cost nothing extra. DFlash2 is 2.3 GB, the recipe image tens of GB, and a carried image tarball the same again while it is staged |
| QSFP / ConnectX-7 cable (200 GbE) | see A2 | the fabric: NCCL over RoCE, the ranks' rendezvous, weight sync |
| Ethernet to the LAN | 1 | first boot, ssh at home, apt. Not needed by the ranks |
| A Mac (or Linux laptop) | — | NVIDIA Sync for first-boot addressing and the ssh alias, plus OpenBeast client mode |
| USB stick / external SSD | — | only for offline staging ([H](#h-fully-offline-operation)). Size it for the weights (≥ 256 GB) if you carry them |

### A2. Cabling and addressing for 1, 2, 3, 4 and 5-8 Sparks

The scripts handle **1 to 8** Sparks, one GPU each, one tensor-parallel rank per
Spark. A profile's `TENSOR_PARALLEL_SIZE` therefore equals the node count.
**Only N = 1 and N = 2 have run on hardware.** Our pair is the N = 2 case.
N ≥ 3 is built and tested against stubs: VERIFY ON HARDWARE.

| Sparks | Fabric | Addressing | Notes |
|---|---|---|---|
| 1 | none | none | `TENSOR_PARALLEL_SIZE=1`, rank 0 only. `spark-guard.sh --no-fabric` makes the engine ports loopback-only |
| 2 | one QSFP cable between the two Sparks | NVIDIA Sync addresses both PCIe "rails" of the port (`10.100.216.0/24` and `10.100.217.0/24` on our pair) | both rails carry the all-gathers when both twins are addressed. **Measured** |
| 3 | a switch, or one fabric subnet that every Spark is on | every node on one subnet | a cabled ring with a subnet per cable cannot rendezvous through `spark-node.sh`, because every rank meets at one `SPARK_HEAD_IP`. `spark-cluster.sh check` flags this. The GLM recipe's `start-tp3.sh` (experimental) does handle a ring: LAN rendezvous plus `NCCL_IB_SUBNET_AWARE_ROUTING=1`. That is the measured 3-Spark path for GLM today |
| 4 | a cabled ring (each Spark cabled to its two neighbours; no switch), or a switch | a subnet per cable in the ring; one subnet with a switch | each Spark has two QSFP ports, so four is the largest switchless layout: GLM 1104's `start-tp4-switchless.sh` ran TP4 on a ring (CLAIMED by its author). Like the triangle, the ring needs a LAN-side rendezvous plus `NCCL_IB_SUBNET_AWARE_ROUTING=1`; `spark-cluster.sh check` does not know it yet (beast-recipe's `topology` gap). Corrected 2026-11-22 ([OWN_RECIPES_INVESTIGATION](reviews/OWN_RECIPES_INVESTIGATION-2026-11-22.md) §4.5): this row used to say a switch is needed past 3 |
| 5-8 | a RoCE-capable 200 GbE switch | every node on the fabric subnet(s) | no cabled topology reaches past 4 |

After cabling, check on each Spark that the links are Up and addressed:

```bash
ibdev2netdev                      # each ConnectX netdev and "(Up)"
ip -4 -o addr show | grep 10.100  # the fabric addresses NVIDIA Sync assigned
```

### A3. Accounts and credentials

| What | Why | Where it lives (never in git, never on argv) |
|---|---|---|
| Linux user (`max`) on every Spark, in `sudo` and `docker` | everything runs as this user; root only for [B](#b-bare-boxes-to-root-setup) | — |
| An ssh key from the Mac, and one from spark-1 to every worker | NVIDIA Sync installs the Mac's key. The head's key is what lets `start.sh` and `spark-cluster.sh` reach the workers over the fabric | `~/.ssh/` |
| Hugging Face account and token | downloading the weights. Only needed once, while the box is online | `~/.config/openbeast/hf-token` (0600). The recipe's `local.sh` reads it from there. `spark.env` names it with `HF_TOKEN_FILE` |
| Tailscale account (the tailnet `tail4109f9.ts.net`) | remote access, client mode, the published URLs | joined by `spark-root-setup.sh` (`tailscale up`, which prints a login URL on first run) |
| GitHub access to the repo | cloning `openbeast` | — |
| Open WebUI admin | the browser chat | created in [E5](#e5-webui-auth-and-the-admin-account-before-publishing) |
| beast-gate device keys | every client that calls inference | `scripts/clients.sh enroll` prints each key once ([E7](#e7-device-keys-for-beast-gate)) |

### A4. The artifact checklist (what to stage for an offline build)

Everything a Spark needs that does not come over the fabric, where it comes
from, and what checks it when it arrives. Section [H](#h-fully-offline-operation)
has the commands.

| Artifact | Size | Comes from | Checked by | Section |
|---|---|---|---|---|
| `libnccl2 2.28.9-1+cuda13.0` and `tailscale 1.104.7` .debs | ~200 MB | `apt-get download` on an online Spark | sha256 pins in `spark-root-setup.sh` (`--check-debs`) | [H3](#h3-debs-libnccl2-and-tailscale) |
| The OpenBeast repo at one commit | ~100 MB | `git clone` / `git bundle` | the commit id | [H2](#h2-the-repo) |
| GLM-5.3-Flash Ablit EXL3 weights | 175.7 GB | the Hub at `57edefd2…` | `model-fetch.sh --verify` against the committed lock | [C](#c-weights) |
| DFlash2 drafter | 2.3 GB | the Hub at `6cc6c481…` (Apache-2.0) | the blob-hash loop | [C4](#c4-the-drafter-dflash2) |
| Recipe image `tensorfold-glm53:v0.6.0` | tens of GB | `prepare.sh`, or `docker pull` from ghcr | image ID `sha256:ead01740…` after `docker load` | [H4](#h4-container-images) |
| The recipe checkout `~/openbeast-recipes/glm53` | small | git (MiaAI-Lab) | commit `33b50fd` | [D1](#d1-the-glm-recipe-the-alternative-deployment) |
| Native TensorFold 1.2.10 installed; 1.3.4 the installer's default and the ark's pin (optional) | ~55 MB an archive | GitHub release | ed25519 signature + SHA256SUMS (`install-native.sh --stage` / `--from`, always with `--version`); the ark catalog pins 1.3.4's three archive sha256s | [H5](#h5-native-tensorfold-and-the-engine-plane) |
| Python wheels for the command center | ~200 MB | `pydeps.sh wheelhouse` or `bundle.sh build` **on a Spark** | `agents/requirements.lock` hashes | [H6](#h6-the-command-center-wheels-and-compose-images) |
| Compose images (open-webui, searxng, extensions) | a few GB | `docker save` on an online Spark | digest pins; `bundle.sh install` rewrites them to the content ID | [H6](#h6-the-command-center-wheels-and-compose-images) |
| pytest (to run the test suites) | small | `.github/ci-requirements/test.txt` | `--require-hashes` | [H7](#h7-test-and-language-toolchains) |
| beast-docs mirror | MBs | `scripts/docs.sh sync` + `pack` | `unpack` re-hashes against `BUNDLE.json` | [H8](#h8-beast-docs-mirror) |
| zig 0.16 aarch64 (optional) | ~50 MB | ziglang.org | sha256 `ea4b09bf…` | [H7](#h7-test-and-language-toolchains) |
| DeepSeek, the default: pack folders (`~/Documents/…-2.9-bpw-v2`, the stock `…-2.9-bpw`), the Engram base shards 47 and 48, the kernel kit **with** its `.git`, `~/openbeast-recipes/deepseek-prep` (runtime at `c7f2b85`, helpers), the unit file | about 210 GB per pack (197 GiB, measured for -v2; packs share identical shards by hard link), 203 GB Engram | the backup overlay (host-local; not in this repo) | the backup's manifest (`verify --deep`); the runtime commit and the kit's `.git` by `spark-restore.sh rebuild` | [SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md), step 22 |
| DeepSeek image `tensorfold-dsv41:exl3-tp2-8a98ae503d85` | 24.9 GB | built locally from Mia's recipe; carried with `docker save` | `rebuild` checks the image is present; the pack scripts check its patch label | [SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md) §1 |

---

## B. Bare boxes to root setup

### B1. First boot

1. Boot each Spark, create the user (`max`), and connect the LAN.
2. Pair each Spark with NVIDIA Sync on the Mac. Sync addresses the ConnectX
   rails (it owns `/etc/netplan/99-nvidia-sync-cluster.yaml`; nothing in
   OpenBeast edits it) and installs the ssh alias and key.
3. Clone the repo on **every** Spark, at the **same commit**:

   ```bash
   git clone https://github.com/MaximilianKhan/openbeast ~/openbeast
   cd ~/openbeast && git checkout main   # or whatever commit the cluster runs
   git log --oneline -1                                 # write it down; every node must match
   ```

4. Give the head an ssh key that reaches every worker over the fabric. The
   recipe's `start.sh` and `spark-cluster.sh` use it:

   ```bash
   # on spark-1
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''        # only if it has none
   ssh-copy-id max@10.100.216.2
   ssh max@10.100.216.2 true && echo fabric-ssh-ok
   ```

### B2. `spark-root-setup.sh`: the only root step (once per Spark)

```bash
cd ~/openbeast
sudo scripts/backends/spark-root-setup.sh spark-1 --guard     # on spark-1 (the head)
sudo scripts/backends/spark-root-setup.sh spark-2 --guard     # on spark-2 (each worker: spark-N)
```

The argument is the **tailnet name** the box joins as. Run it as yourself
through sudo, because it needs `SUDO_USER`. It is idempotent. It does four
things and nothing else:

1. **libnccl2 `2.28.9-1+cuda13.0`**, installed and held. The native TensorFold
   binary dlopens `libnccl.so.2`; without it, `tensorfold capabilities --json`
   shows NCCL `found:false`. The driver is R580 = CUDA 13.0, so this is the
   +cuda13.0 build, not apt's newest +cuda13.5.
2. **`loginctl enable-linger` for you**, so `systemctl --user` services run
   without a login session and survive logout.
3. **Tailscale from its signed apt repo.** The repo key is checked against the
   pinned fingerprint `2596A99EAAB33821893C0A79458CA832957F5868` before apt
   trusts it. Then it runs `tailscale up` with the given name and
   `--operator=$SUDO_USER`, so you can run `tailscale serve` without sudo.
   The first run prints a login URL.
4. **ufw allow rules** for each ConnectX link that is Up, from that link's own
   private subnet, for the day ufw gets enabled. **ufw is installed but
   disabled on DGX OS**, and this script does not enable it, because its
   default deny would also drop LAN ssh. While ufw is off those rules filter
   nothing, and the script prints a warning that says so. `--guard` then runs
   `spark-guard.sh` ([B3](#b3-firewall-spark-guard)), which is
   what actually closes the engine ports.

Other flags:

- `--no-tailscale` skips step 3, for a box with no tailnet to join.
- `--debs DIR` and `--check-debs DIR` are the offline path ([H3](#h3-debs-libnccl2-and-tailscale)).
- `--check-keyring FILE` checks a Tailscale keyring file against the pin, without root.
- `--serve [--port N]` adds a ufw rule for a **keyed** engine on `tailscale0`.
  Leave it off. Inference stays on loopback behind beast-gate.

What it never touches: netplan, the MTU, the driver, Docker. The fabric MTU
has its own opt-in root script, `spark-fabric-mtu.sh`
([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)), which adds one netplan drop-in
for the next reboot and still leaves NVIDIA Sync's file alone.

**Expected output.** This is an excerpt assembled from the script's own
messages, not a captured transcript:

```
==> 1/4 libnccl2 2.28.9-1+cuda13.0
already installed                                   # or apt's install log, then the hold
libnccl.so.2 visible to the loader

==> 2/4 linger for max

==> 3/4 Tailscale as 'spark-1'
  Tailscale repo key verified (2596A99EAAB33821893C0A79458CA832957F5868)
tailnet IPv4: 100.66.191.118

==> 4/4 ufw (fabric allow rules) and exposure
  ufw fabric rules on 2 link(s)

  !! ufw is inactive — the ufw rules above are recorded but FILTER NOTHING.
  ...
  !!   sudo …/spark-guard.sh            (or re-run this with --guard)

==> spark-guard
== spark-guard installed: /etc/openbeast/spark-guard.nft, openbeast-spark-guard.service (enabled; loads before the network at boot)
  note: ufw is inactive. spark-guard does not need it and does not enable it.
Verify from the Mac: nc -z -w 3 <this Spark's tailnet IP> 29551   # must FAIL while a rank listens

Done on spark-3dc6 as tailnet 'spark-1' (100.66.191.118).
```

**Verify:**

```bash
dpkg-query -W libnccl2                          # 2.28.9-1+cuda13.0
apt-mark showhold | grep nccl                   # libnccl2
loginctl show-user "$USER" -p Linger            # Linger=yes
tailscale status | head -3                      # this box, online
tailscale ip -4
```

**The docker group and the user manager (reboot once).** If you were added to
the `docker` group during setup (`sudo usermod -aG docker max`), the systemd
**user** manager that runs `openbeast` and `spark-hydra-inference` was started
before the group change and cannot reach Docker. The symptoms are WebUI and
SearXNG DOWN, and `.run/stack.log` saying "docker daemon not ready". Logging
out is not enough, because linger keeps the user manager alive. **Reboot once.**
Then check:

```bash
systemd-run --user --wait --pipe docker info >/dev/null && echo user-manager-sees-docker
```

### B3. Firewall: spark-guard

**Why it exists (found 2026-11-15).** ufw is installed but **disabled** on both
Sparks (`/etc/ufw/ufw.conf` has `ENABLED=no`), so nothing was filtered. Rank 0's
TensorFold TCPStore listens on `*:29551` with no authentication, and it answered
`nc -z 100.66.191.118 29551` from the Mac over the tailnet (very likely the LAN
too). Enabling ufw would not have closed the tailnet path, because Tailscale's
own `ts-input` chain accepts `tailscale0` traffic before ufw's rules run. sshd
also accepts passwords on `0.0.0.0:22`.

**What it does.** `scripts/backends/spark-guard.sh` closes the engine ports and
touches nothing else:

- It loads one nftables table of its own, `inet openbeast_guard`, with an input
  hook at priority `filter - 10`. That runs ahead of iptables-nft (ufw, Docker)
  and Tailscale.
- It drops TCP to 29501-29502, 29551-29552, 29583-29584 and 29620-29621 (the
  DeepSeek recipe's `MASTER_PORT`, added 2026-11-18) (plus spark.env's
  master ports, each with its +1, and any `--port N`). Every rendezvous port is
  guarded with its +1, whether it is a default or comes from spark.env, so the
  same port never renders two ways. It also drops TCP to the Spark's own fabric
  addresses, where NCCL's ephemeral bootstrap listeners are. Both drops apply
  **unless** the packet arrives on `lo` or on a ConnectX netdev
  (`ibdev2netdev`).
- It never flushes the ruleset. Ubuntu's `nftables.service` would flush it,
  wiping Docker's and Tailscale's rules, so that service is not used. Instead
  its own oneshot unit, `openbeast-spark-guard.service`, reloads
  `/etc/openbeast/spark-guard.nft` before `network-pre.target` at boot.

Unaffected: RoCE (RDMA bypasses netfilter), the NCCL bootstrap on the fabric,
ssh, `tailscale serve` (:443, :8443-8447, :8889) and Docker networking.

```bash
# on EACH Spark, from ~/openbeast at a commit that has it
scripts/backends/spark-guard.sh --render          # read the ruleset first (no root)
sudo scripts/backends/spark-guard.sh              # load + persist (idempotent); same as --apply
sudo scripts/backends/spark-guard.sh --status     # current or STALE, table, counters, listeners, ssh posture
sudo scripts/backends/spark-guard.sh --remove     # undo: table, unit and rules file gone
#   --port N (repeatable)  --iface IF (repeatable)  --no-fabric (one Spark, no peer: loopback-only)
```

What `--render` printed on spark-1 on 2026-11-16 (before 29620-29621 joined the defaults; today's code adds `29620-29621` to the `tcp dport` set and the `guarded TCP` line, so re-run `sudo scripts/backends/spark-guard.sh` on both Sparks after updating):

```
# fabric: enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1   fabric IPv4: 10.100.216.1 10.100.217.1   guarded TCP: 29501-29502 29551-29552 29583-29584
# args:
table inet openbeast_guard {
	chain input {
		type filter hook input priority filter - 10; policy accept;
		iifname "lo" accept
		iifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } accept comment "ConnectX-7 fabric"
		tcp dport { 29501-29502, 29551-29552, 29583-29584 } counter drop comment "engine rendezvous: fabric and loopback only"
		ip daddr { 10.100.216.1, 10.100.217.1 } meta l4proto tcp counter drop comment "fabric addresses (NCCL bootstrap): fabric only"
	}
}
```

`--status` without root still shows the unit and the listeners. It says
`loaded: unknown (listing nftables needs root …)` for the table. It also
re-renders the ruleset from the current code, spark.env and fabric (with the
`--port` / `--iface` / `--no-fabric` inputs the install recorded on its
`# args:` line) and compares it with the installed file: `rules: current`, or
`rules: STALE` with the lines that differ and the command to re-run. On
2026-11-16 both Sparks showed STALE: their files, written 2026-11-15 before
`VLLM_MASTER_PORT=29501` reached spark.env, guard `29501` but not `29502`.
The fix is one `sudo scripts/backends/spark-guard.sh` on each Spark (an
operator step, offline-safe: it reads only local files):

```
== spark-guard on spark-3dc6
rules file: /etc/openbeast/spark-guard.nft
rules: STALE — the installed file differs from what this checkout renders now (code, spark.env ports or fabric changed). Re-run: sudo scripts/backends/spark-guard.sh
  installed: # fabric: … guarded TCP: 29501 29551-29552 29583-29584
  …
  now:       # fabric: … guarded TCP: 29501-29502 29551-29552 29583-29584
unit: openbeast-spark-guard.service enabled, active
TCP listeners (what each could be reached on):
  0.0.0.0:22
  100.66.191.118%tailscale0:443      … :8443 :8444 :8445 :8446 :8447 :8889   (tailscale serve)
  10.100.216.1:35335 …                                               (NCCL bootstrap, fabric only)
  127.0.0.1:3000 :3001 :3002 :3003 :3004 :8000 :8090 :8888           (the stack, loopback)
```

**Verify from another device** (the Mac, while the pair is serving):

```bash
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed    # must print closed (measured 2026-11-15: closed)
nc -z -w 3 192.168.40.153 29551 && echo STILL OPEN || echo closed    # LAN, from home
nc -z -w 3 100.66.191.118 22 && echo ssh-still-reachable              # ssh must stay open
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | head -c 80 # tailscale serve still works
```

The pair keeps serving through the load, because the fabric is accepted before
the drops. After a reboot, `systemctl status openbeast-spark-guard` shows
`active (exited)`, and the counters in `sudo … --status` count the drops.
Re-run the guard whenever the fabric addresses change (NVIDIA Sync
re-addressing, a new switch), after a pull that changes the guard, and after
editing a master port in spark.env. `--status` says `STALE` when one of those
happened and the guard was not re-run.

**sshd hardening is opt-in and OFF by default.**
`sudo scripts/backends/spark-guard.sh --harden-ssh --yes` writes
`/etc/ssh/sshd_config.d/00-openbeast-guard.conf` (`PasswordAuthentication no`,
`KbdInteractiveAuthentication no`), validates it with `sshd -t`, and reloads
sshd. It refuses unless your account has a key in `authorized_keys`. Both
Sparks have one: the Mac uses the NVIDIA Sync key, and spark-1 to spark-2 uses
spark-1's own key. Its warning explains how not to lock yourself out:

1. Run it from an open ssh session.
2. From the Mac, test a NEW key login before closing that session.
3. From spark-1, also test `ssh max@10.100.216.2 true`.

`--unharden-ssh` undoes it.

### B4. Fabric MTU 9000: spark-fabric-mtu

**Status, 2026-11-17: LIVE on both Sparks since 14:20.** Staged with
`--stage` on each, both rebooted (worker, then head), then `--verify` passed
on both: netdev MTU 9000 and RoCE `active_mtu 4096` on every rail, and
8972-byte don't-fragment pings to the peer over both rails. `--status` on
each Spark (no sudo) reads `MTU(now) 9000`, `active_mtu 4096` on all four
ConnectX netdevs. **The performance A/B is not measured yet** (the plan is
below); until it is, no speed gain is claimed anywhere.

Before 2026-11-17 (Max, 2026-11-16: "We absolutely need to fix this on the
next restart.") the pair ran at netdev MTU 1500 on the ConnectX rails, which
gives RoCE `active_mtu 1024` (seen live on spark-1: `ip -d link` said
`mtu 1500 … maxmtu 9978`, and `ibv_devinfo -d rocep1s0f0` said
`max_mtu 4096 (5)`, `active_mtu 1024 (3)`). The target was netdev MTU 9000,
which gives `active_mtu 4096`.

**Why it matters.** RoCE carries each RDMA message in packets of
`active_mtu` bytes. At 1024, a bulk transfer is 4x as many packets as at
4096, with the per-packet headers, completions and NIC work that go with
them. Two kinds of traffic on this pair are bulk:

- **Prefill all-gathers.** A long prompt (tens of thousands of tokens) moves
  large activation tensors between the two ranks at every layer. This is
  where the TTFT of a big prompt goes, and where a bigger MTU should show.
- **Weight-sync and other bulk copies** over the fabric (C3's carry to the
  worker, any NCCL broadcast of large buffers).

TP-2 decode all-reduces are small (one token's activations) and
latency-bound, so the 4-stream decode tok/s should barely move. That is a
prediction, not a measurement: the A/B below is how we find out.

**What the script does.** `scripts/backends/spark-fabric-mtu.sh` (root, one
Spark per run, like `spark-guard.sh`):

- Finds the fabric netdevs with `ibdev2netdev`: every ConnectX RoCE port, Up
  or Down (the cable uses different ports on the two boxes: spark-1's
  `enp1s0f0np0` / `enP2p1s0f0np0` are Up, spark-2's `enp1s0f1np1` /
  `enP2p1s0f1np1`). It leaves out any netdev that carries a default route and
  any netdev netplan does not already manage, and refuses if none is left.
  The LAN NIC `enP7s7` is not a ConnectX port, so it is never a candidate.
- Writes ONE file, `/etc/netplan/99-zz-openbeast-fabric-mtu.yaml` (0600
  root):

  ```yaml
  network:
    version: 2
    ethernets:
      enp1s0f0np0:
        mtu: 9000
      enp1s0f1np1:
        mtu: 9000
      enP2p1s0f0np0:
        mtu: 9000
      enP2p1s0f1np1:
        mtu: 9000
  ```

  It sorts after NVIDIA Sync's `99-nvidia-sync-cluster.yaml`, and netplan
  merges a later file's keys into the same interface: Sync's addresses stay,
  only `mtu` is added, and Sync's file is never edited.
- Runs `netplan generate` to validate the merged config and write the
  boot-time backend files (on the pair: NetworkManager keyfiles in
  `/run/NetworkManager/system-connections/netplan-<dev>.nmconnection`). It
  then checks that netplan rendered the same interfaces with the same backend
  as before and `mtu=9000` on each. If not, it puts the old state back and
  says so. Checked against the real netplan 1.1.2 (the pair's version) with a
  copy of Sync's layout: the drop-in merges, the address is kept.
- **Never runs `netplan apply`.** `generate` only writes `/run` configs and
  reloads udev rules and systemd units. The live links keep MTU 1500 until
  the reboot, so the two ends of the cable never disagree under a running job.

**Why both Sparks must reboot.** If one end of the cable is at 9000 and the
other at 1500, every frame over 1500 bytes is dropped on that link. ping and
ssh keep working (small packets), so it looks healthy, while NCCL's bulk
transfers stall or hang the collective. Stage both, reboot both, verify both.
Until the reboot, a NetworkManager restart or a fabric link flap could bring
the new MTU up early on one box. `--status` shows it (the "MTU(now)" column).
If you see 9000 on one Spark only, reboot the other one too.

**Step 1: stage on each Spark (from the Mac).** `-t` gives sudo a terminal
for the password:

```bash
! ssh -t spark-1.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage'
! ssh -t spark-2.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage'
```

(The leading `!` runs the line in a shell from a Claude Code prompt; drop it
in a plain terminal.) Each checkout must have this script: `git pull` first
if it does not. `--dry-run` (no sudo) prints the file and the actions first.
Expected output ends with:

```
== netplan generate (validates the merged config; the live links are not touched)
   enp1s0f0np0: boot config now mtu 9000 (NetworkManager)
   ...
STAGED on spark-3dc6. Nothing changed on the live links (still MTU 1500).
It takes effect on the NEXT REBOOT, and that reboot must happen on BOTH Sparks:
```

Check either box any time, no sudo needed for the live columns:

```bash
ssh spark-1.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --status'
#   NETDEV          IBDEV          STATE MTU(now)  active_mtu  boot config   in drop-in
#   enp1s0f0np0     rocep1s0f0     Up    1500      1024        (sudo)        no
```

The boot-config and drop-in columns need sudo to read (the files are 0600);
with sudo they show `9000` / `yes` once staged.

**Step 2: measure A (before), still at MTU 1500.** See the A/B plan below.
Staging does not change the live links, so A can run before or after Step 1.

**Step 3: reboot both.** Stop the command center and inference first
(`beast-spark down`: both units, whichever model is selected), so no collective
is in flight while one box is at 9000 and the other at 1500. Then reboot the
worker, then the head:

```bash
ssh spark-1.tail4109f9.ts.net '~/.local/bin/beast-spark down'
! ssh -t spark-2.tail4109f9.ts.net 'sudo reboot'
! ssh -t spark-1.tail4109f9.ts.net 'sudo reboot'
```

Nothing else is needed. The selected inference unit (`deepseek-v41-inference`,
DeepSeek, by default) is enabled on spark-1, starts by itself at boot, and starts
rank 1 on spark-2 over ssh. If spark-2 is not up yet, the unit fails and
retries every 60 s (`Restart=on-failure`; GLM's unit every 30 s). Loading
takes 2-6 min. spark-guard (B3) loads its own table before the network comes
up, as on any boot. Then `scripts/healthcheck.sh` on spark-1 should report
all 9 services healthy (E4).

**Step 4: verify, on each Spark.** `--verify` takes the **peer's** fabric
IPv4 on every Up rail, **all of them in one call**. Running it once per IP
fails: each run checks every local Up rail, and a rail with no peer IP given
is a FAIL. Read the IPs on the peer, do not copy them from here (Sync can
re-address the rails):

```bash
ssh spark-2.tail4109f9.ts.net "ip -br addr | grep -E '^enp1|^enP2'"   # the peer's rail addresses
ssh spark-1.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <spark-2 rail-1 IP> <spark-2 rail-2 IP>'
ssh spark-1.tail4109f9.ts.net "ip -br addr | grep -E '^enp1|^enP2'"
ssh spark-2.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <spark-1 rail-1 IP> <spark-1 rail-2 IP>'
```

No sudo is needed. For each Up rail it checks netdev MTU 9000 and RoCE
`active_mtu 4096` (`ibv_devinfo -d <ibdev>`). For each peer IP, it checks
that the IP routes over a fabric rail, then runs
`ping -M do -s 8972 -c 3 -W 2 -I <rail> <peer>`. That is a 9000-byte frame
with don't-fragment set, which only gets through if both ends are at 9000. A
rail with no peer IP given is a failure, so give one per rail. It prints
`VERIFIED` and exits 0, or names each `FAIL` and exits 1.

**The A/B measurement plan.** Same model, same settings, same prompts, only
the MTU differs. Run on spark-1 against the loopback API (no key, no gate;
stdlib Python, works offline). Two numbers:

1. **TTFT on a ~45K-token prompt.** A non-streaming request with
   `max_tokens: 1` takes prefill plus one token, so its wall time is the TTFT.
   Each run gets a fresh nonce at the **start** of the prompt, so no prefix
   cache can serve it. 5 runs; the first one is a warmup, report the median
   of the other 4.
2. **4-stream decode tok/s.** Four concurrent requests with a short prompt
   and `max_tokens: 512`, at temperature 0. Report the aggregate (sum of
   `completion_tokens` / wall time) and the per-stream rate. 3 rounds, median.
   DFlash2's speedup depends on the text, so the prompts must be identical
   between A and B (they are fixed below).

```bash
ssh spark-1.tail4109f9.ts.net 'python3 - ' <<'PY'
import json, statistics, threading, time, urllib.request, uuid
URL, MODEL = "http://127.0.0.1:8000/v1/chat/completions", "GLM-5.3-Flash-EXL3"
def call(content, max_tokens):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": content}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=900) as r:
        u = json.load(r)["usage"]
    return time.monotonic() - t, u
filler = "".join(f"Record {i}: sensor {i % 97} read {i * 37 % 1009} units at step {i}.\n" for i in range(2500))
ttft = []
for n in range(5):
    dt, u = call(f"[{uuid.uuid4()}]\n{filler}\nReply with one word.", 1)
    print(f"ttft run {n}: {dt:.2f} s, prompt_tokens {u['prompt_tokens']}")
    if n: ttft.append(dt)
print(f"TTFT median (runs 1-4): {statistics.median(ttft):.2f} s")
prompts = [f"Write a detailed Python implementation of {t}, with comments." for t in
           ("a red-black tree", "an LRU cache", "a JSON tokenizer", "Dijkstra's algorithm")]
aggs = []
for rnd in range(3):
    res = [None] * 4
    def run(i): res[i] = call(prompts[i], 512)
    th = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    t = time.monotonic(); [x.start() for x in th]; [x.join() for x in th]; wall = time.monotonic() - t
    toks = sum(u["completion_tokens"] for _, u in res)
    aggs.append(toks / wall)
    print(f"round {rnd}: {toks} tokens in {wall:.1f} s = {toks / wall:.1f} tok/s aggregate, "
          + ", ".join(f"{u['completion_tokens'] / dt:.1f}" for dt, u in res) + " per stream")
print(f"4-stream aggregate median: {statistics.median(aggs):.1f} tok/s")
PY
```

**Status of the A/B, 2026-11-17: not run.** No MTU-1500 "A" was recorded
before the reboot, so the B run at 9000 needs a baseline: either an `--unstage`
window (reboot both into 1500, run A, `--stage` and reboot again) or, weaker,
the 2026-11-13 numbers below, which differ in more than the MTU (the KV pool
size since 2026-11-15). Record which one was used.

Check that `prompt_tokens` is near 45K (2500 filler lines is an estimate of
~18 tokens a line; change `range(2500)` until it is, then keep it the same for A and B).
Record both runs here, under "Serving configuration", with the date and
`active_mtu` (1024 for A, 4096 for B). For reference, the 2026-11-13 numbers
are ~145 / 224 tok/s aggregate (prose / code), TTFT ~160-260 ms on short
prompts. Expect B's TTFT to drop on the long prompt and decode to be about
flat. If B is not better, or worse, keep the numbers anyway and decide
whether to roll back. A failed experiment is still a result.

**Rollback.** Any time, on both Sparks, then reboot both:

```bash
! ssh -t spark-1.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --unstage'
! ssh -t spark-2.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --unstage'
```

`--unstage` removes the drop-in and re-runs `netplan generate`. Like
`--stage`, it takes effect at the next reboot, so reboot both. If the fabric
does not come up after the reboot, and you cannot reach a Spark over the
fabric or the tailnet, the LAN still works: `enP7s7` was never touched. Use
`ssh -o HostName=<LAN IP> spark-N` (0. The pair at a glance). Failing that,
use the box's own console (keyboard and monitor). From there:

```bash
sudo rm /etc/netplan/99-zz-openbeast-fabric-mtu.yaml
sudo netplan generate && sudo reboot
```

**Offline.** None of this needs a network beyond the boxes themselves. The
script, netplan, `ibdev2netdev`, `ibv_devinfo` and `ping` are all local. The
verify pings travel the direct QSFP cable, and the A/B runs on loopback. On a
pair with no tailnet, run the same commands over LAN ssh or at the console.

**Tests.** `tests/test_spark_fabric_mtu.sh` (CI's spark shell job and
`tests/run_tests.sh`) runs every mode against fakes of `ibdev2netdev`,
`netplan`, `ip`, `ibv_devinfo` and `ping`, under a scratch
`SPARK_FABRIC_ROOT`. It covers the file's content and perms, that `apply` is
never called, refusing the default-route NIC, the rollbacks, unstage, status
and verify failures. On Ubuntu it also checks the merge with the real `netplan generate
--root-dir`. Test seams: `SPARK_FABRIC_ROOT` (a path prefix, also passed to
netplan as `--root-dir`), `SPARK_FABRIC_NETPLAN_DIR`, `SPARK_FABRIC_NM_DIR`,
`SPARK_FABRIC_NETWORKD_DIR`, and `IBDEV2NETDEV` / `NETPLAN` / `IP` /
`IBV_DEVINFO` / `PING` for the commands.

---

## C. Weights

The recipe and OpenBeast's own launchers read weights in two different places.
Getting this wrong is the most common way to serve unverified bytes, so here is
the model:

- **OpenBeast's copy** is `~/openbeast-models/<profile>`. `model-fetch.sh`
  writes it, verifies it against the Hub's own hashes for one commit, and
  **locks** it: `scripts/backends/models/<profile>.lock` records each file, its
  size and its sha256. The lock is committed.
- **The recipe's copy** is the HF-cache layout,
  `~/.cache/huggingface/hub/models--<org>--<name>/snapshots/<rev>/`. The
  recipe's `snapshot()` reads only that, and it checks `config.json` there on
  every rank.
- **Hard links make them the same bytes.** Then the lock vouches for what the
  recipe serves. A re-download, or an editor save that replaces a file by
  rename, breaks the link silently. Check `stat -c %h` (≥ 2) after anything
  touches the cache.

### C1. Fetch (online) or verify a local copy

Profile: `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.env`, with
its lock committed next to it.

```bash
cd ~/openbeast
# online, from the Hub: downloads only the pinned REVISION, verifies, renames into place
HF_TOKEN_FILE=~/.config/openbeast/hf-token scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold

# a copy that is already on disk (a USB SSD, rsync from another box): re-hash against the committed lock
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --locate   # what launchers call: fast check, prints the dir
```

Its guarantees:

- Only the profile's `REVISION` (a commit SHA) is ever requested.
- Every file is checked against the Hub's hash for that commit: LFS files by
  sha256, small files by git blob SHA-1.
- Nothing appears under the final name until every file has passed. Downloads
  stage in `MODELS_DIR/.NAME.partial`, and an interrupted run resumes.
- The lock is written on the first fetch and never rewritten. Every later
  fetch or verify must reproduce it exactly.
- Pickles, GGUF and `original/` copies are skipped unless `FETCH_INCLUDE` names
  them.

Exit codes:

- `--verify` and `--locate` exit **3** when there is no lock. Run them in a
  checkout that has the committed lock.

`--verify` (and recording a local lock) hashes 4 files at once
(`MODEL_FETCH_HASH_WORKERS`, `1` = serial). On a GB10 one thread hashes about
1.8 GB/s and four about 5.5 GB/s (measured on spark-2, 2026-11-16), so the
164 GB tree takes about half a minute instead of about a minute and a half.
The report is in the lock's order whatever the timing.
- For a local `SOURCE` with no lock, `--verify --trust-first-use` records a
  lock from the bytes on disk. That is "trust on first use": say so in the
  commit.

`--verify` used to fail with "unlocked file .cache/huggingface/…" on a copy
made with `hf download --local-dir`. This is fixed: hidden paths are skipped by
both lock and verify.

### C2. Hard-link into the HF-cache layout the recipe reads

On spark-1:

```bash
REV=57edefd2f5d9b371c8345883304d5af68b52fa24
C=~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit
mkdir -p "$C/refs" "$C/snapshots/$REV"
printf %s "$REV" > "$C/refs/main"
cp -al ~/openbeast-models/glm53-flash-ablit-exl3/. "$C/snapshots/$REV/"   # hard links, no copy
stat -c '%h %i %n' ~/openbeast-models/glm53-flash-ablit-exl3/config.json "$C/snapshots/$REV/config.json"  # same inode, links >= 2
```

### C3. Carry the weights to every worker

There are two ways, and both keep the hard links.

**By hand (what built our pair).** Send both trees in ONE rsync. `-H` only
links files within one transfer, and `--relative` with `/./` keeps the paths
under the remote home:

```bash
rsync -aH --partial --relative ~/./openbeast-models/glm53-flash-ablit-exl3 \
  ~/./.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit max@10.100.216.2:
ssh max@10.100.216.2 'cd ~/openbeast && scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify'
```

`--verify` on spark-2 needs the lock in spark-2's own checkout. If it is
missing, `--verify` fails rather than recording one. The verify vouches for the
snapshot only while the links hold, so re-run the `stat` line from C2 on each
Spark after anything rewrites the cache.

**With `spark-cluster.sh sync-weights`.** This is the general 1-8 node path. It
needs `scripts/backends/cluster.env` on the head (see
[D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)):

```bash
scripts/backends/spark-cluster.sh sync-weights --profile glm53-flash-ablit-exl3-tensorfold [--dry-run]
```

What it does:

- It refuses unless the head's lock pins every artifact (the model, plus the
  drafter if the profile names one).
- It checks that each worker's profile and lock hash-match the head's. A worker
  that has no lock gets the head's.
- It copies over the fabric, verifies on arrival, and confirms the worker's own
  launcher will find the copy.
- It copies each directory as `SYNC_STREAMS` (default 4, 1..16) parallel
  whole-file rsyncs, the files split by size. One ssh stream is
  cipher-bound on a GB10: measured spark-1 → spark-2 on 2026-11-16, one
  stream did 0.44-0.48 GB/s (about 6 min for the 164 GB tree) and four did
  1.73 GB/s together (about 1.6 min). Four leaves the 200 Gb link room for
  the RoCE all-gathers while the pair serves. The hand-made rsync above
  stays one stream on purpose: `-H` keeps hard links only within one
  transfer.

It syncs `~/openbeast-models/<profile>` (OpenBeast's layout). For the recipe,
still make the HF-cache hard links on each worker as in C2. On our pair the
hand-made rsync above did both at once. Untested on our pair, since the pair
has never run `sync-weights`, though spark-1 does have a `cluster.env`
(written 2026-11-15: `CLUSTER_NODES="local max@10.100.216.2"`,
`CLUSTER_NAME=spark-hydra`, `CLUSTER_REPO=~/openbeast`). Tested against stubs
in `tests/test_spark_cluster.sh`.

### C4. The drafter, DFlash2

`incoai/GLM-5.3-Flash-DFlash2 @ 6cc6c481eee930a5139619ed919f78726371adbc`
(**Apache-2.0**, the 2026-11-10 "Release under Apache 2.0" commit) lives only in the HF cache, in `hf download` layout, and no OpenBeast lock
covers it. Copy the whole repo dir; its relative symlinks into `blobs/` survive
`rsync -a`. Then check every blob against its own name. LFS blobs are named by
their sha256, small files by their git blob SHA-1:

```bash
D=.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2
rsync -a --partial --relative ~/./$D max@10.100.216.2:     # or from a USB copy of the same dir
cd ~/$D/blobs && for b in *; do
  case ${#b} in
    64) [ "$(sha256sum < "$b" | cut -c1-64)" = "$b" ] || echo "BAD $b" ;;
    40) [ "$(git hash-object "$b")" = "$b" ] || echo "BAD $b" ;;
  esac
done; echo checked
readlink ~/$D/snapshots/6cc6c481eee930a5139619ed919f78726371adbc/model.safetensors
# -> ../../blobs/b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac (2.34 GB)
```

**The re-pin from `bf582e4e` (CC BY-NC-ND) to `6cc6c481` (Apache-2.0),
2026-11-23.** The Hub tree API lists the same git blob ids at both revisions
for `config.json` (`083085aa…`), `.gitattributes`, `assets/dflash2-figure.png`
and `model.safetensors` (LFS `b038e1d9…`); only `README.md` differs (its licence
front matter: `d70cae7d…` → `512ace8d…`, 2,396 bytes). So a cache that holds
`bf582e4e` needs one 2.4 KB file, no weight download. On each Spark (the
README fetched once on a box with the internet, or carried on the drive; check
`git hash-object README.md` = `512ace8d969625bfa81f01acd5629a54718e9d1d`):

```bash
D=~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2; R=6cc6c481eee930a5139619ed919f78726371adbc
install -m 0644 README-6cc6c481.md $D/blobs/512ace8d969625bfa81f01acd5629a54718e9d1d
mkdir -p $D/snapshots/$R/assets && cd $D/snapshots/$R
ln -sfn ../../blobs/083085aaf95d59bad59c58f1160865683460254e config.json
ln -sfn ../../blobs/e404a01e1f3116ff0744366d088c8fc2ba4ab735 .gitattributes
ln -sfn ../../blobs/b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac model.safetensors
ln -sfn ../../blobs/512ace8d969625bfa81f01acd5629a54718e9d1d README.md
ln -sfn ../../../blobs/6d8dcc9a9472bddb644c881fe83050b5343d09bf5ffb0912e5598e55fe671e99 assets/dflash2-figure.png
printf %s $R > $D/refs/main      # then the blob loop above
```

Mia's recipes still default to `bf582e4e` (`glm53/scripts/config.sh`,
`glm53-1104/scripts/config.sh`); our `local.sh` overrides it with
`DFLASH2_REVISION` ([D1](#d1-the-glm-recipe-the-alternative-deployment)). Both
snapshots stay in the cache (they share the blobs), so either revision serves
offline. The 1104 recipe and D2 read a flat copy instead:
`~/openbeast-models/glm53-dflash2-6cc6c481` (hard links to the same blobs plus
a `.revision` file), on both Sparks.

That proves the blobs are the bytes their names claim. That the snapshot is the
pinned revision rests on the names above (read off spark-1, 2026-11-15).

---

## D. Inference

There are three ways to run the engine. **Mia's recipes serve the pair:**
DeepSeek's (the default; [DeepSeek deployment](#deepseek-deployment)) and
D1, GLM's (the alternative). D2 is OpenBeast's own general launcher, the challenger. D3 is the one-command
engine plane for single-host and pin management.

<a id="d1-the-mia-recipe-what-serves-today"></a>

<a id="d1-the-glm-recipe-rollback-deployment"></a>

<a id="d1-the-glm-recipe-the-default-deployment"></a>

### D1. The GLM recipe (the alternative deployment)

The recipe is MiaAI-Lab's GLM-5.3-Flash 2× DGX Spark recipe, v1.8, commit
`33b50fd`, cloned to `~/openbeast-recipes/glm53` on spark-1. Its `start.sh`
starts rank 1 on the worker over ssh, then rank 0 locally. It stays attached to
rank 0's log, and when either rank ends it stops the other. `start-tp3.sh` is
its experimental 3-Spark variant.

**`~/openbeast-recipes/glm53/scripts/local.sh` on spark-1 (2026-11-15, the
live values).** `scripts/config.sh` sources it, and
`scripts/local.sh.example` documents every knob.

```bash
# OpenBeast spark-hydra settings (written 2026-11-13). Sourced by scripts/config.sh.
WORKER=max@10.100.216.2        # spark-2 over the ConnectX-7 link (rank 1)
ABLIT=1                        # Mia's abliterated checkpoint @ 57edefd2 (staged locally, not re-downloaded)
DRAFTER=dflash2                # Max 2026-11-13: DFlash2 — 4 streams
DFLASH2_REVISION=6cc6c481eee930a5139619ed919f78726371adbc   # 2026-11-23: the Apache-2.0 release (same weights as the recipe's default bf582e4e, CC BY-NC-ND)
# v0.6.0 has no API key: listen on loopback only. Everything outside reaches it
# through spark-hydra's beast-gate (:8443, per-device keys). 8888 is SearXNG's.
HOST=127.0.0.1
PORT=8000
# The token lives in a 0600 file, never in this file or on argv.
[[ -r "$HOME/.config/openbeast/hf-token" ]] && HF_TOKEN="$(<"$HOME/.config/openbeast/hf-token")"
# 2026-11-15 (adversarial review, ops lens): keep memory headroom for the command
# center beside rank 0 on unified memory. A fixed pool instead of "whatever is free
# at start"; a bigger host reserve; a stalled rank exits so systemd restarts the pair.
KV_POOL_GIB=10
MEMORY_RESERVE_GIB=17.5
export TF_GLM_MULTI_WATCHDOG_EXIT=1
```

What the three memory lines do. These values are live: the running container's
environment shows `TF_GLM_CACHE_GIB=10`, `TENSORFOLD_MEMORY_RESERVE_GIB=17.5`
and `TF_GLM_MULTI_WATCHDOG_EXIT=1`.

- **`KV_POOL_GIB=10`** becomes `TF_GLM_CACHE_GIB`. It caps the KV pool at a
  fixed size instead of "whatever is free at start". Unified memory freezes
  rather than OOMs, so a pool that grows into the command center's share is a
  hang, not an error.
- **`MEMORY_RESERVE_GIB=17.5`** becomes `TENSORFOLD_MEMORY_RESERVE_GIB`. It is
  the host memory TensorFold leaves free at start, for the command center
  beside rank 0. The recipe's default is 14.5 plus extras.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a rank whose collective stalls past the
  watchdog (`TF_GLM_MULTI_WATCHDOG_S`, 300 s) exits instead of hanging.
  `start.sh` then stops the other rank, and systemd's `Restart=on-failure`
  restarts the pair.

Even with these values, spark-1 has only about **9.7-9.9 GiB** of MemAvailable
while serving with the full command center up (healthcheck, 2026-11-15).
[G4](#g4-memory-headroom-and-the-gb10-stack-cap) covers what that means.

**First run (online).** `scripts/prepare.sh` is the recipe's own staging
script. It pulls or builds the image, downloads weights and the drafter, and
copies to the worker. Read the recipe's README before running it. On our pair,
the weights were staged by OpenBeast ([C](#c-weights)) and only the image came
from the recipe.

**Install the unit (on spark-1):**

```bash
mkdir -p ~/.config/systemd/user
sed "s|@RECIPE@|$HOME/openbeast-recipes/glm53|" ~/openbeast/scripts/backends/spark-hydra-inference.service \
  > ~/.config/systemd/user/spark-hydra-inference.service
systemctl --user daemon-reload
systemctl --user enable --now spark-hydra-inference
journalctl --user -fu spark-hydra-inference      # loading takes 2-6 min
curl -s http://127.0.0.1:8000/health             # {"ok": true, "backend": "tensorfold", ... "streams": {... "max": 4}}
```

What the unit sets:

- `Environment=FOREGROUND=1 PREPARE=0`. With `PREPARE=0` the unit never reaches
  the Hub, ghcr or the worker's setup at boot. `HF_HUB_OFFLINE=1` is the
  recipe's own default.
- `TimeoutStartSec=infinity`, `Restart=on-failure`, `RestartSec=30`.

Before starting it, two things must be true:

- **Both Sparks are guarded** ([B3](#b3-firewall-spark-guard)).
- **The user manager sees the docker group** (reboot once, [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark)).

**Changing a recipe setting:**

1. Back it up: `cp scripts/local.sh scripts/local.sh.bak-$(date +%Y%m%d%H%M)`.
2. Edit `~/openbeast-recipes/glm53/scripts/local.sh`.
3. Restart: `systemctl --user restart spark-hydra-inference`. Loading takes 2-6 min.

**While the unit is enabled, start and stop the pair only through
`systemctl --user`.** Never call the recipe's `start.sh` or `stop.sh` by hand,
and never `docker stop` a rank. systemd would see the exit and restart the pair
behind you (`Restart=on-failure`), or the unit would think the pair is up when
it is not.

### D2. Native TensorFold through `spark-node.sh` / `spark-cluster.sh`

**Recipes on D2.** `scripts/beast-recipe.sh` plans and applies a model on D2
end to end: it renders the profile and a `beast-recipe-<name>.service` user
unit around `spark-cluster.sh start`, and `beast-spark <name>` switches to it.
The first D2 serve on the pair is planned as `recipes/glm53-flash-exl3-native-tp2`
([BEAST_RECIPE.md](BEAST_RECIPE.md)).

OpenBeast's own launcher. It is general (any profile, 1 to 8 ranks), keyed
(`--api-key-file`), and launches only verified local weights. On our pair it is
the **challenger**: native TensorFold 1.2.10 is installed on both Sparks
(`tensorfold --version`, checked 2026-11-22) but does not serve. Serving GLM-5.3-Flash on the pair through D2 is VERIFY ON HARDWARE,
and so is comparing it with the recipe ("Not done yet").

**Install the engine** on every Spark. `install-native.sh` verifies three
things, all required: the `.sha256` names exactly this archive, its ed25519
signature checks against the vendored `release-key.pem`, and every unpacked file
matches `SHA256SUMS`.

```bash
scripts/backends/tensorfold/install-native.sh                     # latest, download + verify + install
scripts/backends/tensorfold/install-native.sh --version 1.2.10    # a specific version (the Sparks' release; the default is 1.3.4)
tensorfold capabilities --json | head                             # NCCL must be found:true (root setup installs it)
```

**Per-node settings: `scripts/backends/spark.env`**, copied from
`spark.env.example` on **each** node and parsed, never sourced. The keys that
matter:

| Key | Meaning |
|---|---|
| `TENSORFOLD_ENGINE` | `native` (default) or `container` (the frozen 0.x engine, which pip-installs from GitHub/PyPI at every start: needs the internet every time). A real container run also needs its dependencies pinned: `TENSORFOLD_IMAGE_PREBUILT=true` (an image that already holds them, pinned by `TENSORFOLD_IMAGE_ID`; nothing installed at start, so it is also the offline route), `TENSORFOLD_PIP_LOCK=FILE` (hash-locked requirements), or the acknowledged `TENSORFOLD_ALLOW_UNPINNED_DEPS=true` |
| `TENSORFOLD_VERSION`, `TENSORFOLD_BIN` | the installed version (both Sparks run `1.2.10`, read 2026-11-22; `1.3.4` is `install-native.sh`'s default since 2026-11-23), `~/.local/bin/tensorfold` |
| `TENSORFOLD_API_KEY_FILE` | 0600 key file; the native engine is keyed |
| `SPARK_IFACE`, `SPARK_IB_HCA` | this node's fabric netdev(s) and RoCE device(s); several rails comma-separated |
| `SPARK_HEAD_IP` | rank 0's fabric address. **The same on every node** (`check` enforces it) |
| `SPARK_NODE_IP` | this node's fabric address |
| `SPARK_SERVE_HOST`, `SPARK_SERVE_PORT` | rank 0's HTTP bind. Keep `127.0.0.1` when the command center is on the head; `SPARK_ALLOW_WILDCARD_BIND=false` refuses the wildcard in any spelling (`0.0.0.0`, `::`, `0`, `::0`, `[::0]` …). A server with no API key (the container engine, or `SPARK_NO_API_KEY=true`) binds loopback only unless `SPARK_ALLOW_KEYLESS_BIND=true` |
| `MODELS_DIR`, `HF_CACHE`, `HF_TOKEN_FILE` | where verified weights live |
| `VLLM_IMAGE`, `VLLM_IMAGE_DIGEST`, `VLLM_IMAGE_ID`, `TENSORFOLD_IMAGE*` | container engines. `*_IMAGE_ID` wins over a digest, which is what makes a `docker load`ed image work offline ([H4](#h4-container-images)) |
| `VLLM_MASTER_PORT`, `TENSORFOLD_MASTER_PORT` | rendezvous ports; spark-guard guards them (each +1) too. It reads them from this file only, never from the environment or another `--env` file, so move a port here and re-run `sudo spark-guard.sh`. With the guard installed, `spark-node.sh` refuses a master port its rules do not cover |

**One rank by hand** (start ranks N-1..1 FIRST, then rank 0):

```bash
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1 --print   # print the exact command, run nothing
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1           # on spark-2
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --master 10.100.216.1           # then on spark-1
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --stop                          # stop this rank (idempotent)
```

**The whole cluster from the head.** List the nodes in rank order in
`scripts/backends/cluster.env` (copy `cluster.env.example`), for example
`CLUSTER_NODES="local max@10.100.216.2"`, `CLUSTER_NAME=spark-hydra`,
`CLUSTER_REPO=~/openbeast`. A leading `~/` is sent as-is and expands on each
node. Then:

```bash
scripts/backends/spark-cluster.sh check                          # every node: aarch64 + GB10, docker, NCCL, same commit, same engine build + spark.env pins, fabric pairing, SPARK_HEAD_IP reach
scripts/backends/spark-cluster.sh sync-weights --profile NAME    # model + drafter over the fabric, verified vs the HEAD's lock
scripts/backends/spark-cluster.sh sync-image IMAGE               # docker save | load where it differs; prints the *_IMAGE_ID pin
scripts/backends/spark-cluster.sh save-image IMAGE FILE          # an image to a file, for a cluster with no registry
scripts/backends/spark-cluster.sh start --profile NAME [--engine tensorfold|vllm]
scripts/backends/spark-cluster.sh status                         # per rank: free memory, openbeast-* and spark-hydra-* user units, containers
scripts/backends/spark-cluster.sh stop [--engine tensorfold|vllm]   # every rank, rank 0 first; both engines by default
#   --cluster FILE   --dry-run (print every command, run nothing)
```

How start and stop behave:

- `start` launches ranks N-1..1 and then 0, each with its own
  `spark-node.sh --rank R`. If a later rank fails, it stops the ranks it
  started.
- `stop` stops rank 0 first, and walks every rank even if one is already down.

**Do not run D2 on the ports D1 is using** while the pair serves: both use
:8000 and the 29551 range.

**Which engine scales how far** (2026-11-15):

| Engine | Ranks | |
|---|---|---|
| Mia-AiLab DeepSeek-V4.1 recipe (`tensorfold-dsv41`, the Zig fork + patches) | 2 | serves our pair's default (DeepSeek); its own scripts drive the ranks |
| Mia-AiLab GLM recipe (TensorFold v0.6.0 + patches) | 2 (`start.sh`), 3 (`start-tp3.sh`, experimental) | serves GLM, the alternative; its own scripts drive the ranks |
| native TensorFold ≥ 1.0 (1.2.7) | GLM-5.3-Flash up to 16 (`TF_GLM_WORLD`); MiniMax-M3.1-Flash 2 only (`TF_MINIMAX_*`); Flash Next 1-2; Nemotron 1; other families `--tp` 2/4/8 | from the 1.2.7 binary's own messages and `capabilities --json`; `spark-node.sh` refuses sizes the engine refuses |
| container TensorFold 0.x (@9cd52ab) | per `data/tensorfold.json`: GLM 2, most families 1-2, qwen3_5_moe 1 | the launcher refuses an excluded size at that commit, and warns at any other |
| vLLM (native multi-node) | `--nnodes N`, TP = N | the model's head count must divide by N |

Whether a model *fits* at N is a memory question. `model-inspect.sh` reports the
smallest N that holds the weights with KV room, judges ranks against the native
TensorFold engine, and lists the container engine's ranks separately.
Onboarding a new model: `DGX_SPARK_PLAN.md` §14 (`model-inspect.sh <repo@sha>
--write-profile NAME`, then `model-fetch.sh --profile NAME`, then
`conformance.sh`).

**Several smaller groups instead of one big one.** For example, 4 Sparks can run
as two independent pairs, each its own hydra node (`[nodes.pairA]`,
`[nodes.pairB]`, both in one route). That trades the long single-request context
of TP 4 for twice the concurrent capacity.

### D3. The engine plane: `scripts/engine.sh`

`engine.sh` is one command to list, swap, install, update, pin and roll back the
engine ([ENGINES.md](ENGINES.md) has the full matrix):

```bash
scripts/engine.sh list                         # what this host can run; installed / pinned / active
scripts/engine.sh status                       # active engine: version, pin, endpoint readiness, model served
scripts/engine.sh update tensorfold            # latest release: download, verify, pin
scripts/engine.sh install tensorfold --version 1.2.9
scripts/engine.sh rollback tensorfold          # previous pin, offline when still on disk
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 ./carry   # connected box
scripts/engine.sh install tensorfold --from ./carry                                   # closed box
scripts/engine.sh history
scripts/engine.sh use <engine|default> --profile NAME --dry-run   # print the conf change + launch command
```

On a Spark:

- `install`, `update` and `rollback` also rewrite `scripts/backends/spark.env`
  (`TENSORFOLD_VERSION`, `VLLM_IMAGE` / `VLLM_IMAGE_DIGEST` / `VLLM_IMAGE_ID`), so `spark-node.sh`
  launches the pinned build. **Every rank must run the same build**: update
  each Spark, then restart all ranks.
- `engine.sh use` manages only a **single-host, TP 1** engine
  (`INFERENCE_MANAGED=true`). A multi-Spark job stays unmanaged: `start.sh`
  waits for it and never launches it, and you run it with D1 or D2. Record it
  in the conf with `--unmanaged`, which the head needs because rank 0 answers
  on its own loopback, and a loopback URL alone reads as managed:
  `scripts/engine.sh use tensorfold --unmanaged --url http://127.0.0.1:8000 --profile NAME`
  writes `INFERENCE_BACKEND`, `INFERENCE_URL`, `INFERENCE_MANAGED=false` and,
  from the profile, `INFERENCE_MODEL` / `INFERENCE_SLOTS` (offline: it reads
  only local files).
- `engine.sh` does not manage the recipe (D1). The recipe pins its own image tag.

`engine.sh` is on both live checkouts ([0](#what-runs-where)), but
no swap has run there yet; it is tested hermetically (`tests/test_engines.sh`):
VERIFY ON HARDWARE.

---

## E. The command center on spark-1

### E1. Bootstrap

```bash
cd ~/openbeast
cp openbeast.conf.example openbeast.conf 2>/dev/null || touch openbeast.conf
chmod 600 openbeast.conf
# write the keys in E2 first: INFERENCE_BACKEND=tensorfold makes bootstrap skip llama.cpp and the GGUF
./bootstrap.sh --preflight       # what it will do, and what it cannot reach
./bootstrap.sh --no-start        # install deps, compose images, frontends; do not start yet
```

`INFERENCE_MANAGED` is forced false for TensorFold, so `./bootstrap.sh` builds
no llama.cpp and downloads no GGUF (DGX_SPARK_PLAN §8 row 19). All three compose
images are multi-arch at their pinned index digests, so the same
`docker-compose.yml` pulls arm64 on a Spark. The Python lock installs as-is on
aarch64 / python 3.12.3 (43 packages, measured on spark-1).

### E2. `openbeast.conf`

These are the keys in use on spark-1 on 2026-11-15. Secret values are not shown.
**Changed since (read 2026-11-19):** `INFERENCE_MODEL` is
`DeepSeek-V4.1-Flash-1017`, because the switch helpers rewrite it (and
`LLAMA_API_KEY`, DeepSeek's bearer key) on every switch and touch no other
line. `INFERENCE_THINKING=on` and `AGENT_MAX_TOKENS=16384` were added on
2026-11-17 (E2a). `AGENT_PROFILE=deepseek-best` replaces
`AGENT_MAX_TOKENS=16384` and `BEAST_ASSIST=0` once applied (E2b); since a switch
leaves these lines alone, adjust them by hand after `beastglm` or `beastds`.

| Key | Value on spark-1 | Why |
|---|---|---|
| `INFERENCE_BACKEND` | `tensorfold` | the model is the recipe's TensorFold server |
| `INFERENCE_URL` | `http://127.0.0.1:8000` | same box, loopback. Spawned agents follow it (conf.sh exports `OPENBEAST_AGENT_INFERENCE_URL=$INFERENCE_URL/v1`) |
| `INFERENCE_MODEL` | `GLM-5.3-Flash-EXL3` | the served name; exported as `OPENBEAST_INFERENCE_MODEL`, which `agents/runner.py` sends |
| `INFERENCE_SLOTS` | `4` | matches the recipe's 4 streams; beast-slot reports it. With `EDGE_MAX_INFLIGHT` unset it is also beast-gate's per-device cap, so one device (the Mac running parallel agents) may hold all 4 streams. Before the 2026-11-16 review the gate admitted 2 per device whatever the engine served; the audit showed `max_inflight` 429s with streams idle, each costing an agent turn ~7 s. Live since the 2026-11-16 restart: `/api/slot` reports `admission.per_device_inflight: 4` |
| `EDGE_GATE` | `true` | beast-gate (:8090) is the one inference door (:8443) |
| `EDGE_READ_TIMEOUT` | `3600` | seconds the gate waits for each byte from the model (default 600). A non-streaming completion sends nothing until it is done, so a long eval turn hit the gate's 504 at 600 s. Needed on any pair an eval harness targets ([F6](#f6-the-eval-harness-host-benchmarking-the-pair)) |
| `TS_HOSTNAME` | `spark-1` | the tailnet name `setup-tailscale.sh` joins as. A Spark has no default and the script refuses without it ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)) |
| `WEBUI_AUTH` | `true` | the WebUI login wall; required before :443 is published |
| `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD` | *(secret)* | the admin `configure-webui.sh` uses to apply tool-server config on restarts |
| `SEARXNG_SECRET` | *(secret)* | SearXNG's secret key |
| `EXTENSIONS` | `"dashboard"` | the dashboard serves beast-slot (`/api/slot`) |
| `BEAST_CHAT` | `true` | the operator console |
| `CHAT_OPERATORS` | `maxjkh@gmail.com` | tailnet logins that may read beast-chat |
| `CHAT_LOGIN_FROM` | `unix` | beast-chat honours the login header only on `.run/chat.sock`; `:8445` proxies to that socket (`setup-tailscale.sh --publish-chat`), so the host-network containers below cannot claim a login ([BEAST_CHAT.md](BEAST_CHAT.md)) |
| `BEAST_ARTIFACT` | `true` | the artifact gallery and `publish_artifact` |
| `ARTIFACT_OPERATORS`, `ARTIFACT_ADMINS` | `maxjkh@gmail.com` | who reads and administers artifacts |
| `LANG_PACKS`, `LANG_PACK_CONTEXT` | `off` | language assistance OFF on the pair (below) |
| `BEAST_ASSIST`, `BEAST_ESCALATE` | `0` | ditto |
| `INFERENCE_THINKING` | `on` | thinking on by default for every client that does not choose: beast-gate, the WebUI filter, this host's agents. The best v5 arm (C, 51/71 vs 36/71 off). Since 2026-11-17 ([E2a](#e2a-recommended-serving-configuration-from-v5)) |
| `AGENT_MAX_TOKENS` | `16384` | per-turn cap for this host's own agents: arm C's cap. `REASONING_BUDGET` is a llama.cpp flag ([E2a](#e2a-recommended-serving-configuration-from-v5)) |

Not set, so the defaults apply:

- `ISLAND` = `auto`.
- `OFFLINE` = `false`.
- `MEM_RESERVE_GB` = `3`. This is the GiB the stack's cap leaves beside the
  model on unified memory ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)).
- `MEM_LIMIT_PCT` = `75`.
- `EDGE_MAX_INFLIGHT` unset, so it follows `INFERENCE_SLOTS`: beast-gate
  admits 4 concurrent generations per device key. The eval harness's agents
  all share one key; `run_eval` reads the cap from the gate (a `n=1024` chat
  request the gate refuses before forwarding anything) and clamps `--jobs`
  to it, so a `--jobs 4` profile from the Mac runs 4 workers. Before review
  1116 the default was 2: half the generations got 429, each burned an agent
  iteration, and failing units became unbanked `server_error`s. Set
  `EDGE_MAX_INFLIGHT` only to hold one device **below** the server's slots
  when other clients share the pair.
- `ARTIFACT_LOGIN_FROM` = `loopback`. **Pending:** set it to `unix`, restart,
  and re-run `setup-tailscale.sh --publish-artifact` ([G6](#g6-updating)
  step 6), as `CHAT_LOGIN_FROM` already is.
- `WEBUI_RANDOM_SEED` = `true`: `configure-webui.sh` installs the random seed
  filter ([G6](#g6-updating)).
- `WATCHDOG_AUTOINSTALL` and `LOGROTATE_AUTOINSTALL` = `true`.
- `BIND_HOST` = `127.0.0.1`.

**Language assistance is OFF on the pair** (Max, 2026-11-15). Packs, the pack
context, beast-assist and escalation were measured for the 27B and help it.
GLM-5.3-Flash needs far less help. `language_reference` stays available as a
tool the model can choose to call. Also not enabled on the pair: `AGENT_ROUTER`,
an intent classifier that exists because the 27B would not call `start_agent`
on its own, and `HYDRA` / `INSTINCT`, whose scorer is the rig's 27B.

### E2a. Recommended serving configuration (from v5)

**Thinking on, by default, for every client.** This is the configuration the
2026-11-16/17 v5 arms found best for GLM-5.3-Flash on this pair, and since
2026-11-17 it is how spark-1 serves:

| Arm (v5, 71 units, one seed) | What changed | Scored |
|---|---|---|
| L0 | baseline, thinking off | 26/71 |
| A | runner v2 | 31/71 |
| B | + wall-scaled turns | 36/71 |
| **C** | **+ thinking on** | **51/71 (54 raw)** |
| E | C + diagnostics, edit hints, verify-done, packs, env block, wall warnings | 50/71 |

C is the best arm. E's additions were a wash and stay opt-in. Runner v2,
thinking-stall recovery (#194), the eval turn caps and the integrity seal
were already on main. The serving default adds one thing: thinking.
Evidence: [RESULTS.md](RESULTS.md), [HARNESS_NOTES.md](HARNESS_NOTES.md),
[MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md).

**Why a switch is needed at all.** Thinking is a per-request chat-template
switch, `chat_template_kwargs.enable_thinking`. The TensorFold server thinks
only when a request asks. A request that does not say gets thinking OFF. We
do not reconfigure the inference unit. The default lives in the OpenBeast
layer in front of it, keyed by one conf key.

**The knobs** (`openbeast.conf` on spark-1, both appended 2026-11-17):

| Key | spark-1 | Default elsewhere | What it does |
|---|---|---|---|
| `INFERENCE_THINKING` | `on` | unset: nothing injected anywhere | The host's thinking default for a chat request that names none. **beast-gate** (:8443, which serves opencode, the Mac client, phones and every keyed device) sets `chat_template_kwargs.enable_thinking`. The **WebUI "Thinking Default" filter** does the same for Open WebUI chats (WebUI talks to TensorFold on loopback, not through the gate). **This host's own agents** (`agents/runner.py`, `start_agent`) send it. The local `/gate/health` reports `thinking_default` |
| `AGENT_MAX_TOKENS` | `16384` for GLM; for DeepSeek, unset and `AGENT_PROFILE=deepseek-best` gives 8192 ([E2b](#e2b-deepseek-best-configuration-v5-t2)) | unset: `REASONING_BUDGET` + 12288 (32768) | Per-turn output cap for this host's own agents. It matches arm C's measured cap: ended-on-their-own thinking turns were p50 ~0.4K, p90 ~2.3K, p99 ~12.4K tokens, so 16K leaves room. A turn capped mid-thought keeps its reasoning tail and is told to act (#194). The 32K fallback would double the cost of a runaway: about 14 vs 28 minutes per stalled turn at the ~19 tok/s a stream decodes under 4-way load. `REASONING_BUDGET` is a llama.cpp flag and means nothing to TensorFold, so this key is the cap here |

**What a request can still choose.** Anything explicit wins and is
forwarded untouched:

- `chat_template_kwargs.enable_thinking` (true or false), or `.thinking`;
- a top-level `enable_thinking`, `thinking`, `reasoning`,
  `reasoning_effort`, `reasoning_budget_tokens` or `thinking_budget`.

Only `/v1/chat/completions` is touched, never `/v1/completions` or
`/v1/embeddings`.

**Switching thinking off, for latency:**

- **Per request:** send `"chat_template_kwargs": {"enable_thinking": false}`.
  In opencode, set it in the model's `options` in `opencode.json`. In Open
  WebUI, use the chat's Controls → Custom Parameters, key
  `chat_template_kwargs`, value `{"enable_thinking": false}`. For a
  thinking-off model entry for everyone, put the same key in Admin → Models
  → GLM → Advanced Params; the filter steps aside for it.
- **Per agent:** `OPENBEAST_AGENT_THINKING=off`.
- **For the whole host:** `INFERENCE_THINKING=off` (or delete the line),
  then `systemctl --user restart openbeast`. That restarts the gate and
  re-runs `configure-webui.sh`; it never touches `spark-hydra-inference`.

**Eval runs are unaffected.** Every eval-mode request the runner sends
carries `X-OpenBeast-Gate-Defaults: off`. The gate forwards such a request
exactly as sent and strips the header. The runner also ignores
`INFERENCE_THINKING` in eval mode, so an eval run that names no thinking
mode still gets each server's own default. That is the condition every
baseline was measured under: off on TensorFold, on for llama.cpp's Qwen
templates. Thinking arms set `OPENBEAST_AGENT_THINKING=on` as before. This
moved the eval era only through the `runner.py` hash
([evals/CHANGELOG.md](../evals/CHANGELOG.md)).

**What it costs.** Measured on the pair, 2026-11-17: single stream through
:8443 from the Mac, streaming, three everyday prompts, each run with
thinking off and then on.

| | First token | First answer token | Total | Completion tokens |
|---|---|---|---|---|
| thinking off (median) | 0.21 s | 0.2 s | 1.9 s | 126 |
| thinking on (median) | 0.22 s | 5.0 s | 6.1 s | 395 |
| thinking on, range | 0.20-0.23 s | 1.9-38.7 s | 2.4-41.5 s | 171-3,026 |

The first streamed token arrives just as fast either way, because the
reasoning streams first. WebUI shows it live in a collapsed "Thought for N
seconds" block. What grows is the wait for the answer. A trivial question
thinks for about 2 s. A coding question ("longest palindromic substring")
thought for about 9K characters (3,026 tokens) and answered at 39 s instead
of 3 s. Decode stays about 75 tok/s single-stream.

On agentic work the cost is larger. Arm C averaged 29.0 min per unit
against arm B's 11.7, about 2.3x B's wall time per unit. 14 of C's first 18
passes still finished inside B's original wall. For interactive chat where
latency matters more than the last points of quality, turn thinking off
per request or per client as above.

**Deploy and check** (what was run on 2026-11-17):

```bash
# spark-1
cd ~/openbeast && git pull --ff-only
printf '%s\n' INFERENCE_THINKING=on AGENT_MAX_TOKENS=16384 >> openbeast.conf
systemctl --user restart openbeast     # never spark-hydra-inference
./scripts/configure-webui.sh           # (also run by the restart) installs the Thinking Default filter
curl -s -H "X-OpenBeast-Local: $(cat .run/edge-local.token)" http://127.0.0.1:8090/gate/health
#   ... "thinking_default":"on"
```

From a keyed client: a chat request with no thinking params returns
`reasoning_content`, and one with `enable_thinking: false` returns none.

### E2b. DeepSeek best configuration (v5 T2)

**Max's decision, 2026-11-19:** the v5 arm-T2 configuration is OpenBeast's
default and best setup for DeepSeek-V4.1-Flash (Mia EXL3) on this pair.

**Status, 2026-11-19 ~14:50: live on spark-1.** The "Apply on spark-1" steps
below were run in the gap after arm T_s1: `AGENT_MAX_TOKENS=16384` and
`BEAST_ASSIST=0` removed, `AGENT_PROFILE=deepseek-best` added (backup
`openbeast.conf.bak-20261119`), and only `openbeast` restarted. The model switch does not touch it: after
`beastglm`, remove the `AGENT_PROFILE` line and restore GLM's two lines
(E2a); after `beastds`, the reverse.

| Arm (v5-thinking-first, 71 units, one seed) | What changed | Scored |
|---|---|---|
| T | the best-first composite: thinking on, runner v2, wall-scaled turns, diagnostics + edit hint + verify-done, packs v3 + host pack, env block + wall warnings; 16384-token turn cap | 61/71 |
| **T2** | **T + an 8192-token per-turn cap + the act nudge** | **65/71** |

T2 against T: +4 (168_kv_e, 173, 183, 190), 0 regressions; the gaps between
turns fell from 470-610 s to 280-330 s. Board row DS-T2
(`evals/boards/v5/board.json`), eval era `036638ddd68698ce`.

**T2 stays the recommended DeepSeek config (2026-11-21).** No later arm has
beaten it: T_s1 61 (noise about ±1), T3 (4096 cap) 65 with a lower SCORE,
and the harness10 arms U 59, UP 62, UPA 59, U_8k 63 (the forced-act turn
at the same 8192 cap: no better than the act nudge), UP_8k 65 and UPA_8k 62.
**UP_8k (the planning lines at 8192) tied T2 on passes** (65/71, 2 units in
and 2 out) with a lower SCORE (91.4 against 94.9); a tie keeps T2, and a
lead needs 2 or more units. UPA_8k (the pack at 8192) lost 3 against UP_8k.
The series is complete, and this section stands
([MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md) §5b, §9;
[the final campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md)).

**One line in `openbeast.conf`:** `AGENT_PROFILE=deepseek-best`. It sets a
default for each key below; a key set on its own line (or in the env) wins
over the profile, so remove any older line that contradicts it.

| Key the profile sets | Daily agents (start_agent, beast-chat, agent.sh) |
|---|---|
| `INFERENCE_THINKING=on` | applies (host-wide: also beast-gate and Open WebUI, E2a) |
| `AGENT_MAX_TOKENS=8192` | applies, with the capped-think recovery |
| `AGENT_ACT_NUDGE=1` | applies: from the 2nd capped no-tool turn in a row, "your next reply must be a tool call" |
| `BEAST_ASSIST=1` | applies: push diagnostics after each write, with the checkers installed on spark-1 (gcc, python; no zig, go or rust there). Read per tool call, so Open WebUI's tool calls get it too |
| `DIAG_EDIT_HINT=1` | applies with `BEAST_ASSIST` (same reach) |
| `AGENT_ENV_BLOCK=1` | applies: toolchain versions and workdir on the first turn |
| `LANG_PACK_VARIANT=v3` | applies only to zig tasks where zig 0.16 is installed and `LANG_PACK_CONTEXT`/`LANG_PACKS` allow a pack: **not on spark-1** (no zig, and both keys are off) |
| `AGENT_VERIFY_DONE=1` | **eval-only in effect**: it checks the task's expected files, which only run_eval names (`OPENBEAST_TASK_PATHS`). Exported, never fires |
| `AGENT_WALL_WARN=1` | **eval-only in effect**: it needs a wall budget (`--wall-s` / `OPENBEAST_AGENT_WALL_S`). A daily agent has none unless its caller passes `--wall-s` |

Not reproducible as a switch: T2's **host pack** (eval-only; it was probed on
the eval's Darwin tool host) and its **wall-scaled turns**
(`--turns-per-wall-s`, an eval knob: a daily agent has no wall, and
start_agent's `max_iter`, default 200, is its budget). Making verify-done or
the wall warning work outside an eval would mean editing `agents/tools.py` or
`agents/runner.py`, which are hashed into the eval era, so they stay as they
are.

**Unchanged:** the serving recipe (the Mia EXL3 pack, `CONTEXT=524288`,
`PARALLEL=4`, `THINKING=1`; nothing in `deepseek-v41-inference` changes), and
**opencode**: it has no capped-think recovery, so the 8192 cap is not applied
to its `limit.output`, which stays 32768.

**Apply on spark-1** (in a gap between eval arms; never while an arm runs):

```bash
# spark-1
cd ~/openbeast && git pull --ff-only
cp openbeast.conf openbeast.conf.bak-$(date +%Y%m%d)
# an explicit line beats the profile: drop the two GLM-era lines that contradict it
sed -i -e '/^AGENT_MAX_TOKENS=16384$/d' -e '/^BEAST_ASSIST=0$/d' openbeast.conf
echo 'AGENT_PROFILE=deepseek-best' >> openbeast.conf
systemctl --user restart openbeast     # the command center only, never deepseek-v41-inference
# check what the stack will export:
OB_CONF_READONLY=1 bash -c 'REPO_DIR=~/openbeast; source scripts/lib/conf.sh; env' \
  | grep -E '^(BEAST_ASSIST|OPENBEAST_(INFERENCE_THINKING|AGENT_(MAX_TOKENS|ACT_NUDGE|VERIFY_DONE|ENV_BLOCK|WALL_WARN)|DIAG_EDIT_HINT|LANG_PACK_VARIANT))='
```

The tool server and beast-chat read these when they start, so the restart is
what applies them. A running agent keeps the settings it started with. To go
back: restore the backup and restart again. Eval runs are unaffected: every
campaign arm sets all of these switches itself, and a plain `run_eval.py`
started from a shell that sourced `conf.sh` records any it inherits in its
cache key (`ag-...`), so nothing changes a measured row silently.

### E3. Boot units

```bash
mkdir -p ~/.config/systemd/user
sed "s|@REPO@|$HOME/openbeast|" scripts/openbeast.service > ~/.config/systemd/user/openbeast.service
systemctl --user daemon-reload
systemctl --user enable --now openbeast
```

- `openbeast.service` is a oneshot with `RemainAfterExit`. It runs
  `start.sh -d`, which runs the stack inside its own memory-capped transient
  scope (`openbeast-stack`).
- The unit does not order against `spark-hydra-inference`. `start.sh` waits up
  to `OPENBEAST_LLAMA_LOAD_GRACE` for `INFERENCE_URL` and brings the rest of the
  stack up either way.
- `start.sh -d` installs and enables `openbeast-watchdog.timer`
  (`healthcheck.sh --restart` every 5 min) and `openbeast-logrotate.timer`
  (daily) when they are missing.

What is enabled on spark-1 (`systemctl --user list-unit-files | grep -E 'openbeast|deepseek|spark|hydra'`, 2026-11-18, DeepSeek selected, the default):

```
deepseek-v41-inference.service enabled
openbeast.service              enabled
spark-hydra-inference.service  disabled
openbeast-watchdog.timer       enabled
openbeast-logrotate.timer      enabled
openbeast-stack.service        transient
```

Exactly one of the two inference units is enabled; that is the model
selected for boot. With GLM selected, the two lines swap.

Then reboot once. This is the docker-group gotcha from
[B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark). After the
reboot, everything comes back by itself:

```bash
systemctl --user status deepseek-v41-inference openbeast --no-pager | grep -E 'Active|●'   # spark-hydra-inference while GLM is selected
~/openbeast/scripts/healthcheck.sh        # expect "All 9 services healthy" once the model has loaded
```

### E4. The healthy state

```
Stack health check — 2026-11-15 13:03:38
  OK   TensorFold (tensorfold @ http://127.0.0.1:8000)
  OK   Tool server
  OK   Open WebUI
  OK   SearXNG
  OK   beast-gate
  OK   beast-chat console
  OK   beast-artifact
  OK   Dashboard (beast-slot)
  --   Network: online (watching: islands after 90s without a route); internet reachable at last probe
  OK   Tailscale (remote access)

  Unified memory: 9751 MiB available of 124607 MiB (the model's share is in the total)

All 9 services healthy.
```

### E5. WebUI auth and the admin account, before publishing

Do this **before** `setup-tailscale.sh` publishes :443. Publishing an auth-off
WebUI makes every tailnet device an admin, with `bash`.

1. Put `WEBUI_AUTH=true` in `openbeast.conf` and (re)start the stack:
   `systemctl --user restart openbeast`.
2. On the first auth-on start, Open WebUI's built-in `admin@localhost` default
   password (`admin`) is rotated to a random one, saved as
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` in `openbeast.conf` (0600).
   `setup-tailscale.sh` also checks this.
3. Reach the WebUI over an ssh tunnel (it binds loopback):
   `ssh -L 3000:127.0.0.1:3000 spark-1.tail4109f9.ts.net`, then open
   `http://localhost:3000`. Log in with the rotated credentials and create your
   own admin in Admin Panel → Users. New signups land as `pending`; "first
   signup becomes admin" applies only to a WebUI database that has never had a
   user.
4. If you make your own account the one `configure-webui.sh` uses, put it in
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD`. The rotated built-in password
   then moves to `WEBUI_DEFAULT_ADMIN_PASSWORD`.

On spark-1:

- The admin is `maxjkh@gmail.com`. Its initial password was left in
  `~/.config/openbeast/webui-max-password` (0600). Change it in Settings →
  Account, then delete that file (`shred -u`).
- The WebUI database is spark-1's own: accounts and chats from the beast rig do
  not carry over.

### E6. Publish on the tailnet (`setup-tailscale.sh`, as tailscaled's operator)

`spark-root-setup.sh` set `--operator=max`, so this needs no sudo on a Spark:

```bash
cd ~/openbeast
grep -q '^TS_HOSTNAME=' openbeast.conf || echo 'TS_HOSTNAME=spark-1' >> openbeast.conf
./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng
./scripts/setup-tailscale.sh --status        # read-only: which surface sits on which port
```

`TS_HOSTNAME` (conf key, env overrides) names the box on the tailnet. On a Spark it
has no default: the script refuses to join until it is set, because two Sparks
both defaulting to `beast` would collide. It applies only to the first join; a
Spark already on the tailnet keeps its name, and the script prints a note (never
a rename) when the conf names it differently. Re-running it on a joined Spark is
therefore safe. The script refuses to
publish :443 unless the **running** WebUI enforces auth. It refuses :8443 unless
beast-gate (`EDGE_GATE=true`) or a shared `LLAMA_API_KEY` is in front. The
overrides `--i-accept-open-webui` and `--i-accept-open-inference` exist; do not
use them here. Undo one surface with `--unpublish-<name>`.

The published URLs (live 2026-11-15; the :8447 row added 2026-11-22):

| URL | What | Auth |
|---|---|---|
| `https://spark-1.tail4109f9.ts.net` | Open WebUI | WebUI login (`WEBUI_AUTH=true`) |
| `https://spark-1.tail4109f9.ts.net:8443/v1` | inference, **through beast-gate** | per-device key (`scripts/clients.sh`); 401 without one (checked from the Mac 2026-11-15) |
| `https://spark-1.tail4109f9.ts.net:8444/api/slot` | beast-slot discovery | open, read-only |
| `https://spark-1.tail4109f9.ts.net:8445` | beast-chat console | tailnet login to read, chat-scoped device key to act |
| `https://spark-1.tail4109f9.ts.net:8446` | beast-artifact gallery | tailnet login (`ARTIFACT_OPERATORS`) |
| `https://spark-1.tail4109f9.ts.net:8889` | SearXNG for thin clients | open to the tailnet |
| `https://spark-1.tail4109f9.ts.net:8447` | the status dashboard (page, `/api/status`, `/api/cluster`, `/api/power`) | open to the tailnet, read-only (GET only); mounted by hand, see [BEAST_SLOT.md](BEAST_SLOT.md#the-status-dashboard-8447) |

The model itself still listens on `127.0.0.1:8000` only. `:8443` is the one
inference door, and it always goes through the gate. Public exposure (funnel)
is never offered.

**Not yet:** a stable `svc:spark-hydra` name. That is a Tailscale Service, which
has to be defined in the admin console (and spark-1 tagged) before
`tailscale serve --service=svc:spark-hydra` can advertise it. Until then the
pair is reached by spark-1's name.

### E7. Device keys for beast-gate

```bash
cd ~/openbeast
./scripts/clients.sh enroll macbook --label "Max's MacBook"     # prints the key ONCE; the registry keeps only its SHA-256
./scripts/clients.sh enroll phone --label "phone" --scope chat  # chat scope = may act in beast-chat
./scripts/clients.sh list
./scripts/clients.sh revoke <id>         # a lost device; hot-reloaded, no restart
./scripts/clients.sh rotate <id>         # new key for the same device
```

The registry is `.run/clients.json` (0600, gitignored).

Carry each key to its device **without printing it into a shared log**: copy it
straight into a 0600 file on the device. On the Mac it is
`~/.config/openbeast/spark-hydra-gate.key`.

**Known issue:** plaintext copies of the `macbook` and `phone` keys are in
`~/.config/openbeast/gate-keys/` on spark-1 (0600). They defeat the registry's
hash-only design. They are readable by `max`, the uid the admin-tier `bash` /
`read_file` tools run as, so a prompt-injected admin chat could read a working
device key. Once each device holds its key, delete the copies:
`shred -u ~/.config/openbeast/gate-keys/*.key`. If one is lost, re-enroll to
recover it.

---

## F. The Mac client

**From the Mac: quick reference.** Every command here runs on the Mac.

```bash
openbeast-client status                      # green = env, venv, tailnet, the active rig's :8443 + key, /api/slot, search, opencode
openbeast-client rigs                        # all rigs, * = active, state from each rig's :8444/api/slot
openbeast-client use spark-hydra             # point opencode, the tools and web_search at the pair
openbeast-client use beast                   # ... or at the 5090 rig (refused, nothing changed, while it is down)
openbeast-client rig add beast --api-key-stdin < ~/.config/openbeast/beast-gate.key   # beast's device key, once
cd <any project> && opencode                 # model picker: openbeast-rig/… (active) or openbeast-<rig>/…
opencode run -m openbeast-spark-hydra/DeepSeek-V4.1-Flash-1017 "…"   # one-shot, a chosen rig (GLM-5.3-Flash-EXL3 while GLM serves)
openbeast-client agent "task"                # the OpenBeast CLI agent, tools here, thinking on the active rig
scripts/client-e2e.sh                        # the end-to-end check (F2), PASS/FAIL per line
openbeast-client refresh-config              # also re-copies the soul into opencode's instructions (F3)
brew upgrade opencode && opencode service restart   # opencode >= 2.2.0 loads `instructions`; 2.0.25 ignores them
curl -s https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool   # raw discovery, no key
```

### F1. ssh from the Mac

NVIDIA Sync's `.local` names resolve only on the home LAN. For away from home,
the Mac's `~/.ssh/config` has tailnet entries that reuse the Sync key and pin
the known host keys with `HostKeyAlias`:

```
Host spark-1.tail4109f9.ts.net
  HostKeyAlias spark-3dc6.local
  User max
  IdentityFile <the NVIDIA Sync key>
Host spark-2.tail4109f9.ts.net
  HostKeyAlias spark-9f26.local
  User max
  IdentityFile <the NVIDIA Sync key>
```

Without `HostKeyAlias`, the first tailnet login asks to trust a "new" host
whose key is in fact already known under the `.local` name.

**Quote remote commands.** `ssh spark-1 cat ~/.config/openbeast/x` expands `~`
on the **Mac** (`/Users/…`) before ssh sends it. Write
`ssh spark-1 'cat ~/.config/openbeast/x'`. The same goes for `$HOME`, globs and
`&&` chains.

### F2. Client mode: one or several rigs

The Mac talks to **two rigs**: `spark-hydra` (this pair, through spark-1's
gate) and `beast` (the RTX 5090 llama.cpp rig). Client mode installs once;
after that, rigs are added, listed and switched with `openbeast-client`.

**Install** (once, from a checkout on the Mac):

```bash
cd ~/Documents/projects/openbeast          # a checkout on the Mac
./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra \
  --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key
openbeast-client status                     # green; "active rig: spark-hydra"
```

`--name` is optional: without it the rig is named after the key file that
holds the same key (`spark-hydra-gate.key` → `spark-hydra`), else after the
host's first label. The installer also registers your own tailnet peer named
`beast` (same tailnet, same owner; never another user's), keyless.

**Add a rig's key, list, switch:**

```bash
openbeast-client rig add beast --api-key-stdin < ~/.config/openbeast/beast-gate.key   # key → 0600 file
openbeast-client rig add beast --key-file ~/.config/openbeast/beast-gate.key          # or reference a file in place
openbeast-client rig add lab --host lab.tail4109f9.ts.net --api-key-stdin < lab.key   # a new rig
openbeast-client rigs                       # every rig: state from its :8444/api/slot, model, slots, key
openbeast-client use beast                  # switch: env + opencode.json, atomically; catalog refreshed
openbeast-client use spark-hydra            # and back
openbeast-client use beast --force          # switch even though beast is down / rejects the key
openbeast-client rig remove lab             # the key file is kept
openbeast-client rig detect                 # register the tailnet's 'beast' if it appeared later
openbeast-client refresh-config             # re-probe every rig, rewrite the catalog
```

`openbeast-client rigs` on the Mac, 2026-11-17 (beast offline, no key yet):

```
    RIG            HOST                           STATE        MODEL                    SLOTS  KEY
    beast          beast.tail4109f9.ts.net        offline      ?                        -      NONE
  * spark-hydra    spark-1.tail4109f9.ts.net      healthy      GLM-5.3-Flash-EXL3       0/4    ~/.config/openbeast/spark-hydra-gate.key
```

**What a switch does.** `use <rig>` probes every rig in parallel (`/api/slot`,
public; `/v1/models` with that rig's key). If the target answers nothing, is
down, or rejects the key, it prints why, says `active rig unchanged`, changes
**nothing**, and exits 1. `--force` switches anyway. Otherwise it rewrites,
each by an atomic rename at 0600:

- `~/.openbeast-client.env`: `OPENBEAST_RIG`, the inference, slot and search
  URLs, the key. Lines it does not own (`OPENBEAST_SEARXNG_SECRET`, your own)
  are kept.
- `~/.config/opencode/opencode.json`: the providers, the `openbeast-tools`
  environment, the default model and the soul's `instructions` entries (F3).
  The previous file is kept as `opencode.json.openbeast-prev`.
- `~/.config/openbeast/rigs.json`: the registry, `active` set.

**The registry**, `~/.config/openbeast/rigs.json` (0600, `scripts/lib/client_rigs.py`):
per rig the host, the URLs, the **path** of its 0600 key file (never the
key), the default model (follows what the rig serves, unless pinned with
`rig add <rig> --model <id>`), an optional `output_limit`, and the last
`/api/slot` answer, so an offline rig keeps its catalog. Keys never go on
argv and are never printed. A single-rig install made before this (an env
file, no registry) is migrated on the first `openbeast-client` command.

**Which rig does what.** The **active** rig gets the tool arsenal's own
traffic: `web_search` (its `:8889`) and sub-agents (`start_agent`). In
opencode, any registered rig's model can be picked per session (F3). Tools
always run on the Mac.

What you get, whichever rig is active:

- OpenCode on the Mac, with the full tool arsenal running **locally**: a stdio
  MCP subprocess (`agents/mcp_server.py`) in `~/.openbeast-client/venv`.
  `bash`, `edit_file` and the rest act on the Mac's files.
- Inference from the rig's `:8443/v1` (on the pair: through the gate).
- `web_search` through the rig's `:8889`. The alternatives are
  `--local-search` or `--no-search` (setup), or `rig add <rig> --search local|none`.

File contents the agent reads are sent to the rig as model context. The
promise is "nothing leaves the tailnet", not "nothing leaves the Mac".

**Testing from the Mac.** `scripts/client-e2e.sh` checks the whole path
against the real active rig with a few short requests, one PASS/FAIL line
each, exit status = failures:

```bash
scripts/client-e2e.sh --expect-model DeepSeek-V4.1-Flash-1017 --expect-slots 4   # GLM-5.3-Flash-EXL3 while GLM serves
scripts/client-e2e.sh --skip d               # without the switching test
scripts/client-e2e.sh --keep                 # keep the temp project + opencode logs
```

| | checks |
|---|---|
| (a) | `openbeast-client status` green, names the active rig, the model and the slot count |
| (f) | opencode accepts the config: `opencode models` lists `openbeast-rig/<model>` and `openbeast-<rig>/<model>`; opencode's own parse of the row has `limit.context` = `/api/slot` `model.ctx` |
| (b) | `opencode run` in a fresh temp dir: the agent writes a python file with `openbeast-tools_write_file` and runs it with `openbeast-tools_bash`; the file must exist **on the Mac** and print the expected line (tools local, inference on the rig) |
| (c) | `web_search` through the rig's SearXNG (the MCP tool, called directly) |
| (d) | `use <other rig>`: refused with nothing changed when it is offline, `--force` switches and `status` fails gracefully, `use <rig>` comes back green (a real round trip when the other rig is up). opencode.json is backed up first (`opencode.json.bak-e2e-<time>`, 0600) and a trap restores the rig |
| (e) | the `openbeast-tools` MCP server lists its tools |
| (g) | the soul: opencode's own parse of the config lists `~/.openbeast-client/prompts/system-prompt.md` (and the tool guide) in `instructions`, the installed opencode loads that setting (not 2.0.x), and a fresh `opencode run` asked "who do you serve?" answers Max, which only the soul tells it |

**Measured on the Mac, 2026-11-17** (against spark-1, beast offline, thinking
on at the gate): 6 passed, 0 failed. (a) green, `GLM-5.3-Flash-EXL3`, 0/4
busy, 1024K shared. (f) opencode's parsed limit on both providers:
`{"context": 1048576, "output": 32768}`. (b) 53 s: the agent called
`openbeast-tools_write_file` and `openbeast-tools_bash`, the file ran on the
Mac and printed the expected line, with 2 reasoning parts and 2,185 reasoning
tokens. (c) 3 results through `:8889` in 0.8 s. (d) `use beast` refused
("nothing answers"), files unchanged; `--force` switched, `status` exit 1;
`use spark-hydra` green again. (e) 19 tools.

### F3. What the client writes into opencode

The Mac runs **opencode v2.3.2** (checked 2026-11-19; it ran v2.0.25 when
this section was first written). Its global config is
`~/.config/opencode/opencode.json`. Client mode writes only its own entries,
and leaves everything else in the file alone:

```jsonc
{
  "mcp": { "servers": {
    "openbeast-tools": { "type": "local", "codemode": false,
      "command": ["…/.openbeast-client/venv/bin/python3", "…/agents/mcp_server.py"],
      "environment": { "OPENBEAST_AGENT_INFERENCE_URL": "<active rig>/v1", "SEARXNG_URL": "<active rig>:8889", "OPENBEAST_API_KEY": "…" } } } },
  "provider": {
    "openbeast-rig":         { "name": "OpenBeast rig (active: spark-hydra, …)", "options": { "baseURL": "…", "apiKey": "…" }, "models": { … } },
    "openbeast-spark-hydra": { "name": "OpenBeast spark-hydra (spark-1.tail4109f9.ts.net)", "options": { … },
                               "models": { "DeepSeek-V4.1-Flash-1017": { "name": "DeepSeek-V4.1-Flash-1017  [live on spark-hydra]",
                                                                    "limit": { "context": 524288, "output": 32768 } } } },
    "openbeast-beast":       { … }                       // once beast has a key
  },
  "model": "openbeast-rig/DeepSeek-V4.1-Flash-1017",       // GLM-5.3-Flash-EXL3 (context 1048576) while GLM serves
  "small_model": "openbeast-rig/DeepSeek-V4.1-Flash-1017",
  "instructions": [
    "/Users/<you>/.openbeast-client/prompts/system-prompt.md",        // the soul
    "/Users/<you>/.openbeast-client/prompts/system-prompt-tools.md",  // the tool guide
    "/Users/<you>/.openbeast-client/prompts/opencode-client.md",      // client corrections
    "…your own entries, untouched"
  ]
}
```

- **The soul.** Every opencode session loads the same system prompt Open WebUI
  runs with on the rigs: `system-prompt.md` (the soul) and
  `system-prompt-tools.md` (the tool conventions), the two files
  `scripts/configure-webui.sh` concatenates into the WebUI model's prompt.
  They are **copied** to `~/.openbeast-client/prompts/` (a moved or deleted
  checkout cannot break them, and opencode skips a missing instruction file
  without a word) and listed **first** in `instructions`; every entry that
  does not point there is yours and is never touched or reordered. Every sync
  re-copies them (setup, `use`, `rig add/remove`, `refresh-config`, `update`),
  rewriting a file only when its content changed: opencode watches
  instruction files and announces each change to running sessions.
  `--uninstall` removes our entries and the copies. The source files are
  owner-maintained and never edited by the client.
- **Why the tool guide is included, plus a third file.** Most of
  `system-prompt-tools.md` holds on the client: the same `agents/mcp_server.py`
  serves `read_file`, `edit_file`, `bash`, `web_search`, `fetch`,
  `start_agent`, `skill` and the rest here. Two things do not, and
  `opencode-client.md` (written by `scripts/lib/client_rigs.py`, a few lines)
  says so: in opencode the tools are named `openbeast-tools_<tool>`, and the
  "Where files go" paragraph (a private chat workspace with a
  `.manifest.jsonl` index) is Open WebUI's: here relative paths resolve
  against the tool server's working directory, so the note asks for absolute
  paths.
- **opencode version.** `instructions` is in the v2 schema, but **2.0.25
  does not load it**: its instruction plugin reads only `AGENTS.md` files.
  Measured 2026-11-17 against a stub provider that records each request: on
  2.0.25 the system message carries neither file; on 2.2.0 and 2.3.0 it
  carries both, absolute and `~/` paths alike. So on 2.0.x the entries are
  written but inert, and `setup-client.sh`, `openbeast-client status` and
  `client-e2e.sh` (g) say so. The fix is `brew upgrade opencode` (or
  `opencode upgrade`), then `opencode service restart`, which ends the
  running sessions, so pick the moment. No `~/.config/opencode/AGENTS.md` is
  written as a fallback: that file is the user's, and on a current opencode
  it would load the soul twice.

- **One provider per rig**, `openbeast-<rig>`, for every rig with a key (a
  keyless rig gets none until `rig add <rig> --api-key-stdin`: it would only
  401). **`openbeast-rig` is the active rig**, the id every earlier install
  and doc uses; `use` repoints it. Pick per session in the TUI's model
  picker, or `opencode run -m openbeast-beast/<model> …`.
- **Model rows.** A llama.cpp rig (beast) serves any id (llama-server ignores
  it), so it carries the checkout's catalog plus its live row. Any other
  backend (the pair's TensorFold, vLLM, hydra) gets exactly the ids it serves:
  a llama alias there is a 404 or silently the same model.
- **Limits.** opencode's `limit` takes `{context, output}` on the model row
  (both required; the v2 file layout still reads it from `provider.<id>.models`,
  checked with `opencode debug config` on 2026-11-17). `context` is the rig's
  `/api/slot` `model.ctx`: 524,288 on the pair with DeepSeek (1,048,576 while
  GLM serves), beast's `-c`. That is the window of **one conversation**.
  `capacity.ctx_total` (1,986,560 on the pair under GLM; DeepSeek's server
  reports none, so it is null) is **one** KV pool shared by the 4 slots, not
  4 x the window. `output` is 32768:
  thinking is on by default at the gate and opencode has no reasoning-tail
  recovery, so it gets more headroom than the runner's 16384
  `AGENT_MAX_TOKENS`. Per rig: `openbeast-client rig add spark-hydra --output-limit 16384`
  (`0` = back to the default).
- **The MCP layout.** Everything MCP lives under `mcp.servers.<name>`:
  opencode v2 does **not** merge a v1 entry (`mcp.<name>` with
  `enabled: true`) with v2 ones, and a mixed file loses the v1 ones.
  `setup-client.sh` writes the v2 shape when `opencode --version` is 2.x or the
  file already has `mcp.servers`, and moves other v1 entries in with it (so no
  hand fix-up after a run any more). `codemode: false` turns off v2's Code Mode
  for our server: the model gets `openbeast-tools_bash`,
  `openbeast-tools_write_file` and the rest as direct tools, which is what the
  evals and the conformance checks measure.
- The provider ids `openbeast-*` and the MCP key `openbeast-tools` are distinct
  from the checkout's own `./opencode.json` (`llama-cpp`, `local-tools`), so
  running `opencode` inside the repo cannot shadow them.
- Permissions in v2 are a list:
  `permissions: [{action: "<server>_<tool>", resource: "*", effect: "ask"}]`.
  The last match wins.

**Reasoning (thinking) output.** Both models stream their thinking as
`reasoning_content` (DeepSeek's conformance run checks the field); opencode's `@ai-sdk/openai-compatible` provider turns it
into reasoning parts with no extra config (verified 2026-11-17 against the
pair with thinking on: a reasoning part, then the tool call, then the
answer). The TUI shows it as a collapsible thinking block;
`opencode run --thinking` prints it. opencode sends it back as
`reasoning_content` on the assistant turns of the same session, which GLM's
template expects for interleaved thinking. Nothing to set per model: no
`reasoning`/`interleaved` keys are written, and thinking on or off is the
gate's default (`INFERENCE_THINKING`), not a client setting.

**Two opencode v2 behaviors to know:**

- `opencode run` sends its **first** request before local MCP servers finish
  connecting (about 0.8 s), so a one-step prompt may see only opencode's
  built-in tools; from the second step on, `openbeast-tools_*` are there.
  Prompts that need the arsenal right away should name the tools (as
  `client-e2e.sh` does). Likewise `opencode models` from a directory the
  service has not seen yet can list none of the configured providers on the
  first call, and all of them two seconds later.
- The TUI and `opencode run` (without `--standalone`) go through opencode's
  **background service**. It watches its config dir, so `use` is normally
  picked up; if a running session still shows the old rig, `opencode reload`.
  On 2026-11-17 `opencode mcp list` printed "No MCP servers configured" while
  the log (`~/.local/share/opencode/log/opencode.log`) said
  `mcp connected server=openbeast-tools tools=19`. That message is the
  service's CLI view, not a broken server: `client-e2e.sh` (e) lists the tools
  directly.

`scripts/apps.sh` (F4) writes the same v2 layout for the external-app
servers ([BEAST_APPS.md](BEAST_APPS.md) "opencode").

### F4. beast-apps: external apps through MCP servers

```bash
scripts/apps.sh list                  # every capability, its status here, enabled or not
scripts/apps.sh check ios-build       # probes + the exact fix for anything missing
scripts/apps.sh enable ios-build --dry-run
scripts/apps.sh enable ios-build      # merges into mcp.servers (v2), backs the old file up first
scripts/apps.sh disable ios-build     # removes exactly what enable wrote
```

The Mac on 2026-11-15:

- READY: `ios-build` (enabled, as `mobilebuildmcp`; now `mobile-build-tools`), `android-build`,
  `mobile-ui`, `sim-ros`.
- MISSING: `blender`, `figma`, `3d-print` (off by default: risk).

Offline, `check` and `disable` never touch the network; `enable` stages an
npm server from the local npm cache only, and `refresh` runs what is staged
(npm) or `uvx --offline`. Staging servers for an offline Mac and installing
client mode with no internet: [BEAST_APPS.md](BEAST_APPS.md) § Staging
servers for an offline machine, § Offline client. Skill: `app-control`.

### F5. beast-docs: platform docs, mirrored

```bash
./scripts/docs.sh sources                                   # what can be mirrored
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms
./scripts/docs.sh search NavigationStack
./scripts/docs.sh status                                    # pages, age, STALE flags
./scripts/docs.sh pack mirror.tar.gz                        # carry it; writes mirror.tar.gz.sha256
```

The store is `~/.openbeast/docs` (or `OPENBEAST_DOCS_DIR`). spark-1 has
**no mirror** yet: every source reads "not mirrored". To give the pair one, sync
on the Mac, `pack`, copy, and `unpack` on spark-1 ([H8](#h8-beast-docs-mirror)).
Models reach it through the `platform_docs` MCP tool or `scripts/docs.sh`.
Skill: `platform-docs`.

### F6. The eval harness host: benchmarking the pair

The v5 benchmark runs **on a harness host** (the Mac is the documented one;
a Linux box works with the caveats in [evals/README.md](../evals/README.md)
"Residual gaps") and sends only chat calls to the pair. The agents' tools,
the validators and every compiler run on the harness host, never on the
Sparks, so the toolchains belong there.

**The repo provisions them.** `evals/toolchains.lock` pins what the host
needs, per platform (darwin-arm64, linux-aarch64, linux-x86_64), with a
sha256 for every file; `scripts/eval-host.sh` checks, installs and exports
them. The versions are the ones the v5 units were proven with
(`tests/prove_task.py`, 2026-11):

| Tool | Pin | Online source | Offline (`--from DIR`) |
|---|---|---|---|
| zig | 0.16.0 | ziglang.org, community mirrors first (`@mirrors`) | ark `files/zig/0.16.0/<platform>/` |
| rust | rustc 1.100.0 | rustup-init 1.29.3, then `--profile minimal --default-toolchain 1.100.0` | ark `files/rust/1.100.0/<platform>/`, the standalone installer |
| go | 1.23.3 | dl.google.com | a `fetch` mirror (the ark carries go 1.27.4, not this pin) |
| python | 3.12.16 + `agents/requirements.lock` | python-build-standalone (the ark catalog's pin), then `pydeps.sh install --require-hashes` | ark `files/cpython-3.12/3.12.16/<platform>/` + `--wheelhouse DIR` |
| gcc / g++ / git | present | **not installed by us**: `xcode-select --install` on a Mac, `sudo apt install build-essential clang git` on Linux | the distro's .debs |
| a device key of its own | | `./scripts/clients.sh enroll eval-host --label "eval harness"` on spark-1 ([E7](#e7-device-keys-for-beast-gate)), into a 0600 file | same |
| the gate's read timeout | | `EDGE_READ_TIMEOUT=3600` in spark-1's `openbeast.conf`, then `systemctl --user restart openbeast` | same |

```bash
./scripts/eval-host.sh check                  # one line per tool: ok / missing / wrong / system / blocked
./scripts/eval-host.sh setup                  # install what is missing or wrong (idempotent; exit 3 = sha256 mismatch)
eval "$(./scripts/eval-host.sh env)"          # PATH, RUSTUP_HOME, CARGO_HOME, OPENBEAST_EVAL_PY
# offline: from an ark drive, or a mirror made on a connected box
./scripts/eval-host.sh fetch --to /Volumes/ARK/eval-host --platform all [--wheels]
./scripts/eval-host.sh setup --from /Volumes/ARK/eval-host --wheelhouse ~/wheels
```

Everything lands in `~/.local/opt/openbeast-eval` (`--prefix` /
`OPENBEAST_EVAL_PREFIX`): `bin/` links, `zig-0.16.0/`, `go-1.23.3/`,
`rustup/` + `cargo/`, `cpython-3.12.16/` and `venv/`. Nothing outside the
prefix is written and nothing runs as root. A tool that is already on PATH
at the pinned version is used as it is (the Mac's Homebrew go1.23.3). The
copy that counts is the one the validators will pin
(`vlib.pin_toolchain`): the first root-owned copy on PATH, else the first.
A root-owned copy of the wrong version (an apt `go` in `/usr/bin`) outranks
the prefix, and `check` says `blocked`: remove it.

**A live run does this itself.** `run_eval.py`, `profile.py` and
`benchmark_all.py` run `check` first and `setup` when it fails, put the
prefix first on PATH before the validator toolchain is pinned, and re-exec
under the eval venv's python (`$OPENBEAST_EVAL_PY`) when started by another
one. `--no-provision` (or `OPENBEAST_EVAL_NO_PROVISION=1`) only reports;
profile.py's preflight still refuses a host that fails the check
(`HOST_TOOLS`: `go`, `gcc`, `g++`, `rustc`, `zig`, `git`; zig must print
exactly `0.16.0`). Two guards then keep a broken interpreter off the
board: a live run refuses to start when its python cannot import the
stdlib and the runner's packages, and an agent that dies within 2 s on an
interpreter-startup error is an `interpreter_error` row (never cached,
never scored), followed by a re-check that aborts the run when the
interpreter is broken. That is the 2026-11-16 failure: a venv in a scratch
dir whose python symlink was rewritten mid-run, 22 units scored as FAILs
([HARNESS_NOTES.md](HARNESS_NOTES.md), "Eval host provisioning").

**Why the read timeout.** The gate waits `EDGE_READ_TIMEOUT` seconds (default
600) for each byte from the model. For a non-streaming completion that is the
whole generation, so a long agent turn ends as a gate 504 that the harness
records as a server error, not as the model's answer. spark-1 runs 3600.

**The in-flight cap.** Every agent of a run shares the one key, and the gate
admits `EDGE_MAX_INFLIGHT` generations per key. Unset, that is
`INFERENCE_SLOTS` (4 on the pair), so `--jobs 4` runs 4 workers. `run_eval`
reads the cap from the gate and clamps `--jobs` to it, and `profile.py`
refuses a profile when 4 concurrent requests would draw a 429.

**Run it** (from a full checkout on the harness host):

```bash
export OPENBEAST_SPARK_API_KEY="$(cat ~/.config/openbeast/spark-hydra-gate.key)"   # benchmark_all's glm-5.3-flash entry reads it
./scripts/eval-host.sh setup && eval "$(./scripts/eval-host.sh env)"            # what preflight will check, provisioned
"$OPENBEAST_EVAL_PY" evals/profile.py --model glm-5.3-flash --budget-h 4 --jobs 4   # evals/README.md "How to run a 4-hour profile"
"$OPENBEAST_EVAL_PY" evals/benchmark_all.py --models glm-5.3-flash                  # the plain sweep
```

(`python3 evals/profile.py …` works too: it provisions and re-execs under
the eval venv itself.) The `glm-5.3-flash` entry is the pair's gate, whatever
it serves: `run_eval` reads the served model id from `/v1/models`, so with
DeepSeek selected the run measures DeepSeek. The DeepSeek arms on the board
ran through `evals/campaign.py` ([MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md)). Never point the harness at toolchains or a venv in a
scratch or temp directory: the harness pins each compiler's install tree
when it starts and refuses a validation when that tree changes, and a venv
dies with its base interpreter. The prefix is durable on purpose.

---

## G. Day-2 operations

### G1. Health

```bash
# on spark-1
~/openbeast/scripts/healthcheck.sh               # expect "All 9 services healthy" + the unified-memory line
curl -s http://127.0.0.1:8000/health             # ok, streams, pool tokens, requests_total
~/openbeast/scripts/doctor.sh                    # 39 ok, 1 warning on 2026-11-15 (SSD wear needs a sudoers rule)
~/openbeast/start.sh --status                    # stack + island state
python3 ~/openbeast/agents/island.py status      # online / island, and why
systemctl --user status deepseek-v41-inference openbeast --no-pager   # DeepSeek (default); spark-hydra-inference when GLM is selected
# from the Mac
curl -fsS https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30
openbeast-client status && openbeast-client rigs   # the client's view (F2)
```

### G2. Logs

The unit and container names depend on the selected model:
DeepSeek (the default since 2026-11-18) is `deepseek-v41-inference` and
`dsv41-1017-exl3-tf`; GLM, the alternative, is `spark-hydra-inference` and `glm53-flash-tf`.

```bash
journalctl --user -fu deepseek-v41-inference          # rank 0 + start.sh (DeepSeek, the default)
docker logs -f dsv41-1017-exl3-tf                        # rank 0 container (DeepSeek)
ssh max@10.100.216.2 'docker logs -f dsv41-1017-exl3-tf' # rank 1 (quote it)
journalctl --user -fu spark-hydra-inference           # the same, when GLM is selected
docker logs -f glm53-flash-tf
ssh max@10.100.216.2 'docker logs -f glm53-flash-tf'     # rank 1 (GLM; quote it)
tail -f ~/openbeast/.run/stack.log                   # the command center's supervisor
journalctl --user -u openbeast --since today
```

### G3. Restarts: only through `systemctl --user` while the unit is enabled

```bash
systemctl --user restart deepseek-v41-inference       # both ranks (DeepSeek, the default; spark-hydra-inference for GLM)
systemctl --user stop deepseek-v41-inference
systemctl --user restart openbeast                   # the command center (WebUI, tools, gate, …)
./scripts/healthcheck.sh --restart                   # restart only what is DOWN (the watchdog does this every 5 min)
```

Never `docker stop` a rank container (`glm53-flash-tf`, `dsv41-1017-exl3-tf`), the recipe’s stop script or
`./stop.sh` by hand while the matching unit is enabled. systemd restarts the
pair behind you (`Restart=on-failure`), or the oneshot `openbeast` unit stays
"active" over a stopped stack.

After a mid-stream generation error on several ranks, restart **all** of them:
restart the unit, never one rank.

### G4. Memory headroom and the GB10 stack cap

The following measurements and recipe settings apply to GLM. GB10 memory is **unified**: the model's ~80 GiB per Spark is inside MemTotal
(124,607 MiB). Two caps keep the box from freezing. Unified memory freezes
rather than OOMs.

- **The engine side** ([D1](#d1-the-glm-recipe-the-alternative-deployment)):
  `KV_POOL_GIB=10` fixes the KV pool, and `MEMORY_RESERVE_GIB=17.5` leaves host
  memory for the command center.
- **The stack side** (`start.sh`): `start.sh -d` runs the stack in a
  memory-capped systemd scope. On a unified-memory box running its own model,
  the cap is re-sized once the model is up, to **MemAvailable minus
  `MEM_RESERVE_GB`** (default 3), added on top of what the stack already
  holds. The chat jobs' cap is derived the same way, and comes only out of
  that spare: the stack is never capped below its own current usage. When
  less than the jobs' 1 % of RAM is spare, start.sh prints a "no headroom"
  warning instead of shrinking the stack under what it holds.
  75 % of a MemTotal the model already holds would protect nothing.
  `healthcheck.sh` and `doctor.sh` warn when MemAvailable falls below
  `MEM_RESERVE_GB`.

Watch the `Unified memory: N MiB available` line in `healthcheck.sh`. With the
pair serving at `PARALLEL=4` and the full stack up, it read about **9.7-9.9 GiB**
on 2026-11-15, and spark-2 (rank 1 only) about 11 GiB. Before adding anything to
spark-1 (an extension, a second model, a heavy build), check that line. Do not
try `PARALLEL=8` without raising the reserve. The recipe's notes measured rank
0's lowest free memory at 7.0 GiB for `PARALLEL=8`.

### G5. The watchdog and self-healing

- **`openbeast-watchdog.timer`** runs `healthcheck.sh --restart` every 5 min.
  It restarts DOWN compose services. A down managed engine is only reported,
  never restarted. The unmanaged TensorFold pair is systemd's job, not the
  watchdog's.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a stalled rank exits, and the unit
  restarts the pair.
- **`healthcheck.sh`** also catches a stalled TensorFold pair that still says
  `ok`, via the `.run/tf-liveness` record.
- **beast-island** (`ISLAND=auto`): if the internet goes away, the web tools
  refuse at once after 90 s, and the stack rejoins by itself.

### G6. Updating

**Update everything, in order.** This moves a built pair to the latest
`main` and brings along every component a recent change added. Commands run
on the host named; **sudo** steps need Max at a terminal on that box. Building
from scratch is the other checklist, [Install everything, fresh](#install-everything-fresh).
On the Sparks, `scripts/update.sh` is **not** the update: it moves upstream
pins (llama.cpp to `master`, images to their moving tags) and rewrites tracked
files, and a bare run dies at its first step on a Spark, which has no
`llama.cpp/` clone. It is a maintainer tool ([UPDATING.md](UPDATING.md)).

**1. Pick a quiet moment.** Restarting the command center restarts
beast-gate, which drops every generation in flight through `:8443`. A
benchmark running from the Mac loses its in-flight units. Check
`slots.busy` first (from the Mac):
`curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -c 'import json,sys; print(json.load(sys.stdin)["slots"])'`.
Inference itself (the selected unit: `deepseek-v41-inference`, or
`spark-hydra-inference` while GLM is selected) is not restarted by any step
below unless the recipe or the engine changes.

**2. Read what is coming** (spark-1):

```bash
cd ~/openbeast && git fetch && git status -sb             # never pull over local edits
git log --oneline HEAD..origin/main                        # what the pull brings
git diff --stat HEAD origin/main -- docker-compose.yml agents/requirements.lock extensions/ scripts/backends/
```

Read [UPDATING.md](UPDATING.md) "Upgrade notes" for any line the diff
touches. Offline, `git fetch /media/usb/openbeast.bundle main` instead of
`git fetch`, and compare against `FETCH_HEAD` instead of `origin/main`.

**3. Back up Open WebUI when its pin moves** (spark-1). If the diff touches
the `open-webui` line of `docker-compose.yml`, the new image migrates the
database at first start and cannot be downgraded past it (the 0.11.0 → 0.13.3
sweep, [UPDATING.md](UPDATING.md) "Upgrade notes: the 2026-11 dependency
sweep"). Stop the stack through its unit, then copy the volume:

```bash
systemctl --user stop openbeast                    # the command center only; inference keeps running
mkdir -p ~/backups
docker run --rm -v openbeast_open-webui-data:/data:ro -v ~/backups:/backup alpine \
  tar czf /backup/open-webui-data-$(date +%F-%H%M).tgz -C /data .
```

spark-1 did this before the 0.13.3 sweep:
`~/backups/open-webui-data-2026-11-16-0024.tgz`. `alpine:latest` is in
spark-1's image store, so the command also works offline. On a box without
it, any image already present that has `tar` does the same job. The stack
stays down until step 5's start; skip the stop if the pin did not move.

**4. Pull on both, Python deps on the head** (both Sparks):

```bash
cd ~/openbeast && git pull --ff-only && ./scripts/pydeps.sh install     # spark-1
ssh max@10.100.216.2 'cd ~/openbeast && git pull --ff-only'              # spark-2: same commit
git -C ~/openbeast log --oneline -1; ssh max@10.100.216.2 'git -C ~/openbeast log --oneline -1'   # must match
```

`pydeps.sh install` puts the lock this commit ships into `~/.local`, with
`--require-hashes`, and is a no-op when nothing moved. `start.sh` refuses to
start on a lock it does not match, so this comes before the restart. spark-2
needs no Python layer: it runs no OpenBeast service, but its checkout must
match for `--verify`, `spark-node.sh`, `spark-fabric-mtu.sh` and
`spark-cluster.sh check`. Offline:
`git pull --ff-only /media/usb/openbeast.bundle main` on each, and
`./scripts/pydeps.sh install --from /media/usb/wheels-spark` on spark-1, with
the wheelhouse filled by `pydeps.sh wheelhouse` on an online aarch64 box
([H6](#h6-the-command-center-wheels-and-compose-images)).

**5. Restart the command center** (spark-1):

```bash
systemctl --user restart openbeast       # or "start", after step 3's stop
~/openbeast/scripts/healthcheck.sh       # "All 9 services healthy"
~/openbeast/scripts/doctor.sh
```

`start.sh -d` pulls the image digests the new commit pins (online), restarts
beast-gate, the tool server, beast-chat, beast-artifact and the dashboard,
and runs `configure-webui.sh`, which re-applies the tool servers and installs
or updates the random seed filter. Offline, new images come from a bundle
built on an online Spark at the **same** commit:
`./scripts/bundle.sh verify` + `install` before the restart
([H6](#h6-the-command-center-wheels-and-compose-images)). `bundle.sh install`
rewrites `docker-compose.yml` to content IDs and keeps
`docker-compose.yml.pre-bundle`; `git checkout -- docker-compose.yml` before
the next pull, or that pull conflicts.

**6. The per-change steps.** Each row is a change a recent PR added; do the
ones whose "done when" is not yet true.

| Change | Where | Do | Done when |
|---|---|---|---|
| spark-guard rules (#168, and any change to spark.env ports or the fabric) | each Spark, **sudo** | `scripts/backends/spark-guard.sh --status` (the rules readout works without sudo); if `STALE`, `sudo scripts/backends/spark-guard.sh` ([B3](#b3-firewall-spark-guard)) | `rules: current` on both |
| the gate's in-flight cap follows `INFERENCE_SLOTS` (#168) | spark-1 | nothing beyond step 5, with `EDGE_MAX_INFLIGHT` unset | `/api/slot` `admission.per_device_inflight` is 4 (live since 2026-11-16) |
| `TS_HOSTNAME` (#160) | spark-1 (and any Spark that will run `setup-tailscale.sh`) | `grep -q '^TS_HOSTNAME=' openbeast.conf \|\| echo 'TS_HOSTNAME=spark-1' >> openbeast.conf` ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)) | the key is set (spark-1: yes) |
| Open WebUI 0.13.3, SearXNG, ntfy (#161) | spark-1 | step 3 before, step 5 does the rest | WebUI healthy, chats still there; `docker exec open-webui sh -c 'grep -m1 version /app/package.json'` says 0.13.3 |
| `ARTIFACT_LOGIN_FROM=unix` (#165) | spark-1 | `echo 'ARTIFACT_LOGIN_FROM=unix' >> openbeast.conf`, `systemctl --user restart openbeast`, then `./scripts/setup-tailscale.sh --publish-artifact` (as operator, no sudo) | `tailscale serve status` shows `:8446` → `unix:…/.run/artifact.sock` |
| engines hardening (#174) | each Spark | only if native TensorFold is in use: `scripts/engine.sh status`, then `scripts/backends/spark-cluster.sh check` from spark-1 | `check` passes (same commit, same engine pins) |
| fabric MTU 9000 (#178) | each Spark, **sudo**, then a reboot of both | `sudo scripts/backends/spark-fabric-mtu.sh --stage` on both, then the reboot and `--verify` (all peer IPs in one call) in [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) | `--verify` prints `VERIFIED` on both (the pair: done 2026-11-17) |
| thinking on by default (#202, #203) | spark-1 | `printf '%s\n' INFERENCE_THINKING=on AGENT_MAX_TOKENS=16384 >> openbeast.conf`, then step 5 (which re-runs `configure-webui.sh`) ([E2a](#e2a-recommended-serving-configuration-from-v5)) | `/gate/health` says `"thinking_default":"on"`; Admin Panel → Functions → "Thinking Default" on (the pair: done 2026-11-17) |
| multi-rig client (#204) | each Mac / Linux client | step 7; an older single-rig install migrates on the first `openbeast-client` command ([F2](#f2-client-mode-one-or-several-rigs)) | `openbeast-client rigs` lists the rig with `*`; `scripts/client-e2e.sh` all PASS |
| random seed for Regenerate (#181) | spark-1 | step 5 (or `./scripts/configure-webui.sh` alone, no restart) | Admin Panel → Functions → "Random Seed", on and global |
| `EDGE_READ_TIMEOUT` | spark-1, when an eval harness targets the pair | `echo 'EDGE_READ_TIMEOUT=3600' >> openbeast.conf`, `systemctl --user restart openbeast` | set (spark-1: 3600) |
| zig 0.16.0 for `language_reference` | spark-1 (and the harness host) | `ark restore zig` from a drive, or the pinned tarball ([H7](#h7-test-and-language-toolchains)) | `zig version` prints 0.16.0 |
| beast-docs mirror | spark-1 | sync + pack on the Mac, `docs.sh unpack` on spark-1 ([H8](#h8-beast-docs-mirror)) | `docs.sh status` lists the sources, none `STALE` |
| beast-ark | the drive | `DRIVE/ark verify DRIVE`; a maintainer moves pins with `ark.sh update` online ([BEAST_ARK.md](BEAST_ARK.md) §10) | `verify` passes |

**7. Clients** (each client): `openbeast-client update` (it re-probes every
registered rig and rewrites the catalog, [F3](#f3-what-the-client-writes-into-opencode)),
then `scripts/client-e2e.sh`. Offline: pull the client's repo from a bundle, then
`pydeps.sh install --from` a wheelhouse for the client's platform
([UPDATING.md](UPDATING.md) "Clients update themselves").

**8. The OS** (each Spark, **sudo**, rarely). `libnccl2` is held, so
`sudo apt update && sudo apt upgrade` leaves it alone and moves tailscale and
the rest of DGX OS. A driver, kernel or firmware update needs the reboot
order from [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) step 3:
`~/.local/bin/beast-spark down` on spark-1, reboot the worker, then the head. Offline: carry new .debs and pins as in
[H3](#h3-debs-libnccl2-and-tailscale). OpenBeast never upgrades the driver.

**Re-read 2026-11-19 (read-only):** everything below is still pending, with
one change: spark-1 has a docs mirror with the `tailscale` source only
(`docs.sh status`), not the full set. `AGENT_PROFILE=deepseek-best`
([E2b](#e2b-deepseek-best-configuration-v5-t2)) went live on 2026-11-19 ~14:50.

**Pending on the live pair (read 2026-11-17, evening, over ssh, no sudo).**
What the checklist above still has to do on spark-1 and spark-2:

- **spark-guard `STALE` on both** (sudo, step 6): the installed rules guard
  `29501` but not `29502` or `29620-29621` (the DeepSeek recipe's MASTER_PORT).
- **`ARTIFACT_LOGIN_FROM` unset** on spark-1: beast-artifact still honours
  the login header from loopback, and `:8446` proxies to `127.0.0.1:3004`
  (step 6; [Known issues](#known-issues-and-errata-v190)).
- **No zig and no docs mirror** on spark-1 (step 6).
- **The MTU A/B** ([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)): the MTU
  itself is done.
- From earlier: the plaintext gate keys in `~/.config/openbeast/gate-keys/`
  and `~/.config/openbeast/webui-max-password` are still on spark-1
  ([E7](#e7-device-keys-for-beast-gate), [E5](#e5-webui-auth-and-the-admin-account-before-publishing)).

Done since the 2026-11-16 reading: both Sparks pulled to one commit
(`d3727185`, #203); the command center restarted, so the random seed and
Thinking Default filters are live; `INFERENCE_THINKING=on` and
`AGENT_MAX_TOKENS=16384` in spark-1's `openbeast.conf`; MTU 9000 staged,
rebooted and verified on both.

The detail behind steps 4 and 6 follows.

**OpenBeast** on every node, the same commit everywhere. The worker runs no
OpenBeast services. Its checkout matters for the lock (`--verify`), for
`spark-node.sh`, `spark-fabric-mtu.sh` and `spark-cluster.sh check`.

**The engine:**

- Native TensorFold: `scripts/engine.sh update tensorfold` (or
  `install-native.sh --version V`) on every Spark, then restart all ranks.
  `spark-cluster.sh check` fails while the ranks disagree on the native
  binary or on any spark.env engine pin (`TENSORFOLD_VERSION`, `_REF`,
  `_IMAGE*`, `VLLM_IMAGE*`), so run it before `start`.
- GLM's recipe: a new recipe version or image means a new `git pull` in
  `~/openbeast-recipes/glm53`, `prepare.sh` (online), the image carried to every
  rank, and `systemctl --user restart spark-hydra-inference` (only while GLM
  is selected). DeepSeek's runtime stays pinned at `c7f2b85`; a new pack goes
  through `spark-deepseek-pack.sh` ([DeepSeek packs](#deepseek-packs-spark-deepseek-pack)),
  and a new recipe or image is a new deployment, recorded like the
  [DeepSeek deployment](#deepseek-deployment).
- Before moving any engine pin, read [ENGINES.md](ENGINES.md) "Research notes".

**A model:** a new profile is `model-inspect.sh` → `model-fetch.sh` (lock) →
`conformance.sh` → serve. The lock of a served model never changes. A new
revision is a new profile.

**Open WebUI's Regenerate on the GLM pair.** TensorFold falls back to a
fixed seed when a request names none: three unseeded requests at temperature
0.6 returned one text, seeds 1/2/3 three texts, seed 1 twice the same text
(probed 2026-11-16). So Regenerate in WebUI, which re-sends the same request
without a seed, returned the same answer. `configure-webui.sh` (run by every
`systemctl --user restart openbeast`) installs the global Open WebUI filter
`agents/webui_functions/random_seed.py`, which adds a random seed to every
WebUI chat request without one. To apply it after a `git pull` without a
restart, run only that script; it touches no inference unit:

```bash
cd ~/openbeast && ./scripts/configure-webui.sh     # idempotent; "Random seed filter … already installed" on a re-run
```

Check it in Admin Panel → Functions ("Random Seed", on, global). A seed set
in a chat's Controls or a model's params still wins, so a pinned seed stays
reproducible. It is WebUI-only: opencode, the Mac client, the eval runner
and anything else on `:8000` / `:8443` still get TensorFold's fixed default
(`seed` in the request is the fix there). `WEBUI_RANDOM_SEED=false` in
`openbeast.conf` switches the filter off at the next run.

### G7. Rotating keys and secrets

| Secret | Rotate with | Notes |
|---|---|---|
| a device key | `./scripts/clients.sh rotate <id>` | hot-reloaded; give the device its new key |
| a lost device | `./scripts/clients.sh revoke <id>` | immediate |
| WebUI admin password | WebUI Settings → Account, then update `WEBUI_ADMIN_PASSWORD` if it is the configured admin | `systemctl --user restart openbeast` so `configure-webui.sh` uses it |
| HF token | replace `~/.config/openbeast/hf-token` (0600) | only used online |
| TensorFold / vLLM API key files | rewrite the 0600 file named in `spark.env`, restart all ranks | the recipe (v0.6.0) has no key |

**A key that was printed.** Rotate at once, then check both sides:

```bash
cd ~/openbeast && ./scripts/clients.sh rotate macbook     # on spark-1: the new key prints once
# on the device: write the new key straight into its 0600 file (on the Mac,
# ~/.config/openbeast/spark-hydra-gate.key), then from the device:
curl -s -o /dev/null -w '%{http_code}\n' https://spark-1.tail4109f9.ts.net:8443/v1/models \
  -H "Authorization: Bearer $(cat ~/.config/openbeast/spark-hydra-gate.key)"   # 200 with the new key
```

The old key must now get 401 from the same call. `rotate` is hot-reloaded:
no restart of the command center is needed.

**Incident, 2026-11-17.** The Mac's gate key was printed into an agent
transcript: a BSD `sed` redaction that used `\|` alternation matched nothing
on macOS (BSD basic regexes have no `\|`), so the "redacted" output was the
raw key. The key was rotated the same minute with `clients.sh rotate
macbook`; the old key answers 401, the new one 200. Lessons, for anyone
(human or agent) handling secrets on these boxes:

- **Never print a secret to redact it afterwards.** Read keys from their
  0600 files inside the command that uses them (`$(cat …)`, as above,
  `--api-key-stdin <`, `--key-file`), so the value never reaches a terminal
  or a log.
- **Never redact with `sed` alternation.** `\|` is a GNU extension; BSD sed
  (macOS) silently treats it as a literal, matches nothing and passes the
  secret through. GNU and BSD also disagree on `-E`/`-r` and `\b`. If output
  must be filtered, use python (`re.sub`), which behaves the same on every
  box, and test the filter on a dummy value first.
- **Assume an agent transcript is a shared log.** It is kept, exported and
  sometimes published; a key that reached one is a leaked key. Rotate, do
  not argue.

### G8. When X fails: troubleshooting

Every row below was hit on the way to the live pair.

| Symptom | Cause | Fix |
|---|---|---|
| WebUI / SearXNG DOWN; `.run/stack.log`: "docker daemon not ready" | the systemd user manager started before `max` joined the `docker` group | reboot once; `systemd-run --user --wait --pipe docker info` proves it |
| `ssh spark-1` cannot resolve `spark-3dc6.local` | mDNS right after a reboot, or away from home | `ssh -o HostName=192.168.40.153 spark-1` at home; the tailnet name with `HostKeyAlias` away ([F1](#f1-ssh-from-the-mac)) |
| `ssh spark-1.tail4109f9.ts.net` asks to trust a new host key | no `HostKeyAlias` | add `HostKeyAlias spark-3dc6.local` to that `Host` block |
| `ssh spark-1 cat ~/.config/openbeast/x`: "No such file" | the Mac's shell expanded `~` to `/Users/…` | quote the remote command: `ssh spark-1 'cat ~/.config/openbeast/x'` |
| ranks cannot reach each other | a firewall drops the peer on the fabric (ufw, if someone enables it without the fabric rules) | `spark-root-setup.sh` adds a ufw rule per Up link for that link's subnet; `spark-guard.sh` always accepts the fabric. ufw itself is **disabled** on DGX OS (`/etc/ufw/ufw.conf` `ENABLED=no`) and was never what blocked or protected anything here |
| after a reboot, ping and ssh over the fabric work but the pair hangs loading or mid-prefill | a fabric MTU mismatch: one Spark at 9000 (`spark-fabric-mtu.sh` staged and rebooted), the other still at 1500, so frames over 1500 bytes are dropped | `scripts/backends/spark-fabric-mtu.sh --status` on both; reboot the one still at 1500 (stage it first), or `--unstage` the other and reboot it; `--verify` ([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)) |
| `nc -z <tailnet IP> 29551` succeeds from a laptop | rank 0's TCPStore binds `*:29551`, ufw is off, and Tailscale's `ts-input` accepts tailnet traffic first | `sudo scripts/backends/spark-guard.sh` on each Spark ([B3](#b3-firewall-spark-guard)) |
| `tensorfold capabilities` shows NCCL `found:false` | no libnccl2 | `spark-root-setup.sh` installs 2.28.9+cuda13.0 and holds it |
| `model-fetch --verify`: "unlocked file .cache/huggingface/…" | `hf download --local-dir` metadata | fixed: hidden paths are skipped by both lock and verify |
| `model-fetch --verify` / `--locate` exit 3 | no lock in this checkout | pull the commit with the lock, or `--verify --trust-first-use` for a local SOURCE |
| the recipe serves, but the lock "does not cover" it | the HF-cache snapshot lost its hard links (re-download, rename-save) | re-run the `stat` check (C2); re-link with `cp -al` and re-verify |
| a container engine on an offline worker tries to pull | `*_IMAGE_DIGEST` pin; `docker save/load` drops the RepoDigest | pin `*_IMAGE_ID` instead (`sync-image` prints the line) ([H4](#h4-container-images)) |
| `TENSORFOLD_ENGINE=container` fails offline | it pip-installs TensorFold from GitHub and deps from PyPI at every start | use the native engine (default) or the recipe; or, on a connected box, build an image with TensorFold at `TENSORFOLD_REF` and its locked deps installed, carry it with `spark-cluster.sh sync-image` / `save-image`, pin `TENSORFOLD_IMAGE_ID` and set `TENSORFOLD_IMAGE_PREBUILT=true` |
| box freezes / ssh hangs under load | unified memory exhausted (it freezes rather than OOMs) | keep `KV_POOL_GIB` / `MEMORY_RESERVE_GIB` as in D1; watch `Unified memory` in healthcheck ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)) |
| pair stops answering mid-stream, `/health` still `ok` | a stalled collective | `TF_GLM_MULTI_WATCHDOG_EXIT=1` makes the rank exit so the unit restarts both; healthcheck's liveness check flags it |
| `:443` not published after `setup-tailscale.sh` | the running WebUI still had auth off | `systemctl --user restart openbeast`, then re-run `setup-tailscale.sh` ([E5](#e5-webui-auth-and-the-admin-account-before-publishing)) |
| `:8443` answers 401 | no device key, or a revoked one | `clients.sh list`; give the client its key |
| Regenerate in Open WebUI returns the same answer, word for word | TensorFold uses a fixed seed when the request names none | `./scripts/configure-webui.sh` installs the random seed filter; check Admin Panel → Functions → "Random Seed" is on and global, and `WEBUI_RANDOM_SEED` is not `false` ([G6](#g6-updating)) |
| GLM answers with no reasoning (no "Thought for…" block in WebUI, no `reasoning_content` from :8443) | `INFERENCE_THINKING` unset, the gate not restarted since it was set, the WebUI filter off, or the client sent `enable_thinking: false` / another thinking control | the local `/gate/health` must say `"thinking_default":"on"` (else `systemctl --user restart openbeast`); Admin Panel → Functions → "Thinking Default" on and global (`./scripts/configure-webui.sh`); check the client's own params ([E2a](#e2a-recommended-serving-configuration-from-v5)) |
| opencode on the Mac lost `openbeast-tools` (or another server) | a v1 `mcp.<name>` entry next to v2 `mcp.servers.<name>`: v2 does not merge them | move everything under `mcp.servers`, drop `enabled` ([F3](#f3-what-the-client-writes-into-opencode)); `setup-client.sh` does this itself now |
| `tests/test_scripts.sh` failures on a copy without `.git` (7 seen on spark-2) | some suites need a git checkout | run from a git checkout |
| shell suites fail on the Mac but pass on a Spark | BSD vs GNU tools (`tests/test_engines.sh` needs GNU coreutils; `test_skill_import.sh` showed 5 BSD-only failures on 2026-11-15) | run shell suites on a Spark ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md) "Tests") |
| `python3 -m pytest` says no module | no pytest for that python (none on spark-1 or the Mac's default python; spark-2 has `~/obvenv`, 2026-11-18) | `source ~/obvenv/bin/activate` on spark-2, the eval venv on the Mac, or build the hash-pinned test venv ([H7](#h7-test-and-language-toolchains)) |
| opencode on spark-2 cannot reach the model | the `opencode-model-link` tunnel is down (spark-1 rebooting, fabric down) | `systemctl --user status opencode-model-link` on spark-2; it retries every 10 s by itself |
| `bootstrap.sh` offline asks for `bundle.sh build` "on a connected box" | the bundle must be built for the **target's** platform | build it on an online Spark (aarch64, same overlay2 store) ([H6](#h6-the-command-center-wheels-and-compose-images)) |
| web tools stall instead of refusing in a spawned agent | `agents/tools.py` (era-hashed) has no island guard; only `mcp_server.py` does | expected (BEAST_ISLAND "Known limits"); web_search is bounded at 15 s, fetch at 45 s |

---

## H. Fully offline operation

### H1. Two switches, and what keeps working

| Switch | Meaning | Effect |
|---|---|---|
| `ISLAND=auto` (default) / `force` (`./start.sh -d --offline`) | runtime: "the internet just went away" | `web_search` / `fetch` (MCP + `:3001`) refuse at once; WebUI in `OFFLINE_MODE`; compose `--pull never` when islanded at launch; healthcheck excuses an offline Tailscale ([BEAST_ISLAND.md](BEAST_ISLAND.md)) |
| `OFFLINE=true` in `openbeast.conf` (env `OPENBEAST_OFFLINE`) | install: "this box never has internet" | `bootstrap.sh`, `update.sh`, `engine.sh update`, `model-fetch.sh`, `apps.sh refresh` refuse the network up front and say what to stage instead |

**What keeps working offline:**

- **Inference** works: the recipe runs with `PREPARE=0` and `HF_HUB_OFFLINE=1`,
  and everything is local.
- **The whole command center** works except the web: WebUI, tools, agents,
  skills, files, beast-chat, beast-artifact, beast-gate, beast-slot.
- **opencode on both Sparks** (v2.3.2, since 2026-11-18) talks to
  `127.0.0.1:8000` directly, spark-2 through its `opencode-model-link` ssh
  tunnel over the fabric. No gate, tailnet or LAN in the path, so it works
  with everything outside the pair down. It has no OpenBeast MCP tools yet
  ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md#opencode-on-the-sparks)).
  opencode on the Mac in client mode (F2) keeps working over the LAN or the
  tailnet.
- **`./agent.sh`** works on spark-1.
- **`language_reference`, `platform_docs`, `scripts/docs.sh search`** work
  against what is mirrored.
- **beast-ark** works: `verify`, `restore` and `serve` an ark drive (H8a,
  [BEAST_ARK.md](BEAST_ARK.md)).
- **The tests** work (hermetic).
- **The tailnet** keeps working wherever the LAN still routes between devices.
  Tailscale needs its coordination server to set up new paths, so existing
  direct connections are the ones that survive.

What does not work: `web_search` (refused), `fetch` (refused), WebUI's built-in
Web Search toggle (empty results), Hub downloads, image pulls, apt, pip from an
index, `git pull` from GitHub.

### H2. The repo

`git bundle` carries the whole history; a plain copy of a checkout works too:

```bash
git -C ~/openbeast bundle create /media/usb/openbeast.bundle --all     # online box
git clone /media/usb/openbeast.bundle ~/openbeast                      # closed box
git -C ~/openbeast log --oneline -1                                     # the commit you expect
```

For **developing** offline (branches, review, merge, and the sync back to
GitHub), the Sparks share a bare hub repository on spark-1,
`~/git/openbeast.git`, with an offline trunk `offline/main`:
[SELF_DEVELOPMENT.md §11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable).
The hub is not created yet (2026-11-18).

### H3. .debs: libnccl2 and tailscale

On a Spark that can reach apt's repos (apt checks the repo signatures as it
downloads):

```bash
mkdir debs && cd debs && apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale=1.104.7 && cd ..
~/openbeast/scripts/backends/spark-root-setup.sh --check-debs debs        # no root: sha256 vs the pins -> "OK: debs holds the pinned .debs"
sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --debs debs --guard   # on the closed Spark
```

A `.deb` given by path skips apt's signature check. So `--debs` installs only
those exact versions, and only if each matches the sha256 pinned in the script
(`NCCL_DEB_SHA256`, `TS_DEB_SHA256`, taken from the signed repo indexes). A
newer tailscale means bumping `TS_DEB_VERSION` / `TS_DEB_SHA256` there
(`apt-cache show tailscale=<V> | grep SHA256`). A closed box cannot finish
`tailscale up` without a route to the coordination server. The script says so
and continues; re-run it once there is a route.

### H4. Container images

**The recipe image** is `tensorfold-glm53:v0.6.0`, ID
`sha256:ead0174052ac16b07d98fba9b68e7d7101b1e1db5c0ccdbde62f41ccf229c647`. The
recipe checks it by **tag**, so `docker load` satisfies it:

```bash
docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load      # spark-1 -> spark-2 over the fabric
docker save -o tensorfold-glm53-v0.6.0.tar tensorfold-glm53:v0.6.0 && sha256sum tensorfold-glm53-v0.6.0.tar   # to a stick
docker load -i tensorfold-glm53-v0.6.0.tar                                    # on the closed Spark
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0                     # must print the ID above
```

The ID compares across the Sparks because both use the same classic overlay2
store. `scripts/bundle.sh` explains why it would not compare across store kinds.

**OpenBeast's container engines** (`spark-node.sh` with vLLM or container
TensorFold): `docker save | docker load` keeps an image's ID but drops its
RepoDigest. A digest pin (`*_IMAGE_DIGEST`) would therefore make an offline
worker try a registry pull. **Pin by ID instead:**

- `VLLM_IMAGE_ID` / `TENSORFOLD_IMAGE_ID` in `spark.env` win over a digest.
- The container runs as `sha256:<id>`; docker never pulls an ID.
- `spark-cluster.sh sync-image IMAGE` prints the exact line to pin.
- `save-image IMAGE FILE` writes the image to a file for a cluster with no
  registry at all.

The container TensorFold engine still needs GitHub and PyPI at every start, so
it is not offline-capable. The native engine is.

### H5. Native TensorFold and the engine plane

On any connected box, the Mac included:

```bash
scripts/backends/tensorfold/install-native.sh --version 1.2.10 --stage tf-1.2.10                       # stages linux-aarch64 by default
scripts/backends/tensorfold/install-native.sh --version 1.2.10 --from tf-1.2.10 --platform linux-aarch64   # on the Spark
# --from computes the archive name from --version: without it, it looks for the
# default release (1.3.4) and refuses a folder staged for another one.
# The ark mirrors the default (1.3.4): `ark` fetches the three archives with their
# signed .sha256 files, and install-native.sh --version 1.3.4 --from <that files dir> installs it.
# or through the engine plane, which also pins and records history:
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 /media/usb/tf
scripts/engine.sh install tensorfold --from /media/usb/tf
```

Both ends verify:

- The `.sha256` must name exactly `tensorfold-<V>-linux-aarch64.tar.gz`.
- Its ed25519 signature is checked against the vendored key. That needs
  OpenSSL ≥ 3 or python3 `cryptography`. On macOS the script finds Homebrew's
  `openssl@3` itself; LibreSSL and OpenSSL 1.1 cannot check ed25519.

Under `OFFLINE=true`, `engine.sh update` without `--from` refuses and says why.

### H6. The command center: wheels and compose images

`./bootstrap.sh` needs PyPI and the compose images. `scripts/bundle.sh build`
packs both, but for the **build host's** platform and image store. A bundle
built on the Mac holds macOS wheels and fails on a Spark. Build it **on a Spark
while it is online** (aarch64, the same overlay2 store) and keep it:

```bash
./scripts/bundle.sh build ~/openbeast-bundle      # on spark-1, online
./scripts/bundle.sh sign  ~/openbeast-bundle --key ~/.ssh/openbeast-bundle   # optional; prints the allowed-signers line
./scripts/bundle.sh verify  ~/openbeast-bundle [--key ~/.config/openbeast/allowed-signers]   # on the closed Spark
./scripts/bundle.sh install ~/openbeast-bundle [--key …]                                     # same commit as the build
echo 'OFFLINE=true' >> ~/openbeast/openbeast.conf
./bootstrap.sh --no-start && ./scripts/doctor.sh  # doctor says "serves offline" vs "can be maintained offline"
```

Off a Spark, only the wheels can be cross-built:

1. Run `./scripts/pydeps.sh wheelhouse wheels-spark` with the manylinux aarch64
   flags it prints. One `--platform manylinux_2_39_aarch64` alone misses cffi.
2. On the Spark, run `./scripts/pydeps.sh install --from wheels-spark`.

The lock never travels with the wheels. `install --from` accepts only the
checkout's committed lock, or one vouched for with `--lock-sha256`.

The compose images still have to come from a Spark-side `docker save`. They are
multi-arch at the pinned digests, so a connected Spark pulls the arm64 ones.
`bundle.sh install` rewrites the compose references to the loaded content IDs
and keeps a `.pre-bundle` copy.

`bootstrap.sh`'s OFFLINE message ("bundle.sh build on a connected box") does
not say the box must be aarch64. That is a known issue below.

### H7. Test and language toolchains

**pytest.** It is not in `agents/requirements.lock`, and neither spark-1's
`python3` (3.12.3) nor the Mac's default `python3` has it. The hash-pinned CI
closure is `.github/ci-requirements/test.txt`. This recipe was verified on the
Mac with python 3.12 on 2026-11-15 (95 tests passed). On a Spark it is the same
commands with `/usr/bin/python3`.

```bash
python3.12 -m venv ~/.venvs/openbeast-test
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes -r ~/openbeast/agents/requirements.lock
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes --no-deps -r ~/openbeast/.github/ci-requirements/test.txt
# offline: fill a wheelhouse online with the same files, then install with --no-index --find-links DIR
python3 -m pip download --require-hashes --no-deps -r .github/ci-requirements/test.txt -d wheels-test   # VERIFY ON HARDWARE for aarch64
```

**zig 0.16.** Nothing in the stack downloads a zig *compiler*. For
`language_reference` and the zig eval variants on a Spark, install it by hand,
pinned like CI's: `zig-aarch64-linux-0.16.0.tar.xz`, sha256
`ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17` (ziglang.org
index, read 2026-11-13). `lang-library.sh acquire zig` fetches the x86_64
tarball on any host, and that is fine: it is read as a std-source corpus, never
executed. No zig is installed on either Spark today (checked 2026-11-16).
The simplest pinned route, online or offline, is beast-ark
([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)): its zig 0.16.0
entry carries that same sha256 plus the minisign signature, and
`ark restore zig --from DRIVE` puts it in `~/.local/opt/zig-0.16.0` with a
link in `~/.local/bin`. By hand, online:

```bash
cd /tmp && curl -fsSLO https://ziglang.org/download/0.16.0/zig-aarch64-linux-0.16.0.tar.xz
echo 'ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17  zig-aarch64-linux-0.16.0.tar.xz' | sha256sum -c -
mkdir -p ~/.local/opt ~/.local/bin && tar -xJf zig-aarch64-linux-0.16.0.tar.xz -C ~/.local/opt
ln -sfn ~/.local/opt/zig-aarch64-linux-0.16.0/zig ~/.local/bin/zig && zig version   # 0.16.0
```

**Rust, Go, clang.** beast-ark carries them pinned and checked: `go` 1.27.4,
`rust` 1.100.0 (`install = "manual"`: `restore` prints the installer path,
[BEAST_ARK.md](BEAST_ARK.md) §8.2) and `llvm` 23.1.5 (clang, lld). Restore
them from a drive the same way, online or offline. None is installed on
either Spark today: they have gcc / g++ 13.3 only. A Spark needs them only
for `language_reference` on those languages or to run eval validators
locally; the harness host needs them for every run
([F6](#f6-the-eval-harness-host-benchmarking-the-pair)).

**The language library** (`scripts/lang-library.sh acquire | check | list |
verify`) lives outside git. Acquire it online and copy `lang-library.sh where`'s
directory.

### H8. beast-docs mirror

```bash
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms   # online box
./scripts/docs.sh pack mirror.tar.gz                             # writes mirror.tar.gz + mirror.tar.gz.sha256
scp mirror.tar.gz mirror.tar.gz.sha256 spark-1.tail4109f9.ts.net:
ssh spark-1.tail4109f9.ts.net 'cd ~/openbeast && ./scripts/docs.sh unpack ~/mirror.tar.gz && ./scripts/docs.sh status'
```

`unpack` checks the tarball's hash first. It refuses absolute paths, `..`,
links and devices. It stages, requires the member set to equal `BUNDLE.json`,
re-hashes every file, and only then swaps each source in and reindexes. The
tarball hash proves integrity, not authenticity; sign the `.sha256` the way
`bundle.sh sign` does if you need authenticity.

### H8a. beast-ark: the rest of the software, on a drive

H2-H8 carry OpenBeast itself. Building or testing a larger system next to
the pair needs much more software. beast-ark
([BEAST_ARK.md](BEAST_ARK.md)) holds it: 112 open-source packages, each
pinned to a commit, with a sha256 or digest for every file and a build for
linux-aarch64. That includes Kubernetes and k3s with their images,
containerd, Docker, Go, Rust, Zig 0.16, Node, Temurin, Postgres, NATS,
Prometheus and Grafana, headscale, Forgejo, step-ca, Trivy with its DB, and
DevDocs. Fill a drive on any connected box, then restore on the Spark with
no network:

```bash
./scripts/ark.sh fetch --to /Volumes/ARK --profile core --platform linux-aarch64   # ~5.4 GiB + source
# plug the drive into the Spark (or rsync it over the cable)
/media/max/ARK/ark verify /media/max/ARK                       # re-hash; signatures re-checked
/media/max/ARK/ark restore go --from /media/max/ARK            # → ~/.local/opt/go-1.27.4, ~/.local/bin/go
/media/max/ARK/ark restore k3s --from /media/max/ARK           # + the air-gap image bundle (BEAST_ARK §8.4)
/media/max/ARK/ark serve /media/max/ARK --tailnet              # registry + pip/npm/go mirrors for the other boxes
```

The drive runs its own copy of the tool (`DRIVE/ark`), so it needs no
checkout. The Spark's python 3.12 is enough. On a box with no python, the
wrapper unpacks the CPython archived on the drive after checking it against
`SHA256SUMS`. Verified on spark-2 on 2026-11-15 (Zig 0.16.0 with its
minisign signature, and jq: fetched, verified, restored, run). `fetch`,
`lock` and `update` exit 3 under `OFFLINE=true` or island mode. The
TensorFold and llama.cpp pins in the ark are the repo's own (H5, H6), held
equal by tests.

### H9. The order for a pair built entirely offline

This order has not been run end to end. Every piece is measured on its own, and
the sequence is VERIFY ON HARDWARE.

1. Cable, NVIDIA Sync addressing (A2, B1). The repo from a bundle (H2).
2. `spark-root-setup.sh spark-N --debs debs --guard` on each Spark (H3).
   Optionally `spark-fabric-mtu.sh --stage` (B4, fully local). Reboot once.
3. Weights: copy, `model-fetch --verify`, HF-cache hard links, rsync to workers,
   verify there (C1-C3). DFlash2 plus the blob loop (C4).
4. Recipe checkout, `local.sh` (D1), image via `docker load` with the ID
   checked (H4). Unit installed with `PREPARE=0`.
5. Native TensorFold, optional (H5).
6. The command center from a Spark-built bundle with `OFFLINE=true` (H6),
   `openbeast.conf` (E2), units (E3), reboot.
7. Optional: pytest venv, zig, beast-docs mirror (H7, H8). If native
   TensorFold wrote `spark.env` in step 5, re-run `sudo spark-guard.sh` on
   that Spark (B3).
8. Optional: everything else from a beast-ark drive (H8a). `ark verify`
   first, then `ark restore <name>` per tool, or `ark serve --tailnet` for
   the whole fleet.
9. DeepSeek, the default: its host-local files from the backup overlay, the
   offline rank-file rebuild, then `beastds` (step 22 of
   [Install everything, fresh](#install-everything-fresh)).

The same order with every command, per host, online and offline side by side:
[Install everything, fresh](#install-everything-fresh).

---

## Reference

### Serving configuration (measured 2026-11-13)

DFlash2, 4 parallel streams, 1M-token window, fp8 KV (pool ~2.2-2.6M tokens at
the time; `KV_POOL_GIB=10` since 2026-11-15), q4 dense, thinking off by default
(Ablit), RoCE all-gathers on both rails at MTU 1500. These numbers predate two
changes of 2026-11-17: thinking on by default for every client (E2a; it
lengthens the wait for the answer, not the decode rate) and MTU 9000 on the
fabric (B4; not re-measured).

| | 1 stream | 4 streams aggregate (each) | TTFT |
|---|---|---|---|
| prose | 43-44 tok/s | 145 tok/s (37) | ~160-250 ms |
| code | 68-70 tok/s | 224 tok/s (57) | ~170-260 ms |

With MTP instead (`DRAFTER=mtp`): 44 / 56 tok/s single, but **one request at a
time**; 4 queued requests wait 16-20 s. DFlash2 wins and is kept. Not yet
tried: `PARALLEL=8`, which needs a larger memory reserve (G4).

### Parity with the beast rig (verified 2026-11-15)

`openbeast.conf` on spark-1 enables what the rig ran in production (E2).

| Check | Result |
|---|---|
| `scripts/backends/conformance.sh --model GLM-5.3-Flash-EXL3 --heavy` | PASS: chat, stream, tools, tool_result, 2 parallel calls, max_tokens |
| `./agent.sh` (create / edit / run a file) | done in 5 iterations, 0 API errors |
| `./agent.sh` on a real repo fix, network tools disabled (SELF_DEVELOPMENT.md "Proof") | correct 2-file fix, test green, in 25 iterations / 68 s; hit the iteration cap before `task_done` |
| a WebUI chat that calls `bash` through the `:3001` tool server | executed server-side, answered `aarch64` / `20` |
| remote: every surface in E6 from off-LAN (phone hotspot) | 200; `:8443` 401 without a key, chat answers with one |
| `./scripts/doctor.sh` | 39 ok, 1 warning (SSD wear needs a sudoers rule) |

### Scaling: 1 to 8 Sparks

Scripts and their headers point here by this name. The content lives in the
linear sections:

- cabling and addressing per node count: [A2](#a2-cabling-and-addressing-for-1-2-3-4-and-5-8-sparks)
- root setup per node: [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark), [B3](#b3-firewall-spark-guard)
- weights to every node: [C3](#c3-carry-the-weights-to-every-worker)
- `cluster.env`, `spark.env`, `spark-cluster.sh`, and which engine scales how far: [D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)
- the offline image pins: [H4](#h4-container-images)

### Offline staging

This is the old name for the staging steps, which the unit file's header still
uses. They are now:

- A, the .debs: [H3](#h3-debs-libnccl2-and-tailscale)
- B, the weights in the HF-cache layout: [C2](#c2-hard-link-into-the-hf-cache-layout-the-recipe-reads), [C3](#c3-carry-the-weights-to-every-worker)
- C, DFlash2: [C4](#c4-the-drafter-dflash2)
- D, the recipe image: [H4](#h4-container-images)
- E, native TensorFold: [H5](#h5-native-tensorfold-and-the-engine-plane)
- F, the command center: [H6](#h6-the-command-center-wheels-and-compose-images)

### Rebuild from scratch (short form)

1. Cable the CX-7 ports; NVIDIA Sync addresses both rails (A2, B1).
2. On each Spark: `sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --guard`
   (B2, B3). Offline: `--debs DIR` (H3). `sudo …/spark-fabric-mtu.sh --stage`
   (B4). Reboot once.
3. Weights on spark-1, `model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify`,
   hard-link into the HF-cache layout, copy both to spark-2 with `-H` (C). The
   recipe never looks at `~/openbeast-models`.
4. Recipe to `~/openbeast-recipes/glm53`, `scripts/local.sh` as in D1,
   `scripts/prepare.sh` online. Offline: stage the image and DFlash2 (H4, C4)
   and leave `PREPARE=0`, as the unit does. Install the unit, then
   `~/openbeast/scripts/backends/spark-beast.sh install` (beast-spark, the pack
   tools, the aliases).
5. Native TensorFold, optional (D2, H5).
6. `~/openbeast`: `openbeast.conf` (E2), `./bootstrap.sh --no-start`, install
   both units (E3), reboot once, WebUI admin (E5), `setup-tailscale.sh` (E6),
   device keys (E7). Offline: H6.
7. The Mac client (F), then optional zig / docs mirror / ark / harness host.
8. DeepSeek, the default: restore its host-local files from the backup,
   `beastdown`, `spark-restore.sh rebuild --apply`, `beastds` (step 22).

The long form: [Install everything, fresh](#install-everything-fresh).

Back up the pair and restore either Spark fully offline, OS included (golden DGX OS image + overlay): [SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md).

### Known issues and errata (v1.9.0)

These are corrections to `RELEASE_NOTES_v1.9.0.md` and to this page as first
published, from the 2026-11-15 review:

- **ufw was never active.** The release notes and this page said ufw was
  "active by default", and that the root setup's rules were what let the ranks
  talk while keeping the LAN and tailnet out. ufw is installed but disabled on
  both Sparks, so the rules filtered nothing, and the unauthenticated
  rendezvous (29551) was reachable from the tailnet. Fixed by `spark-guard.sh`
  (B3), which runs on both Sparks: `active` on 2026-11-15, and 29551 closed
  from the Mac.
- **sshd accepts passwords** on both Sparks, and `max` is in `sudo` and
  `docker`. Hardening is opt-in: `spark-guard.sh --harden-ssh`.
- **Offline images for `spark-node.sh`**: pin `*_IMAGE_ID`, not a digest (H4).
  The container TensorFold engine needs GitHub and PyPI at every start.
- **The served snapshot is not what the lock names.** The recipe serves the
  HF-cache snapshot; the lock covers `~/openbeast-models/...`. They are the
  same bytes only while hard-linked (C2). DFlash2 is outside every lock (C4).
- **Plaintext device keys on spark-1** (E7): delete them once the devices hold
  their keys. The same goes for `webui-max-password` once it is changed (E5).
- **The recipe container runs with `--network host`**, as root, from
  third-party alpha code. Anything in it can reach `127.0.0.1:8000` (no key, no
  gate audit) and the loopback-trusting beast-chat / beast-artifact ports
  (`OPENBEAST_CHAT_LOGIN_FROM=loopback` trusts any local TCP peer's
  `Tailscale-User-Login`). **beast-chat: fixed on the pair (2026-11-15)** with
  `CHAT_LOGIN_FROM=unix` (E2), so the header counts only on the 0600 socket
  tailscaled proxies to. beast-artifact has the same switch since review
  2026-11-16 (`ARTIFACT_LOGIN_FROM=unix`, docs/BEAST_ARTIFACT.md "Where the
  login header counts"), but the pair still runs it in `loopback` until the
  operator sets it, restarts and re-runs `--publish-artifact`. Still open:
  that switch on the pair, and the recipe's network mode.
- **`bootstrap.sh` OFFLINE hint** points at `bundle.sh build` "on a connected
  box" without saying it must be an aarch64 Spark (H6).
- ~~**`setup-client.sh` writes the opencode v1 MCP shape**~~ Fixed: it writes
  `mcp.servers.<name>` on opencode v2 and moves other v1 entries in (F3;
  verified on the Mac 2026-11-17).
- **Checkout skew**: on 2026-11-16 evening spark-1 was at `29e5241a` and
  spark-2 at `de931dfe` (0). Fixed by 2026-11-17: both at `d3727185`.
  [G6](#g6-updating) step 4 pulls both. **Recurred 2026-11-22:** spark-1
  `e8e4b2fa`, spark-2 `ab62b348`; open until both are on one commit.

From the 2026-11-16 review (fixes in this checkout; the live boxes pick them
up on the operator steps named):

- **Loopback is not isolation on spark-1.** Open WebUI and SearXNG also run
  with `network_mode: host` (`docker-compose.yml`), and SearXNG runs as uid 0
  while it parses untrusted web content. From inside either container,
  `127.0.0.1:8000` answers without a key, gate caps or the inference audit
  (checked read-only: `docker exec searxng wget -qO- http://127.0.0.1:8000/health`
  returns TensorFold's health), and rank 0's unauthenticated TCPStore on
  `*:29551` is reachable on loopback too. The agent `bash` tool has the same
  reach, since Sandlock leaves the network open by default
  ([SANDBOXING.md](SANDBOXING.md)). spark-guard cannot help: it must accept
  `lo` (Open WebUI and the agents reach inference there by design), and an
  nftables `meta skuid` rule would not separate the containers either, because
  Open WebUI, SearXNG and the recipe all run as uid 0. A compromised SearXNG
  could therefore run unaudited generations, starve the 4 streams, or disturb
  the TCPStore during a restart. It cannot read data or escalate. The real
  fixes are still open: serve with native TensorFold (D2) and its
  `--api-key-file`, so loopback reach alone grants nothing on `/v1`, and move
  SearXNG to a bridge network that reaches only what it needs.
- **spark-guard rules are stale on both Sparks**: installed 2026-11-15, they
  guard `29501` but not `29502` or `29620-29621` (the DeepSeek recipe's
  MASTER_PORT) (B3). `--status` now says `STALE`. Fix: one
  `sudo scripts/backends/spark-guard.sh` on each Spark.
- **beast-gate admitted 2 generations per device** while the pair serves 4.
  The gate now defaults to `INFERENCE_SLOTS` (E2). Live on spark-1 since the
  2026-11-16 restart (`/api/slot`: `per_device_inflight: 4`).
- **spark-2's spark.env names a TensorFold key file that is not there**
  (`TENSORFOLD_API_KEY_FILE`; spark-2's `~/.config/openbeast` holds only
  `hf-token`). It matters only for a native TensorFold launch (D2): write the
  0600 key file on spark-2 first.
- ~~**The RoCE fabric runs at `active_mtu 1024`** (netdev MTU 1500).~~
  **Fixed 2026-11-17:** MTU 9000 staged on each Spark, both rebooted, and
  `--verify` passed on both (`active_mtu 4096`, 8972-byte don't-fragment
  pings on both rails). Still open: the performance A/B. Runbook, rollback
  and measurement plan: [B4](#b4-fabric-mtu-9000-spark-fabric-mtu).

### Not done yet

- zig for `language_reference` on a Spark (H7), and a full beast-docs mirror on spark-1 (F5; only `tailscale` is mirrored, 2026-11-19).
- ~~The offline git hub on spark-1~~ Done: `~/git/openbeast.git` exists
  ([SELF_DEVELOPMENT.md §11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable));
  on 2026-11-19 it was at `5c582c18` (#222), so refresh it from `main` before
  relying on it offline.
- ~~`AGENT_PROFILE=deepseek-best` in spark-1's `openbeast.conf` (E2b)~~ Done
  2026-11-19 ~14:50.
- OpenBeast MCP tools and the soul in opencode on the Sparks; GLM through
  that opencode config is untested.
- pytest on spark-1 and ShellCheck on either Spark (none installed, 2026-11-18).
- A cold boot of both Sparks with the WAN disconnected, for the DeepSeek deployment.
- The fabric MTU A/B (B4): MTU 9000 is live since 2026-11-17; its speed
  effect on prefill and decode is unmeasured.
- A `svc:spark-hydra` Tailscale Service name (an admin-console step), and the
  beast rig's hydra node for the pair (`https://spark-1.tail4109f9.ts.net:8443`,
  `engine = "openai"`, a gate device key, `slots = 4`, upstream the served id,
  `DeepSeek-V4.1-Flash-1017` by default), once the rig is back on. `hydra.toml.example`'s
  `[nodes.sparks]` is already that shape.
- Switching the primary between `beast` and `spark-hydra` on the hydra
  side. Clients switch since #204 (`openbeast-client use <rig>`, F2).
- A `PARALLEL=8` measurement; native TensorFold 1.2.7 (D2) vs the recipe.
- A fully offline rebuild of the pair, end to end (H9).
- `tests/test_scripts.sh` showed 7 failures when run from a non-git copy on
  spark-2 (`test_uninstall.sh` among them). Rerun from a git checkout before
  merging.
