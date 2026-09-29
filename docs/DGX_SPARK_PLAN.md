# Two DGX Sparks as the inference plane — plan and runbook

**Status (2026-09-29): milestone 1 is built and tested hermetically. Nothing
here has run on a GB10 yet.** Every step marked **VERIFY ON HARDWARE** comes
from vendor docs, recipes or source reading, not from a measurement on our
boxes. Model onboarding is **model-agnostic**: the launchers know no model;
every per-model fact is read from the checkpoint, declared in a profile, or
measured against the live server (§14). The default path —
`INFERENCE_BACKEND=llama`, llama-server launched by `start.sh` on the
5090 — behaves exactly as before.

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
- [14. Onboarding a model you've never seen](#14-onboarding-a-model-youve-never-seen)

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
| Rank launchers (`--profile`) | **Sparks** | `scripts/backends/{vllm,tensorfold}/spark-node.sh`, `scripts/backends/spark.env.example` (host settings only) |
| Model profiles | Sparks + rig | `scripts/backends/models/<name>.env`, `TEMPLATE.env`, `pylib/obprofile.py` |
| Inspect / fetch / conformance / use-model | anywhere / Sparks / rig / rig | `scripts/backends/{model-inspect,model-fetch,conformance,use-model}.sh`, `pylib/`, vendored engine lists in `data/` |
| Tests | CI | `tests/test_backends.sh`, `tests/test_model_{profiles,inspect,fetch}.py`, `tests/test_conformance.py`, `tests/test_use_model.py`, `tests/test_beast_slot.py`, `tests/test_harness_agentics.py` |

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
  be true), and for a llama `INFERENCE_URL` on another machine (a notice
  says so; an explicit `INFERENCE_MANAGED=true` there is warned about as
  conflicting config). `start.sh` launches nothing: it waits up to
  `OPENBEAST_LLAMA_LOAD_GRACE` seconds (900) for `INFERENCE_URL` to be ready,
  then brings up tools, WebUI, search, router, gate and extensions **either
  way** — a server still down after the grace gets a loud "inference backend
  NOT ready at <INFERENCE_URL>" warning, not a dead stack. `./start.sh -d`
  then prints "Stack is up — but inference is NOT ready" and exits 0; it
  never claims readiness it did not see. The supervisor only logs readiness
  transitions, so stack.log records the moment the Sparks come up.
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

`scripts/backends/vllm/spark-node.sh --profile NAME --rank 0|1` builds one
rank's `docker run` from the host settings in `scripts/backends/spark.env`
(copy the `.example`) and the model settings in the profile
`scripts/backends/models/NAME.env` (§14). `--print` shows the exact command
and runs nothing. It is vLLM's **native multi-node** launch (no Ray), from the
Qwen3.6-27B DGX Spark recipe; a `TENSOR_PARALLEL_SIZE=1` profile runs on one
Spark with no multi-node flags. Rows marked *profile* come from the profile —
the values shown are the shipped example `models/qwen38-27b-nvfp4-vllm.env`:

