# beast-hydra: plan for routing inference across machines

**Status:** plan and MVP spec, 2026-09-30. Nothing here has been run against a DGX Spark or a 3090 Ti rig. Items marked **VERIFY** are beliefs taken from docs or source reading, not measurements. The MVP is designed so it can be tested end to end today, against stub engines and against the real 5090 when hydra fronts only the rig.

**Anchors:** line numbers refer to the `integ-ca` worktree (`794e198`). Builders should re-anchor them on their own branch.

---

## 0. The decision on one screen

- **What hydra is.** beast-hydra is one new core process on the rig, `agents/hydra.py`, bound to `127.0.0.1:8095`.
  - It speaks the OpenAI API and routes on the request's `model` field.
  - It reads one declarative TOML file, parsed with stdlib `tomllib`.
  - It proxies to any number of OpenAI-compatible engines (llama.cpp, vLLM, TensorFold, or generic) on any box on the tailnet.
  - It adds no new dependencies (FastAPI, uvicorn and httpx are already pinned) and changes no era-locked file.
- **Switching it on and off.**
  - `HYDRA=true` in `openbeast.conf` points every consumer at hydra: WebUI, router, beast-gate, spawned agents and clients via the gate.
  - `INFERENCE_*` keeps its current meaning: the engine that *this* rig manages. Hydra treats that engine as one node among several.
  - `HYDRA=false` is exactly v1.6.
  - `HYDRA=true` with no `hydra.toml` generates an implicit single-node config that behaves like today, plus provenance headers.
- **The data model has three nouns and one verb.**
  - A **node** is one engine endpoint.
  - A **deployment** is one model on one node. Its id is strict: it is served by that deployment and nothing else.
  - A **route** is a virtual model id with a policy, such as `beast`, `beast:fast`, `beast:max`, `beast:long` or `classify`.
  - **Rules** are deterministic `when → then` rewrites.
- **Routing.** The pipeline is: resolve the id, apply rules, apply hard filters (health, capability, context fit, drain), pick from priority groups, then least-loaded with session affinity. Groups spill when saturated. Failover happens only before the commit point (the first body byte). There is never a replay mid-stream. 4xx responses pass through byte for byte.
- **Eval integrity.**
  - Evals bypass hydra by default.
  - Deployment ids and the `/pin/<deployment>/v1` path are strict: no fallback, no rules, 503 when down.
  - Hydra rewrites nothing except `model` and an invalid `id_slot`.
  - Every response carries `X-Hydra-*` provenance, and every request gets an audit line.
- **The spine is Proposal 1** (minimal and shippable), with grafts:
  - from Proposal 2: implicit config, commit on first body byte, `/pin` path, AUTH_FAILED state, probe hysteresis, family guard;
  - from Proposal 3: modelling the rig as `-np 1`, conformance as the admission gate with caps = declared ∩ measured, prompt-scaled TTFT deadlines, `exclusive_group`, and the doctrine of *evaluating routing policies as models*.
  
  Queues, node agents, classifiers and learned routing come NEXT or FUTURE, each behind a measurement.
- **What the MVP buys now is capacity, not "intelligence".**
  - The default rig config runs MTP with `-np 1` (`serve-qwen38-27b-uncensored-mtp-q5.sh:75`), so today every second conversation and every router classify call waits behind the first.
  - Spilling to the Sparks and moving the classify call off the 5090 are the first measurable wins. `ROUTER_SIDECAR_PLAN.md` measured 17.8 s and 45.2 s against 15.1 s for turns that trigger the classify.
  - Quality routing waits for data: the v4 capability band is 95.01 to 98.70 (`evals/leaderboard.json`, 12 entries).

---

## 1. Facts that shape the design

| # | Fact | Source | Consequence for hydra |
|---|---|---|---|
| F1 | The default rig model is MTP, and MTP forces `-np 1` | `scripts/serve-qwen38-27b-uncensored-mtp-q5.sh:49,75` | Rig `slots = 1`. Spill and classify offload are the main value now. |
| F2 | `--kv-unified`: each slot sees the full `-c` | AGENTS.md, BEAST_SLOT.md | A deployment's `ctx` is the full `-c`. Never divide it by the slot count. |
| F3 | Readiness differs per engine: llama 200 `{"status":"ok"}` with 503 "Loading model"; vLLM empty 200; TensorFold `ok:true`/`status:"ok"` | `scripts/lib/backend.sh:114-126` | Hydra's Python parser must match the bash one exactly, enforced by a parity test. |
| F4 | TensorFold has no auth. vLLM's `--api-key` guards `/v1` only, so `/metrics` is open | DGX_SPARK_PLAN §6–7 | Never send a key to TensorFold. It must be firewalled to the rig. |
| F5 | runner.py detects context overflow from the upstream error text | `agents/runner.py:289-310` | 4xx bodies pass through verbatim. Rewrapping them breaks compaction. |
| F6 | runner.py uses the OpenAI SDK 3.22.0, non-streaming, with `max_retries`, and the default 600 s timeout | `runner.py:792,1010`, `agents/requirements.txt` | A non-stream "TTFT" is the whole generation, so it needs its own deadline under 600 s. The SDK retries hydra's 503s itself. |
| F7 | The gate's httpx read timeout is 600 s and connect is 10 s; the read timeout is **per read gap**, not total | `edge.py:1136-1140` | Hydra's pre-commit budget and every idle timeout must stay under 600 s. |
| F8 | `tailscale serve` proxies into 127.0.0.1, so a loopback peer proves nothing | `edge.py:480-492` docstring | Every `/hydra/*` admin route uses a local token. Loopback alone is never trusted. |
| F9 | Extensions start after the inference wait | `start.sh:973-994` vs `:627`, `:683` | Hydra is a core process launched before the wait, never an extension. |
| F10 | `ob_backend_normalize` turns an unknown backend into llama, and non-llama forces MANAGED=false | `backend.sh:46-61`, `conf.sh:462-465` | There is no `INFERENCE_BACKEND=hydra`. Hydra has its own switch. |
| F11 | The era files are runner.py, tools.py, system-prompt*.md, opencode.json and SUITE_VERSION | `evals/cache.py:49-58` | Hydra touches none of them. Consumers just get a URL and a model id from the environment. |
| F12 | The GPU lease is advisory and pid-plus-start-time checked, and a stale lease file can exist | `scripts/gpu-lease.sh:22,59-115` | Drain on `gpu-lease.sh check` exit 4, never on the file merely existing. |
| F13 | conformance already supports `--backend llama\|vllm\|tensorfold`, `--key-file` and `--out DIR` | `conformance.py:497-531` | Per-deployment reports need **no conformance change**: `--out .run/conformance/<deployment>/`. |
| F14 | `tests/test_conformance.py::Stub` already emulates the vLLM, TensorFold and "broken" shapes | `tests/test_conformance.py:28-160` | The fake engine is extracted from it and extended, not invented from scratch. |
| F15 | The v4 suite spans only 95.01 to 98.70 capability; decode speed spans 35.7 to 359.1 tok/s | `evals/leaderboard.json` | Routing on speed and capacity is measurable now. Routing for quality needs a harder corpus first. |
| F16 | The gate already sets `X-OpenBeast-Device`, namespaces `X-Conversation-Id` per device, and swaps `Authorization` | `edge.py:678-701` | Hydra reuses these, but trusts the device header only when it comes with the caller token. |

---

## 2. Scoring the three proposals

Scale: 1 to 10, higher is better. For dependency cost, 10 means no new cost.

| Criterion | P1 Minimal | P2 Scale & robustness | P3 Intelligence-first |
|---|---|---|---|
| Elegance / config ergonomics | **8**: three nouns plus rules, flat TOML, readable | 6: six concepts, plus `aliases_compat`, devices and weights | 7: `node/model` refs read well; tiers, classifier and devices add surface |
| Fit with OpenBeast | **9**: reuses the gate's proxy pattern, profiles, conformance, readiness parity; wiring map exact | 8: same wiring, but the node agent is a new component on every box | 8: same wiring; models the rig's `-np 1` correctly; conformance caps intersection |
| Testable without real nodes | **9**: pure `decide()`, fake engines, sim script | 8: the same, with more surface (queue, scoring, agent) | 8: the same, with more surface (keepalive, classifier hooks) |
| Robustness / failover | 7: breaker, pre-commit failover; commits on status line; no probe hysteresis | **9**: hysteresis, held headers, AUTH_FAILED, late-binding queue, slow start | 7: cooldown plus breaker; SSE keepalive commits HTTP 200 and so breaks verbatim 4xx |
| Security | **8**: caller token, local-token admin, per-node keys, refuses raw publication | 7: sound keys, but "read-only `/hydra/*` loopback only" ignores F8; the agent opens a new listener on each node | **8**: caller token, SSRF validation, TensorFold firewall paths, doctor ACL warning |
| Eval integrity | 8: strict ids, lease drain, loud mislabel; `/props` is 404 through hydra | **9**: `/pin/<d>/v1` with `/props` passthrough, 422 on incompatible pin, identity mismatch 409 | **9**: fingerprints, run-level affinity, routing policy evaluated as a model with McNemar tests |
| Future headroom | 7 | 9 | **10** |
| Dependency / supply-chain cost | **10**: none | 8: no packages, but a systemd unit and agent on each node | 9: none at runtime; offline research scripts |
| **Total /80** | **66** | 64 | 66 |

**Flaws found while scoring (all fixed in the final plan):**

- **P1**
  - The example sets the rig to `slots = 4`, but the default is `-np 1` (F1).
  - It drains when `.run/gpu.lease` merely exists, which is wrong for a stale lease (F12).
  - It calls `healthcheck.sh:396` an "existing bug". Today the gate's upstream and `INFERENCE_URL` are the same value (`start.sh:182,807`), so it is a *latent* bug that hydra would trigger.
  - It commits on the status line, so a node that sends headers and then dies loses a free retry.
- **P2**
  - Its loopback-only reads are unsafe because of F8.
  - `max_inflight = 2× MAX_NUM_SEQS` is an unmeasured guess.
  - It is sized for about 10 nodes, not 2 to 4.
- **P3**
  - SSE keepalive commits a 200 before routing is final, which breaks the verbatim 400 overflow path (F5).
  - It hand-types `quality` numbers into config, where they drift from the leaderboard.
  - `quality` for a default route that is actually a different slug.

**Verdict: P1 is the spine.** P1 and P3 tie on the raw total. The deciding requirement is being ready to test the moment the nodes are online with the least untested machinery, and P1 wins on testability, dependency cost and fit. P3 supplies the doctrine for how "intelligence" features earn their place. P2 supplies the robustness mechanics that cost little.

**Grafts:**

| Graft | From | Where it goes |
|---|---|---|
| Implicit single-node config when `hydra.toml` is absent | P2 | NOW |
| Commit point = first upstream **body** byte, so pre-body transport failures can be retried | P2 | NOW |
| `/pin/<deployment>/v1/*` strict path, plus `/pin/<d>/props` passthrough for llama | P2 | NOW |
| AUTH_FAILED state on 401/403 from a node, which fails over and is never shown to the caller as *their* auth error | P2 | NOW |
| Probe hysteresis (`down_after`, `up_after`) | P2 | NOW |
| Deployment `family` plus route `same_family` guard | P2 (`fallback_family`) | NOW |
| `exclusive_group` (vLLM and TensorFold on the same pair) | P3 | NOW |
| Prompt-scaled TTFT deadline (`prefill_tps_floor`) | P3, P2 | NOW |
| Effective caps = declared ∩ conformance-measured | P3 | NOW (`tools` only) |
| Routing policy evaluated as a model (non-inferiority plus McNemar) | P3 | NEXT (doctrine applies from day one) |
| Late-binding hydra queue, priority classes, DRR fair share | P2, P3 | NEXT |
| Node agent (TP-rank liveness, provenance, power) | P2 | NEXT |
| Reasoning-field normalization | P2, P3 | NEXT |
| Offline learned rules (`hydra-learn.py`), classifier, cascades, specbench break-even | P3 | FUTURE |

---

## 3. Final architecture

### 3.1 Topology

```
                         tailnet (WireGuard)                                  private ConnectX-7
 client devices ──:8443──► beast-gate :8090 ──┐                                (NCCL only, never hydra)
 (per-device keys)          + X-Hydra-Caller  │
 WebUI :3000 (host net) ──► router :8088 ─────┤                    ┌───────────────┐       ┌───────────────┐
                            + X-Hydra-Caller  ▼                    │ spark-a rank0 │◄─CX7─►│ spark-b rank1 │
 spawned agents (runner.py) ───────────► HYDRA 127.0.0.1:8095 ────►│ vLLM :8000    │       │ headless      │
 beast-chat, mcp start_agent ──────────►   holds node keys         │ (or TF, excl.)│       └───────────────┘
                                           probes, audits, routes  └───────────────┘
                                                │
                                                ├──► rig llama-server 127.0.0.1:8080  (5090, managed by start.sh, -np 1)
                                                └──► ti-rig llama-server :8080 / :8081 (2×3090 Ti; optional CPU classifier)
```

- **Hydra runs on the rig only.** It observes nodes and never supervises them. The rig's llama-server stays under `start.sh`.
- **Clients never reach nodes or hydra directly.** They reach the gate on `:8443`, and the gate forwards to hydra.
- **Hydra only carries `/v1/*` traffic.** Tools still execute where the calling process runs.
- **A TP=2 Spark pair is one node**: rank 0's HTTP endpoint. `members` is informational in the MVP.

### 3.2 Request resolution in one paragraph

A request's id is resolved in this order:

1. `/pin/<d>/…` or `X-Hydra-Pin: <d>` → **strict** to deployment `<d>`.
2. The body `model` equals a deployment id → strict.
3. The body `model` equals a route id or an alias → that route.
4. Anything else → `default_route`, or 404 when `unknown_model = "404"`.

Strict requests skip rules, spill and fallback. Route requests go through rules, then filters, then selection, then failover.

### 3.3 Non-goals for hydra

- It does not pool weights across boxes (that is RPC or TP inside a node).
- It does not start or stop remote engines (MVP).
- It does not route tools.
- It does not make the rig highly available.
- It does not rewrite sampling or reasoning parameters, ever.
- It does not change which eval era a row belongs to.

### 3.4 How the engines differ (what hydra must normalize or respect)

| Aspect | llama.cpp | vLLM | TensorFold |
|---|---|---|---|
| Ready | `/health` 200 `{"status":"ok"}`; 503 "Loading model" = LOADING | `/health` 200, empty body | 200 with `"ok": true` or `"status": "ok"` |
| Auth | `--api-key` (the rig uses `LLAMA_API_KEY`) | `VLLM_API_KEY`, guards `/v1` only | **none**, so never send a key |
| `model` id | ignored; any id works | strict: an unknown id returns 404 (**VERIFY** on the NGC build) | per conformance `strict_model_names` |
| `id_slot` | honoured | ignored (**VERIFY**, DGX §12) | unknown, so hydra drops it |
| `grammar` body field | yes | no; uses `response_format` | no |
| `response_format: json_schema` | yes | yes | **no** (DGX §6) |
| Reasoning field | `reasoning_content` | `reasoning` (**VERIFY** that WebUI renders it) | per conformance `reasoning_field` |
| Overflow text | `exceed_context_size`… | "maximum context length is N" | CUDA and MLX texts (runner regex) |
| Load metrics | `/metrics` (serve.sh passes `--metrics`): `requests_processing`, `requests_deferred` | `/metrics` open: `num_requests_running`, `num_requests_waiting`, `kv_cache_usage_perc` (**VERIFY** names) | none; hydra's own in-flight count only |
| Slots | `-np` (default rig: 1) | `MAX_NUM_SEQS` (profile: 8) | `--parallel` |

