# Two DGX Sparks as the inference plane — plan and runbook

**Status (2026-09-29): milestone 1 is built and tested hermetically. Nothing
here has run on a GB10 yet.** Every step marked **VERIFY ON HARDWARE** comes
from vendor docs, recipes or source reading, not from a measurement on our
boxes. The default path — `INFERENCE_BACKEND=llama`, llama-server launched by
`start.sh` on the 5090 — behaves exactly as before.

- [1. What milestone 1 is](#1-what-milestone-1-is)
- [2. Hardware facts](#2-hardware-facts)
- [3. Topology](#3-topology)
- [4. Rig configuration](#4-rig-configuration)
- [5. vLLM recipe](#5-vllm-recipe)
- [6. TensorFold recipe and its limits](#6-tensorfold-recipe-and-its-limits)
- [7. Security](#7-security)
- [8. Coupling inventory — status after this PR](#8-coupling-inventory--status-after-this-pr)
- [9. Runbook: the day the Sparks arrive](#9-runbook-the-day-the-sparks-arrive)
- [10. Eval policy](#10-eval-policy)
- [11. Open decisions for Max](#11-open-decisions-for-max)
- [12. Verify-on-hardware list](#12-verify-on-hardware-list)
- [13. Sources](#13-sources)

## 1. What milestone 1 is

The OpenBeast stack stays on the x86 5090 rig (the command center). The
model runs on two NVIDIA DGX Sparks, tensor-parallel across both, under
**vLLM** or **TensorFold**. The rig points at that server with four conf
keys and stops trying to own it.

| Piece | Where | File |
|---|---|---|
| Backend conf keys | rig | `scripts/lib/conf.sh`, `openbeast.conf.example`, `docs/REFERENCE.md` |
| Per-backend readiness | rig | `scripts/lib/backend.sh` |
| Unmanaged start / supervise / health / stop / doctor | rig | `start.sh`, `scripts/healthcheck.sh`, `stop.sh`, `scripts/doctor.sh` |
| `/api/slot` for vLLM / TensorFold | rig | `extensions/dashboard/dashboard.py`, [`BEAST_SLOT.md`](BEAST_SLOT.md) |
| Context-overflow detection | rig (and clients) | `agents/runner.py` |
| Rank launchers | **Sparks** | `scripts/backends/{vllm,tensorfold}/spark-node.sh`, `scripts/backends/spark.env.example` |
| Tests | CI | `tests/test_backends.sh`, `tests/test_beast_slot.py`, `tests/test_harness_agentics.py` |

## 2. Hardware facts

| Fact | Consequence | Source |
|---|---|---|
| GB10 Grace Blackwell superchip, **aarch64**, one GPU per box, compute capability **sm_121** | Two boxes = TP 2 across the link, one rank per box. TensorFold compiles kernels for the device's own capability on first start (`cuda/build.py:25`) | NVIDIA playbook; TensorFold source |
| **128 GB unified memory** per box, shared by CPU and GPU | `--gpu-memory-utilization` is a fraction of the one pool the OS also lives in. `nvidia-smi --query-gpu=memory.*` reports **[N/A]** | NVIDIA vLLM playbook, "Troubleshooting" |
| **ConnectX-7** QSFP link between the boxes | NCCL over RDMA when the interface and HCA are pinned; TCP fallback otherwise (much slower) | build.nvidia.com/spark/connect-two-sparks |
| CUDA 13.0, Docker + NVIDIA Container Toolkit | Container images are the supported install; do not pip-install vLLM on the host | NVIDIA vLLM playbook, "Prerequisites" |

## 3. Topology

```
            tailnet / LAN                        ConnectX-7 (private link)
┌──────────────────────────────┐          ┌──────────────┐   NCCL    ┌──────────────┐
│  RIG (x86, RTX 5090)          │  HTTP    │  Spark 1     │◄────────►│  Spark 2     │
│  command center               │ ───────► │  rank 0      │  RDMA    │  rank 1      │
│  WebUI :3000  tools :3001     │ /v1/...  │  vLLM HTTP   │          │  headless    │
│  router :8088 gate :8090      │          │  :8000       │          │              │
│  SearXNG :8888  dashboard     │          └──────────────┘          └──────────────┘
│  (no llama-server)            │
└──────────────────────────────┘
```

- **Rig = command center.** Tools execute here (and on clients), as today.
  Only chat/completion calls cross to the Sparks.
- **Sparks = inference only.** Rank 0 serves the OpenAI API on a *specific*
  address (its tailnet or private-LAN IP). Rank 1 has no HTTP.
- **The GPU on the rig is free.** Nothing in M1 uses it; llama-server can
  still be run by hand (`INFERENCE_BACKEND=llama ./scripts/measure-vram.sh …`
  works) but the stack does not start one.
- **T2** (the whole stack on Spark 1, next to vLLM) is *not* M1: it needs an
  aarch64 Python lock, the arm zig tarball and GB10 detection in
  `hardware.sh`. See §8.

## 4. Rig configuration

```bash
# openbeast.conf on the rig
INFERENCE_BACKEND=vllm                    # or tensorfold
INFERENCE_URL=http://100.101.102.103:8000 # rank 0's SPARK_SERVE_HOST:PORT, no /v1
INFERENCE_SLOTS=8                         # = MAX_NUM_SEQS (vLLM) / --parallel (TensorFold)
LLAMA_API_KEY=<the key in the Spark's VLLM_API_KEY_FILE>   # vLLM only
```

What follows from those four lines (all in `scripts/lib/conf.sh`):

- `INFERENCE_MANAGED` defaults to **false** for vLLM / TensorFold (and cannot
  be true). `start.sh` launches nothing: it waits up to
  `OPENBEAST_LLAMA_LOAD_GRACE` seconds (900) for `INFERENCE_URL` to be ready,
  naming the URL if it times out, then brings up tools, WebUI, router, gate
  and extensions. The supervisor only logs readiness transitions.
- Readiness is per backend (`scripts/lib/backend.sh`): llama = 200
  `{"status":"ok"}` (503 `Loading model` is not ready); vLLM = any 200 (its
  body is empty); TensorFold = 200 `{"ok": true}`.
- `OPENBEAST_MODEL_URL` (WebUI), the router's and beast-gate's upstream, and
  spawned agents' `AGENT_INFERENCE_URL` all become `INFERENCE_URL[/v1]`.
- `healthcheck.sh --restart` (the 5-minute watchdog) reports the server and
  **never** restarts or kills it; `stop.sh` leaves it alone.
- Fast boot, model rollback, KV warming, the weight-registry rows,
  `measure-vram.sh` and the MTP profilers print "not applicable".

## 5. vLLM recipe

`scripts/backends/vllm/spark-node.sh --rank 0|1` builds one rank's `docker
run` from `scripts/backends/spark.env` (copy the `.example`). `--print` shows
the exact command and runs nothing. It is vLLM's **native multi-node**
launch (no Ray), from the Qwen3.6-27B DGX Spark recipe:

| Setting | Value | Why / source |
|---|---|---|
| Image | `nvcr.io/nvidia/vllm:26.05-py3` pinned by digest (placeholder until pulled) | Two-Spark playbook pin. Upstream `vllm/vllm-openai:cu130-nightly` also runs (vLLM blog). A real run refuses an unpinned digest |
| Parallelism | `--tensor-parallel-size 2 --nnodes 2 --node-rank R --master-addr <rank0 link IP> --master-port 29501`, `--headless` on rank 1 | recipes.vllm.ai Qwen3.6-27B; docs.vllm.ai parallelism_scaling. One GPU per box, so TP spans the boxes |
| Link pinning | `NCCL_SOCKET_IFNAME` `GLOO_SOCKET_IFNAME` `TP_SOCKET_IFNAME` `UCX_NET_DEVICES` `OMPI_MCA_btl_tcp_if_include` = the CX-7 interface; `NCCL_IB_HCA` = its HCA; `NCCL_IB_DISABLE=0` | Same recipe + playbook |
| Model | `nvidia/Qwen3.8-27B-NVFP4` (default) | GGUF in vLLM is "highly experimental" and out of tree — serve safetensors. See §11 |
| Reasoning | `--reasoning-parser qwen3` | docs.vllm.ai reasoning_outputs. vLLM returns it as **`reasoning`**, not `reasoning_content` |
| Tools | `--enable-auto-tool-choice --tool-call-parser qwen3_xml` | docs.vllm.ai tool_calling for Qwen3.x; the Spark recipes use `qwen3_coder` — **VERIFY ON HARDWARE** which round-trips our tools |
| Model id | `VLLM_SKIP_MODEL_NAME_VALIDATION=1` | vLLM 404s an unknown request `model`; the stack (opencode.json, clients) assumes llama-server's "ignore it". Verified in `vllm/envs.py` and `serve/engine/serving.py` |
| API key | `VLLM_API_KEY` env, value read from a 0600 file, forwarded as `-e VLLM_API_KEY` | vLLM reads the env var natively (`vllm/envs.py`; `middleware/register.py` prefers `--api-key`, which we never pass). The key never reaches any argv |
| Memory | `--gpu-memory-utilization 0.80` | Playbook 0.8, blog 0.85, Qwen3.6-27B TP2 recipe 0.5 — **VERIFY ON HARDWARE** |
| MTP | `SPECULATIVE_CONFIG={"method":"mtp","num_speculative_tokens":3}` (optional) | Qwen3.8 / 3.6 recipes; batches, unlike llama.cpp MTP |
| Extras | `--kv-cache-dtype fp8 --enable-prefix-caching` (`VLLM_EXTRA_ARGS`) | Recipe. `--trust-remote-code` deliberately omitted (it runs repo code) |

Endpoints vLLM gives the rig: `/health` (200, empty; 503 only on
EngineDeadError), `/v1/models` (with `max_model_len`), `/metrics`
(`vllm:num_requests_running`, `vllm:num_requests_waiting`,
`vllm:kv_cache_usage_perc` — read by `/api/slot`). The HTTP port opens only
after the engine is built; the playbook polls `/health` for up to 900 s,
which is what `start.sh` does too.

## 6. TensorFold recipe and its limits

`scripts/backends/tensorfold/spark-node.sh --rank 0|1 --master <rank0 link IP>`
runs NVIDIA's PyTorch container, pip-installs the **pinned tag**
(`TENSORFOLD_VERSION=v0.3.7`) and execs `tensorfold serve <ckpt> --tp 2
--rank R --master IP --master-port 29551`, with `--name --host --port` on
rank 0. **Start rank 1 first**, then rank 0 (RUNBOOK.md, "two ranks").

Limits — each one is a reason TensorFold is the *second* backend, not the
first:

| Limit | Detail |
|---|---|
| **Alpha** | PyPI "Development Status :: 3 - Alpha"; five releases in ~36 h around v0.3.7. The launcher refuses `main`/`HEAD` |
| **No GGUF** | CUDA serves MLX-affine 4-bit safetensors (Vontra/* conversions), NVFP4 and EXL3 — and Qwen3.8-27B on **two** ranks only as MLX-4bit (NVFP4/EXL3 are one-GPU) |
| **No API key, no auth at all** | Must sit behind beast-gate or a firewall (§7) |
| **Unauthenticated rendezvous on 29551** | The TCPStore listener's bind address is not configurable in TensorFold's CLI — firewall it to the peer's link address |
| **No `/metrics`, `/props`, `/slots`** | `/api/slot` reports healthy + model id + `INFERENCE_SLOTS`, capacity null |
| **No thinking budget on CUDA** | `thinking_budget` / `reasoning_effort` are MLX-only; the runner's `max_tokens` is the only bound |
| **Serial by default** | `--parallel auto` = one request at a time on CUDA; set an integer for shared rounds (not with Flash Next at `--tp 2`) |
| **Restart both ranks** after a mid-stream error on two ranks | docs/api.md "Context and errors" |
| No `response_format` | The opt-in router's classifier falls through (it already catches the error) |

TensorFold's own numbers (not ours): one Spark `--parallel 16` 161.7 tok/s
aggregate vs vLLM NVFP4 MTP=3 132.1; single-stream ~50–57 tok/s; historical
two-rank 71–82 tok/s (docs/recipes/qwen3.8-27b.md). **VERIFY ON HARDWARE.**

## 7. Security

- **Bind to a specific address.** Rank 0's HTTP binds `SPARK_SERVE_HOST` —
  the Spark's tailnet or private-LAN IP. Both launchers refuse `0.0.0.0`/`::`
  without `SPARK_ALLOW_WILDCARD_BIND=true`. vLLM's `--api-key` guards only
  `/v1` (and `/v2`, `/inference`, `/cohere`): **`/health` and `/metrics` stay
  open** (`middleware/authenticate.py`); TensorFold has no key at all.
- **Firewall the link.** vLLM's `--master-port` (29501) and TensorFold's 29551
  should accept only the peer's CX-7 address, e.g. on each Spark
  `nft add rule inet filter input ip saddr != <peer-link-ip> tcp dport {29501, 29551} drop`
  (adapt to the Spark's firewall; **VERIFY ON HARDWARE** that the CX-7 link
  is not also routed to the LAN).
- **Key the vLLM API, and present the key from the rig.** `VLLM_API_KEY_FILE`
  (0600, checked by the launcher) on Spark 1; the same value in the rig's
  `LLAMA_API_KEY`, which WebUI, the runner, the router, the gate and the
  dashboard already present. `doctor.sh` warns when a remote vLLM is reached
  keyless and whenever the backend is TensorFold.
- **Remote clients go through beast-gate.** `EDGE_GATE=true`: the gate's
  upstream follows `INFERENCE_URL`, so per-device keys, the path allowlist and
  the inference audit keep working. The *raw* `:8443` publish
  (`setup-tailscale.sh` with the gate off) still points at the rig's own
  `:8080`, where nothing listens in M1 — use the gate.
- **Two identity layers stay separate** (AGENTS.md): the gate identifies
  devices; the vLLM key only proves "is the rig". Never hand the vLLM key to
  a client.

## 8. Coupling inventory — status after this PR

From the 2026-09-29 research pass. **done** = handled here; **n/a msg** =
prints "not applicable" instead of failing; **later** = not needed for M1.

| # | Coupling | Status |
|---|---|---|
| 1 | `ob_llama_ready` demanded `{"status":"ok"}` — vLLM (empty 200) and TensorFold (`{"ok":true}`) never "ready" | **done** — `ob_backend_ready`, llama semantics unchanged |
| 2 | `start.sh` launch / rollback / supervisor own a local child | **done** — `INFERENCE_MANAGED=false` launches, rolls back and kills nothing |
| 3 | `healthcheck.sh --restart` + watchdog would kill/relaunch a local llama-server | **done** — reports only; tested with a recording `pkill` |
| 4 | `MODEL_URL` + ~88 `:8080` literals | **done** for the conf-derived consumers (WebUI, router + gate upstream, health probes, dashboard, spawned agents). **later**: `opencode.json`, raw `:8443` in `setup-tailscale.sh`, `evals/run_eval.py` defaults |
| 5 | Request `model` id assumed ignored | **done** — `VLLM_SKIP_MODEL_NAME_VALIDATION=1` in the launcher; TensorFold ignores it |
| 6 | `/api/slot` read `/props` `/slots` `llamacpp:` metrics | **done** — vLLM/TensorFold branch; llama answer byte-identical |
| 7 | beast-gate allowlist / `id_slot` / upstream | OK — upstream follows `INFERENCE_URL`; `id_slot` "ignored with a warning" by vLLM is **unverified** |
| 8 | Reasoning field: vLLM `reasoning` vs `reasoning_content` | **later** — Open WebUI rendering is a live check (acceptance list) |
| 9 | Runner context-overflow regex was llama-only | **done** — vLLM (both wordings) and TensorFold (CUDA + MLX) |
| 10 | `REASONING_BUDGET` maps to llama's `--reasoning-budget` | **later** — runner already bounds `max_tokens`; vLLM has per-request `thinking_token_budget`; TensorFold CUDA has none |
| 11 | Router `response_format` | OK on vLLM, degrades cleanly on TensorFold |
| 12 | `doctor.sh` VRAM rows / llama probe | **done** — backend probe, key warnings, n/a rows |
| 13 | Weights, serve scripts, `WEIGHT_ENFORCE` (GGUF) | **n/a msg** in doctor. **later**: pin HF `repo@revision` + safetensors index hash |
| 14 | `hardware.sh` GB10 (`memory.total` N/A) | **later** (T2 only) |
| 15 | `gpu-lease.sh` busy check silently fail-open on `[N/A]` | **done** — says "not applicable" |
| 16 | `stop.sh` llama sweep | **done** — leaves an unmanaged server alone |
| 17 | Eval provenance (`capture_server_config`, `/props` jobs clamp) | **later** — see §10 |
| 18 | `measure-vram.sh`, `profile-*-mtp.sh` | **n/a msg**. `evals/profile_mtp.py` **later** |
| 19 | `bootstrap.sh` / `update.sh --llama` | **later** — harmless in T1 (the rig can still build llama.cpp) |
| 20 | aarch64 (pydeps lock, zig tarball, air-gap bundle) | **later** (T2 only) |
| 21 | `MEM_LIMIT_PCT` scope | N/A in T1 (containers on other boxes) |
| 22 | `client.sh` reads `/props` `n_ctx` | **later** — falls back cleanly; could read `max_model_len` |

## 9. Runbook: the day the Sparks arrive

**A. Cable and network** (build.nvidia.com/spark/connect-two-sparks)

1. QSFP cable between the two CX-7 ports. Update both Sparks (DGX OS).
2. On each: `ibdev2netdev` — note the port that is **Up** (e.g.
   `rocep1s0f1 port 1 ==> enp1s0f1np1 (Up)`): HCA left, interface right.
3. Static IPs on that interface (playbook), e.g. `192.168.100.10` (Spark 1)
   and `.11` (Spark 2); `ping` across. Passwordless SSH Spark 1 → Spark 2.
4. Firewall the rendezvous ports to the peer (§7). Put each Spark on the
   tailnet (or the rig's LAN) for the HTTP side.

**B. Prove the link is RDMA, not TCP**

5. `docker pull nvcr.io/nvidia/vllm:26.05-py3` on both; record the digest
   (`docker inspect --format '{{index .RepoDigests 0}}' …`) in `spark.env`.
6. Fill `scripts/backends/spark.env` on both (identical but `SPARK_NODE_IP`):
   interface, HCA, `SPARK_HEAD_IP`, `SPARK_SERVE_HOST` (Spark 1's tailnet IP),
   model, key file (`(umask 077; openssl rand -hex 32 > ~/.config/openbeast/vllm-api-key)`).
7. First bring-up with tracing: `NCCL_DEBUG=TRACE` in the shell (forwarded by
   name). In `docker logs openbeast-vllm-rank0` look for
   **`NET/IB`** (e.g. `[send] via NET/IB/GDRDMA`). **`NET/Socket` means the
   TCP fallback** — fix the interface/HCA before measuring anything
   (docs.vllm.ai parallelism_scaling).

**C. Start the ranks**

8. `--print` first on each node and read the command.
9. vLLM: rank 1 (`spark-node.sh --rank 1`) on Spark 2, then rank 0 on
   Spark 1 (either order works for vLLM's rendezvous; keep one habit).
   TensorFold: **rank 1 first, always.**
10. On Spark 1: `curl -fsS http://<SPARK_SERVE_HOST>:8000/health` → 200
    (vLLM: empty body; TensorFold: `{"ok": true}`), and
    `curl -fsS -H "Authorization: Bearer $(cat <keyfile>)" http://…:8000/v1/models`.

**D. Point the rig at it**

11. On the rig: `./stop.sh`, then set §4's keys in `openbeast.conf`
    (`INFERENCE_BACKEND`, `INFERENCE_URL`, `INFERENCE_SLOTS`, `LLAMA_API_KEY`;
    `EDGE_GATE=true` if clients will use it).
12. `./start.sh -d` — expect "Waiting for vLLM at … (not managed here)", no
    llama-server, then "Stack is up" with the Spark URL as the model server.
13. `./scripts/doctor.sh` — expect the vLLM row ready and serving the model,
    weight rows "not applicable", no keyless warning.
14. `./scripts/healthcheck.sh` — expect `OK vLLM (vllm @ …)`.

**E. Acceptance checklist** (all must pass before calling M1 done)

- [ ] `./start.sh -d` green; `./start.sh --status` shows no llama pid.
- [ ] Open WebUI lists the model; a chat **streams**, and thinking renders
      (vLLM sends `reasoning`, not `reasoning_content` — watch this one).
- [ ] A WebUI tool call through `:3001` round-trips (web_search, then bash).
- [ ] `./agent.sh "…create a file, edit it, run it…"` completes (proves the
      tool-call parser; switch `qwen3_xml` ↔ `qwen3_coder` if not).
- [ ] `opencode` with an arbitrary model id works (model-name validation off).
- [ ] `curl localhost:3002/api/slot` → `healthy: true`, `backend: "vllm"`,
      `model.ctx` = `MAX_MODEL_LEN`, `slots.busy` moves under load.
- [ ] Pull the cable / stop rank 0: `healthcheck.sh --restart` reports DOWN
      and restarts **nothing**; stack.log logs one "NOT ready" line; restoring
      rank 0 logs "ready again".
- [ ] `./stop.sh` on the rig leaves both Spark containers running.
- [ ] With `EDGE_GATE=true`, an enrolled client completes a chat through
      `:8443`.

## 10. Eval policy

- **Any vLLM or TensorFold row is a new eval era**, never paired with a llama
  row — different engine, different weights (NVFP4/MLX-4bit, not our GGUF),
  different sampling and tokenizer paths. Compare within the era only.
- **Provenance gap.** `evals/run_eval.py capture_server_config` inspects a
  *local* llama-server (`pgrep`, `/proc/net/tcp`, `--version`). Against a
  remote backend it records nothing and the cache era falls back with a
  WARNING. Before any leaderboard row: stamp backend, image digest, model
  `repo@revision`, TensorFold tag and the serve flags into the era (M2).
- `--jobs` must be explicit (the `/props` `total_slots` clamp is llama-only).
- `runner.py`'s overflow patterns changed in this PR, which rotates the era
  hash for *every* backend (runner.py is one of the six era files). Llama rows
  from before and after this merge are not paired across it.

## 11. Open decisions for Max

1. **Which model.** vLLM and TensorFold cannot serve the benchmarked
   uncensored GGUF (JonathanColetti Qwen3.8-27B-Uncensored, capability 98.4).
   Options: stock `nvidia/Qwen3.8-27B-NVFP4` (the launcher default — censored,
   unbenchmarked here) or an unvetted uncensored build
   (`orcarouter/Qwen3.8-27B-Uncensored-{NVFP4,FP8,MLX}` — a *different*
   abliteration, needs a full eval pass and a supply-chain pin). TensorFold's
   family check accepting the orcarouter MLX layout is unverified.
2. **Is 2-box TP worth it for a 27B?** A 27B fits one Spark. TP 2 buys
   aggregate throughput and context, and pays a CX-7 hop per token. The pair
   earns its keep on models **larger than one Spark's 128 GB**: Qwen3.8 Flash
   Next (177B MoE — shelved on the 5090 as too slow at 38 tok/s with CPU
   offload) or GLM-5.3-Flash (TensorFold *requires* two ranks for it).
   Recommendation to decide against: bring up the 27B first because it is the
   known quantity for acceptance, then measure Flash Next on two ranks.
3. **Where the command center lives.** T1 (rig stays x86, this PR) vs T2
   (stack on Spark 1: frees the rig, but needs aarch64 work in §8 rows 14/20
   and puts the tool plane on the inference box).

## 12. Verify-on-hardware list

- Interface/HCA names from `ibdev2netdev`; NCCL shows `NET/IB`.
- `--gpu-memory-utilization` that leaves the OS healthy (0.8?).
- `qwen3_xml` vs `qwen3_coder` for our tool round-trip.
- Non-privileged `docker run` (`--device /dev/infiniband --cap-add IPC_LOCK
  --ulimit memlock=-1`) is enough for RDMA; the vLLM recipe uses
  `--privileged`. If NCCL falls back to sockets, try that before anything else.
- `--entrypoint vllm … serve` works on the NGC image (it does on
  `vllm/vllm-openai`, whose entrypoint is `vllm serve`).
- vLLM ignores the stack's extra request fields (`id_slot` from beast-gate).
- Open WebUI renders vLLM's `reasoning` field.
- TensorFold: the rendezvous listener's bind address (`ss -ltnp | grep 29551`)
  and whether the orcarouter MLX checkpoint passes its family check.
- vLLM `/metrics` label set and `kv_cache_usage_perc` scale on this build.

## 13. Sources

- NVIDIA DGX Spark vLLM playbook: https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md
- Connect two Sparks: https://build.nvidia.com/spark/connect-two-sparks
- vLLM on DGX Spark (blog): https://vllm.ai/blog/2026-06-01-vllm-dgx-spark
- vLLM recipe, Qwen3.6-27B on DGX Spark: https://recipes.vllm.ai/Qwen/Qwen3.6-27B
- vLLM recipe, Qwen3.8-27B: https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- vLLM parallelism & multi-node: https://docs.vllm.ai/en/latest/serving/parallelism_scaling.html
- vLLM tool calling: https://docs.vllm.ai/en/latest/features/tool_calling.html
- vLLM reasoning outputs: https://docs.vllm.ai/en/latest/features/reasoning_outputs.html
- vLLM GGUF: https://docs.vllm.ai/en/latest/features/quantization/gguf.html
- vLLM metrics: https://docs.vllm.ai/en/latest/design/metrics.html
- vLLM source read 2026-09-29 (main `df4dbe46`): `vllm/envs.py` (`VLLM_API_KEY`,
  `VLLM_SKIP_MODEL_NAME_VALIDATION`), `vllm/entrypoints/serve/middleware/register.py`
  and `authenticate.py`, `vllm/renderers/params.py` (context-length error),
  `vllm/v1/metrics/loggers.py` (gauge names), `vllm/engine/arg_utils.py`
  (`--nnodes/--node-rank/--master-addr/--master-port`)
- TensorFold: https://github.com/ashhart/TensorFold (v0.3.7 read at HEAD
  6b2e4c4): `RUNBOOK.md`, `docs/recipes/qwen3.8-27b.md`, `docs/api.md`,
  `src/tensorfold/cli.py`, `src/tensorfold/cuda/server.py`, `src/tensorfold/cuda/comm.py`