| Setting | Value | Why / source |
|---|---|---|
| Image | `nvcr.io/nvidia/vllm:26.05-py3` pinned by digest (placeholder until pulled) | Two-Spark playbook pin. Upstream `vllm/vllm-openai:cu130-nightly` also runs (vLLM blog). A real run refuses an unpinned digest |
| Parallelism | `--tensor-parallel-size 2 --nnodes 2 --node-rank R --master-addr <rank0 link IP> --master-port 29501`, `--headless` on rank 1 | recipes.vllm.ai Qwen3.6-27B; docs.vllm.ai parallelism_scaling. One GPU per box, so TP spans the boxes |
| Link pinning | `NCCL_SOCKET_IFNAME` `GLOO_SOCKET_IFNAME` `TP_SOCKET_IFNAME` `UCX_NET_DEVICES` `OMPI_MCA_btl_tcp_if_include` = the CX-7 interface; `NCCL_IB_HCA` = its HCA; `NCCL_IB_DISABLE=0` | Same recipe + playbook |
| Model (*profile*) | `SOURCE` at `REVISION` (a commit SHA): the `model-fetch.sh`-verified copy in `MODELS_DIR`, mounted read-only; else the Hub at `--revision/--tokenizer-revision <sha>` with an "unverified" warning | No default model exists. GGUF in vLLM is "highly experimental" and out of tree — serve safetensors. See §11 |
| Reasoning (*profile*) | `--reasoning-parser` = `REASONING_PARSER` (example: `qwen3`) | docs.vllm.ai reasoning_outputs. vLLM returns it as **`reasoning`**, not `reasoning_content` |
| Tools (*profile*) | `--enable-auto-tool-choice --tool-call-parser` = `TOOL_CALL_PARSER` (example: `qwen3_xml`) | `model-inspect.sh` suggests one from the chat template; `conformance.sh` proves it. At the vendored vLLM commit `a96ee59` `qwen3_xml` and `qwen3_coder` are **one** parser class (`Qwen3EngineToolParser`) — the old "which one" question is moot there, **VERIFY** on the NGC image's vLLM |
| Model id | `VLLM_SKIP_MODEL_NAME_VALIDATION=1` | vLLM 404s an unknown request `model`; the stack (opencode.json, clients) assumes llama-server's "ignore it". Verified in `vllm/envs.py` and `serve/engine/serving.py` |
| API key | `VLLM_API_KEY` env, value read from a 0600 file, forwarded as `-e VLLM_API_KEY` | vLLM reads the env var natively (`vllm/envs.py`; `middleware/register.py` prefers `--api-key`, which we never pass). The key never reaches any argv |
| Memory (*profile*) | `--gpu-memory-utilization` = `GPU_MEMORY_UTILIZATION` (default 0.80) | Playbook 0.8, blog 0.85, Qwen3.6-27B TP2 recipe 0.5 — **VERIFY ON HARDWARE** |
| MTP (*profile*) | `SPECULATIVE_CONFIG` JSON (optional) | Qwen3.8 / 3.6 recipes; batches, unlike llama.cpp MTP |
| Extras (*profile*) | `EXTRA_ARGS` JSON array (example: `--kv-cache-dtype fp8 --enable-prefix-caching`) | Recipe. Flags with their own key, `--api-key` and `--trust-remote-code` are refused there |
| Remote code (*profile*) | `--trust-remote-code` only with `TRUST_REMOTE_CODE=true` **and** `TRUST_REMOTE_CODE_ACK=<REVISION>`; the code is the fetched, locked copy's (or `--code-revision <sha>` from the Hub) | It runs Python from the model repo in the container; binding the acknowledgement to the SHA voids it when the revision moves |

Endpoints vLLM gives the rig: `/health` (200, empty; 503 only on
EngineDeadError), `/v1/models` (with `max_model_len`), `/metrics`
(`vllm:num_requests_running`, `vllm:num_requests_waiting`,
`vllm:kv_cache_usage_perc` — read by `/api/slot`). The HTTP port opens only
after the engine is built; the playbook polls `/health` for up to 900 s,
which is what `start.sh` does too.

## 6. TensorFold recipe and its limits

`scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0|1 --master <rank0 link IP>`
runs NVIDIA's PyTorch container, pip-installs TensorFold **pinned by commit
SHA** (`TENSORFOLD_REF`, a full 40-hex commit — v0.3.7 is
`6b2e4c40064b1e4a05965f61b19ce87b5e0265b3`; tags and branches are refused
because they can move) and execs `tensorfold serve /models/NAME --tp N --rank R
--no-update-check --drafter none|/models/NAME.drafter [--master IP --master-port 29551]`
(no phoning GitHub for releases), with `--name --host --port` on rank 0.
**The checkpoint must be fetched first** (`model-fetch.sh`): TensorFold's own
download (`hub.py pull` → `snapshot_download(repo)`) takes the latest commit
with no revision argument, so the launcher only hands it the verified
directory, read-only, with `HF_HUB_OFFLINE=1`. A drafter is pinned the same
way (`DRAFTER_SOURCE`/`DRAFTER_REVISION`); without one it passes `--drafter
none`, never `auto` (which would pick up any unpinned draft model in the HF
cache). A derived image built once
and pinned by digest would avoid the pip install at every start — later. **Start rank 1 first**, then rank 0 (RUNBOOK.md, "two ranks").

