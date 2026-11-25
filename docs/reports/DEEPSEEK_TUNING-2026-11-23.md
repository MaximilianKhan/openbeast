# DeepSeek tuning session, 2026-11-23: PARALLEL, the 1M window, KV sharding, the WebUI

On 2026-11-23, 19:04-20:05 PST, Max approved a tuning session on the pair.
Nothing else used the Sparks. DeepSeek-V4.1-Flash (Mia EXL3, the "-v2" pack
`04c13f12`) served on TensorFold TP2 (Mia's recipe `c7f2b85`, unit
`deepseek-v41-inference`). Both checkouts were at `ea45b9fb`, with
`AGENT_PROFILE=deepseek-best` live. Each configuration below served on the
same pair, on the same day, and was measured with the same probe through
beast-gate. The session ended with the pair serving **`PARALLEL=8` at the
524,288-token window**, verified.

**Verdicts.**

- **`PARALLEL=8` is the new production setting.** It aggregates 147.4 tok/s
  at 8 streams against 116.5 at 4 (+27%), and 133.8 at 6 (+15%). At 1 to 4
  streams it matches `PARALLEL=4` within noise in decode, TTFT and prefill.
  Memory is unchanged: the engine sizes no per-stream memory (`8 streams at
  once, 0.00 GiB each`), and 8 cold prompts at once run one prefill at a
  time. `INFERENCE_SLOTS` goes from 4 to 8, so one device can use all 8
  streams through the gate.
