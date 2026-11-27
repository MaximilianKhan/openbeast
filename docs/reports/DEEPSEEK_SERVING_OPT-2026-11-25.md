# DeepSeek serving optimisation, 2026-11-25: the prompt cache's "failed" lookups, spill v2 and prefill cut-in

Max, 2026-11-25: "If you think we have any room to optimize our serving, please do so for the deepseek model."
The session ran 17:45-18:45 PST against DeepSeek-V4.1-Flash-1017 (Mia EXL3, pack `04c13f12`, TensorFold TP2,
recipe `c7f2b85`, image `tensorfold-dsv41:exl3-tp2-8a98ae503d85`) while Max's long-prompt job ran. Max
approved dropping in-flight work, and the engine was restarted twice: 18:25 (same config) and 18:27 (the change).
His client never stopped submitting. The clean synthetic before/after benches therefore did not run, and the
numbers below come from his live job. They are early evidence, not a controlled bench.

**Verdicts.**

1. **The cache's `failed` lookups were not a bug, and not a size problem.** Max's prompts *do* share long
   prefixes, and the cache found them: after the first 51 requests, every lookup matched a kept state. The
   restore then failed because the state had gone **stale**. The kept state's KV rows sit in the shared
   524,288-position pool, and new requests' placements had reclaimed them, oldest first. The 512 MiB budget
   was never the limit (0 refused, 6 evicted). It only holds the small per-state part, about 6.4 MiB per kept
   point. No dates or per-request content are injected at the top: the gate only sets
   `chat_template_kwargs.enable_thinking`, and the chat template has no date.