Limits — each one is a reason TensorFold is the *second* backend, not the
first:

| Limit | Detail |
|---|---|
| **Alpha** | PyPI "Development Status :: 3 - Alpha"; five releases in ~36 h around v0.3.7. The launcher accepts only a commit SHA |
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
  keyless and whenever the backend is TensorFold. **With TensorFold leave
  `LLAMA_API_KEY` empty:** it has no auth, so a key sent there buys nothing
  and is one more place to be logged. The dashboard and doctor's probes never
  send it to TensorFold; WebUI and the agent runner would, if it were set.
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
| 4 | `MODEL_URL` + ~88 `:8080` literals | **done** for the conf-derived consumers (WebUI, router + gate upstream, health probes, dashboard, spawned agents). `opencode.json`: `use-model.sh` prints a provider entry for the user-level config (the tracked file is era-hashed and left alone). **later**: raw `:8443` in `setup-tailscale.sh`, `evals/run_eval.py` defaults |
| 5 | Request `model` id assumed ignored | **done** — `VLLM_SKIP_MODEL_NAME_VALIDATION=1` in the launcher; TensorFold ignores it; and `INFERENCE_MODEL` (`use-model.sh`) makes the runner send the served id anyway, `doctor.sh` checks it |
| 6 | `/api/slot` read `/props` `/slots` `llamacpp:` metrics | **done** — vLLM/TensorFold branch; llama answer byte-identical |
| 7 | beast-gate allowlist / `id_slot` / upstream | OK — upstream follows `INFERENCE_URL`; `id_slot` "ignored with a warning" by vLLM is **unverified** |
| 8 | Reasoning field: vLLM `reasoning` vs `reasoning_content` | **measured per model** by `conformance.sh` (which field, or inline `<think>`); Open WebUI rendering stays a live check (acceptance list) |
| 9 | Runner context-overflow regex was llama-only | **done** — vLLM (both wordings) and TensorFold (CUDA + MLX) |
| 10 | `REASONING_BUDGET` maps to llama's `--reasoning-budget` | **later** — runner already bounds `max_tokens`; vLLM has per-request `thinking_token_budget`; TensorFold CUDA has none |
| 11 | Router `response_format` | OK on vLLM, degrades cleanly on TensorFold |
| 12 | `doctor.sh` VRAM rows / llama probe | **done** — backend probe, key warnings, n/a rows |
| 13 | Weights, serve scripts, `WEIGHT_ENFORCE` (GGUF) | **n/a msg** in doctor. **done** for the Sparks: profiles pin `SOURCE@REVISION` (commit SHA only); `model-fetch.sh` verifies every file against the Hub's LFS sha256 / git blob id and writes `models/<name>.lock` |
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
   `MODELS_DIR`, key file (`(umask 077; openssl rand -hex 32 > ~/.config/openbeast/vllm-api-key)`).
   The model is NOT set here: pick or write a profile and fetch it on both
   Sparks (§14 steps 1–3).
7. First bring-up with tracing: `NCCL_DEBUG=TRACE` in the shell (forwarded by
   name). In `docker logs openbeast-vllm-rank0` look for
   **`NET/IB`** (e.g. `[send] via NET/IB/GDRDMA`). **`NET/Socket` means the
   TCP fallback** — fix the interface/HCA before measuring anything
   (docs.vllm.ai parallelism_scaling).

**C. Start the ranks**

8. `--print` first on each node and read the command.
9. vLLM: rank 1 (`spark-node.sh --profile NAME --rank 1`) on Spark 2, then
   rank 0 on Spark 1 (either order works for vLLM's rendezvous; keep one
   habit). TensorFold: **rank 1 first, always.**
10. On Spark 1: `curl -fsS http://<SPARK_SERVE_HOST>:8000/health` → 200
    (vLLM: empty body; TensorFold: `{"ok": true}`), and
    `curl -fsS -H "Authorization: Bearer $(cat <keyfile>)" http://…:8000/v1/models`.