- **The model serves a 1,048,576-token window**, verified: a needle at
  999,059 prompt tokens was found (509 s cold, about 1,960 tok/s), and a
  cache-hit follow-up turn on the same 999K history answered a second
  needle in 3.1 s (997,376 tokens cached). Speed is the same as at 524,288.
  It costs **0.88 GiB per rank** of headroom, on spark-1, which already has
  the least headroom. So it **stays out of production** until spark-1 has
  that memory back (see [What would unlock 1M](#what-would-unlock-1m)).
- **`KV_SHARD=1` works on the pair** (pool 2,101,248 positions, each Spark
  holding one whole 1M window). It costs about 2% of decode and another
  0.25 GiB per rank against the 1M window without sharding. It is the right
  way to run 1M once 1M is on, because without it one near-1M request holds
  the whole pool. It does nothing at 524,288.
- **The WebUI stays on spark-1 for now.** Moving it needs Max's decisions and
  code work; the plan is [below](#moving-the-open-webui-off-the-head-node).
  The cheaper win comes first: running spark-1 headless (sudo, Max).

## The probe

The probe is the GLM window's probe ([GLM_SERVING_WINDOW-2026-11-23.md](GLM_SERVING_WINDOW-2026-11-23.md#the-probe)),
extended to 6 and 8 streams. It runs from the Mac through beast-gate
(`https://spark-1.tail4109f9.ts.net:8443`, the device key,
`X-OpenBeast-Gate-Defaults: off`, thinking off via
`chat_template_kwargs.enable_thinking=false`):

- **1 stream:** `beast-recipe`'s `bench()` (`tokps.one_run`: temperature 0,
  seed 42, `max_tokens` 512, one warm-up, then the median of three runs).
- **N streams (4, 6, 8):** N requests started at once. *Aggregate* is
  completion tokens over the wall time; *per stream* is the median of each
  stream's own decode rate; *TTFT* is the median (and the maximum) of the N
  first tokens, all prompts arriving together.
- **Loaded TTFT:** N-1 streams decoding, then one short request; its TTFT.
- **Prefill:** cold prompts of about 12K and 48K tokens, `max_tokens` 8;
  the rate is `prompt_tokens / TTFT`, including the gate's round trip.
- **Functional:** `gfunc.py`'s nine checks (plain chat; thinking in
  `reasoning_content`, non-stream and streamed; tool calls non-stream and
  streamed with thinking off and on; a tool-result round trip; a needle at
  37% of a ~103K prompt). Long needles use the same generator.
- **Conformance:** `scripts/backends/conformance.sh --concurrency N` on
  spark-1 against `127.0.0.1:8000`.
- **Memory:** `MemAvailable` sampled every 0.25 s on both Sparks during
  every test (`memsample.sh`). The long-needle runs also had a client-side
  guard that would abort the request if spark-1 fell under 3.0 GiB, above
  memguard's 2.5 GiB floor. It never fired, and memguard never fired either
  (`memguard.log` has only its start lines).

Scripts and raw records are on spark-1 in `~/openbeast-runs/ds-tuning-1123/`:
`dsbench.py`, `gfunc.py`, `stress.py`, `resume.py`, `bgload.py`, the two
guarded runners, `bench.jsonl`, `func.jsonl`, `mem-*.log` (spark-2's
`mem-*.log` are in the same folder on spark-2), and `conf/*/conformance-*.json`.
A single-stream run varies by about ±5 tok/s across the prompt rotation
(61-71 here), so treat 1-stream differences under ~3 tok/s as noise.

## Benches

Decode is in tok/s, TTFT in seconds, prefill in tok/s at ~12K / ~48K
tokens. "Per" is the median stream's decode rate, with the slowest stream in
brackets.

| Configuration | 1 stream (TTFT) | 4 streams: agg / per / TTFT med-max | 6 streams: agg / per / TTFT | 8 streams: agg / per / TTFT | Loaded TTFT at 4 / 6 / 8 | Prefill 12K / 48K |
|---|---:|---|---|---|---|---:|
| **A: today's, `PARALLEL=4`, 524,288** (baseline) | 67.8 (0.22) | **116.5** / 30.8 (30.1) / 0.51-0.79 | (capped at 4) | (capped at 4) | 0.19 / - / - | 2,288 / 2,377 |
| B: `PARALLEL=8`, 524,288 | 68.9 (0.23) | 116.6 / 30.8 (29.9) / 0.52-0.77 | 134.5 / 24.2 (23.4) / 0.70-1.22 | **147.9** / 20.1 (18.8) / 0.89-1.51 | 0.16 / 0.28 / 0.21 | 2,307 / 2,385 |
| C: `PARALLEL=8`, 1,048,576 | 68.4 (0.23) | 115.3 / 30.5 (29.9) / 0.61-0.88 | 134.0 / 24.3 (23.3) / 0.77-1.21 | 147.3 / 20.1 (19.1) / 0.93-1.55 | 0.26 / 0.27 / 0.29 | 2,061 / 2,376 |
| D: `PARALLEL=8`, 1,048,576, `KV_SHARD=1` (pool 2,101,248) | 67.0 (0.23) | 113.9 / 30.5 (28.9) / 0.60-0.96 | 131.4 / 24.0 (22.5) / 0.70-1.18 | 143.6 / 19.4 (18.7) / 0.92-1.52 | 0.22 / 0.31 / 0.30 | 2,314 / 2,359 |
| **F: end state = B, re-measured** | 67.9 (0.22) | 115.0 / 30.9 (29.4) / 0.50-0.79 | 133.8 / 24.1 (23.0) / 0.70-1.24 | **147.4** / 19.9 (19.1) / 0.92-1.53 | 0.14 / 0.29 / 0.20 | 2,246 / 2,391 |

Against A, B and F gain +15% aggregate at 6 streams and +27% at 8. At 1
to 4 streams they are A within noise: the 4-stream aggregate is 115.0 to
116.6 for all three, the per-stream rate 30.8 to 30.9, and the TTFT the
same. Each stream slows as streams are added (30.8 → 24.1 → 19.9 tok/s at
4 / 6 / 8), which is what an engine at its decode limit does. With
`PARALLEL=4` the fifth to eighth requests would wait instead: the engine
queues them, or the gate's per-device cap of 4 answers 429.

C's 12K prefill (2,061) is a single cold run; the 48K run and D's runs sit
at A's level. The 1M window does not change decode. D's KV sharding costs
1-3% of decode, consistent with the recipe's "1-4%".

## Memory

`MemAvailable`, GiB, spark-1 / spark-2. Memguard kills a rank under 2.5 GiB
held for 4 s, or under 1 GiB at once.

| Configuration | Engine boot line (model, per rank) | Idle after load | Lowest under the bench | Lowest under the long test |
|---|---|---|---|---|
| A: P4, 524K | 104.08 GiB | 5.41 / 8.10 | 5.25 / 7.87 | 5.15 / 7.77 (needle, 499,060 tokens, cold) |
| B: P8, 524K | 104.08 GiB | 5.69 / 8.36 | 5.23 / 7.86 | 5.14 / 7.78 (8 cold 48K prompts at once, 384,306 tokens) |
| C: P8, 1M | 104.96 GiB | 4.88 / 7.45 | 4.45 / 6.96 | 4.26 / 6.91 (needle, 999,059, cold); 4.32 / 6.87 (999K cold + cache-hit turn) |
| D: P8, 1M, KV shard | 105.21 GiB ("compressed planes 1062 MiB a rank (1783 MiB replicated)") | 4.59 / 6.63 | 4.18 / 6.61 | (not run) |
| F: end state (B) | 104.08 GiB | 5.80 / 8.35 | | 5.16 / 7.72 (needle, 499,061 tokens, while 7 streams decoded) |

- **Streams cost no memory.** B and A are the same within 0.1 GiB in every
  reading. Eight cold 48K prompts arriving at once were prefilled one after
  another (TTFTs 20, 40, ... 160 s), so the prefill workspace of only one
  prompt is live at a time.
- **The 1M window costs 0.88 GiB per rank** (boot line 104.08 → 104.96), and
  `KV_SHARD=1` another 0.25 (105.21). On spark-1 the lowest reading went
  from 5.15 to 4.26 GiB: from 2.65 GiB above memguard's floor to 1.76 GiB.
- **A long prompt barely moved memory here.** Cold 499K and 999K prefills,
  and a cache-hit turn on a 999K history, dipped 0.1-0.2 GiB under idle. The
  recipe's issue #4 saw a ~3 GiB dip on a cache-hit turn, with a desktop,
  sparkDash and hermes on its head. We did not see one, but our margin at
  1M is only 1.76 GiB, which is why 1M waits.
- **spark-1 is the constraint.** It holds rank 0 plus the command center
  (Open WebUI ~0.6 GiB, SearXNG, beast-gate, the dashboard, the tool server)
  and a GNOME desktop session (`graphical.target`, ~0.45 GiB of RSS plus
  what the compositor holds in GPU memory). spark-2 runs rank 1 and a
  desktop, and sits ~2.6 GiB higher.
- `healthcheck.sh` after the session: "Unified memory: 5409 MiB available",
  all 9 services healthy. That is lower than G4's 2026-11-15 reading
  (9.7-9.9 GiB), which was GLM's.

## Functional and conformance

| Configuration | gfunc (9 checks) | Long needle | Conformance |
|---|---|---|---|
| A | 9/9 (needle 103,208 tokens, 45 s) | ✔ 499,060 tokens, 232 s | PASS at concurrency 4 |
| B | | | PASS at concurrency 8 (8/8 in 1.7 s) |
| C | | ✔ **999,059 tokens, 509 s**; resume: ✔ 999,107 tokens, 997,376 cached, 3.1 s | |
| F (end state) | 9/9 (needle 103,210, 45 s) | ✔ 499,061 tokens, 257 s, with 7 streams decoding | PASS at concurrency 4 and at 8 |

Conformance (records in `conf/A`, `conf/B-c8`, `conf/final-c4`,
`conf/final-c8`): chat, stream, reasoning in `reasoning_content`, tools,
tool result, parallel tool calls, `max_tokens`, concurrency all PASS;
overflow was skipped (it needs `--heavy`).

**A finding for later: long prefills block new requests.** While the
499K needle prefilled in F, the 7 background streams finished only 14
requests in 257 s, against about 70 when nothing prefills. A stream that
is already decoding gets rounds between the prompt's chunks
(`PREFILL_INTERLEAVE_MS=250`), but a new request's short prompt waits for
the long one to finish. This is the same at `PARALLEL=4`, so B does not
cause it. The recipe's `TF_FILL_CHUNKS` / `TF_FILL_FIRST` / `TF_FILL_CUT_IN`
(`docs/configuration.md` in the runtime) let a short prompt cut in. They
are untested here, and a TODO item now.

## What changed in production

Two lines, both on spark-1. Each has a dated backup.

| File | Before | After | Backup |
|---|---|---|---|
| `~/openbeast-recipes/deepseek-prep/runtime/scripts/local.sh` | (no `PARALLEL`: the recipe's 4) | `PARALLEL=8` after `CONTEXT=524288`, with a two-line comment naming this report and the rollback | `local.sh.bak-20261123-tuning` (sha256 `b1742985d1599b97…`, identical to the pre-session file). The tested 1M + KV-shard file is kept as `local.sh.tested-D-20261123` |
| `~/openbeast/openbeast.conf` | `INFERENCE_SLOTS=4` | `INFERENCE_SLOTS=8`, with a comment line above it | `openbeast.conf.bak-20261123-tuning` |

`openbeast.conf` differs from its backup only in those two lines.
`AGENT_PROFILE=deepseek-best` is unchanged. The command center was
restarted once (19:15) for the new slot count, and
`deepseek-v41-inference` was restarted for each configuration (A → B → C →
D → end state); each restart took about 37 s to `/v1/models`. The
`spark-deepseek-pack.sh` managed block and the context are as before.

**Effects to know.**

- beast-gate admits 8 generations per device key (`/api/slot`:
  `slots.total 8`, `admission.per_device_inflight 8`). An agent swarm on the
  Mac can hold 8 streams; each then runs at ~20 tok/s instead of ~31.
- **Evals:** `run_eval` reads the gate's cap and clamps `--jobs` to it. A
  profile at `--jobs 4` still runs 4 workers at the same ~31 tok/s per
  stream as the board's arms, so the board stays comparable, **provided no
  other client decodes at the same time**: at `PARALLEL=4` the arm held
  every slot and others queued; at 8 the gate admits another device (a
  WebUI chat, opencode, a Mac swarm) beside it, and each extra stream cuts
  the arm's per-stream rate (6 streams ~24, 8 ~20 tok/s). Keep the pair
  quiet during an arm. Since review 1124 `run_eval` samples beast-slot's
  `slots.busy` every 60 s and records it in the results file
  (`harness.engine_load`: samples, max busy, samples with busy above
  `--jobs`), warning at the start and the end when another client was
  seen; `OPENBEAST_EVAL_SLOT_URL` names the slot URL when the base URL is
  not a gate on `:8443`. Do not raise
  an arm's `--jobs` above 4 without noting it: per-stream decode, and with
  it the wall-scaled turn budget, changes.
- **`beastglm`:** GLM's recipe serves 4 streams, and no switch script
  rewrites `INFERENCE_SLOTS`. After `beastglm`, set `INFERENCE_SLOTS=4`
  (with the E2a lines); after `beastds`, set it back to 8.
  `spark-deepseek-pack.sh use` keeps the line, because it only rewrites its
  managed block.

**Rollback** (spark-1; about 1 minute of DeepSeek downtime, plus the
command-center restart):

```bash
cd ~/openbeast-recipes/deepseek-prep/runtime/scripts
cp -p local.sh.bak-20261123-tuning local.sh         # PARALLEL back to the recipe's 4
systemctl --user restart deepseek-v41-inference      # (reset-failed first if three starts ran in 30 min)
cd ~/openbeast
sed -i -e '/^# 2026-11-23 DeepSeek tuning: 8 = /d' -e 's/^INFERENCE_SLOTS=8$/INFERENCE_SLOTS=4/' openbeast.conf
systemctl --user restart openbeast
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot   # slots.total 4, per_device_inflight 4
```

## Final verification (20:00-20:05 PST)

- `deepseek-v41-inference` active and enabled; `spark-hydra-inference`
  disabled; `openbeast` active; `healthcheck.sh` "All 9 services healthy".
- Boot line: "model 104.08 GiB; 8 streams at once ... 524288-token window".
- Through the gate from the Mac: `/v1/models` lists `DeepSeek-V4.1-Flash-1017`;
  a chat with the gate's defaults (thinking on) answers `391`, finish
  `stop`; a tool call returns `get_weather {"city":"Lisbon"}`, finish
  `tool_calls`; the dashboard (`:8447`) answers 200.
- gfunc 9/9 and conformance PASS at 4 and 8 (above); memguard never fired.

## What would unlock 1M

The 1M window works. The only thing against it is 0.88 GiB of headroom on
spark-1 (plus 0.25 GiB with `KV_SHARD=1`, which 1M should use). Ways to get
that memory back, cheapest first:

1. **spark-1 headless** (Max, sudo, a reboot): `sudo systemctl set-default
   multi-user.target`. This frees the GNOME session (~0.45 GiB of RSS, plus
   compositor and GPU memory; the recipe's `docs/memory-and-safety.md`
   counts "a GiB or two"). The browser chat is unaffected: it is served to
   other devices. Its cost is no local monitor login on spark-1. spark-2
   can follow for symmetry.
2. **The WebUI off spark-1** (~0.6 GiB, more after document uploads): the
   plan below.
3. Then re-run C/D with this report's probe. Adopt `CONTEXT=1048576
   KV_SHARD=1` if spark-1's lowest reading under the 999K needle and resume
   stays at or above this session's 524K level (≥ 5.1 GiB). Rollback is
   `local.sh.bak-20261123-tuning` plus `PARALLEL=8`. Also check opencode's
   `limit.context` and the conformance "Recommended settings" line, which
   follow the window.

## Moving the Open WebUI off the head node

**What the docs say.** Nothing yet: no document plans a WebUI move. The
command center is one stack on spark-1 by design (SPARK_HYDRA "What runs
where": spark-2 is "rank 1, nothing else"; E1-E7). The recipe's memory doc
advises moving "sparkDash and similar" to the worker or another machine when
the head is close to the floor. The Mac Mini serves openbeast.ai (TODO,
deployed 2026-11-17) and has no role on the pair.

**How the WebUI is wired today** (read on spark-1): container `open-webui`
(Open WebUI 0.13.3), its data in the docker volume
`openbeast_open-webui-data` (accounts, chats, `webui.db`), two bind-mounted
files from the checkout (`scripts/webui/sitecustomize.py`,
`agents/hostpolicy.py`), the model at `OPENAI_API_BASE_URL` = TensorFold on
loopback `127.0.0.1:8000`, OpenBeast's tool server at loopback `:3001`,
SearXNG at `:8888`, published by `tailscale serve` as
`https://spark-1.tail4109f9.ts.net` (:443). `configure-webui.sh` installs
the Thinking Default and random-seed filters through the admin API at every
start.

**Recommendation: spark-2, not the Mac Mini**, when it moves:

- spark-2 has ~2.6 GiB more headroom than spark-1, measured under every
  configuration above. The WebUI's ~0.6 GiB leaves it at ~7 GiB, far above
  the floor.
- It keeps the browser chat inside the pair, which works offline over the
  fabric (the offline-first rule). The Mac Mini would add a third box, the
  LAN or tailnet, and a Mac docker runtime to the chat's dependencies, and
  it serves the website.
- The pattern already exists: spark-2's opencode reaches the model through
  the `opencode-model-link` ssh tunnel to spark-1's loopback `:8000`. The
  same tunnel (or the fabric) can carry `:8000`, `:3001` and `:8888`.

**Not done today**, because it needs decisions from Max and code work:

1. **Max's decision** to give spark-2 a role besides rank 1 (it is also the
   backup host; SPARK_BACKUP_RESTORE).
2. **The URL.** Either the browser URL changes to
   `https://spark-2.tail4109f9.ts.net` (every user re-bookmarks; WebUI
   sessions are per origin), or spark-1's `tailscale serve :443` proxies to
   spark-2's `:3000` over the fabric (the URL stays, and spark-1 still
   publishes it).