2. **The pool fills because of Max's job shape.** He sends 47K-117K-token prompts with no `max_tokens`, so
   each request reserves prompt + 32,768 (the server's `--max-tokens`) + 18 positions in the pool. Four of
   them hold 370-600K positions, more than the pool. That is why 4 ran and 3 waited despite `PARALLEL=8`, and
   why every kept prefix was given away before the next request sharing it arrived.
3. **Applied and live: spill v2 (`SPILL_GIB=64`) and prefill cut-in
   (`TF_FILL_CHUNKS=1 TF_FILL_FIRST=1 TF_FILL_CUT_IN=8192`).** Spill writes kept states to each Spark's NVMe.
   When the pool stales a state, the engine restores it from disk instead of prefilling it again. Cut-in lets
   a prompt of at most 8,192 new tokens run between the 2,048-token pieces of a long prompt pass. In the first
   10 minutes on Max's job: **18.2% of prompt tokens came from the cache, against 0.7% before** (it fell to 9.0% by 18 requests, see the update below). That is
   290,816 tokens restored from disk in 5 restores, and 0 failed lookups against 88. Mean TTFT was 93 s
   against ~155 s, and decode while generating 22.8 tok/s per request against ~11. Memguard never fired, and
   spark-1 stayed at 5.5-6.7 GiB available.
4. **Follow-up:** beast-gate's default `max_tokens` (8192) for requests that send none, merged
   2026-11-25 and pending deploy ([SPARK_HYDRA.md E7a](../SPARK_HYDRA.md#e7a-beast-gates-default-max_tokens)).
5. **Deferred (TODO):** the clean before/after bench (the script is ready), `KV_SHARD=1` (it doubles the pool
   to 1,052,672 positions at about the same memory per Spark), and a longer live sample.

## Update at 18:45: the gain did not hold (read this first)

At 18 finished requests (18:45, when Max's client paused and the engine went idle), the cached share had
**fallen to 9.0%**: 96,256 of 1,071,893 prompt tokens, with no hit since the first 9 requests. The other
counters:

- `prompt_cache_failed` stayed at **0**.
- `prompt_cache_restores_begun` was 10 and `prompt_cache_restored_tokens` 565,248, with 0 restores failed.
  But those restored tokens are **not** in `prompt_tokens_cached_total`. The latest cache lines read
  `resumed at 0, kept 0`.
- Mean TTFT was 96.1 s, TPOT 0.039 s (25.7 tok/s), and mean prefill 33.2 s per request.

So spill does stop the `failed` lookups, and it reads states back from disk. Whether those restores shorten
the prompt passes is **not established**. Possible explanations: the restore finishes after the request has
already been admitted from 0, a restored state is staled again before use, or the counter simply does not
count disk resumes. The ~18% figure below is from the first 10 minutes only. The TTFT/TPOT changes are
confounded by how many long prefills overlapped. **The clean bench (c) decides this**: S9's `cached` after the
load, and TTFT with and without spill. Until it runs, treat spill + cut-in as *harmless and live* (no memory
cost seen, memguard quiet, spark-1 5.6-6.7 GiB, 0 errors), not as a proven win. If the bench shows no gain,
roll back with `local.sh.bak-20261125-serving`, or keep only cut-in (`local.sh.cand-fill`). `KV_SHARD=1`
(more pool room, so fewer stale states in the first place) is then the next lever, along with Max's
`max_tokens` and system-message advice below, which needs no server change.

## Root cause in the code (TensorFold `fbbc971` + the recipe's patches)

- `zig/src/core/prompt_cache.zig`. The log line's `failed` counts three things: `resumed(ok=false)` (the
  backend found the kept state unusable), `forget` (a peer could not resume), and `fail` (a keep that threw).
  `misses` counts only the lookups that matched nothing. From 14:24 on (and again after the 17:19 restart,
  `misses` flat at 51, `failed` 0 → 88), lookups matched but restores failed.
- `zig/src/families/dsv41/lanes.zig` `fillOpen`: `usable(sn)` is false when `sn.stale`. This sets
  `reuse_failed`, and the prompt runs from 0. `placeKept` stales the oldest kept state that is not inside a
  live extent whenever a new placement does not fit (`"a placement needed its room (oldest first)"`, logged
  only for states ≥ 65,536 tokens: 22 such lines in the 17:19-18:25 run).
- **What gets kept.** `server/chat.zig` marks the end of the history (`history_len`) and three cuts at the end
  of the **system prefix**: everything before the first user message, system messages and tools included
  (`systemPrefixLen`, ≥ 512 tokens). `prompt_cache.zig` `marks()` also keeps the "stable" point where a prompt
  diverges from the last prompt that resumed the same state. **A shared corpus inside the user message is
  only found through that stable mark (after one match). A shared corpus in the system message is kept on the
  first request.**
- The one hit before the change shows the size of the prefixes: a request at 18:0x resumed **40,960 of
  48,739** tokens (`cached=40960`). The other 87 matches would have saved a similar amount each, but were
  stale.

## The change (live on spark-1 since 18:29:53 PST)

`~/openbeast-recipes/deepseek-prep/runtime/scripts/local.sh`, three lines after `PARALLEL=8` (the
`spark-deepseek-pack` managed block is unchanged):

```bash
# 2026-11-25 serving opt (docs/reports/DEEPSEEK_SERVING_OPT-2026-11-25.md): spill v2 (kept prompt states on NVMe, restored when the pool stales them); prompt passes in pieces, short prompts cut in
SPILL_GIB=64
export TF_FILL_CHUNKS=1 TF_FILL_FIRST=1 TF_FILL_CUT_IN=8192
```

- `TF_FILL_*` must be **exported**: `start.sh` passes `env | grep ^TF_FILL_` to both ranks. With
  `TF_FILL_CHUNKS` set, the `PREFILL_INTERLEAVE_MS=250` interleave is off (the two are alternatives).
- Spill: 64 GiB per Spark under `/home/max/openbeast-models/dsv41-tensorfold-spill` (local NVMe on both,
  2.0-2.1 TB free), 50 GiB floor, 128 MiB pinned staging per rank. The engine log says `dsv41 spill: rank 0's
  store under /spill (64.0 GiB, staging 128 MiB); the ranks agreed`. The first start computed the
  weight-file digests (start.sh: "about a minute a Spark"); the start took 2 min 17 s, and later starts use
  the cached digests.
- **Durable.** `local.sh` is read at every start (boot, `systemctl --user restart`, `beast-spark ds`).
  `spark-deepseek-pack.sh use` rewrites only its managed block, and a new test
  (`tests/test_spark_deepseek_pack.sh`, "PARALLEL, SPILL_GIB and the exported TF_FILL_* lines survive two
  swaps") pins that behaviour. Nothing on the repo side needed redeploying.
- **Rollback** (about 1 min of downtime):
  `cd ~/openbeast-recipes/deepseek-prep/runtime/scripts && cp -p local.sh.bak-20261125-serving local.sh && systemctl --user restart deepseek-v41-inference`.
  The backup has sha256 `3466ea16ac4b9b46…`, identical to the pre-session file. Spilled files can be removed
  afterwards with `rm -rf ~/openbeast-models/dsv41-tensorfold-spill/native-spill-*` (root-owned while a rank
  runs; the recipe hands them back at stop). The other candidates are staged beside it, not applied:
  `local.sh.cand-{fill,shard,both,spillfill,all}`.

## Measurements: Max's live job

The before numbers come from the 17:19-18:25 run (the saved rank-0 log, plus the coordinator's metrics read at
41 requests). The after numbers come from the 18:29:53 run up to 18:39:32 (9 finished requests, 5 in flight).
The job is the same, with mean prompts of 59,336 and 58,870 tokens.

| | Before (17:19-18:25, `PARALLEL=8`, interleave 250 ms) | After (spill + cut-in, first 10 min) |
|---|---:|---:|
| Requests finished | 100 | 9 |
| Prompt tokens from cache | 40,960 of 5,933,611 (**0.7%**), 1 hit | 96,256 of 529,830 (**18.2%**), 2 memory hits + disk restores |
| Lookups that matched but failed | 88 | **0** |
| Disk restores (`prompt_cache_restores_begun` / `restored_tokens`) | (tier off) | 5 / 290,816 tokens, 0 failed, 0 dropped |
| Resumes seen in the log | 40,960 of 48,739 | 47,104 of 51,070; 49,152 of 52,668; "restored 59392 tokens from disk" |
| TTFT mean (`time_to_first_token`) | ~155 s (at 41 requests) | 93.2 s |
| Decode while generating (TPOT) | 0.089 s (~11 tok/s) | 0.044 s (22.8 tok/s) |
| Spec acceptance | 72% | 74% |
| Request latency mean | (not read) | 154.6 s |
| spark-1 / spark-2 MemAvailable | 5.4-6.5 / 7.8 GiB | 5.5-6.7 / 8.0-8.3 GiB; memguard: no dip logged |

**Caveats.** Nine requests is a small sample. TTFT and TPOT depend on how many long prefills overlap, and that
was not controlled. Part of the TPOT gain may be the cut-in's piece scheduling (`TF_FILL_ROUNDS` 4: four
decode rounds between 2,048-token pieces, against 250 ms of rounds per chunk with the interleave), not the
cache. The cached-token gain is the robust signal: the matches that used to fail now resume. A short request's
latency during a long prefill (bench d) was not measured; the recipe's own measurement for cut-in is that a
short prompt runs between pieces instead of waiting for the whole pass.

Raw data on spark-1 in `~/openbeast-runs/ds-serving-1125/`: `rank0-A-1719-1825.log.gz` (the baseline run's
rank-0 log), `metrics-A-1826.txt`, `metrics-S1-*.txt` (`/metrics` snapshots), `memsample.sh`. The summaries
come from `scripts/backends/pylib/tf_logstat.py` (log) and the `/metrics` arithmetic in the bench.

## Prepared, not run: the clean bench

`scripts/backends/deepseek-serving-bench.py LABEL [--only a,b,c,d] [--shared-in system|user]`, stdlib only,
through beast-gate with the device key (`~/.config/openbeast/spark-hydra-gate.key`), thinking off, a per-run
salt:

- (a) 1 stream, short prompt, 512 tokens (median of 3 after a warm-up);
- (b) 4 and 8 short streams at once (aggregate, per-stream median and min, TTFT);
- (c) Max's shape: 6 prompts of about 60K tokens, 1 s apart. S0, S2 and S4 share a 40K corpus (in the
  system message by default), and U1, U3 and U5 are unique. They send no `max_tokens`, so each holds the
  32,768-token reservation. A short probe stream decodes throughout, and its longest gap between chunks is
  the stall a generating stream sees. Then S9 follows with the same corpus: does the kept prefix survive the
  load?
- (d) one stream decoding, a cold 60K prompt, and 3 s later a short request: its TTFT, the long prompt's
  TTFT, and the decoding stream's longest gap.

The prompt generator was calibrated with `/tokenize` at 29.3 tokens per record. Run it with the engine idle
(0 running, 0 waiting) on the original config (`local.sh.bak-20261125-serving`), then on the live one, then
with `KV_SHARD=1` (`local.sh.cand-all`). Keep `KV_SHARD=1` only if (c) and (a)/(b) say so: the recipe
measured 1-4% decode for it.

## Advice for Max's job

1. **Put the shared material first, in the system message**, or at least before the first user message, and
   keep it byte-identical across requests. Then the engine keeps it on the first request. A shared corpus
   inside the user message is found only after one match, through the stable mark.
2. **Send `max_tokens`** close to what the job needs: its replies average 1,100-1,400 tokens, so 4,096 is
   ample with thinking. Without it, every request holds 32,768 extra positions in the pool. At about 60K
   prompts that is a third of each reservation: it caps the job at 4 running, and it stales kept prefixes.
   **Follow-up (2026-11-25, Max approved):** beast-gate now gives any request that names no cap
   `max_tokens: 8192` (`GATE_DEFAULT_MAX_TOKENS`, from `AGENT_PROFILE=deepseek-best`), so a client that
   omits it reserves 8,192 instead of 32,768. A request that sets its own cap is never changed. Merged,
   deployed at Max's next break ([SPARK_HYDRA.md E7a](../SPARK_HYDRA.md#e7a-beast-gates-default-max_tokens);
   steps in [TODO](../TODO.md)).
3. **5-6 in flight is enough.** Prompt passes run one at a time, so more streams only queue. With prefix hits,
   each request needs only its tail prefilled, and that is where the throughput comes from.
4. **Read the cache's effect** in `/metrics`: `prompt_tokens_cached_total / prompt_tokens_total`,
   `prompt_cache_restored_tokens`, and `prompt_cache_failed` (should stay near 0).

## Not changed, and why

- **The prompt-cache budget (`--prompt-cache-gib`, 512 MiB):** not the limit (0 refused). Raising it would
  only take host memory from spark-1.
- **`POOL` replicated (e.g. 1,048,576):** +0.45 GiB per rank on spark-1, which has the least headroom.
  `KV_SHARD=1` gets the same positions at about the current memory, so it is the one to try (deferred).
- **CUDA graphs** (`GRAPHS=1`, `GRAPH_CAP=128`) are already on. **NCCL:** the fabric is 200 Gb/s, MTU 9000,
  0 RoCE errors (the slowdown report), and decode is not network-bound at TP2. Nothing measured to gain.
- **Spec decode draft length:** acceptance was 72-74%, and the pinned engine has no draft-depth switch beyond
  the patches' calibration. Not touched.
- **`PARALLEL` 8 vs 4:** the 2026-11-23 session measured 8 as no worse at 1-4 streams. For this job the pool,
  not the slot count, sets the concurrency. Unchanged.

## Verification after the change (18:29-18:40)

- `deepseek-v41-inference` active; rank 1 up on spark-2; the env of rank 0 has `TF_FILL_CHUNKS=1`,
  `TF_FILL_FIRST=1`, `TF_FILL_CUT_IN=8192` and `TF_DS_SPILL_GIB=64`.
- `healthcheck.sh`: "All 10 services healthy", unified memory 6,236 MiB available.
- The gate over Tailscale, `GET /v1/models`: 200, `DeepSeek-V4.1-Flash-1017`.
- Max's job served throughout, with no errors in the engine log.
- Not run: gfunc and conformance on the new config (they need the idle window; deferred with the bench).
  Spill and cut-in are the recipe's validated settings ("replies byte-identical to the stock engine";
  `SPILL_GIB=64 KV_SHARD=1` is what the pair was validated with).