**D. Point the rig at it**

11. On the rig: `./stop.sh`, then set §4's keys in `openbeast.conf`
    (`INFERENCE_BACKEND`, `INFERENCE_URL`, `INFERENCE_SLOTS`, `LLAMA_API_KEY`;
    `EDGE_GATE=true` if clients will use it).
12. `./start.sh -d` — expect "Waiting for vLLM at … (not managed here)", no
    llama-server, then "Stack is up" with the Spark URL as the model server.
    "Stack is up — but inference is NOT ready" means the rig is fine and the
    Sparks are not answering yet: go back to C.
13. `scripts/backends/conformance.sh` then `scripts/backends/use-model.sh
    --profile NAME` (§14 steps 5–6).
    `./scripts/doctor.sh` — expect the vLLM row ready and serving the model,
    `INFERENCE_MODEL` matching it, weight rows "not applicable", no keyless
    warning.
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
   family check accepting the orcarouter MLX layout is unverified. With
   profiles the choice is a data file, not a code change: `model-inspect.sh`
   answers the family/quantization question for any candidate before a
   byte of weights moves.
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
4. **Tool-argument coercion.** A server that does no schema coercion
   (TensorFold's CUDA XML parser, and any vLLM parser that keeps XML
   parameter values as text) delivers `{"timeout": "30"}`. `agents/tools.py`
   does not coerce: `bash(command, timeout="30")` returns *"unsupported
   operand type(s) for +: 'float' and 'str'"* (reproduced 2026-09-29).
   `conformance.sh` flags it. Fix options: coerce by schema in the runner's
   dispatch (era-hashed file) or in `tools.py` (client-facing). Not done here.

## 12. Verify-on-hardware list

- Interface/HCA names from `ibdev2netdev`; NCCL shows `NET/IB`.
- `--gpu-memory-utilization` that leaves the OS healthy (0.8?).
- `qwen3_xml` vs `qwen3_coder` for our tool round-trip (one class at vLLM
  `a96ee59`; the NGC 26.05 image predates or postdates that — `conformance.sh`
  on the real server decides).
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
- Model onboarding, none of it run against a real Spark or a real engine yet:
  vLLM serving a read-only `/models/<name>` directory with `--served-model-name`;
  the `--revision/--tokenizer-revision` path for an unfetched profile;
  TensorFold serving a read-only mounted directory with `HF_HUB_OFFLINE=1`
  (does it write anything next to the checkpoint?) and `--drafter none`;
  `model-fetch.sh` throughput and resume against the real Hub/CDN (Xet-backed
  repos included — tested only against a stub that serves plain bytes);
  `model-inspect.sh`'s memory fit (5 GB/rank runtime overhead and 3 % TP slack
  are assumptions) against measured usage; `conformance.sh` against a real
  vLLM and TensorFold (the stubs emulate their documented shapes).

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
- Vendored engine lists (`scripts/backends/data/`, regenerated by
  `pylib/vendor_lists.py`, which parses source with `ast` and imports nothing):
  vLLM `a96ee5935d769ab72ca5c4dea5ee9b9964c9a45e` (main, 2026-09-29) —
  `vllm/tool_parsers/__init__.py` `_TOOL_PARSERS_TO_REGISTER`,
  `vllm/reasoning/__init__.py` `_REASONING_PARSERS_TO_REGISTER`,
  `vllm/model_executor/models/registry.py` (generation / pooling /
  speculative / previously-supported architecture dicts),
  `vllm/model_executor/layers/quantization/__init__.py` `QuantizationMethods`;
  TensorFold `6b2e4c40064b1e4a05965f61b19ce87b5e0265b3` (v0.3.7) —
  `src/tensorfold/families/*/__init__.py` (`MODEL_TYPES`, `QUANT_METHODS`,
  `CUDA_QUANTIZATION`, `MODELS`, `cuda_engine`/`load`), with rank limits
  hand-read from the cited `raise` lines.
