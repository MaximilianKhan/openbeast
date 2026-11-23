# GLM serving window, 2026-11-23: the 1104 A/B, the DFlash2 re-pin, the first native D2 serve

On 2026-11-23 the pair was taken off DeepSeek for a GLM serving window
(08:25-10:20 PST, Max's approval). Every configuration below served on the
same pair on the same day, and was measured with the same probe through
beast-gate. The window ended with the pair serving **the board's GLM
configuration** (`beast-spark glm`, Mia's recipe v1.8 at `33b50fd`,
`GLM-5.3-Flash-EXL3`), ready for the GLM UP control arm. No eval arm ran.

**Verdicts.**

- **1104 does not beat the current recipe on this pair.** Served as Mia
  ships it (`DENSE=q8`), it is about 15% slower at one stream and four, and
  about 30% slower in prefill. At v1.8's numerics (`DENSE=q4`) it ties at one
  stream and stays 4-12% behind at four streams and about 28% behind in
  prefill. No functional regression. GLM keeps v1.8 (WEBSITE_UPDATE Q-J: no
  upgraded line).
- **DFlash2 is re-pinned to the Apache-2.0 revision `6cc6c481`** in our docs,
  data and the live `local.sh`. No weight download was needed: the weights
  are the same bytes. The board's GLM configuration now serves the
  Apache-2.0 revision, and its speed is unchanged.
- **D2 served on the pair for the first time** (`beast-recipe adopt` →
  `beast-spark NAME` → `check --run --bench`), with conformance PASS and
  9/9 functional checks. It is slower than v1.8: 30.7 / 57.7 tok/s as
  rendered (bf16 dense linears), and 42-45 / 66-70 with 4-bit dense. Its
  window is 245,760 by default and 524,288 after tuning. Prefill is about
  1.4K tok/s. It stays the challenger.
- Three operational bugs surfaced. Two are fixed and merged; one is open
  (see [Bugs found](#bugs-found)).

## The probe

The probe runs from the Mac through beast-gate
(`https://spark-1.tail4109f9.ts.net:8443`, with the device key and the
`X-OpenBeast-Gate-Defaults: off` header):

- **Decode.** It runs `beast-recipe`'s own `bench()` (`scripts/backends/pylib/recipe.py`,
  which is `tokps.one_run`): temperature 0, seed 42, `max_tokens` 512, one
  warm-up, then the median of three single-stream runs. Four streams at
  once give the aggregate: completion tokens over the wall time. Thinking
  is set explicitly through `chat_template_kwargs.enable_thinking`, because
  the engines' defaults differ (v1.8 thinks by default; 1104 does not, since
  its `THINKING=0`).
- **Prefill.** A cold prompt with a fresh header, of about 12.4K and 49.6K
  tokens, with `max_tokens` 8. The rate is `prompt_tokens / TTFT`, and the
  TTFT includes the gate's round trip of about 0.1 s.
- **Functional.** Nine checks: plain chat; thinking in `reasoning_content`,
  both non-stream and streamed; OpenAI tool calls, non-stream and streamed,
  each with thinking off and on; a tool-result round trip; and a needle at
  37% depth of a ~105K-token prompt (214K on the tuned D2).
- **Conformance.** `scripts/backends/conformance.sh --concurrency 4` was run
  on spark-1 against `127.0.0.1:8000` (for D2, through `beast-recipe check`).

The scripts and the raw records are on spark-1 in
`~/openbeast-runs/glm-window-1123/`: `gbench.py`, `gfunc.py`,
`bench.jsonl`, `func.jsonl`, and the start/stop/build logs. A single-stream
run varies by about ±5 tok/s between prompts of the rotation (for v1.8:
43.2-52.8), so treat 1-stream differences under ~3 tok/s as noise.

## Benches

Decode is in tok/s. "1" is the median of three single-stream runs. "4" is
the aggregate of four streams at once. Prefill is in tok/s at ~12.4K / ~49.6K
prompt tokens, with thinking off.

| Configuration | Thinking | 1 stream | 4 streams | Prefill 12K / 50K |
|---|---|---:|---:|---:|
| **v1.8 current** (`33b50fd`, TF 0.6.0, `DENSE=q4`, DFlash2 `bf582e4e`): the baseline | off | **48.4** | **78.4** | **1,969 / 2,047** |
| v1.8 current | on | 48.9 | 85.9 | |
| 1104 (`82c442f`, TF 1.3.0-era `81de26f6` + 15 patches, Mia's `DENSE=q8 HEAD=q8`) | off | 41.3 | 66.8 | 1,368 / 1,407 |
| 1104 | on | 40.6 | 69.2 | |
| 1104 at `DENSE=q4` (v1.8's numerics; not Mia's choice) | off | 45.4 | 75.5 | 1,404 / 1,484 |
| 1104 at `DENSE=q4` | on | 49.3 | 75.4 | |
| v1.8 after the DFlash2 re-pin (`6cc6c481`) | off | 48.2 | 79.8 | 1,980 / 2,003 |
| v1.8 after the re-pin | on | 48.7 | 86.4 | |
| **D2 native** (TF 1.2.10, `recipe-glm53-flash-exl3-native-tp2` as rendered: bf16 dense, 245,760 window, 17.5 GiB reserve) | off | 30.7 | 57.7 | 1,372 / 1,448 |
| D2 native, as rendered | on | 29.5 | 57.9 | |
| D2 native, `beast-recipe check --run --bench` (server default: thinking on) | default | 29.6 | 58.1 | |
| D2 + `TF_GLM_DENSE_BITS=4` (experiment) | off | 42.2 | 65.8 | 1,343 / 1,438 |
| D2 + `TF_GLM_DENSE_BITS=4` | on | 44.6 | 69.8 | |
| D2 + 4-bit dense, tuned (524,288 window, 11.5 GiB reserve) | off | 41.8 | 64.7 | 1,376 / 1,451 |

Mia's 1104 README publishes 47.8 / 87.3 tok/s (1 / 4 streams, thinking on,
T=0.7, sparkDash) and 38.4 / 79-81 (greedy, thinking off, her harness). Our
greedy probe on our pair gives 40.6-41.3 / 66.8-69.2 at her numerics. Three
things differ from her setup: our 17.5 GiB reserve (hers is 6.5), `PARALLEL=4`
(hers is 8), and the Ablit checkpoint. None of them should cost decode
speed, but we did not chase the gap. On the same day, the same pair and
the same probe, the v1.8 recipe is faster, which is the comparison that
decides.

## Functional checks

All are 9/9 unless noted. The needle was found in every configuration.

| Configuration | Plain chat | Thinking → `reasoning_content` (+ streamed) | Tool calls: non-stream / stream, thinking off / on | Tool-result round trip | Needle | Conformance |
|---|---|---|---|---|---|---|
| v1.8 current | ✔ | ✔ ✔ | ✔ ✔ ✔ ✔ | ✔ | ✔ 105,591 tokens, 56 s | PASS (reasoning UNKNOWN: thinking is off on its default prompt) |
| 1104 | ✔ | ✔ ✔ | ✔ ✔ ✔ ✔ | ✔ | ✔ 105,592 tokens, 77 s | PASS (same) |
| D2 native | ✔ | ✔ ✔ | ✔ ✔ ✔ ✔ | ✔ | ✔ 105,598 tokens, 77 s; tuned: 213,797 tokens, 162 s | PASS (reasoning PASS: thinking is on by default) |
| End state (v1.8, re-pinned) | ✔ | ✔ ✔ | ✔ ✔ ✔ ✔ | ✔ | ✔ 31,037 tokens | (step 1's PASS stands) |

No functional regression in 1104 or D2: the tool calls carry the same
arguments (`{"city":"Lisbon","unit":"c"}`), and with thinking on the
tool-call replies have empty content. 1104 and D2 list
`max_model_len` and capabilities on `/v1/models`, which v1.8 does not.

## Step 1: the baseline (`beast-spark glm`)

`beast-spark glm` from DeepSeek ran `restore-glm.sh`. GLM was serving within
2.5 minutes. The command-center restart inside `restore-glm.sh` failed
(see [bug 1](#bugs-found)), so `beast-spark` exited 1 although GLM served.
`systemctl --user start openbeast` once GLM listened fixed it. Memory at the
start: "CUDA rank 0 startup estimate 88.09 GiB within 96.25 GiB; native
1048576, allocated prompt/reply window 1048576"; the pool holds 1,978,368
tokens for 4 streams.

## Step 2: the 1104 A/B

- **Image.** `scripts/prepare.sh --image` built `tensorfold-glm53-spark:b9f8eb919ae0`
  (ID `sha256:e4fdc510…`, 3.85 GB) on spark-1 and sent it to spark-2 by
  content. **Downloads:** the `nvidia/cuda:13.3.1-devel` and `-runtime`
  bases, Zig 0.17.0, xgrammar, libjpeg-turbo/libwebp and TensorFold
  `81de26f6`. ziglang.org delivered about 1 KB/s, so the build was re-run with
  `DOCKER_BUILD_FLAGS="--build-arg ZIG_URL=https://zig.linus.dev/zig/zig-aarch64-linux-0.17.0.tar.xz"`.
  The Dockerfile checks the pinned `ZIG_SHA256` (`9e8d1166…`, which matched
  before the build), so a mirror is safe. Build time was about 10 minutes
  with the pair down. **Offline path:** the image is on both Sparks;
  `docker save tensorfold-glm53-spark:b9f8eb919ae0 | zstd -T0 > glm53-1104-b9f8eb919ae0.tar.zst`
  carries it, and `prepare.sh` skips the build when that label exists.
- **Settings** (`~/openbeast-recipes/glm53-1104/scripts/local.sh`, untracked,
  backup `local.sh.bak-202611230840`): our Ablit dir, `WORKER_WEIGHTS=check`,
  `DFLASH2_DIR=~/openbeast-models/glm53-dflash2-6cc6c481` (flat hard links,
  on both Sparks), `DFLASH2_REVISION=6cc6c481…`, `PARALLEL=4`,
  `CONTEXT=auto`, `KV=fp8`, `DENSE=q8`, `HEAD=q8`, `L2PF=1`,
  `MEMORY_RESERVE_GIB=17.5`, `CACHE_FIRST=1 PROMPT_CACHE_GIB=4`, `VISION=0`,
  and `SERVED_NAME=GLM-5.3-Flash-1104`. For the window it ran on `PORT=8000`
  with the GLM unit stopped and the command center up, so beast-gate fronted
  it. Afterwards it was put back to `PORT=8020` and `DENSE=q8`.
- **Start line.** It loaded in 151.5 s. q8 used "model 82.76 GiB; … 2393
  pages of 512 rows (7.94 GiB) … enough for 1 at a 1048576-token window"
  (a 1,225,216-token pool shared by 4 streams). q4 used "model 80.95 GiB;
  2592 pages (8.60 GiB)". MemAvailable was about 17 GiB on each Spark with
  the command center up.
- **Stopping it:** `./stop.sh` (both ranks, about 10 s); `docker ps` was clean
  on both.

## Step 3: the DFlash2 re-pin to `6cc6c481`

The Hub tree API at `bf582e4e` and at `6cc6c481` lists the same blob ids for
`config.json`, `.gitattributes`, `assets/dflash2-figure.png` and
`model.safetensors` (LFS `b038e1d9…`). Only `README.md` differs (the licence
front matter). We fetched that one 2,396-byte README
(`git hash-object` = `512ace8d…`, matching the tree) and staged it on both
Sparks: the blob, a `snapshots/6cc6c481…` tree of links to the existing
blobs, and `refs/main`. The blob loop passed. Both snapshots stay; they
share the blobs. The recipe for doing this offline is in
[SPARK_HYDRA C4](../SPARK_HYDRA.md#c4-the-drafter-dflash2).

- Repo: PR #287 (SPARK_HYDRA, EXL3_PLAYBOOK, `spark-families.json`, the
  licence notes; the beast-recipe data already had `6cc6c481`). The board
  records (`weights.json`, RESULTS) keep `bf582e4e`, because that is what
  those rows ran.
- Live: `~/openbeast-recipes/glm53/scripts/local.sh` gained
  `DFLASH2_REVISION=6cc6c481…` (backup `local.sh.bak-202611230910`). The
  rank-0 command line now reads
  `--drafter …/models--incoai--GLM-5.3-Flash-DFlash2/snapshots/6cc6c481…`.
  It is in the serving path, so v1.8 was benched again: 48.2 / 79.8 (off),
  48.7 / 86.4 (on), the same as before.
- The D2 recipe's drafter (`~/openbeast-models/recipe-glm53-flash-exl3-native-tp2.drafter`)
  was fetched by `model-fetch.sh` from a stage seeded with the same blobs,
  so nothing was downloaded. Its lock is in PR #289.

## Step 4: the first native D2 serve

The commands were `beast-recipe adopt glm53-flash-exl3-native-tp2 --run`
(it installs the unit and writes `recipes.d/…env`; nothing is enabled),
`beast-spark glm53-flash-exl3-native-tp2`, then
`beast-recipe check glm53-flash-exl3-native-tp2 --run --bench --key-file ~/.config/openbeast/tensorfold-api-key`.
Before that, `spark-cluster.sh check` passed: same commit, TF 1.2.10 on both
Sparks, both rails, and the head reachable.

1. **First try.** The model served after 130 s. Then the command-center
   restart failed ([bug 1](#bugs-found)), and beast-spark rolled back to
   GLM as designed. GLM served again within 2 minutes, and the pair was
   never left down.
2. **Second try**, with the command center stopped first: the model served
   after 81 s and the switch completed. The gate then answered **401** to
   every request ([bug 2](#bugs-found)). It was fixed in #290/#291 and
   applied by re-running `beast-spark glm53-flash-exl3-native-tp2`.
3. **The start line, as rendered:** "model 87.69 GiB; 4 streams at once,
   1.86 GiB each at a 245760-token window (fp8 caches), of 6.9 GiB left after
   a 17.5 GiB reserve; … a 1.4 GiB prompt cache". `--kv-cache fp8` works
   on 1.2.10 (it was VERIFY). It reached about 1,372-1,448 tok/s prefill;
   decode is in the table.
4. **Why it is slow, and the experiment.** 87.69 GiB means the dense
   linears are unquantized. Native 1.2.10 reads `TF_GLM_DENSE_BITS`
   (4, 6, 8 or 16) and `TF_GLM_HEAD_BITS`, but **the profile grammar cannot
   pass them** (no env pass-through; obprofile knows only the
   `TENSORFOLD_*` keys). For one run, `systemctl --user set-environment
   TF_GLM_DENSE_BITS=4` on both Sparks, which the rank units inherit, gave
   "model 82.36 GiB", 12.8 GiB left, and 42.2 / 65.8 tok/s. The variable
   was unset afterwards (`show-environment` is clean on both).
5. **Tuning from the start line.** Under the 4-stream bench with the
   command center up, MemAvailable bottomed at 16.3 GiB (spark-1) and
   17.4 GiB (spark-2) at the 17.5 GiB reserve. So we tried
   `MEMORY_RESERVE_GIB=11.5` and `CONTEXT=524288` (4-bit dense): "model
   84.33 GiB; 4 streams at once, 3.66 GiB each at a 524288-token window, of
   16.1 GiB left after a 11.5 GiB reserve; a 5.2 GiB prompt cache". Under
   the bench plus a concurrent 214K-token needle, MemAvailable bottomed at
   18.3 / 19.4 GiB. Results: 9/9, 41.8 / 64.7 tok/s. **`KV_POOL_GIB` does not
   apply here:** native TensorFold has no pool flag; it sizes the pages
   from the reserve. The profile edits were reverted (`git checkout` on
   both checkouts). The committed recipe still renders bf16 dense, 245,760
   and 17.5; see [What remains](#what-remains).
6. **Rollback:** `beast-spark glm` stopped the recipe unit (both rank units
   gone, no stray containers) and brought v1.8 back.

## Bugs found

1. **The command-center restart fails after a model switch (fixed
   2026-11-23; cause and fix in [TODO](../TODO.md)).** The `python3` was the
   dashboard extension, whose SIGTERM handler deadlocked when a second
   SIGTERM re-entered it. What was observed:
   `systemctl --user restart openbeast` timed out stopping, 90 s by
   default. The supervisor exits at once, but one `python3` process in
   the `openbeast-stack` unit ignores SIGTERM and is SIGKILLed at 90 s.
   `start.sh` then runs while that transient unit still exists, and exits
   1 within a second. It happened twice, both times when the inference
   server had been stopped or replaced under a running stack
   (`restore-glm.sh` from DeepSeek; the recipe switch from GLM). A plain stop
   at a quiet moment takes 7-15 s. It breaks `beast-spark glm` (exit 1,
   GLM serves without a command center) and makes `beast-spark <recipe>`
   roll back. **Workaround:** `systemctl --user stop openbeast` before a
   switch. Then the switch "restarts" a stopped unit, which is a plain
   start.
2. **A keyed recipe after GLM got no key (fixed, #290).** `restore-glm.sh`
   clears `LLAMA_API_KEY`, because v0.6.0 has no key. The recipe and Hy4
   switches set only `INFERENCE_BACKEND`/`INFERENCE_MODEL`, so beast-gate
   reached the keyed native engine without a key, and every request got
   401. `ob_conf_key_sync` now sets it from the engine key file; the key is
   read in python and is never on argv or in the output.
3. **Re-running `beast-spark <recipe>` while it served did nothing (fixed,
   #291).** It now syncs the key and starts the command center (a restart
   when the conf changed), as `glm`/`ds` do.

## End state

- `beast-spark status`: **selected glm**. `spark-hydra-inference` is active
  and enabled; `deepseek-v41-inference` is disabled; Hy4 is static; the
  D2 recipe unit is inactive (adopted, never enabled); `openbeast` is active.
  **A reboot comes back to GLM.** `beastds` returns to DeepSeek.
- Served: `GLM-5.3-Flash-EXL3`, Mia v1.8 `33b50fd`, image
  `tensorfold-glm53:v0.6.0`, Ablit `57edefd2`, DFlash2 `6cc6c481` (the same
  bytes as the board's `bf582e4e`), and `local.sh` as in
  [D1](../SPARK_HYDRA.md#d1-the-glm-recipe-the-alternative-deployment).
- `openbeast.conf` (backup `openbeast.conf.bak-20261123-glmwindow`): GLM's
  board lines per [E2a](../SPARK_HYDRA.md#e2a-recommended-serving-configuration-from-v5):
  `INFERENCE_THINKING=on`, `BEAST_ASSIST=0`, `AGENT_MAX_TOKENS=16384`,
  `INFERENCE_MODEL=GLM-5.3-Flash-EXL3`, `LLAMA_API_KEY` empty, and
  `AGENT_PROFILE=deepseek-best` removed (put it back after `beastds`, as
  E2b says).
- Through the gate from the Mac: `/v1/models` lists `GLM-5.3-Flash-EXL3`,
  and a tool call returns `finish_reason: tool_calls`
  (`get_weather {"city":"Oslo"}`). The 9 functional checks pass.
- Both checkouts are at `main` after #291.
- On disk, new: the 1104 image (3.85 GB, both Sparks), the DFlash2
  `6cc6c481` snapshot and the flat copies `glm53-dflash2-6cc6c481` and
  `recipe-glm53-flash-exl3-native-tp2.drafter` (hard links: no extra
  space), and the D2 unit plus `recipes.d` entry. Nothing was deleted.

## What remains

- **D2 profile key for the dense bits.** Add a `TENSORFOLD_GLM_DENSE_BITS`
  (and `_HEAD_BITS`) profile key, or a guarded env pass-through, through
  obprofile, `spark-node.sh` and the recipe render. Then set the recipe to
  4-bit dense, `CONTEXT=524288` and `MEMORY_RESERVE_GIB=11.5` (measured
  above) and re-run `check --bench`. Even so, D2 is about 10-15% behind
  v1.8 on decode and about 30% behind on prefill.
- **Prefill on the native engine.** 1104 and native 1.2.10 both reach
  about 1.4K tok/s here, against v1.8's 2.0K. Is it `ABSORB_MMA`, the prompt
  chunk (1104 sets it), or a 1.3.x engine change? Look at 1.3.4 when it is
  staged (`beast-recipe engine … --stage`).
- **Bug 1** (the command-center stop hang): find the `python3` that ignores
  SIGTERM, and make `start.sh` wait out or reset a lingering
  `openbeast-stack` unit.
- **1104 at Mia's own reserve and `PARALLEL=8`** (6.5 GiB, which our
  command center beside rank 0 does not allow), only if a GLM speed claim
  matters again.
- **The GLM UP control arm** runs on this serving (Max launches it).