---

## 4. Every approach considered

| # | Approach | Verdict | Reason |
|---|---|---|---|
| A1 | **Rig-side OpenAI-compatible router process (hydra) routing on `model`** | **CHOSEN** | Stdlib plus already-pinned FastAPI, uvicorn and httpx. One URL change for consumers. Reuses the gate's relay pattern. Testable without hardware. No era-locked file changes. |
| A2 | LiteLLM proxy as a dependency | Reject; copy its semantics | Heavy dependency tree, and litellm 1.82.7 and 1.82.8 were backdoored on PyPI on 2026-03-24 through compromised CI, which conflicts with hash pinning. We keep its vocabulary: model groups, `order`, cooldown, `allowed_fails`, context-window pre-call filtering. |
| A3 | llama-swap with `peers` | Reject as a runtime; copy the config shape | Another Go binary to pin. No hook for our identity, audit or eval pinning. Its models→peers, `aliases`, `useModelName`, `stripParams`, `concurrencyLimit` and exclusive `groups` shape informed our schema. |
| A4 | SGLang router (sgl-model-gateway) as a binary | Reject as a runtime; copy the algorithm | A Rust binary with SGLang-centric health semantics and no identity or audit. The approximate prefix tree, the two-threshold switch between cache and load, and the circuit-breaker defaults (5/2/30 s) are ported. |
| A5 | vLLM production-stack router | Reject | Kubernetes/Helm-first and vLLM-shaped; its docs and README disagree on which features exist. We copy the static backend list, session-header stickiness, hot-reload and `/metrics` scraping. |
| A6 | llm-d, KServe, Gateway API Inference Extension | Reject | Kubernetes required. Its EPP scorer-plugin pattern (a weighted sum) informs the FUTURE scoring. |
| A7 | NVIDIA Dynamo | Reject the framework; copy its cost formula | No llama.cpp support. It pays off at more than 4 replicas of one engine family. Its approximate KV mode goes to FUTURE. |
| A8 | Ray Serve LLM | Reject | Needs a Ray cluster across aarch64 and x86. Ray does not manage llama.cpp. |
| A9 | GPUStack | Reject | Duplicates our control plane (weights, identity, dashboard). Worth mining for inventory and metering UX. |
| A10 | Envoy AI Gateway / Agent Router, standalone `aigw` | Reject for now | An Envoy binary to pin. The gate already covers device limits. Could be re-evaluated if token-budget limits become necessary. |
| A11 | nginx, HAProxy, Caddy, Traefik (L7 load balancer) | Reject | They cannot see the model id, capabilities or context fit. No SSE-aware commit semantics, no provenance, and every upstream would need the same model. |
| A12 | DNS round-robin, or L4/`tailscale serve` load balancing | Reject | Model-blind and health-blind. Clients cache DNS. No failover semantics. |
| A13 | Client-side routing: opencode with several providers, WebUI with N connections, a clients.sh per-node catalog | Reject as the primary design; keep as break-glass (FUTURE) | Spreads node keys and policy to every device and leaves no single audit record. Every consumer, including agents, would re-implement failover. |
| A14 | Open WebUI's native multi-connection (it merges `/v1/models` across URLs) | Reject | Covers WebUI only. No health-aware failover, and agents and clients get nothing. |
| A15 | Routing inside beast-gate (`edge.py`) | Reject | Mixes device identity with fleet routing. The gate is opt-in, but hydra must work with the gate off. Keeping them separate keeps each small and auditable. |
| A16 | Hydra as a dashboard-style extension | Reject | Deadlocks boot (F9). |
| A17 | `INFERENCE_BACKEND=hydra` as a fourth value | Reject | Normalization and managed-flag logic would stop supervising the local 5090 (F10). |
| A18 | llama.cpp RPC pooling (one GGUF split over the 5090 and the 3090 Tis) | FUTURE, as a *node type* | No auth. Upstream says never run it on an open network, so private cable only. Hydra would see it as one llama node in an `exclusive_group` with its member nodes. |
| A19 | llama.cpp router mode (`--models-dir`, one child per model) | Compatible, not an alternative | That is model swapping on one box. Hydra can front a router-mode llama-server as a node that lists several deployments. |
| A20 | exo | Reject | CPU-only on Linux today. MLX-first. Its PD break-even formula is kept for FUTURE. |
| A21 | vLLM multi-node pipeline or tensor parallel over the tailnet | Reject | Latency per token over WireGuard. TP stays on ConnectX-7 inside the Spark pair, which is the node's own business. |
| A22 | Prefill/decode disaggregation | FUTURE: vLLM↔vLLM, Spark↔Spark, over CX-7 only | KV layouts are incompatible across engines, so llama.cpp on the 5090 to vLLM on a Spark is impossible. Pays off only at roughly 5k to 40k tokens of context (exo measurement). |
| A23 | Cross-machine speculative decoding (llama.cpp #23982) | FUTURE, behind a break-even benchmark | `speedup = (E+1)·t_t / (k·t_d + RTT + t_v)`. The rig's in-process MTP (~7 ms/token) already beats anything that adds a tailnet RTT. Only a slow flagship on the Sparks could gain, and vLLM MTP/EAGLE probably captures that anyway. No engine support exists. |
| A24 | Petals, distributed-llama, LocalAI federated/P2P mode (from memory, not re-verified) | Reject | Public-swarm or CPU/ARM-oriented designs with another runtime stack. They do not fit CUDA engines or the pinning ethos. |
| A25 | Ollama clustering (OLOL, Olla, Hive, Herd) | Reject; take the lesson | A merged catalog plus health plus model-aware routing is most of the homelab value, and that is exactly hydra's MVP. |
| A26 | Cloud routers (OpenRouter, NotDiamond, Martian, Cloudflare AI Gateway) | Reject | Local-only ethos. We copy OpenRouter's per-request API (`only`, `ignore`, `order`, `require_parameters`, served-model reporting, `:suffix` policies). |
| A27 | Semantic or learned routers (vLLM Semantic Router, RouteLLM) | FUTURE, opt-in, gated by A/B | Intent is a weak signal for choosing a model (the vLLM-SR paper's own caveat). Our eval data is ready training data, but it is saturated (F15). |
| A28 | Distributed control plane (every node routes, gossip, HA) | FUTURE | Useful only once the command center itself can fail over. The rig is already the single point of failure for tools and WebUI. |
| A29 | Node self-registration or tailnet auto-discovery | NEXT (suggest-only discovery); FUTURE (enrollment) | Static config is right for 2 to 4 boxes. Auto-add needs a trust protocol. |
| A30 | A node agent on each box | NEXT, optional | Worth it for TP-rank liveness, remote provenance and lease/power data. Not needed for routing. |
| A31 | Kubernetes, Nomad, Slurm | Reject | Operational weight far beyond one owner with 2 to 4 boxes. |
| A32 | MCP-level routing (the model picks a model via a tool) | FUTURE: `start_agent(model=…)` | Local models rarely call optional tools (beast-lang lesson). Orchestrator-level escalation is more reliable than model-initiated escalation. |

---

## 5. Feature catalog

Complexity: S is under a day, M is 1 to 3 days, L is more than 3 days or research.

### 5.1 NOW: the MVP (hermetic, ships before any node exists)

| # | Feature | Value | Cx |
|---|---|---|---|
| N1 | Virtual model namespace: routes, aliases, legacy ids (`qwen-27b-q5` → `beast`) | Agents and WebUI call names like `beast` or `beast:fast`, and era-locked files stay untouched | S |
| N2 | Strict deployment ids, `X-Hydra-Pin`, `/pin/<d>/v1/*` (plus `/pin/<d>/props` and `/health` for llama) | Reproducible, never-substituted answers for evals and debugging | S |
| N3 | Declarative `hydra.toml`, `--check`, fail-closed validation, hot reload (SIGHUP or `/hydra/reload`) that keeps the last good config, config hash | One file. A typo never takes the fleet down. | M |
| N4 | Implicit config when the file is absent, plus `--print-default-config` | `HYDRA=true` on a single rig behaves exactly like today on day one | S |
| N5 | Profile-derived deployments (`profile = "…"` → upstream, ctx, slots, engine) | Each fact is declared once and reused from onboarding | S |
| N6 | Engine-aware readiness with bash/Python parity, plus a `/v1/models` MISMATCH check | Health checks that engines cannot silently diverge from | S |
| N7 | Health state machine: hysteresis, circuit breaker, LOADING, AUTH_FAILED, MISMATCH, DRAINED | Flapping nodes do not oscillate. A loading model is not "down". | M |
| N8 | Drain and undrain (admin), `enabled = false`, GPU-lease drain via `gpu-lease.sh check` | Protects campaigns and gives an operator control | S |
| N9 | Capability filters (tools, json_schema, grammar, vision, reasoning_budget, id_slot, embeddings), with caps = declared ∩ conformance | Never sends grammar to vLLM or json_schema to TensorFold | S |
| N10 | Context-fit filter (conservative estimate, unified-KV ctx, margin), `min_ctx` per route | 200K prompts go only where they fit | S |
| N11 | Priority groups, spill on saturation, weights, least in-flight, random tie-break | Uses the Sparks while the `-np 1` rig is busy | S |
| N12 | Session affinity (`session`, `sticky`, `none`) keyed on conversation id, header or message hash | Keeps llama prefix reuse and consistent model behaviour within a conversation | S |
| N13 | Family guard (`family` on deployments, `same_family` on routes) | An uncensored default never silently falls back to a stock model when the owner opts in | S |
| N14 | `exclusive_group` (e.g. vLLM and TensorFold on the same pair) | Validation stops two engines that share GPUs from both being enabled | S |
| N15 | Rules: model, device, role, images, tools, json_schema, prompt size, hours → route, only/ignore nodes, require, prefer | Deterministic, auditable policy such as "images → vision" or "guests off the 5090" | M |
| N16 | Pre-commit failover (commit point = first body byte), `max_attempts`, pre-commit budget under the gate timeout | A dead or loading node is invisible to the user when there is an alternative | M |
| N17 | 4xx passthrough byte for byte; node 401/403 → AUTH_FAILED plus failover; node 404-model → MISMATCH plus failover | runner compaction keeps working, and callers never see a node's auth error as their own | S |
| N18 | Prompt-scaled TTFT deadline (`prefill_tps_floor`); separate non-stream deadline | A 256K prefill on a Spark is not mistaken for a hang | S |
| N19 | Defined mid-stream failure: SSE error event, no `[DONE]`, no replay | Truncation is loud, never silent | S |
| N20 | Per-node keys (`key_env`, or `key_file` mode 0600); inbound key required; never forward the caller's key; never send a key to TensorFold | Node credentials live in exactly one place | S |
| N21 | Caller token (`X-Hydra-Caller`) that makes `X-OpenBeast-Device` and WebUI role trusted; spoofed headers stripped | Per-device and per-tier rules that cannot be forged by header | S |
| N22 | Provenance headers (`X-Hydra-Route`, `-Deployment`, `-Node`, `-Engine`, `-Upstream-Model`, `-Attempts`, `-Config`, `-Request-Id`) | Every answer names where it came from | S |
| N23 | Audit JSONL (no prompt text), `/hydra/status`, `/hydra/explain` (dry-run trace), `/hydra/decisions`, `/hydra/metrics` (Prometheus text) | Routing you can debug and measure | M |
| N24 | Merged `/v1/models` catalog with an `openbeast` metadata block (route, strict, caps, ctx, healthy) | WebUI gets one row per id automatically, with tools wired | S |
| N25 | Stack wiring: conf keys, start/stop/healthcheck/doctor, gate and router caller token, router `classify` model, setup-tailscale refusal | One switch in `openbeast.conf` | M |
| N26 | `scripts/hydra.sh` (check, status, explain, reload, drain, undrain, add-node, conformance, tail, pin-smoke) | Operator ergonomics | S |
| N27 | Fake engine (llama, vLLM, TensorFold personalities with fault injection) plus `scripts/hydra-sim.sh` | Drive WebUI or beast-chat against a simulated fleet today | M |
| N28 | Test suite: core, proxy, parity, wiring, sim | The MVP is proven before any hardware arrives | M |

### 5.2 NEXT: after the first real multi-node test (each needs data from it)

| # | Feature | Value | Cx |
|---|---|---|---|
| X1 | Load scraping from `/metrics` (llama `requests_deferred`; vLLM `num_requests_waiting`, `kv_cache_usage_perc`) folded into the load score | Sees traffic that bypasses hydra (campaigns, direct callers) | M |
| X2 | Late-binding hydra queue: per-route, bounded depth and wait, priority classes (interactive > agent > batch) with aging, DRR fair share per device | A saturated fleet is fair, and waiting requests bind to whichever node frees first | L |
| X3 | Reasoning-field normalization (`reasoning` → `reasoning_content`), opt-in per node, never on strict | Uniform WebUI rendering across engines | S |
| X4 | Exact token counts via llama `/tokenize` near the context limit | Fewer overflow round-trips for WebUI users | S |
| X5 | `on_overflow = "larger_ctx"`: retry an overflow 400 on a larger-ctx target; otherwise send the original body | Recovers estimator misses | S |
| X6 | `/api/slot` v3 (additive `models[]`, `nodes[]`, `hydra{}`), dashboard Fleet panel, `services_status` hydra row | Clients and beast-chat see the fleet | M |
| X7 | Client catalogs: `client.sh _refresh_oc_catalog` and `setup-client.sh` built from `/v1/models` plus v3; `use-model --hydra --node` writes a stanza and a user-level opencode entry | Remote devices can pick any route | M |
| X8 | `configure-webui.sh` wires tools only to ids whose caps include `tools` | Non-tool models do not get tool ids | S |
| X9 | `run_eval --via-hydra --pin <d>` plus refusal of a non-strict hydra base URL; stamp `/hydra/provenance/<d>` into the row | Remote rows get honest provenance | M |
| X10 | Node agent `agents/hydra_node.py` (stdlib, read-only, keyed): engine argv hash, image digest, weights id, TP-rank liveness, lease, power | Closes the remote-provenance gap and detects a dead rank 1 | M |
| X11 | Latency and throughput EWMA sorting (`sort = "latency"\|"throughput"`, `preferred_max_ttft_ms`) | Speed-first routes (`beast:fast`) | S |
| X12 | Slow-start ramp after a node recovers | No thundering herd onto a node that just came back | S |
| X13 | Per-device policy from the `clients.json` registry (`routes` allowlist, `priority`, `slot_node`) via `clients.sh --routes` | Per-device ACLs such as a CI bot limited to `beast:fast` | M |
| X14 | Hydra owns llama slot pinning per (device, deployment); the gate stops injecting `id_slot` when hydra is on | Correct slot pinning across several llama nodes | S |
| X15 | Tailnet suggest-only discovery (`hydra.sh discover` over `tailscale status --json`) and `hydra.sh init` from profiles | Faster onboarding with no auto-trust | S |
| X16 | Weighted canary and shadow traffic (mirror N%, discard output, never for evals) | A/B a new deployment on real traffic safely | M |
| X17 | Hedged requests for short `classify` calls | Tail latency of the classifier | S |
| X18 | Capacity-spill A/B and classify-offload A/B (P3 §11.4 protocol) | Measured proof of the first claimed wins | M |
| X19 | Faster liveness loop for hydra itself (30 s, not the 5-minute watchdog) | Shrinks the single-point-of-failure window | S |

### 5.3 FUTURE: research, gated on measurement

| # | Feature | Value | Cx |
|---|---|---|---|
| R1 | Eval-score routing via `hydra-learn.py`: paired McNemar on shared units, same era only; emits reviewed rules | Model per task class, e.g. zig to the best zig model | M |
| R2 | A discriminating routing corpus (hard tier, zig, long context, real beast-chat traces) via `eval-task-author` | Gives R1 and R3 something to learn from | L |
| R3 | Classifier routing via the CPU sidecar (`ROUTER_SIDECAR_PLAN` §6 gates) | Separates side traffic (chat vs agent vs guest) | M |
| R4 | Orchestrator cascade: fast model first, re-run on `beast:max` when the verifier fails (beast-chat, `start_agent(model=…)`) | Speed on the easy majority, quality on the hard tail | M |
| R5 | Structural-verifier cascade (non-stream JSON or tool-call parse failure → next tier) | Recovers engine tool-parser misses | M |
| R6 | Confidence cascade (logprob thresholds) | Cheap escalation for short answers | L |
| R7 | Fan-out: first-valid, majority vote, or verifier (non-stream) | Hedging and quality where an executable verifier exists | L |
| R8 | SGLang-style approximate prefix affinity for same-fingerprint replica pools | Matters only if the Sparks run 2×TP1 replicas | M |
| R9 | Fingerprints `sha256(engine, version/image, weights id, serve flags, hydra edits)` and same-fingerprint replica semantics | A precise definition of "interchangeable" | M |
| R10 | Routing policy as an evaluated model (slug `hydra-<route>-<cfghash8>`, non-inferiority margin 0.5, McNemar) | The only way an "intelligence routing" claim ships | M |
| R11 | Node-agent action channel (HMAC plus nonce, allowlist in the node's own config): load profile, stop engine, wake-on-LAN, scale to zero | On-demand placement and power savings | L |
| R12 | Placement advisor `hydra-plan.py` (Helix/HexGen-style feasibility, advisory only) | Which model should run where | L |
| R13 | vLLM PD disaggregation across the Spark pair over CX-7 (`role = prefill\|decode`, `kv_transport`) | Long-context throughput | L |
| R14 | llama RPC pool node (5090 plus 3090 Ti, private cable, exclusive with its members) | One GGUF larger than any single box | L |
| R15 | Cross-box speculative decoding, after `hydra-specbench.py` proves break-even | Possibly faster big-model decode | L |
| R16 | Energy and time policy (J/token from the node agent, `sort = "energy"` for batch) | Watts-aware night jobs | M |
| R17 | Same-fingerprint mid-stream resume (prefill with the partial answer) | Survives node loss mid-answer | L |
| R18 | Exact KV-event routing (when engines expose events) | Precise cache reuse | L |
| R19 | HA: standby hydra plus client break-glass to a node-mode gate; federation with another owner's hydra | Survives the rig going down | L |

---

## 6. MVP implementation spec

### 6.1 Files

**New:**

| Path | Contents | Est. lines |
|---|---|---|
| `agents/hydra_core.py` | **Pure, no I/O.** Dataclasses (`Node`, `Deployment`, `Target`, `Route`, `Rule`, `Config`, `Features`, `Caller`, `Candidate`, `Decision`, `Trace`), `load_config(path, env)`, `implicit_config(env)`, `validate()`, `config_hash()`, `engine_ready(engine, status, body)`, `extract_features(path, body, headers, cfg)`, `match_rules()`, `decide(cfg, state, feats, caller, now)`, `HealthState` (hysteresis plus breaker), `AffinityLRU`, `effective_ttft()`, `sse_error_event()` | ~700 |
| `agents/hydra.py` | FastAPI app and lifespan: prober task, models checker, lease checker, per-node `httpx.AsyncClient`, proxy, `/pin`, admin routes, audit writer with rotation, metrics text, SIGHUP reload, CLI (`--check PATH`, `--explain JSON`, `--print-default-config`, default = serve) | ~650 |
| `hydra.toml.example` | The §6.2 example, heavily commented | ~150 |
| `scripts/hydra.sh` | Ops CLI (§6.9) | ~250 |
| `scripts/hydra-sim.sh` | Launches the fake fleet plus hydra on ephemeral ports | ~120 |
| `tests/fakes/fake_engine.py` | Fake engine (§6.11). The shape helpers are extracted from `tests/test_conformance.py::Stub`, which is refactored to import them | ~350 |
| `tests/fixtures/hydra/*.toml`, `tests/fixtures/hydra/ready/*.json` | Validation fixtures, readiness bodies | n/a |
| `tests/test_hydra_core.py`, `tests/test_hydra_proxy.py`, `tests/test_hydra_ready_parity.sh`, `tests/test_hydra_sim.sh` | §6.11 | ~1200 |
| `docs/BEAST_HYDRA_PLAN.md` | This document | n/a |

**Edited (all outside the era lock):**

- `scripts/lib/conf.sh`
- `scripts/lib/backend.sh` (`ob_hydra_ready`)
- `start.sh`
- `stop.sh`
- `scripts/healthcheck.sh`
- `scripts/doctor.sh`
- `agents/edge.py` (forward the request id plus the caller token; `x-hydra-caller` becomes spoofable)
- `agents/router.py` (`ROUTER_CLASSIFY_MODEL`, the caller token)
- `scripts/setup-tailscale.sh` (refuse raw publication when HYDRA is on)
- `openbeast.conf.example`
- `docs/REFERENCE.md`
- `.gitignore` (`hydra.toml`)
- `tests/run_tests.sh`
- `tests/test_scripts.sh`
- `tests/test_edge.py`
- `tests/test_router.py`
- `tests/test_conformance.py` (imports the shared stub shapes only)

**Untouched, and asserted untouched by acceptance:** `agents/runner.py`, `agents/tools.py`, `system-prompt*.md`, `opencode.json`, `evals/SUITE_VERSION`, `extensions/dashboard/dashboard.py`, `scripts/backends/pylib/conformance.py`.

### 6.2 Configuration

**Location.** `HYDRA_CONFIG`, default `$REPO_DIR/hydra.toml`, which is gitignored (it holds tailnet hostnames). `hydra.toml.example` is tracked. The port and bind are **not** in the TOML: the port comes from `HYDRA_PORT` in conf.sh, and hydra always binds `127.0.0.1`.

**Keys.** Keys live in `~/.config/openbeast/hydra/<node>.key`, mode 0600. This matches the `~/.config/openbeast/vllm-api-key` convention in `spark.env.example`.

#### 6.2.1 Full example: 5090 rig + 2× Spark (TP=2) + 2× 3090 Ti

```toml
# hydra.toml — beast-hydra routing config.  Validate: scripts/hydra.sh check
# Reload: scripts/hydra.sh reload (or kill -HUP $(cat .run/hydra.pid)).
schema = 1

[hydra]
default_route       = "beast"      # what an unknown/legacy model id resolves to
unknown_model       = "default"    # "default" | "404"
inbound_key_env     = "LLAMA_API_KEY"   # callers must present it; empty env = open (loopback only anyway)
probe_interval_s    = 5            # READY/LOADING probe cadence
probe_down_interval_s = 30         # DOWN/AUTH_FAILED probe cadence
models_interval_s   = 60           # /v1/models re-check (catches a swapped model)
down_after          = 2            # consecutive failed probes → DOWN
up_after            = 2            # consecutive good probes → READY
pre_commit_budget_s = 580          # all attempts before commit; MUST be < gate read timeout (600) and < SDK 600
chars_per_token     = 3.0          # conservative estimate (over-estimates tokens)
ctx_margin          = 0.05
default_max_tokens  = 4096         # assumed completion size when a request has none
list_deployments    = true         # also list strict deployment ids in /v1/models
affinity_ttl_s      = 1800
affinity_max        = 4096
audit               = ".run/hydra-audit.jsonl"
audit_max_mb        = 50

[hydra.breaker]
fail_threshold    = 5
open_s            = 30
success_threshold = 2

# ─────────────────────────── nodes ───────────────────────────
[nodes.rig]                                   # the 5090, launched + supervised by start.sh
url         = "http://127.0.0.1:8080"
engine      = "llama"                         # llama | vllm | tensorfold | openai
key_env     = "LLAMA_API_KEY"
slots       = 1                               # MTP forces -np 1
gpu_lease   = true                            # drain while an eval campaign holds scripts/gpu-lease.sh
ttft_timeout_s = 120
labels      = ["local", "5090", "mtp"]

[nodes.sparks]                                # TP=2 pair = ONE node (rank 0 serves HTTP)
url         = "http://100.101.102.103:8000"   # spark-node.sh SPARK_SERVE_HOST:PORT (tailnet IP)
engine      = "vllm"
key_file    = "~/.config/openbeast/hydra/sparks.key"   # same value as the Spark's VLLM_API_KEY_FILE
slots       = 8                               # = MAX_NUM_SEQS (profile)
members     = ["spark-a", "spark-b"]
exclusive_group = "spark-pair"
connect_timeout_s = 5
ttft_timeout_s    = 60
prefill_tps_floor = 1500                      # VERIFY: ttft deadline += est_prompt_tokens / floor
idle_timeout_s    = 180
labels      = ["remote", "gb10", "tp2"]

[nodes.sparks-tf]                             # same boxes, TensorFold — mutually exclusive with vLLM
url         = "http://100.101.102.103:8000"
engine      = "tensorfold"                    # never gets a key; MUST be firewalled to the rig (DGX §7)
enabled     = false
exclusive_group = "spark-pair"
slots       = 4

[nodes.ti]                                    # 2× RTX 3090 Ti, llama.cpp --tensor-split
url         = "http://100.64.10.20:8080"
engine      = "llama"
key_file    = "~/.config/openbeast/hydra/ti.key"
slots       = 2
connect_timeout_s = 5
labels      = ["remote", "ampere"]

# ─────────────────────── deployments (strict ids) ───────────────────────
[deployments."qwen38-unc-q5@rig"]
node     = "rig"
upstream = "qwen38-27b-uncensored-mtp-q5"
ctx      = 262144
family   = "qwen3.8-27b-uncensored"
caps     = ["tools", "json_schema", "grammar", "reasoning_budget", "vision", "id_slot"]

[deployments."qwen38-nvfp4@sparks"]
node     = "sparks"
profile  = "qwen38-27b-nvfp4-vllm"            # upstream/ctx/engine from scripts/backends/models/*.env
family   = "qwen3.8-27b"                      # STOCK (not abliterated) — different family
caps     = ["tools", "json_schema", "reasoning_budget"]
conformance = "required"                      # default for non-loopback nodes anyway

[deployments."qwen38-mlx4@sparks-tf"]
node     = "sparks-tf"
profile  = "qwen38-27b-mlx4-tensorfold"
family   = "qwen3.8-27b"
caps     = ["tools"]                          # no json_schema on TensorFold

[deployments."qwen36-a3b-q4@ti"]
node     = "ti"
upstream = "qwen36-35b-a3b-q4"
ctx      = 131072                             # VERIFY on 48 GB
family   = "qwen3.6-35b-a3b"
caps     = ["tools", "json_schema", "grammar", "reasoning_budget", "id_slot"]

# ────────────────────────── routes (virtual ids) ──────────────────────────
# Lower priority = tried first. Inside a group: affinity, then least (inflight+1)/slots/weight.
[routes.beast]
description = "Daily driver: the champion, spill to the Sparks when the 5090 is busy"
targets = [
  { d = "qwen38-unc-q5@rig",   priority = 0 },
  { d = "qwen38-nvfp4@sparks", priority = 1 },
]
spill        = true
same_family  = false          # OPEN DECISION (§10): true = never answer `beast` with a stock model
affinity     = "sticky"       # a conversation that spilled stays where its cache is while healthy
aliases      = ["qwen-27b-q5", "qwen38-27b-uncensored-mtp-q5", "default", "local"]

[routes."beast:max"]
description = "Quality-first: wait for the best model rather than spill down"
targets = [ { d = "qwen38-unc-q5@rig", priority = 0 } ]   # a Sparks flagship joins ONLY after a paired eval win
spill   = false

[routes."beast:fast"]
description = "Throughput: the MoE on the Ti rig, then anything free"
targets = [
  { d = "qwen36-a3b-q4@ti",    priority = 0 },
  { d = "qwen38-nvfp4@sparks", priority = 1 },
  { d = "qwen38-unc-q5@rig",   priority = 2 },
]

[routes."beast:long"]
description = "Long context"
targets = [
  { d = "qwen38-nvfp4@sparks", priority = 0, weight = 2 },
  { d = "qwen38-unc-q5@rig",   priority = 0 },
]
min_ctx = 200000

[routes."beast:vision"]
targets = [ { d = "qwen38-unc-q5@rig", priority = 0 } ]
require = ["vision"]

[routes.classify]                             # agents/router.py sends model:"classify" (ROUTER_CLASSIFY_MODEL)
description = "Grammar-constrained spawn classifier — a routing DECISION, so a full 27B"
# Decisions go to a full, uncensored dense 27B-class model, never a small or
# ~3B-active MoE (hydra.toml.example; pinned in tests/test_hydra_core.py).
targets = [
  { d = "qwen38-unc-q5@rig",   priority = 0 },
]
require      = ["json_schema"]
affinity     = "none"
max_attempts = 2
listed       = false

# ─────────────────────────────── rules ───────────────────────────────
# Evaluated top to bottom for NON-strict requests. For each `then` key the FIRST rule that sets it wins.
[[rules]]
name = "images-need-vision"
when = { has_images = true, model = ["beast", "beast:fast"] }
then = { route = "beast:vision" }

[[rules]]
name = "huge-prompts-go-long"
when = { min_prompt_tokens = 120000, model = ["beast", "beast:fast"] }
then = { route = "beast:long" }

[[rules]]
name = "guests-off-the-5090"
when = { role = "user" }                      # trusted only with X-Hydra-Caller (router)
then = { ignore_nodes = ["rig"] }

[[rules]]
name = "phone-stays-on-fast"
when = { device = "max-phone" }               # trusted only with X-Hydra-Caller (gate)
then = { route = "beast:fast" }
```

**Implicit config.** When `HYDRA=true` and the file is missing, hydra builds the following from the environment and logs it once:

- one node `rig` with `url = INFERENCE_URL`, `engine = INFERENCE_BACKEND`, `slots = INFERENCE_SLOTS or 1`, and `key_env = LLAMA_API_KEY`;
- one deployment `local@rig` with `upstream = INFERENCE_MODEL or "local"`, `ctx = 0` (unknown, so the ctx filter is off) and `caps = all`;
- one route `beast` aliased to `qwen-27b-q5`, `default` and `local`.

`python3 agents/hydra.py --print-default-config > hydra.toml` writes it out as a starting file.

#### 6.2.2 Field tables

These tables work as a JSON schema. Validation rejects unknown keys at every level.

**Top level**

| Key | Type | Req | Default | Notes |
|---|---|---|---|---|
| `schema` | int | yes | n/a | Must be `1` |
| `hydra` | table | no | defaults | |
| `nodes` | table of Node | yes (≥1) | n/a | |
| `deployments` | table of Deployment | yes (≥1) | n/a | |
| `routes` | table of Route | yes (≥1) | n/a | Must contain `default_route` |
| `rules` | array of Rule | no | `[]` | |

**`[hydra]`**

| Key | Type | Default | Constraint |
|---|---|---|---|
| `default_route` | str | `"beast"` | Must name a route |
| `unknown_model` | enum `default\|404` | `default` | |
| `inbound_key_env` | str | `LLAMA_API_KEY` | Env var name; an empty value means no inbound auth |
| `probe_interval_s` | float | 5 | 1 to 60 |
| `probe_down_interval_s` | float | 30 | ≥ `probe_interval_s` |
| `models_interval_s` | float | 60 | 10 to 3600 |
| `down_after` / `up_after` | int | 2 / 2 | 1 to 10 |
| `pre_commit_budget_s` | float | 580 | `< gate_read_timeout` (env `OPENBEAST_EDGE_READ_TIMEOUT`, default 600). Also warn when ≥ 590, because the OpenAI SDK default timeout is 600 |
| `chars_per_token` | float | 3.0 | 1.5 to 6 |
| `ctx_margin` | float | 0.05 | 0 to 0.5 |
| `default_max_tokens` | int | 4096 | ≥ 1 |
| `list_deployments` | bool | true | |
| `affinity_ttl_s` / `affinity_max` | float / int | 1800 / 4096 | |
| `audit` | path | `.run/hydra-audit.jsonl` | Relative to the repo |
| `audit_max_mb` | int | 50 | Rotates to `.1` |
| `breaker.fail_threshold` / `open_s` / `success_threshold` | int / float / int | 5 / 30 / 2 | |

**`[nodes.<id>]`** (id must match `^[a-z0-9][a-z0-9_-]{0,31}$`)

| Key | Type | Req | Default | Constraint |
|---|---|---|---|---|
| `url` | str | yes | n/a | `http(s)://host:port`, no path. The host must be loopback, RFC1918, or tailnet (`100.64.0.0/10`, `*.ts.net`) unless `allow_public = true` |
| `engine` | enum `llama\|vllm\|tensorfold\|openai` | yes | n/a | `openai` means generic: any 200 on `/health` is ready |
| `enabled` | bool | no | true | |
| `key_env` / `key_file` | str | no | none | At most one. A `key_file` must be mode 0600 and owned by the user. Either one on `tensorfold` is an **error** |
| `slots` | int | no | 1 | ≥ 1. The saturation point |
| `connect_timeout_s` | float | no | 3 (loopback) / 5 | |
| `ttft_timeout_s` | float | no | 120 | ≤ `pre_commit_budget_s` |
| `prefill_tps_floor` | float | no | none | When set, `ttft = min(ttft_timeout_s + est_prompt/floor, pre_commit_budget_s)` |
| `idle_timeout_s` | float | no | 120 | `< gate_read_timeout` |
| `nonstream_timeout_s` | float | no | = `pre_commit_budget_s` | Whole-response deadline for non-stream requests |
| `gpu_lease` | bool | no | false | Only allowed when `url` is loopback |
| `exclusive_group` | str | no | none | At most one **enabled** node per group |
| `members` | [str] | no | [] | Informational |
| `labels` | [str] | no | [] | Informational; rules may use them later |
| `allow_public` | bool | no | false | Doctor warns when true |

**`[deployments.<id>]`** (id must match `^[a-z0-9][a-z0-9._:-]*(@[a-z0-9][a-z0-9_-]*)?$`, and it must not collide with any route id or alias)

| Key | Type | Req | Default | Constraint |
|---|---|---|---|---|
| `node` | str | yes | n/a | An existing node |
| `profile` | str | no | none | Loaded through `obprofile.load()`. Its `BACKEND` must equal the node's engine. It supplies `upstream = SERVED_MODEL_NAME` and `ctx = MAX_MODEL_LEN`. When `slots` is not set on the node, it also warns if `MAX_NUM_SEQS` differs from the node's `slots` |
| `upstream` | str | yes, unless `profile` | from profile | Stated in addition to a profile: must equal it |
| `ctx` | int | yes, unless `profile` | from profile | `0` means unknown, and then the ctx filter is skipped with a warning |
| `family` | str | no | = upstream | Used by `same_family` |
| `caps` | [enum] | no | `[]` | `tools, json_schema, grammar, vision, reasoning_budget, id_slot, embeddings`. `id_slot` or `grammar` on a non-llama engine is an **error** |
| `conformance` | enum `required\|advisory\|off` | no | `required` if the node URL is non-loopback, else `off` | `required`: the deployment is not routable without a passing report (§6.5) |
| `enabled` | bool | no | true | |
| `listed` | bool | no | = `list_deployments` | |

**`[routes.<id>]`** (id must match `^[a-z0-9][a-z0-9._:-]{0,63}$`)

| Key | Type | Req | Default | Notes |
|---|---|---|---|---|
| `targets` | [{d, priority=0, weight=1.0}] | yes, non-empty | n/a | Each `d` must be an existing deployment. `weight` > 0 |
| `aliases` | [str] | no | [] | Globally unique across routes and deployments |
| `spill` | bool | no | true | |
| `same_family` | bool | no | false | When true, candidates must share the family of the highest-priority target |
| `affinity` | enum `session\|sticky\|none` | no | `session` | |
| `require` | [cap] | no | [] | |
| `min_ctx` | int | no | 0 | |
| `max_attempts` | int | no | 3 | 1 to 6 |
| `retry_on_ttft_timeout` | bool | no | false | |
| `description` | str | no | "" | Shown in `/v1/models` metadata |
| `listed` | bool | no | true | |

**`[[rules]]`**

| Key | Type | Notes |
|---|---|---|
| `name` | str, required, unique | Recorded in the audit |
| `when.model` | str or [str] | Matches the *requested* route or alias id |
| `when.device` / `when.role` | str or [str] | Matched **only** with a trusted caller; otherwise the rule does not match |
| `when.has_images` / `has_tools` / `needs_json_schema` | bool | |
| `when.min_prompt_tokens` | int | Uses the estimate |
| `when.hours` | `"HH:MM-HH:MM"` | Rig local time; may wrap midnight |
| `then.route` | str | Applied once; no chaining. The target route must exist |
| `then.only_nodes` / `then.ignore_nodes` | [node] | Must exist |
| `then.require` | [cap] | |
| `then.prefer` | [deployment] | Moved to priority −1 |

**Validation errors (fail closed):**

- unknown key; wrong type; dangling reference
- a route with no targets, or a missing `default_route`
- alias collisions
- a profile whose engine does not match the node
- a key on TensorFold; a key file that is not mode 0600
- `id_slot` or `grammar` caps on a non-llama engine
- two enabled nodes in one `exclusive_group`
- `gpu_lease` on a non-loopback node
- a public URL without `allow_public`
- a budget at or above the gate timeout
- `ttft_timeout_s` greater than the budget
- a rule whose `then.route` does not exist
- an invalid `hours` value

**Warnings:**

- a remote non-TensorFold node with no key
- `allow_public`
- `ctx = 0`
- a rule setting `then.route` with no `when.model` scope (it would redirect *every* route)
- a route whose targets are all disabled
- a budget ≥ 590

#### 6.2.3 Config hash

`config_hash = sha256(json.dumps(normalized_config, sort_keys=True, separators=(",",":")))[:12]`.

Key file *contents* are excluded; paths and presence are included. The hash is stamped on every response (`X-Hydra-Config`), every audit line and `/hydra/status`.

### 6.3 Endpoints

| Method and path | Auth | Behaviour |
|---|---|---|
| `GET /health` | none | 200 `{"status":"ok"}` when `default_route` has at least one routable deployment. Otherwise 503 `{"status":"loading","hydra":"no routable deployment for <route>"}`. This is llama-shaped, so `ob_llama_ready` works unchanged |
| `GET /v1/models` | inbound key | `{"object":"list","data":[…]}` in this order: the default route first, then the other listed routes, then listed deployments. Each entry has `id`, `object:"model"`, `owned_by:"hydra"`, and `openbeast:{kind:"route"\|"deployment", strict, description, caps, max_ctx, healthy, targets:[…]}` |
| `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings` | inbound key | Routed proxy (§6.4 to §6.6) |
| `GET/POST /pin/{d}/v1/{models,chat/completions,completions,embeddings}` | inbound key | Strict proxy to `{d}`. `/v1/models` returns only `{d}` |
| `GET /pin/{d}/health`, `GET /pin/{d}/props`, `GET /pin/{d}/slots` | inbound key | Passthrough for llama deployments; 404 otherwise. This lets the run_eval `--jobs` clamp and `capture` work for a pinned llama deployment |
| `GET /hydra/status` | local token | Config hash, `loaded_at`, `last_reload_error`, nodes, deployments, routes, and the last 50 decisions (no prompt text) |
| `POST /hydra/explain` | local token | Body = a request body, plus optional `{"headers":{…}}`. Returns the Decision trace without dispatching |
| `GET /hydra/decisions?n=200` | local token | Ring buffer of traces |
| `POST /hydra/reload` | local token | Validates, then swaps atomically. Returns the result |
| `POST /hydra/drain/{node}` / `/hydra/undrain/{node}` | local token | Held in memory, logged, and shown in status |
| `GET /hydra/metrics` | local token | Prometheus text |
| anything else | n/a | 404 `{"error":{"type":"hydra_not_routed","message":"hydra routes /v1 only; address a node directly or use /pin/<deployment>/"}}` |

**Local token.** The token is minted per start at `.run/hydra-local.token` (0600), with the header `X-OpenBeast-Local`, following the same pattern as `edge.py:_local_token`/`_is_local`, including the byte-wise `compare_digest`. That code is factored into `agents/_localtoken.py` or copied (it is about 25 lines).

**Caller token.** `.run/hydra-caller.token` is minted by `start.sh` before hydra, the gate and the router launch, and it is reused by watchdog restarts. It is sent as `X-Hydra-Caller`.

### 6.4 Request pipeline and routing algorithm

```python
# agents/hydra_core.py — pure; the same code runs /hydra/explain and the proxy.

def extract_features(path, body, headers, cfg) -> Features:
    msgs  = body.get("messages") or body.get("prompt") or body.get("input") or ""
    text  = json.dumps(msgs, ensure_ascii=False) + json.dumps(body.get("tools") or [])
    rf    = body.get("response_format") or {}
    return Features(
        path=path, model=str(body.get("model") or ""), stream=bool(body.get("stream")),
        has_images=_any_image_part(body.get("messages")),
        has_tools=bool(body.get("tools")),
        needs_json_schema=rf.get("type") in ("json_schema", "json_object") or "json_schema" in body,
        needs_grammar="grammar" in body,
        needs_reasoning_budget="reasoning_budget_tokens" in body,
        needs_embeddings=path.endswith("/embeddings"),
        est_prompt_tokens=ceil(len(text) / cfg.chars_per_token),
        max_tokens=int(body.get("max_tokens") or body.get("max_completion_tokens") or cfg.default_max_tokens),
        session_key=(headers.get("x-conversation-id") or headers.get("x-hydra-session")
                     or sha1(first_system + first_user)[:16] or None),
        pin=headers.get("x-hydra-pin"))

def decide(cfg, state, f, caller, now, pin=None) -> Decision:
    t = Trace(requested=f.model)
    # 1. resolve
    pin = pin or f.pin
    if pin or f.model in cfg.deployments:
        d = cfg.deployments.get(pin or f.model)
        if d is None: return Decision.err(404, "hydra_unknown_deployment", t)
        return _strict(cfg, state, f, d, t)            # no rules / spill / fallback
    route = cfg.route_by_id_or_alias.get(f.model)
    if route is None:
        if cfg.unknown_model == "404": return Decision.err(404, "hydra_unknown_model", t)
        route = cfg.routes[cfg.default_route]; t.note("unknown id → default_route")
    # 2. rules (first setter wins per key; device/role only if caller.trusted)
    eff = {}
    for r in cfg.rules:
        if _matches(r.when, f, caller, now, requested=route.id):
            for k, v in r.then.items(): eff.setdefault(k, (v, r.name))
            t.rule(r.name)
    if "route" in eff: route = cfg.routes[eff["route"][0]]         # one hop
    need = set(route.require) | set(eff.get("require", ((),))[0]) | _implied_caps(f)
    # 3. candidates + filters (every exclusion recorded with a reason string)
    cands = []
    for tg in route.targets:
        d, n = cfg.deployments[tg.d], cfg.nodes[cfg.deployments[tg.d].node]
        prio = -1 if tg.d in eff.get("prefer", ((),))[0] else tg.priority
        why = _exclude(d, n, state, f, need, route, eff)   # None or "ctx 262144 < need 270000" …
        (t.excluded(tg.d, why) if why else cands.append(Candidate(d, n, prio, tg.weight)))
    if route.same_family and cands:
        fam = cfg.deployments[min(route.targets, key=lambda x: x.priority).d].family
        for c in [c for c in cands if c.d.family != fam]:
            cands.remove(c); t.excluded(c.d.id, f"family {c.d.family} != {fam}")
    if not cands: return Decision.err(503, "hydra_unavailable", t, retry_after=5)
    # 4. select
    groups = sorted({c.prio for c in cands})
    def load(c): return (state.inflight(c.d.id) + 1) / c.n.slots / c.weight
    def unsat(c): return state.inflight(c.d.id) < c.n.slots
    sticky = state.affinity.get(f.session_key) if route.affinity != "none" else None
    ordered = []
    if route.affinity == "sticky" and sticky:
        c = next((c for c in cands if c.d.id == sticky and unsat(c)), None)
        if c: ordered.append(c); t.note("affinity: sticky hit")
    chosen_group = None
    for g in groups:
        live = [c for c in cands if c.prio == g]
        free = [c for c in live if unsat(c)]
        if free:
            chosen_group = g
            aff = next((c for c in free if c.d.id == sticky), None) if route.affinity == "session" else None
            ordered += ([aff] if aff else []) + sorted(free, key=lambda c: (load(c), random()))
            break
        if not route.spill: chosen_group = g; ordered += sorted(live, key=load); break
    if chosen_group is None:          # everything saturated everywhere: queue at the PREFERRED group's engine
        ordered += sorted([c for c in cands if c.prio == groups[0]], key=load)
        t.note("all saturated → engine queue on preferred group")
    # alternates = the rest, preferred-first, for pre-commit failover
    rest = sorted([c for c in cands if c not in ordered], key=lambda c: (c.prio, load(c)))
    plan = _dedupe(ordered + rest)[: route.max_attempts]
    return Decision(route=route.id, attempts=plan, rules=t.rules, trace=t)
```

**`_exclude` returns a reason when any of these hold:**

- `node.enabled` is false; the node is drained (`manual`, `lease` or `config`); an `exclusive_group` peer is enabled (caught by validation)
- the deployment state is not in `{READY}` and it is not a HALF_OPEN trial slot
- the breaker is OPEN
- the conformance requirement is not met
- `need − effective_caps` is not empty
- `f.est_prompt_tokens + f.max_tokens > ctx × (1 − ctx_margin)` (skipped when `ctx == 0`), or `ctx < route.min_ctx`
- the node is outside `only_nodes`, or inside `ignore_nodes`

**Implied caps:**

| Feature | Cap |
|---|---|
| `has_images` | `vision` |
| `has_tools` | `tools` |
| `needs_json_schema` | `json_schema` |
| `needs_grammar` | `grammar` |
| `needs_reasoning_budget` | `reasoning_budget` |
| `needs_embeddings` | `embeddings` |

**`_strict`** applies the same filters to a single deployment.

- A failed capability returns **422** `hydra_pin_incompatible`. Hydra never strips a field to make it fit.
- A failed health, drain or conformance check returns **503** `hydra_pinned_unavailable`.
- Otherwise the plan has one attempt.

**Affinity update.** When a request commits to a deployment, `affinity[session_key] = deployment`. The table is an LRU with a TTL.

### 6.5 Health model

**Per-deployment state:**

```
UNKNOWN ─probe ok ×up_after──► READY ◄──────────── probe ok ×up_after ────┐
   │                            │  ▲                                        │
   │ llama 503 "Loading"        │  └─ HALF_OPEN ─ok×success_threshold──┐    │
   ▼                            │        ▲                             │    │
LOADING ─probe ok──► READY      │   open_s elapsed                     │    │
                                │        │                             │    │
                   request fails×fail_threshold ──► OPEN ──────────────┘    │
                   probe fails ×down_after ──────► DOWN ────────────────────┘
AUTH_FAILED   (401/403 on /v1/models or a proxied request) — excluded; re-probed at probe_down_interval_s
MISMATCH      (upstream id not in /v1/models; or 404 model-not-found from a strict-name engine)
Flags (orthogonal): drained{manual|lease}, conformance{pass|fail|missing|n/a}
```

**Probes.** Node health is probed once per node, then fanned out to its deployments.

| Probe | Request | Interval | Result |
|---|---|---|---|
| Readiness | `GET /health`, no key, 3 s | 5 s while READY or LOADING; 30 s while DOWN or AUTH_FAILED | `engine_ready(engine, status, body)` → ready / loading / down |
| Model list | `GET /v1/models` with the node key (no key for TensorFold), 5 s | 60 s, and on every transition to READY | Each deployment's `upstream` must be listed, otherwise MISMATCH. 401/403 → AUTH_FAILED |
| Lease | `scripts/gpu-lease.sh check` (subprocess, 3 s) | Every probe tick, rig `gpu_lease` nodes only | Exit 4 → drained(lease). Exits 0 and 3 → not drained. Any other exit → not drained, plus a warning |
| Conformance | Read `.run/conformance/<deployment-id>/latest.json` | Load, reload, and every 60 s (mtime) | `ok == true` and `url` host:port equals the node URL → pass. `facts`/`results` for `tools` == fail → remove `tools` from effective caps |

**`engine_ready` rules**, which must match `ob_backend_ready`:

- `llama`: `status == 200` and `json(body).status == "ok"` → ready. `status == 503` and the body contains "Loading model" → loading. Anything else → down.
- `vllm`: 200 → ready, anything else → down.
- `tensorfold`: 200 and (`"ok": true` or `"status": "ok"`, using the same regex as bash) → ready, anything else → down.
- `openai`: 200 → ready.

**Breaker.**

- Counted failures: connect error or timeout, 5xx, TTFT timeout, and mid-stream upstream failure.
- Not counted: 4xx (except 401/403/404-model, which set their own state) and client disconnect.
- Probes never count toward closing the breaker, so real traffic has to prove recovery.
- A HALF_OPEN deployment admits exactly one in-flight trial request.

**Boot and readiness.** `/health` is ok only when the default route is routable. A Spark that is down never blocks rig boot.

### 6.6 Proxy semantics

**Body.** Hydra reads the request body up to 32 MiB (the gate's `MAX_BODY_BYTES`); larger bodies get 413. Invalid JSON gets 400 `hydra_bad_request`.

The forwarded body is the original parsed object with only these edits, re-serialized:

- `model` becomes `deployment.upstream`.
- `id_slot` is kept only when the engine is llama, `id_slot` is in the deployment's caps, and `0 ≤ id_slot < node.slots`. Otherwise it is removed and the removal is recorded in the trace.

Nothing else changes: sampling, `chat_template_kwargs`, `reasoning_budget_tokens`, `stream_options`, `max_tokens` and message content are untouched. This is asserted by a property test.

**Headers to the upstream:**

- Hop-by-hop headers are dropped. So are `Authorization`, `X-Hydra-*`, and `X-OpenBeast-Local`.
- `X-OpenBeast-Device` and `X-OpenWebUI-User-*` are forwarded only when the caller is trusted, and dropped otherwise.
- The node key is injected as `Authorization: Bearer …`, never for TensorFold.
- `X-Conversation-Id` is forwarded as is.
- `X-OpenBeast-Request-Id` is added (the gate's id, or a new one).

**Commit point = the first upstream body byte**, for streaming requests. For non-streaming requests it is the complete upstream body, which is buffered up to 16 MiB before hydra replies.

| Upstream outcome before commit | Action |
|---|---|
| Connect error or connect timeout | Breaker failure. Try the next attempt |
| `/health`-style 503 "Loading model", or 502, 503 or 504 | Failure (a llama 503 loading also sets LOADING). Next attempt |
| Other 5xx | Failure. Next attempt |
| 401 or 403 | Node → AUTH_FAILED. Next attempt. If none remain: **502** `hydra_upstream_auth`. The node's 401 is never shown to the caller |
| 404 whose body names the model (vLLM or TensorFold strict names) | Deployment → MISMATCH. Next attempt |
| 429 | Next attempt, with no breaker failure |
| Headers 2xx, then the connection drops before the first body byte | Failure. Next attempt |
| TTFT deadline hit (streaming: no first byte within `effective_ttft`; non-stream: no complete body within `nonstream_timeout_s`) | Breaker failure. **504** `hydra_timeout` unless `route.retry_on_ttft_timeout` |
| Any other 4xx (400, 413, 422 …) | **Pass through**: status, body bytes and `content-type` unchanged, plus `X-Hydra-*` headers. No failover |
| 2xx with a first body byte | **Commit**: send headers (upstream headers minus hop-by-hop, plus `X-Hydra-*`), then stream |

Every attempt is bounded by `pre_commit_budget_s` minus the time already spent. A strict plan has exactly one attempt.

**After commit (streaming):**

- Hydra relays chunk by chunk with no buffering beyond one chunk, and upstream reads are paced by client writes.
- The idle gap is capped at `idle_timeout_s`.
- On an upstream transport error or idle timeout:
  - hydra emits `data: {"error":{"message":"upstream <node> failed mid-stream","type":"hydra_upstream_error","code":"upstream_failed_midstream","hydra_deployment":"<d>","request_id":"<id>"}}\n\n`;
  - it closes **without `data: [DONE]`**;
  - it records a breaker failure and the audit outcome `upstream_failed_midstream`.
- There is never a replay.

**Client disconnect.** Hydra closes the upstream response, which makes the engine abort and frees the slot. It releases in-flight counts and records `client_disconnect` in the audit, which does not count against the node.

**Release discipline.** Each attempt holds an `Admission` whose `release()` is idempotent. It is called from `finally` on every path, including `CancelledError`, mirroring `edge.py:827-1057`.

**Usage.** For the audit only, hydra takes `usage` from the non-stream JSON, or from the last SSE `data: {` line containing `"usage"` (the gate's `_usage_from_sse` tail approach).

**Hydra's own errors** use one shape, which matches the gate's error JSON:

```json
{"error":{"message":"no routable deployment for route beast:max (qwen38-unc-q5@rig: DOWN; …)","type":"hydra_unavailable","hydra":{"route":"beast:max","excluded":{…},"config":"a1b2c3d4e5f6"}}}
```

It is sent with `Retry-After: 5` on 503.

| Type | Status |
|---|---|
| `hydra_unavailable` | 503 |
| `hydra_pinned_unavailable` | 503 |
| `hydra_pin_incompatible` | 422 |
| `hydra_unknown_model` | 404 |
| `hydra_unknown_deployment` | 404 |
| `hydra_timeout` | 504 |
| `hydra_upstream_auth` | 502 |
| `hydra_upstream_error` | 502, pre-commit exhaustion |
| `hydra_unauthorized` | 401, bad inbound key |
| `hydra_bad_request` | 400 |

**Timeout invariants**, checked at load:

- `pre_commit_budget_s < gate_read_timeout`
- every node: `ttft_timeout_s ≤ pre_commit_budget_s` and `idle_timeout_s < gate_read_timeout`
- `nonstream_timeout_s ≤ pre_commit_budget_s`

`gate_read_timeout` is read from `OPENBEAST_EDGE_READ_TIMEOUT`, default 600.

**Connection pools.** Hydra keeps one `httpx.AsyncClient` per node, with `Timeout(None, connect=node.connect_timeout_s, read=None, pool=5)`. It enforces deadlines itself with `asyncio.wait_for` on the first byte and on each chunk read, because TTFT and idle limits differ. It uses `limits=httpx.Limits(max_connections=slots*4, max_keepalive_connections=slots)`.

### 6.7 Integration changes

#### conf.sh (new block, placed after the INFERENCE block and before `MODEL_URL`)

| Key | Env override | Default | Effect |
|---|---|---|---|
| `HYDRA` | `OPENBEAST_HYDRA` | `false` | Master switch, normalized to true or false |
| `HYDRA_PORT` | `OPENBEAST_HYDRA_PORT` | `8095` | Always bound on 127.0.0.1 |
| `HYDRA_CONFIG` | `OPENBEAST_HYDRA_CONFIG` | `$REPO_DIR/hydra.toml` | Relative paths are resolved against the repo |
| `HYDRA_DEFAULT_MODEL` | `OPENBEAST_HYDRA_DEFAULT_MODEL` | `beast` | The id agents send |
| `HYDRA_READY_GRACE` | `OPENBEAST_HYDRA_READY_GRACE` | `60` | Seconds start.sh waits for `ob_hydra_ready` |

Derived when `HYDRA=true`:

- `HYDRA_URL=http://127.0.0.1:$HYDRA_PORT`; export `OPENBEAST_HYDRA_URL`, `OPENBEAST_HYDRA_CONFIG` and `OPENBEAST_HYDRA_PORT`.
- `CONSUMER_BASE=$HYDRA_URL` (otherwise `CONSUMER_BASE=$INFERENCE_URL`); export `OPENBEAST_CONSUMER_BASE`.
- `MODEL_URL`: the router first when on (unchanged), else `http://localhost:$HYDRA_PORT/v1`. The `localhost` spelling follows the `conf.sh:515-517` convention.
- `AGENT_INFERENCE_URL`: an explicit value wins, else `$HYDRA_URL/v1`.
- `OPENBEAST_INFERENCE_MODEL=$HYDRA_DEFAULT_MODEL` for **every** backend. `runner.py:51` then sends a routable id with no edit.

When `HYDRA=false`, **every derived variable is byte-identical to today**. The wiring test asserts this.

#### backend.sh

`ob_hydra_ready <url>` is `ob_llama_ready "$url"`: the same `200 {"status":"ok"}` shape, exposed under its own name for readability and future divergence.

#### start.sh

1. After sourcing conf, set `CONSUMER_BASE="${OPENBEAST_CONSUMER_BASE:-$LLAMA_BASE}"`. `LLAMA_BASE` still means the local engine.
2. When `HYDRA=true`, run `python3 agents/hydra.py --check "$HYDRA_CONFIG"`, or the implicit-config check when the file is absent. On failure, **exit 1** with the errors. Never silently bypass hydra.
3. Mint `.run/hydra-caller.token` (umask 077, 32 hex bytes from `python3 -c 'import secrets;print(secrets.token_hex(32))'`).
4. Launch hydra using the router/gate block pattern (`start.sh:776-830`): env, then `python3 agents/hydra.py >> .run/hydra.log 2>&1 &`, then `.run/hydra.pid`, then poll until the process answers `/health` with any status (≤ 10 s). Placement:
   - **Managed path:** right after the local engine is launched and before the existing readiness wait at `:627`, so hydra observes the engine loading.
   - **Unmanaged path:** before the wait at `:683`. When `HYDRA=true` that wait polls `ob_hydra_ready "$HYDRA_URL"` instead of `ob_backend_ready "$LLAMA_BASE"`.
5. After the local wait (managed), wait up to `HYDRA_READY_GRACE` for `ob_hydra_ready`. On timeout, print a loud WARNING that includes `scripts/hydra.sh status`, and continue, as the existing inference wait does.
6. The router (`:782`) and gate (`:807`) get `OPENBEAST_LLAMA_UPSTREAM="$CONSUMER_BASE"` and `OPENBEAST_HYDRA_CALLER_TOKEN_FILE=.run/hydra-caller.token`.
7. The router gets `ROUTER_CLASSIFY_MODEL=classify` when `HYDRA=true` and the config has a `classify` route. Otherwise it is unset, and the call resolves to the default route.
8. The status block prints `beast-hydra: http://127.0.0.1:8095 (N nodes, M routable routes)`. `--status` reports hydra's pid and health.
9. The unmanaged idle loop (`:1056-1070`) reports `ob_hydra_ready` when `HYDRA=true`.

#### stop.sh

`pkill -f "$(_ob_ere "$REPO_DIR/agents/hydra.py")"`, using the same pattern as the edge and router lines at `:93-96`, then remove `.run/hydra.pid`. It never contacts nodes.

#### healthcheck.sh

- **New hydra section:**
  - `GET /health` gives three outcomes: 200 is OK; 503 is "up, no routable default" (a WARN, no restart); no answer within 3 s is DOWN.
  - `GET /hydra/status` with the local token gives a table per deployment (state, in-flight/slots, breaker, drain).
  - `--restart` restarts hydra **only when it does not answer**. A restart would not fix a 503.
- **Fix the latent bug at `:396`:** `OPENBEAST_LLAMA_UPSTREAM="${OPENBEAST_CONSUMER_BASE:-$INFERENCE_URL}"`, plus the caller-token env var. The router restart path, if any, gets the same fix.

#### doctor.sh

New "hydra" section, printed only when `HYDRA=true`:

| Row | PASS | WARN | FAIL |
|---|---|---|---|
| hydra config | `--check` ok | Validation warnings | Validation errors |
| hydra process | `/health` 200 | 503 (no routable default) | Not answering |
| gate → hydra | `/gate/health` upstream == `HYDRA_URL` | n/a | Gate on but its upstream differs (drift) |
| per node: reachable | Probe ok | LOADING | DOWN |
| per node: key | Key set, file 0600 | Remote with no key | Key on TensorFold; file mode wrong; AUTH_FAILED |
| per node: address | Loopback, tailnet or RFC1918 | `allow_public` | n/a |
| per deployment: served id | In `/v1/models` | n/a | MISMATCH |
| per deployment: conformance | Pass, URL matches | Report older than 30 days; `advisory` and missing | `required` and missing or failing |
| per route | At least one routable | Only the last-priority group routable | None routable |
| publication | Gate on | n/a | Raw `tailscale serve` of an inference port while `HYDRA=true` |
| GPU lease | n/a | Lease held by another process and the loopback node lacks `gpu_lease=true` | n/a |

#### agents/edge.py (additive)

1. Forward `X-OpenBeast-Request-Id` upstream. It is already minted; add it to `_upstream_headers`.
2. If `OPENBEAST_HYDRA_CALLER_TOKEN_FILE` is readable, add `X-Hydra-Caller: <token>`. Cache it by mtime.
3. Add `x-hydra-caller` to `_CLIENT_SPOOFABLE`.

`ALLOWED_PATHS` is **unchanged**, so `/pin` and `/hydra` are never reachable remotely. A remote device may send `X-Hydra-Pin`: it only narrows routing.

New tests in `test_edge.py`: the request id reaches the upstream; the caller token is added when configured; a client's `X-Hydra-Caller` is stripped.

#### agents/router.py (additive)

1. `ROUTER_CLASSIFY_MODEL` env: if set, `_classify` adds `"model": <value>`.
2. Add `X-Hydra-Caller` (same file-based token) to proxied requests and to classify. The WebUI `X-OpenWebUI-User-*` headers are forwarded as today, so hydra trusts the role only because the router vouches for it.

New tests in `test_router.py`: the classify body carries the model when set; the caller token is attached.

#### setup-tailscale.sh

In the gate-off raw branch (`:433-438`), when `HYDRA=true`: refuse with "hydra holds node keys: enable EDGE_GATE=true to publish inference". Never publish `:8095`. The gate-on branch is unchanged, because the gate's upstream is already hydra.

#### Unchanged in the MVP (catalogued for NEXT)

- The dashboard `/api/slot` keeps describing the rig engine (the v2 contract, `test_beast_slot.py` untouched).
- `client.sh` still takes `data[0]` through the gate, which is now the default route `beast`. That is the correct id to send.
- `configure-webui.sh` works: `MODEL_URL` changes once, WebUI restarts once, and one row is created per listed id.
- opencode's tracked catalog ids resolve through `unknown_model = default`.
- beast-chat's `/health` probe works against hydra's llama-shaped health.

### 6.8 Security

- **Bind.** `127.0.0.1` only, not configurable. WebUI reaches it through `network_mode: host`.
- **Inbound authentication.** `Authorization: Bearer $LLAMA_API_KEY`, compared in constant time, as bytes. That is the key every existing caller already sends (WebUI, the gate's `OPENBEAST_API_KEY`, runner, router). The posture is identical to llama-server's today.
- **Node keys.**
  - Each node has its own key, from `key_env` or `key_file` (0600, owned by the user, checked at load and reload).
  - Keys are never logged. Status shows `key: set|none`, and the audit records a 8-hex sha256 fingerprint only.
  - The inbound key is never forwarded upstream.
  - A key is never sent to TensorFold.
- **Trusted identity.** `X-OpenBeast-Device` and `X-OpenWebUI-User-*` are trusted only together with `X-Hydra-Caller`. Otherwise they are stripped, and rules keyed on them do not match. The admin surface uses the local token for everything (F8).
- **SSRF.** Node URLs come only from config, validated to loopback, RFC1918 or tailnet. No request field can name a URL. `/pin/{d}` must name a configured deployment.
- **Remote-node posture** (documented in the runbook):
  - vLLM with `VLLM_API_KEY`, bound to the tailnet IP (spark-node.sh already refuses wildcard binds).
  - TensorFold firewalled to the rig's tailnet IP, or behind a node-local gate.
  - llama-server on the Ti rig with `--api-key` from a file.
  - A recommended Tailscale ACL allowing only `tag:openbeast-rig` to reach node ports.
  - llama RPC and NCCL never on the tailnet.
- **Stated limit.** Local processes (including model-authored bash on the tool server) share the user's trust domain. `.run/hydra-caller.token` protects against *remote* spoofing, not against local code. This is the same limit as `edge-local.token` today.
- **Supply chain.** No new packages. `tomllib` is stdlib. The lockfile and wheelhouse are unchanged. This is asserted by `test_pydeps_lock.py` staying green.

### 6.9 Observability

**Response headers** on every proxied or error response:

| Header | Value |
|---|---|
| `X-Hydra-Request-Id` | The gate's id or a new one |
| `X-Hydra-Route` | Route id, or `pin` |
| `X-Hydra-Rule` | Comma-separated rule names that fired |
| `X-Hydra-Deployment` | e.g. `qwen38-nvfp4@sparks` |
| `X-Hydra-Node` | Node id |
| `X-Hydra-Engine` | Engine |
| `X-Hydra-Upstream-Model` | The upstream id |
| `X-Hydra-Attempts` | e.g. `qwen38-unc-q5@rig:503,qwen38-nvfp4@sparks:200` |
| `X-Hydra-Config` | Config hash |

The body's `model` field is passed through as the engine returned it: the served id, as OpenRouter does.

**Audit** (`.run/hydra-audit.jsonl`, mode 0600, append-only, rotated to `.1`). One line per request. There is never any prompt or completion text; a test asserts that a sentinel string never appears.

```json
{"ts":"2026-10-02T21:14:03Z","request_id":"…","device":"max-mbp","role":null,"trusted":true,
 "requested":"beast","strict":false,"rules":["huge-prompts-go-long"],"route":"beast:long",
 "features":{"stream":true,"est_prompt_tokens":131020,"max_tokens":4096,"has_tools":true,"has_images":false},
 "excluded":{"qwen38-unc-q5@rig":"inflight 1/1 (saturated, spill)"},
 "attempts":[{"d":"qwen38-nvfp4@sparks","node":"sparks","engine":"vllm","status":200,"ttft_ms":8120,"outcome":"ok"}],
 "deployment":"qwen38-nvfp4@sparks","status":200,"outcome":"ok","ms":42011,
 "usage":{"prompt_tokens":129877,"completion_tokens":1204},"body_edits":["model"],"config":"a1b2c3d4e5f6"}
```

`outcome` is one of `ok | client_disconnect | upstream_failed_midstream | timeout | unavailable | pinned_unavailable | upstream_4xx | upstream_auth | bad_request`. The line joins to the gate's audit on `request_id`.

**`/hydra/status`** includes:

- `config_hash`, `loaded_at`, `last_reload_error`, `uptime_s`
- `nodes{}`: url host only, engine, enabled, drained (with reason), `key: set|none`, `last_probe{t, ms, result}`, probe failure streak
- `deployments{}`: state, breaker, in-flight/slots, effective caps, ctx, conformance, `served_total`, `fail_total`, TTFT EWMA, tok/s EWMA (displayed, not used for routing)
- `routes{}`: routable, candidate count per group
- `decisions[-50:]`

**Metrics** (hand-rolled Prometheus text):

- `hydra_requests_total{route,deployment,outcome}`
- `hydra_inflight{deployment}`
- `hydra_attempts_total{deployment,result}`
- `hydra_failover_total{from,to,reason}`
- `hydra_ttft_seconds_bucket{deployment}`
- `hydra_deployment_state{deployment,state}` (one-hot)
- `hydra_breaker_open{deployment}`
- `hydra_probe_seconds{node}`
- `hydra_config_info{hash}`

**CLI `scripts/hydra.sh`**, which reads the local token and uses `ob_curl` helpers:

| Command | What it does |
|---|---|
| `check [path]` | `agents/hydra.py --check` |
| `status` | Tables |
| `explain '<json>' \| -f file [-H 'Header: v']` | Decision trace |
| `reload` | Validate and swap |
| `drain <node>` / `undrain <node>` | |
| `tail` | Pretty-prints the audit |
| `decisions [n]` | Recent traces |
| `add-node <id> --url U --engine E [--key-file F] [--slots N] [--profile P --deployment D]` | Probes readiness and models with the key, then prints the TOML stanza to paste. It **never** edits `hydra.toml` itself. |
| `conformance <deployment> [--heavy]` | Runs `scripts/backends/conformance.sh --url <node> --backend <engine> --model <upstream> --key-file <f> --out .run/conformance/<deployment>` |
| `pin-smoke [deployment\|all]` | A 1-token non-stream plus a streaming chat per deployment via `/pin`, printing TTFT and headers |
| `sim` | Execs `scripts/hydra-sim.sh` |

### 6.10 Eval integrity

1. **Evals bypass hydra by default.**
   - `benchmark_all.py` launches serve scripts on `:8080` and talks to them directly.
   - Hydra is on `:8095` and never on `:8080`.
   - A rig node with `gpu_lease = true` is drained whenever a campaign holds the lease, so chat traffic cannot contend with an eval. That protection is new.
2. **Pointing `run_eval.py` at non-strict hydra fails visibly, not silently.**
   - `detect_model` gets `beast`, a route and not a slug, so the row is obviously mislabelled.
   - `/props` returns 404, so the jobs clamp warns.
   - `capture_server_config` finds no llama-server behind `:8095` and takes its WARNING path.
   - Doctor flags it. NEXT X9 turns this into a hard refusal.
3. **Strict paths are exact.** A deployment id, `X-Hydra-Pin`, or `/pin/<d>/v1` means one deployment, no rules, no spill, no failover and no field stripping (422 instead). When the deployment is down, the answer is 503 `hydra_pinned_unavailable`, never another model. `/pin/<d>/props` and `/pin/<d>/slots` pass through for llama deployments.
4. **Body fidelity.** The only edits are `model` and an invalid `id_slot`. They are recorded in the audit `body_edits` field and pinned by a property test.
5. **Provenance.** Headers plus the audit name the deployment, node, engine, upstream id and config hash for every request. Remote rows keep DGX plan §10 policy: every vLLM or TensorFold row is a new era. Hydra does not change era membership.
6. **Doctrine for "intelligence" features (from P3).** A routing policy that claims a quality or speed benefit is evaluated *as a model*: slug `hydra-<route>-<cfghash8>`, its own era, paired McNemar against the pinned champion. It ships as a default only when:
   - SCORE is non-inferior within 0.5 points;
   - the pre-registered speed or throughput win holds;
   - both hold on the hard tier and on zig.

### 6.11 Test plan (all hermetic; no second rig)

**Fake engine** (`tests/fakes/fake_engine.py`): stdlib `http.server` in a thread, like the conformance Stub, so its tests need no uvicorn. It also has a `__main__` for `hydra-sim.sh`.

- **Personalities** `llama | vllm | tensorfold`. Each sets:
  - the `/health` shape (llama: ok, plus 503 "Loading model" while loading; vLLM: empty 200; TensorFold: `{"ok":true}`);
  - `/v1/models`;
  - strict model names (vLLM 404 on an unknown id);
  - the reasoning field key;
  - the auth rule (TensorFold ignores keys);
  - `/props`, `/slots` and `/metrics` for llama; `/metrics` for vLLM.
- **Chat.** Non-stream JSON and SSE streaming (`chunks=N`, `tok_ms`), with a `usage` chunk when `stream_options.include_usage` is set.
- **Faults.** Set per request with `X-Fake-Fault: <mode>[:arg]`, or sticky with `POST /_fake/fault {"mode":…,"count":n}`:

  | Fault | Behaviour |
  |---|---|
  | `refuse` | Stop listening |
  | `loading` | Loading state |
  | `http_500` | 500 |
  | `http_503` | 503 |
  | `http_401` | 401 |
  | `http_404_model` | 404 naming the model |
  | `http_429` | 429 |
  | `overflow_400` | 400 with the exact overflow text for this personality; its texts are asserted to match `conformance.runner_overflow_re()` |
  | `ttft_ms:N` | Delay before the first byte |
  | `headers_then_close` | Send headers, then close |
  | `die_after_chunks:N` | Close after N chunks |
  | `stall_after_chunks:N:ms` | Stall after N chunks |
  | `wrong_model` | `/v1/models` lists another id |

- **Recording.** `GET /_fake/requests` returns the last 200 received `{path, headers, body}`, used to assert body fidelity, key swap and header stripping. `GET /_fake/inflight`.

**`tests/test_hydra_core.py`** (pure, fast):

- **Validation.**
  - The example config validates.
  - The implicit config comes from env.
  - One test per rejection in §6.2.2 (about 20, parametrized).
  - Warnings are emitted as specified.
  - The hash is stable under key reordering and changes on a semantic change.
- **Profiles.** Profile-derived fields come from `scripts/backends/models/qwen38-27b-nvfp4-vllm.env`. An explicit value that conflicts with the profile is rejected. A profile whose engine does not match the node is rejected.
- **`engine_ready` matrix** against `tests/fixtures/hydra/ready/*.json`.
- **Feature extraction.** Image parts, tools, json_schema vs json_object, grammar, reasoning budget, embeddings path, token estimate monotonic in length, session-key precedence.
- **Resolution.** Strict by id, strict by pin, route by id, route by alias, unknown → default, unknown → 404.
- **Rules.**
  - The first setter wins for each key.
  - A route rewrite happens once.
  - `hours` wraps midnight.
  - Device and role match only with a trusted caller.
  - A `prefer` target moves to priority −1.
- **Filters.** Each one yields its exact reason string: state, breaker OPEN, HALF_OPEN single trial, drained(lease), conformance required or missing, caps, ctx with margin, `ctx = 0` skip, `min_ctx`, only/ignore nodes.
- **Selection.**
  - Group 0 is preferred.
  - With `spill = true`, a saturated group 0 goes to group 1.
  - With `spill = false`, a saturated group queues on group 0.
  - When everything is saturated, the request goes to the preferred group.
  - Weights and least-load apply, with a seeded random tie-break.
  - Affinity: session hit within the group; sticky hit across groups; bypassed when saturated; `none`; TTL and LRU eviction.
  - `same_family` drops other families.
  - The plan respects `max_attempts`.
- **Health.**
  - Hysteresis: `down_after` and `up_after`.
  - LOADING is not DOWN and does not count toward the breaker.
  - The breaker walks CLOSED → OPEN → HALF_OPEN → CLOSED, and HALF_OPEN → OPEN on failure.
  - Probes do not close the breaker.
  - AUTH_FAILED and MISMATCH transitions.
- **`effective_ttft`** with and without `prefill_tps_floor`, capped by the budget.

**`tests/test_hydra_proxy.py`**: real uvicorn hydra on an ephemeral port, in front of fake engines, using an httpx client.

- **Streaming.** Passthrough is byte-identical (chunk contents and order). The OpenAI SDK consumes it fine.
- **Pre-commit failover.** On `refuse`, `loading`, `http_500`, `http_503`, `headers_then_close`, `http_429`, the request lands on the alternate, and `X-Hydra-Attempts` shows both.
- **Node auth.** `http_401` → AUTH_FAILED, then the alternate. With no alternate the client gets 502 `hydra_upstream_auth`, never 401.
- **Model mismatch.** `http_404_model` → MISMATCH, then the alternate.
- **4xx passthrough.**
  - `overflow_400` passes through with byte-identical status, body and content-type, for each personality.
  - `runner._CTX_OVERFLOW_RE` matches it, loaded read-only through `conformance.runner_overflow_re()`.
  - No failover occurs.
- **TTFT.** `ttft_ms` beyond the deadline gives 504 `hydra_timeout` with no failover. With `retry_on_ttft_timeout` it fails over.
- **Mid-stream.** `die_after_chunks:3` and `stall_after_chunks` produce the error event and no `[DONE]`. The breaker is incremented and the audit outcome is `upstream_failed_midstream`.
- **Strict.**
  - A pin to a DOWN deployment gives 503 `hydra_pinned_unavailable` even while alternates are healthy.
  - A pin whose caps don't fit gives 422.
  - `/pin/<d>/props` works for llama and is 404 for vLLM.
  - `/pin/<d>/v1/models` lists only `<d>`.
- **Body fidelity** (property test over about 30 bodies, including sampling params, `chat_template_kwargs`, `reasoning_budget_tokens`, `stream_options`, tools and images):
  - the upstream body equals the input except for `model` and the `id_slot` rule;
  - `id_slot` is kept on llama when in range, dropped when out of range, and dropped on vLLM and TensorFold.
- **Keys and trust.**
  - With the inbound key set, a missing or wrong key gets 401 in the gate's shape.
  - The node key reaches the right node.
  - TensorFold receives no `Authorization`.
  - The inbound key never appears upstream.
  - A spoofed `X-OpenBeast-Device` without the caller token is stripped, and device rules don't match. With the token, it is honoured.
- **Headers and audit.**
  - All provenance headers are present and correct.
  - The audit line is complete and schema-checked.
  - A sentinel prompt string never appears in the audit, status or decisions.
- **Catalog and health.**
  - `/v1/models` order and metadata; `listed = false` is respected.
  - `/health` returns 200 only while the default route is routable.
- **Drain.** A stub `gpu-lease.sh` (via the env override `OPENBEAST_HYDRA_LEASE_CMD`) returning 4 drains the rig. Admin drain and undrain work.
- **Reload.** A bad config keeps the old one and sets `last_reload_error`. A good reload swaps, and requests in flight finish on the old snapshot.
- **Concurrency.** A rig with 1 slot plus 2 concurrent `beast` requests puts one on each node (the spill is observed). 100 client-cancelled streams leave in-flight at 0, and the fake sees disconnects.
- **Admin.** All `/hydra/*` routes require the local token; without it they return 403, even from loopback.

**`tests/test_hydra_ready_parity.sh`.** For each fixture (status, body) and each engine, it serves the fixture from a one-line Python server. It compares `ob_backend_ready "$url" "$engine"` from `backend.sh` with `python3 -c 'engine_ready(...)'` and asserts that they agree. It includes negative controls: a llama 200 with a non-ok body, a TensorFold 200 without ok, and a vLLM 503.

**`tests/test_scripts.sh` additions:**

- Source conf.sh in a clean env with `HYDRA` unset. Assert that `MODEL_URL`, `OPENBEAST_AGENT_INFERENCE_URL`, `OPENBEAST_INFERENCE_MODEL` (and whether it is set at all), and `OPENBEAST_CONSUMER_BASE` equal the pre-hydra values, for each of these scenarios: local llama, remote `INFERENCE_URL`, vLLM backend, and router on.
- With `HYDRA=true`, assert the `:8095` derivations, an explicit `AGENT_INFERENCE_URL` winning, and `OPENBEAST_INFERENCE_MODEL=beast` for the llama backend.
- `grep` that `start.sh`'s router and gate launch lines use `CONSUMER_BASE`, and that `healthcheck.sh` no longer uses bare `$INFERENCE_URL` for the gate upstream.
- `test_backends.sh` passes unchanged, with no fourth backend.

**`tests/test_hydra_sim.sh`** (end to end, real processes; runs in `run_tests.sh` and skips if port binding fails). It runs `hydra-sim.sh` with five scenarios:

1. `beast` with the rig fake busy (1 slot held by a slow stream) → the second request lands on sparks.
2. Kill the sparks fake → the breaker opens, `beast:fast` falls to its next target, and `/health` stays 200.
3. A mid-stream kill → the error event arrives, with no replay.
4. A pin to a dead deployment → 503 `hydra_pinned_unavailable`.
5. A 150K-token prompt to `beast` → the `huge-prompts-go-long` rule sends it to `beast:long`, and the trace shows it.

**Era guard (acceptance).** `python3 -c "import sys; sys.path.insert(0,'evals'); import cache; print(cache.context_hash())"` gives the same value before and after the PRs, and `git diff --stat` shows no era file.

**MVP acceptance, all required:**

- The full suite is green, and existing suites are unchanged: `test_beast_slot`, `test_backends`, `test_edge` (plus additions), `test_router` (plus additions), `test_clients`, `test_pydeps_lock`, `test_conformance`.
- `HYDRA=false` is byte-identical.
- On the **real 5090** with `HYDRA=true` and the implicit config:
  - `./start.sh` boots, WebUI chats, a spawned agent completes, and a client reaches the gate.
  - Every response carries `X-Hydra-*`.
  - `scripts/hydra.sh status` shows `local@rig READY`.
  - `stop.sh` leaves no hydra process.
  - This is the memory lesson: always smoke the real stack after boot-path merges.
- Measured hydra overhead on the rig with n ≥ 20 per arm, hydra against direct:
  - added TTFT p50 < 5 ms, p95 < 15 ms;
  - streaming decode tok/s within 1%.
  
  These are the targets. The first measurement becomes the published number.
- `hydra-sim.sh` shows all five scenarios.

---

## 7. Runbook: the day the nodes come online

**Before any hardware, on the rig (can be done today).** Complete MVP acceptance, including the overhead measurement. Commit `hydra.toml` generated from `--print-default-config` and edit it to name the rig deployment explicitly (`qwen38-unc-q5@rig`, `slots = 1`, `gpu_lease = true`).

**Sparks (vLLM, TP=2).**

1. Bring up the pair per `DGX_SPARK_PLAN.md` §9, then confirm node-direct:
   - `curl -fsS http://<spark-a>:8000/health` returns 200.
   - `curl -fsS -H "Authorization: Bearer $(cat ~/.config/openbeast/vllm-api-key)" http://<spark-a>:8000/v1/models` lists `qwen3.8-27b-nvfp4`.
2. Copy the Spark's key to the rig: `install -m 600 /dev/stdin ~/.config/openbeast/hydra/sparks.key`.
3. Run `scripts/hydra.sh add-node sparks --url http://<spark-a-tailnet-ip>:8000 --engine vllm --key-file ~/.config/openbeast/hydra/sparks.key --profile qwen38-27b-nvfp4-vllm --deployment qwen38-nvfp4@sparks`. Paste the printed stanza and add the deployment to routes as in §6.2.1.
4. `scripts/hydra.sh conformance qwen38-nvfp4@sparks --heavy`. It must pass. Record `reasoning_field`, `strict_model_names` and tool results.
5. `scripts/hydra.sh check`, then `scripts/hydra.sh reload`, then `scripts/hydra.sh status`. Expect `qwen38-nvfp4@sparks READY`, conformance pass, `key: set`.
6. `scripts/hydra.sh explain '{"model":"beast","messages":[{"role":"user","content":"hi"}]}'` and the same for `beast:long`, `beast:fast` and `classify`. Every route resolves as intended.
7. `scripts/hydra.sh pin-smoke all`. Every deployment answers non-stream and stream, with correct headers.
8. Smoke WebUI with one chat per route id. Confirm the model dropdown lists the ids. Note how vLLM `reasoning` renders (**VERIFY**, DGX §12).
9. **Failure drills.** Each one must match the stated result:

   | Drill | Expected result |
   |---|---|
   | Hold the rig slot (long stream), send a second `beast` chat | It lands on sparks (`X-Hydra-Deployment`) |
   | `docker stop` vLLM on spark-a | Status goes DOWN within about 10 s. `beast` stays on the rig. A `qwen38-nvfp4@sparks` pin returns 503 |
   | Stop spark-b (rank 1) | vLLM `/health` should go non-200 → DOWN. **VERIFY**: if it does not, record it. The node agent (X10) is then promoted to NOW |
   | Kill vLLM mid-stream | Client sees the error event with no `[DONE]`. Audit records `upstream_failed_midstream` |
   | Rotate the Spark key without updating the rig | AUTH_FAILED, a doctor FAIL, and the caller never sees a 401 |
   | Relaunch the Sparks with a different model | MISMATCH, not routable |
   | `scripts/gpu-lease.sh acquire test` from another shell | `rig` drained; `beast` goes to sparks; release → undrained |
   | Quit NordVPN/Tailscale on the rig (memory: NordVPN severs the tailnet) | Remote nodes DOWN, the rig keeps serving, `/health` stays 200 |
10. **Measurements to take and write back into config:**
    - tailnet RTT rig↔spark;
    - Spark TTFT at 1K, 32K, 128K and 250K prompts (sets `prefill_tps_floor` and `ttft_timeout_s`);
    - vLLM decode tok/s at concurrency 1, 4 and 8;
    - whether hydra's in-flight count matches `vllm:num_requests_running` under load;
    - `/metrics` label names (for X1);
    - hydra-routed against node-direct TTFT (n ≥ 20).
11. **Capacity A/B (X18).** With 2 to 3 concurrent chats, compare TTFT p95 for `HYDRA=true` with spill against spill off. That is the first published hydra claim.

**Ti rig (llama.cpp, 2× 3090 Ti).**

1. Run a plain `llama-server --api-key-file … --host <tailnet-ip> --port 8080 -np 2 --metrics --tensor-split 1,1`, or a node-mode OpenBeast install with only llama exposed. Confirm health and models node-direct.
2. Follow steps 2 to 9 above, with `engine = llama`. `id_slot` is allowed. Conformance is `--backend llama`.
3. Point `classify` at the Ti. Measure the router's classify turn latency against the `ROUTER_SIDECAR_PLAN` baseline of 17.8 s / 45.2 s against 15.1 s.

**TensorFold on the Sparks (optional).** Stop vLLM. Set `sparks.enabled = false` and `sparks-tf.enabled = true` (the exclusive group is enforced). Firewall the port to the rig's IP (DGX §7). Run conformance `--backend tensorfold`. Route only tool-free or json_schema-free traffic to it.

**VERIFY checklist.** Each item is recorded in `docs/BEAST_HYDRA_PLAN.md` §12 when measured:

- [ ] vLLM `/health` returns 200 only when it can actually serve (not early during load)
- [ ] vLLM `/health` returns non-200 when rank 1 dies
- [ ] vLLM returns 404 for an unknown model id (strict names) on the NGC build
- [ ] vLLM ignores `id_slot` (hydra drops it anyway)
- [ ] vLLM `/metrics` names and `kv_cache_usage_perc` scale
- [ ] llama-server streaming: are headers sent before prefill finishes? (It affects only TTFT accounting; the design handles both.)
- [ ] OpenAI SDK 3.22.0 raises on the hydra SSE error event (pinned by a test, re-checked end to end)
- [ ] WebUI renders vLLM `reasoning`
- [ ] TensorFold `/health` body shape and its overflow text through the proxy
- [ ] Spark prefill floor and 3090 Ti MoE tok/s
- [ ] hydra overhead on the tailnet path

---

## 8. Phasing and PR sequence

| PR | Contents | Hermetic? | Gate to merge |
|---|---|---|---|
| H0a | `hydra_core.py`, `hydra.py`, `hydra.toml.example`, fake engine (plus the conformance Stub refactor), core, proxy and parity tests, `scripts/hydra.sh`, `hydra-sim.sh`, sim test | Yes | Suite green, sim scenarios pass, era hash unchanged |
| H0b | Stack wiring: conf.sh, backend.sh, start, stop, healthcheck (including the `:396` fix), doctor, edge.py and router.py additions, setup-tailscale, `openbeast.conf.example`, REFERENCE.md, wiring tests | Yes, plus a real-rig smoke | `HYDRA=false` byte-identical; real 5090 smoke with `HYDRA=true`; overhead measured |
| H1 | Day-one fixes from the runbook, measured defaults written back, VERIFY results recorded here | Needs hardware | Drills pass |
| H2 | NEXT tranche 1: X1 metrics scrape, X3 reasoning normalization, X6 `/api/slot` v3, X7 client catalogs, X8 WebUI caps-aware tools, X9 run_eval `--via-hydra`, X19 liveness | Mostly | Each with its own tests. v3 is additive, so `test_beast_slot` is extended, not rewritten |
| H3 | NEXT tranche 2: X2 queue, X10 node agent, X13 per-device policy, X14 slot ownership, X11, X12, X16, X17, X18 A/Bs | Mixed | Measurements justify each |
| H4+ | FUTURE items, each behind its own break-even measurement or A/B | n/a | P3 §11.4 doctrine |

H0a and H0b branch from `main`, never from the campaign branch. Each follows `git-discipline`: atomic commits and imperative subjects.

---

## 9. Risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | **A single point of failure on the inference path.** If hydra dies, all inference stops until it restarts. | Low / high | Crash-only design, all state soft. The start.sh health poll. Watchdog restart today (5 min) and a 30 s liveness loop in X19. `HYDRA=false` is the escape hatch. The rig is already the single point of failure for tools and WebUI. |
| 2 | **Silent behaviour change under an alias.** `beast` may answer from the stock NVFP4 on vLLM, with a different refusal behaviour and a different reasoning field. | Medium / medium | `same_family`, sticky affinity, headers plus the audit, the served `model` field, and strict ids for anyone who needs consistency. The docs say so plainly. §10 decision 1. |
| 3 | **Hydra's in-flight count is blind to direct traffic.** A campaign or another stack calling a node directly is invisible until X1. | Medium / low | X1 metrics scrape. GPU-lease drain on the rig. |
| 4 | **The token estimate is wrong near the limit** (code, CJK). | Medium / low | The conservative 3.0 chars/token, a 5% margin, verbatim 400 so the runner compacts, X4 `/tokenize`, X5 `larger_ctx`. |
| 5 | **Engine semantics are taken from docs, not measurement** (vLLM health timing, strict 404, rank-1 death). The fake engine encodes *beliefs*. | High / medium | The VERIFY list in the runbook. Fake personalities are corrected from measurements in H1. Failure drills are mandatory. |
| 6 | **Timeout layering** (gate 600 s, SDK 600 s, hydra budget, node TTFT). | Medium / medium | Invariants validated at load, a doctor row, the 580 s default. |
| 7 | **Mid-stream loss is unrecoverable.** A long agent turn dies on a flaky tailnet. | Medium / medium | This is the right trade (replay could duplicate tool effects). The runner or SDK retries the whole turn. R17 is research. |
| 8 | **Config split** across `hydra.toml`, profiles and node-side flags (`-np`, keys) can drift. | Medium / low | Profile derivation, MISMATCH, conformance admission, doctor rows. X10 node agent and X15 `init`. |
| 9 | **Local trust domain.** Model-authored local code can read the caller token. | Low / medium | Stated limit, the same as `edge-local.token`. Per-device policy guards against remote spoofing only. |
| 10 | **The WebUI model list gets cluttered** (routes plus deployments). | Medium / low | `listed = false`; `list_deployments = false`. |
| 11 | **"Intelligence routing" expectations outrun the data** (eval saturation, F15). | High / low | This plan claims capacity wins only. Quality claims go through the §6.10(6) doctrine. |
| 12 | **llama.cpp's unbounded FIFO when every node is saturated.** One heavy device can starve others until X2. | Medium / low | The gate's per-device in-flight caps. X2 queue with fair share. |

---

## 10. Open decisions for Max

Each has a recommendation.

1. **Family policy for `beast`.** May the uncensored default spill to the stock `nvidia/Qwen3.8-27B-NVFP4` on the Sparks?
   - `same_family = false`: capacity. The rig is `-np 1`, so this is where the spill win lives.
   - `same_family = true`: behaviour consistency.
   - **Recommendation:** `false` now, clearly audited. Flip it to `true` once a vetted uncensored Spark build (DGX §11.1 option, orcarouter NVFP4, eval-passed and pinned) makes both targets the same family.
2. **Which model the Sparks serve** (DGX §11.1) **and whether a Sparks flagship joins `beast:max`.** **Recommendation:** the 27B NVFP4 first, as the acceptance quantity. A larger flagship (Flash-Next, GLM-5.3-Flash) enters `beast:max` only after a paired new-era eval win (MoE lesson: total params ≠ capability).
3. **TP=2 pair against 2× TP=1 replicas.** Hydra supports both: one node, or two nodes in one route. **Recommendation:** measure after acceptance. For 27B chat concurrency, two replicas are likely to beat TP=2, and they make R8 prefix affinity meaningful.
4. **Is the gate required whenever hydra is on?** **Recommendation:** yes. Raw publication is refused because hydra holds node keys. Already in the MVP.
5. **What an unknown model id does.** **Recommendation:** `default` (legacy ids keep working), with the legacy ids also listed as explicit aliases. Switch to `404` once the client catalogs (X7) ship.
6. **Conformance required for remote deployments by default.** **Recommendation:** yes. It is one command and prevents routing to an engine whose tool parser is broken.
7. **Mid-stream error shape.** The error event with no `[DONE]` (loud), or with `[DONE]` (tidier for some UIs)? **Recommendation:** no `[DONE]`. A truncated answer must never look finished.
8. **Where `hydra.toml` lives.** **Recommendation:** the repo root, gitignored, next to `openbeast.conf`. Add both to the docsync backup profile. Memory note: edit both profile copies.
9. **Port and name.** **Recommendation:** `:8095`, which is unused in the repo. `:8096` is reserved for a future node agent.
10. **The Ti rig's role.** **Recommendation:** `beast:fast` (the 35B-A3B MoE, the speed champion at 359 tok/s on the 5090; its Ti speed is **VERIFY**) plus the `classify` target. This moves side traffic off the one-slot 5090, which is the first measurable win.
11. **Priority of NEXT items after day one.** **Recommendation:** X18 A/Bs → X1 metrics → X6/X7 fleet visibility → X9 eval pinning → X2 queue, only if the A/Bs show contention. Then X10 node agent, promoted earlier if drill 9 shows a dead rank 1 goes unnoticed.

---

## Reconciliation: beast-hydra ↔ beast-instinct (2026-09-30)

The two plans were designed in parallel and reconciled before either was built. This section binds both documents; where it differs from the text above, it wins.

1. **One classifier, not two.** hydra's NEXT/FUTURE "classifier routing" (R3) **is** instinct's `instinct-route/1` `task_class`. hydra does not grow its own classifier. hydra rules gain a `when.task_class = "<label>"` condition, populated only from an `instinct-route/1` answer with `action == "act"` **and** `enforce == true`; otherwise the condition is simply false and the static policy applies. hydra stays fully functional with instinct absent (tested with instinct on a dead port).
2. **Division of labour.** hydra owns eligibility (identity, capability, context fit, health, drain), capacity, the final pick and failover. instinct owns *judgement* (task class now, `pool_fit` later) and never sees or returns anything it was not sent. Mechanical facts (vision, context length) are never delegated to instinct.
3. **The agent router (`agents/router.py`).** Order on a spawn-candidate turn: identity gate (unchanged, first) → instinct `router.spawn_intent` (skip-only when confident and enforced) → otherwise the existing generative classify, which under `HYDRA=true` is sent with `model: "classify"` so hydra can place it off the one-slot 5090. Both changes are additive and opt-in.
4. **Ports and isolation.** instinct `127.0.0.1:8094` (its CPU scorer `:8082`), hydra `127.0.0.1:8095`. An instinct engine URL is never registered as a hydra pool for instinct's own traffic (instinct lints this; hydra refuses a node flagged `role = "instinct-engine"`).
5. **Feedback from day one.** hydra logs request → deployment → outcome (TTFT, errors, status) in its audit from the MVP, and — when instinct is present — `POST /v1/instinct/feedback` keyed by `trace_id`. `pool_fit` needs this data; collecting it costs nothing now.
6. **Build order.** hydra MVP (static policy, capacity spill, strict pins, provenance) and instinct P0 (library + service + `rules`/`linear` tiers + CPU llama.cpp scorer + `router.spawn_intent` in shadow) are built in parallel; the `when.task_class` hook lands in hydra behind the contract probe (`GET /v1/instinct/contract`), shadow-only until instinct's eval gate promotes a decision.
7. **Eval integrity is shared.** Neither service is ever on an eval unit's path unless the run pins it explicitly; both stamp provenance; instinct decisions that change behaviour are enforced only through a gate record written by the eval harness, never a config toggle.


---

## Revision: Max's answers, the uncensored policy, and reconciliation §4 (2026-09-30)

Max answered §10 decisions 1 and 2 on 2026-09-30, and set the direction for beast-instinct's engine. This section binds. Where it differs from anything above (§6.2.1's example, §9 risk 2, §10.1–2, reconciliation §4), it wins. The shipped `hydra.toml.example` and `scripts/hydra-sim.sh` follow it.

### R1. §10 decision 1, answered: never stock

> "No, all of our models are uncensored."

`beast` never spills to a stock model, and neither does any other route. Max made this a fleet rule, so it is enforced as **policy**, not a per-route opt-in:

- **`[hydra] allowed_families = [...]`** is a hard filter inside `_exclude`. It applies to every non-strict candidate: first choice, spill, sticky affinity, failover, the ctx last resort, and after a rule hop.
  - A route target outside it is a **config error**. A stock model can't be listed in a route by mistake.
  - **Leaving it out is also a config error once the routes span more than one family** (review R-hydra-1). hydra can only enforce the rule when the families are declared, so a `hydra.toml` copied from the pre-2026-09-30 template (which spilled `beast` to stock) no longer loads; it used to pass `check` with a warning. A single-family fleet and the implicit single-node config need no declaration.
  - A deployment outside it is only reachable by its strict id (a pin). `check` warns about it. Pins stay exempt because naming a deployment is explicit and is how evals measure one.
  - The proxy re-checks the policy against the *current* config before every failover attempt, so a reload that tightens it mid-request can't let a stale plan call a stock engine.
- **The `same_family` anchor is explicit.** A route's `family = "..."` sets it, and implies `same_family`. Without it, the anchor is the one family of the top-priority targets. If that group has more than one family, it is an error: the anchor is never inferred from list order. The old anchor was the first-listed target, which made `beast:long` anchor on stock and drop the uncensored rig (review A-hydra-2).
- **The anchor survives a rule hop.** A `same_family` route keeps its family after a rule sends it to another route. Validation refuses a rule that would send it somewhere with no target of that family (every such request would 503). Before this, a ~130K-token `beast` prompt reached stock through `huge-prompts-go-long` (B-hydra-2).
- **The ctx last resort chooses only among targets the policy allows** (A-hydra-3). Before, a larger stock context turned the engine's overflow 400 into a hydra 503.
- `/hydra/status` shows `allowed_families` and each route's `family`.

The §10.1 recommendation ("`false` now, flip later") is withdrawn. So is the capacity argument behind it: spill capacity now comes from the same uncensored weights on a second box (R2), not from a different model.

### R2. §10 decision 2, answered: GLM-5.3-Flash on the Sparks

> The Sparks will serve "GLM-5.3-Flash by orcarouter, or an EXL3 variant."

**Chosen:** GLM-5.3-Flash Uncensored, EXL3 TR3 4bpw (`neko-legends/GLM-5.3-Flash-Uncensored-EXL3`, built from orcarouter's FP8).
- It runs on **TensorFold TP=2**: one hydra node, rank 0 serving HTTP.
- Profile name: `glm53-flash-unc-exl3-tensorfold` (GLM track).
- Deployment `glm53-flash-unc@sparks`, family `glm-5.3-flash-uncensored`.

Why this variant:
- TensorFold's `glm5_next` reader accepts only 4-bit `mcg` routed-experts-only EXL3. Any other EXL3 raises.
- At 175.6 GB it fits two Sparks at about 95 GB per rank.

Why not the alternatives:
- **orcarouter FP8** (328 GB) fits nowhere.
- **orcarouter NVFP4** (205 GB) fits two Sparks only at `gpu_memory_utilization` ≥ 0.9, which starves the OS. It also needs vLLM ≥ v0.30.0: the NGC 26.05 pin cannot load `Glm5Next`. And compressed-tensors NVFP4 MoE on SM121 is unproven.
- **Stock exllamav3 EXL3** cannot span two hosts.

What routes GLM:
- It is a flagship, so §10.2's doctrine still holds: it joins `beast:max` only after a paired new-era eval win.
- Until then it is reachable by name (`beast:glm`, GLM or nothing) and is first in `beast:long`.
- `beast` stays on the uncensored Qwen3.8 27B family, even through the long-prompt rule.

**The Ti rig** serves the same uncensored 27B GGUF as the rig, without MTP so that it can run several slots. It is `beast`'s spill target and the first target of `beast:fast` and `classify`. The stock 35B-A3B MoE is gone. It was stock, and with about 3B active parameters it is not the "full model" Max wants decisions made on (MoE lesson: total params ≠ capability).

### R3. Reconciliation §4, revised: an instinct engine may also be a node

Max's direction: beast-instinct decides on a **full** model.
- The target engine is **Open-Jev-27B-v1.1, run locally**: the Qwen3.8-27B base plus a LoRA and a scalar decision head, with a custom loader. It is never a hosted API. It needs a GPU of its own: the freed 5090 once the Sparks carry generation, or a Spark.
- Until then, the interim engine is the rig's own 27B scoring by logprobs. It replaces the router's generative classify, which already runs on that same model. The 0.6B CPU scorer becomes the fallback only.

What changes in hydra:
- **Only the instinct SERVICE URL is refused as a node.** That rule prevents a loop: hydra asks instinct, and instinct is routed back through hydra.
- An **instinct ENGINE** listed in `hydra.instinct.engine_urls` may also be a node (the rig at `:8080`). instinct calls it directly, never through hydra, so there is no loop. `check` warns (risk 13).
- A node flagged `role = "instinct-engine"` (a dedicated scorer) is still refused. So are instinct's own `:8094`/`:8082`.
- **hydra's consult is answered by the 27B, not the small tiers.** The shipped example sets `hydra.instinct.deadline_ms = 800`, sized for a 27B scorer: the instinct brief estimates about 0.2–0.6 s for 512–1,600 tokens on the 5090 (unmeasured). The call sits before a generation of several seconds; a miss falls back to hydra's static policy for that request, never to a different decider. The 0.6B stays instinct's fallback only, as Max set it. (`hydra_core`'s built-in default is still 25 ms, and instinct's own `agents/instinct/decisions/hydra.task_class.toml` still caps the decision at 25 ms with `linear` first in its chain; instinct answers with `min(caller, cap)`, so both must follow this on the instinct track. The validator accepts 1..2000.)
- **The only open item is the hardware measurement** (R4 item 5): the rig 27B's real scoring latency, and later Open-Jev's on its own GPU, to set the deadline from data instead of the estimate.

### Risk 13 (new): hydra cannot see instinct's direct calls on a 1-slot node

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| When the rig is both a node and an instinct engine, instinct's scoring calls reach `:8080` without passing through hydra. hydra's in-flight count for the rig misses them. It can plan a routed turn onto a rig it believes is free, and that turn then queues behind a scoring prefill. On `-np 1`, each scoring call also swaps the conversation's slot state out and back (`prompt_save`/`prompt_load`, `--cache-ram` 8 GiB). | Medium / low | Scoring is used only in place of the router's classify, which already hits the rig, so the load is not new. instinct's planned `busy_skip` (instinct track) checks `/slots` and falls to the 0.6B when the rig is busy. X1 (`/metrics` scrape) makes the traffic visible to hydra. Open-Jev on its own GPU removes the overlap. |

### R4. VERIFY on hardware (adds to §7)

1. The TensorFold GLM node:
   - the served id (`SERVED_MODEL_NAME` → the deployment's `upstream`);
   - the real context window (the example says 262144; upstream calls EXL3 long context "TBD");
   - `TENSORFOLD_PARALLEL=auto` = 1 slot;
   - TTFT and prefill rate for the node's `ttft_timeout_s` and `prefill_tps_floor`;
   - tool-call conformance (`scripts/hydra.sh conformance glm53-flash-unc@sparks`).
2. When the `glm53-flash-unc-exl3-tensorfold` profile lands, replace the example's explicit `upstream`/`ctx` with `profile = ...`. They must match or `check` refuses the file.
3. The Ti: the `-np` its serve script runs (→ `slots`) and whether 262144 fits 48 GB at that slot count.
4. The rig as instinct engine: the effect of scoring calls on a routed turn's TTFT (risk 13), measured with X1 or the audit's `ttft_ms`.
5. The 27B scorer's latency on hydra's consult (p50/p95 of the audit's instinct `ms`) against `hydra.instinct.deadline_ms = 800`: tighten it to about p95 plus a margin, or raise it if the rig misses too often.