- TensorFold: https://github.com/ashhart/TensorFold (v0.3.7 read at HEAD
  6b2e4c4): `RUNBOOK.md`, `docs/recipes/qwen3.8-27b.md`, `docs/api.md`,
  `src/tensorfold/cli.py`, `src/tensorfold/cuda/server.py`, `src/tensorfold/cuda/comm.py`

## 14. Onboarding a model you've never seen

The launchers know no model. Everything model-specific is **read** from the
checkpoint's own files, **declared** in a profile
(`scripts/backends/models/<name>.env`), or **measured** against the live
server. Seven steps, in order; each one's report tells you whether to go on.

**1. Inspect** (anywhere with python3; the Hub read-only, or a local dir offline)

```bash
scripts/backends/model-inspect.sh <owner/repo>@<40-hex sha>      # or a local directory
```

It reads config.json, generation_config.json, the chat template
(tokenizer_config.json / chat_template.jinja), hf_quant_config.json, the
safetensors index and every safetensors *header* (one Range request each —
never the weights). Given `owner/repo` without `@sha` it resolves `main` to a
commit and warns: pin that SHA. How to read it:

| Line | Meaning / what to do |
|---|---|
| `Arch` | `architectures` + `model_type` (and the text model's). Decides engine support |
| `Weights` | the quantization scheme as the checkpoint declares it (FP8, ModelOpt NVFP4/FP8, compressed-tensors, AWQ/GPTQ, MXFP4, MLX affine, EXL3, bitsandbytes) and the exact safetensors bytes — the weight memory |
| `Params` | stored elements per dtype from the headers, and a *logical* estimate that unpacks packed dtypes (U8 ×2 for FP4, U32 ×8 for 4-bit) — an estimate, labelled |
| `Context` | `max_position_embeddings`, rope scaling, and a note when rope extension is declared |
| `MoE` | experts / active per token: total params decide memory, active params decide speed |
| `KV cache` | bytes per token from layer/head counts — hybrid (linear-attention / SSM) models count only full-attention layers; MLA counts the latent |
| `Fit` | 1 vs 2 Sparks: weights/TP × (1 + 3 % slack) + 5 GB runtime per rank against `GPU_MEMORY_UTILIZATION` × 128 GB; what is left is KV room, shown in tokens. **fits / tight / does not fit**, with every assumption printed |
| `Template` | does it render `tools`; which tool-call markers (`<tool_call>`, `<function=`, `<|python_tag|>`, `[TOOL_CALLS]`, …) and reasoning markers (`<think>`, …) appear |
| `Suggest` | `--tool-call-parser` / `--reasoning-parser` names **from vLLM's registry at the vendored commit**, each with a confidence and its evidence. A template that never renders `tools` gets "no parser": OpenBeast's tools will not work with that model |
| `vllm` / `tensorfold` | SUPPORTED / UNSUPPORTED / UNKNOWN at the vendored engine commits (`data/*.json`). "unknown to vLLM @a96ee59…" = needs a newer image, the Transformers backend, or trust_remote_code. TensorFold: family by `model_type`, a CUDA engine, a readable quant format, the ranks allowed for that format, tested vs untested checkpoint |

`--json` for machines. Refresh the vendored lists when the images move:
`python3 scripts/backends/pylib/vendor_lists.py vllm --commit <sha>` (and
`tensorfold --commit <sha> [--src <checkout>]`); the commit is recorded in the
file and printed on every verdict.

**2. Write the profile**

```bash
scripts/backends/model-inspect.sh <owner/repo>@<sha> --write-profile <name> [--backend vllm|tensorfold]
python3 scripts/backends/pylib/obprofile.py check <name>
```

The draft carries the suggestions with every uncertain line marked
`# VERIFY` (parsers with their confidence, TP from the fit verdict,
`MAX_MODEL_LEN` from the KV estimate, the served name). Every key is
documented in `models/TEMPLATE.env`. The parser is strict on purpose: an
unknown or repeated key, a branch or tag `REVISION`, `TRUST_REMOTE_CODE=true`
without `TRUST_REMOTE_CODE_ACK=<REVISION>`, a vLLM key in a TensorFold
profile, or an `EXTRA_ARGS` flag that has its own key (or is `--api-key` /
`--trust-remote-code`) is an error. Values are data — never sourced or eval'd.

**3. Fetch and verify** (on each Spark that runs a rank)

```bash
scripts/backends/model-fetch.sh --profile <name>           # → MODELS_DIR/<name>, verified, locked
scripts/backends/model-fetch.sh --profile <name> --verify  # later: re-hash against the lock
```

Only `resolve/<REVISION>/…` is requested. Each file must match the Hub's LFS
sha256 (weights) or git blob id (small files) for that commit before it
counts. Downloads land in `MODELS_DIR/.<name>.partial` (resumable, same
filesystem), and one rename publishes the directory only when every file has
passed. The lock `models/<name>.lock` (file → size → sha256, plus source and
revision) is written on the first fetch and is the pin from then on: a later
fetch of that revision (the second Spark, a wiped directory, a mirror) must
reproduce its file set, sizes and sha256s exactly or it is refused, and the
lock is never rewritten — commit it with the profile. Re-runs verify instead
of downloading, an interrupted publish is completed by the next run, and a
changed `REVISION` never overwrites the old directory. Pickled weights, GGUF,
ONNX and `original/` are skipped unless `FETCH_INCLUDE` names them; a
disk-space preflight refuses before downloading. `HF_TOKEN_FILE` (0600) for
gated repos: the token is never on argv and never follows a redirect to
another host.

**4. Launch the ranks with `--profile`**

```bash
scripts/backends/vllm/spark-node.sh --profile <name> --rank 1 --print   # read it, then without --print
scripts/backends/vllm/spark-node.sh --profile <name> --rank 0
# TensorFold: rank 1 FIRST, both with --master <rank0 link IP>
```

The launcher mounts the fetched directory read-only and refuses one whose
sizes no longer match its lock. vLLM may serve an unfetched Hub profile at
its pinned revision (with an "UNVERIFIED" warning); TensorFold refuses,
because it cannot pin a revision itself.

**5. Conformance** (on the rig, or anywhere that reaches rank 0)

```bash
scripts/backends/conformance.sh --model <SERVED_MODEL_NAME> [--heavy] [--concurrency 4]
```

Black-box, through the OpenAI API, with OpenBeast's real `bash` and
`read_file` schemas. Reading the table:

- `*` rows are required (models, chat, stream, tools, tool_result) and
  decide the exit code.
- `unknown_id` says whether consumers must send the exact id.
- `reasoning` says which field Open WebUI and the runner will see:
  `reasoning_content`, vLLM's `reasoning`, or inline `<think>` (= set
  `REASONING_PARSER`).