3. **Code:** `start.sh` and compose run the WebUI as part of spark-1's stack.
   A remote-WebUI mode needs: compose on spark-2 with the same pins,
   `MODEL_URL` and the tool-server URL pointing at the tunnel,
   `configure-webui.sh` against a remote admin API, `healthcheck.sh` and the
   watchdog knowing where it runs, beast-island's `OFFLINE_MODE` handling,
   and spark-guard rules for the new ports (sudo). The tool server must not
   be published beyond the fabric or tunnel: it is loopback-only by design,
   and RBAC reads the WebUI's identity headers.
4. **Migration:** a planned outage to copy `openbeast_open-webui-data`
   (WebUI stopped on both ends), then the admin login and filters checked;
   the old volume kept as the rollback.
5. **Backups:** SPARK_BACKUP_RESTORE's capture then takes the volume from
   spark-2.

Until then, the headless step (1 above) is the better first move: it frees
about as much memory with no architecture change.

## What remains

- [ ] spark-1 headless (Max, sudo), then the WebUI decision; then the 1M
  re-test and, if it holds, `CONTEXT=1048576 KV_SHARD=1` in production.
- [ ] Prefill cut-in (`TF_FILL_CHUNKS=1 TF_FILL_FIRST=1 TF_FILL_CUT_IN=8192`,
  the recipe's example): measure loaded TTFT during a long cold prompt, and
  the long prompt's own TTFT, against `PREFILL_INTERLEAVE_MS=250`.
- [ ] Thinking-on numbers at 6 and 8 streams. This session measured thinking
  off, like the pack bench and the GLM window. The drafts may behave
  differently with reasoning text.