- `tools` FAIL "as TEXT" = a wrong or missing `TOOL_CALL_PARSER`. A PASS
  "with a caveat" on string-valued arguments is §11 item 4.
- `--heavy` sends a prompt past `max_model_len` and checks the error text
  against `agents/runner.py`'s own overflow detector.

Reports go to `.run/conformance/*.json|.txt` (`latest.json` feeds step 6).
Fix the profile, relaunch, and re-run until VERDICT: PASS.

**6. use-model** (on the rig)

```bash
scripts/backends/use-model.sh --profile <name>
```

It refuses without a passing conformance report for exactly that id at this
rig's `INFERENCE_URL` (`--force` overrides). It records
`INFERENCE_MODEL="<id>"` in openbeast.conf; conf.sh exports it for
vllm/tensorfold and `agents/runner.py` sends it. It prints the opencode
provider entry for `~/.config/opencode/opencode.json` (the repo's
`opencode.json` is tracked and era-hashed, so it is not edited). Open WebUI
needs nothing: it lists `/v1/models`, which conformance proved.

**7. Doctor**

```bash
./scripts/doctor.sh
```

Expect the backend row ready and serving `<id>`, and
`INFERENCE_MODEL='<id>'` passing. "INFERENCE_MODEL is not set" or "is not
what … lists" means step 6 was skipped, or the Sparks now serve another
profile.
