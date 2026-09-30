# beast-hydra — prior-art survey (2026-09-30)

Research input to [`docs/BEAST_HYDRA_PLAN.md`](../BEAST_HYDRA_PLAN.md). Every claim cites its source as fetched on 2026-09-30.

# beast-hydra prior-art survey: how existing systems route and pool LLM inference across machines and engines (as of 2026-09-30)

Each claim below cites the page I fetched or searched. A few of those pages turned out thin or out of date, and I say so where that happens.

---

## 1. LiteLLM proxy

- **Config model:** a `model_list`. Each entry has a virtual `model_name` (the alias or model group) and `litellm_params`, which name the real deployment. Several entries that share one `model_name` form a load-balanced group. Settings go in `router_settings` or on the SDK `Router` class. https://docs.litellm.ai/docs/routing
- **Routing strategies:**
  - `simple-shuffle`, the default: weighted by rpm or tpm.
  - `latency-based`: picks the lowest latency over a TTL window.
  - `usage-based-v2`: picks the lowest TPM used this minute. It needs Redis.
  - `least-busy`: picks the fewest in-flight requests.
  - `cost-based`.
  - Custom routing through `CustomRoutingStrategyBase`.
  - An `order` field gives deployments a priority.
  
  Source: https://docs.litellm.ai/docs/routing
- **Health and failover:**
  - Per-deployment cooldown: `allowed_fails` defaults to 3 and `cooldown_time` to 5 s. Deployments are keyed by a hash of their `litellm_params`.
  - `num_retries` with exponential backoff, plus a per-error-type `RetryPolicy`. https://docs.litellm.ai/docs/routing
  - Fallbacks: `fallbacks` across model groups, `default_fallbacks`, and `context_window_fallbacks`. The last one uses `enable_pre_call_checks: true` to filter out deployments whose context is too small before the call is sent. There are also content-policy fallbacks. https://docs.litellm.ai/docs/proxy/reliability
  - Health endpoints: `/health/liveliness` and `/health/readiness`. `/health` sends a real completion to every model. `background_health_checks` runs every 300 s by default. https://docs.litellm.ai/docs/proxy/health
- **Streaming:** the docs describe what happens when a stream fails before its first chunk (a fallback, or the deployment's own error when fallbacks are disabled). They say nothing about falling back after tokens have already started flowing. https://docs.litellm.ai/docs/proxy/reliability
- **What we can reuse:** almost the whole vocabulary:
  - model groups as aliases
  - `order` for priority
  - cooldown and allowed_fails
  - context-window pre-call filtering
  - fallbacks across groups
  - least-busy and latency strategies
- **What to avoid:** taking it as a dependency. It is heavy, and litellm 1.82.7 and 1.82.8 were **backdoored on PyPI on 2026-03-24**. The attacker got in through a compromised Trivy in LiteLLM's CI. https://docs.litellm.ai/blog/security-update-march-2026, https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/. That conflicts with our pinning ethos. Copy the semantics, not the package.

## 2. vLLM production-stack router

- **Signals and strategies:** the docs index lists load-aware, KV-cache-aware, prefix-aware and session routing, plus disaggregated prefill. https://docs.vllm.ai/projects/production-stack/en/latest/. The README only lists round-robin, session-ID, and prefix-aware (WIP), and puts KV-aware and disaggregated prefill on the roadmap. The two sources conflict. https://github.com/vllm-project/production-stack
- **Config:** `--service-discovery static|k8s`, `--static-backends`, `--static-models`, `--routing-logic`, `--session-key` (a header), `--static-backend-health-checks` (sends dummy requests), `--engine-stats-interval` (scrapes `/metrics`, 30 s), and `--dynamic-config-yaml`, which is re-read every 10 s. https://github.com/vllm-project/production-stack/tree/main/src/vllm_router
- **Deployment:** Helm and Kubernetes first. https://github.com/vllm-project/production-stack
- **What we can reuse:**
  - the static backend list
  - session-header stickiness
  - hot-reloaded config
  - scraping engine `/metrics`
  
  The Kubernetes stack is overkill for us.

## 3. llm-d

- **What it is:** a CNCF Sandbox project from Red Hat, Google, IBM, CoreWeave and NVIDIA. It routes on prefix-cache awareness, load (GPU utilisation), and an experimental predicted-latency scheduler. It supports prefill/decode disaggregation and tiered KV offload to CPU or disk. It **requires Kubernetes** and runs vLLM or SGLang underneath. https://github.com/llm-d/llm-d
- **Scheduler:** built on the Gateway API Inference Extension. An InferencePool plus an Endpoint Picker (EPP) scores pods with plugins for queue depth, KV-cache utilisation and prefix affinity. An InferenceObjective carries per-request priority. https://github.com/kubernetes-sigs/gateway-api-inference-extension, https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/
- **What we can reuse:** the scorer-plugin pattern (a weighted sum of queue, KV and prefix scores) and priority classes. The infrastructure itself is overkill.

## 4. SGLang router (sgl-model-gateway)

- **Policies:** `random`, `round_robin`, `power_of_two`, `cache_aware` (the default) and `bucket`. https://docs.sglang.io/advanced_features/sgl_model_gateway.html
- **How `cache_aware` works:** it keeps an approximate radix tree of prompts that the router itself has sent to each worker. Three thresholds control it:
  - `--cache-threshold 0.3`: the minimum prefix match
  - `--balance-abs-threshold 64`
  - `--balance-rel-threshold 1.5`
  
  When load is imbalanced past those thresholds, it falls back to the shortest queue. It never reads engine KV state.
- **Health and failover:**
  - `--health-check-interval-secs`
  - retries with jittered exponential backoff (`--retry-max-retries 5`, initial 50 ms, multiplier 2.0)
  - a per-worker circuit breaker (`--cb-failure-threshold 5`, `--cb-success-threshold 2`, `--cb-timeout-duration-secs 30`)
- **Other features:**
  - PD mode: `--pd-disaggregation --prefill URL --decode URL`, with a separate policy for each side.
  - Multi-model through `--enable-igw`, with per-model policies and worker labels.
  - Kubernetes pod-selector discovery.
  - OpenAI-compatible endpoints.
- **What we can reuse:** this is the most directly portable design:
  - the approximate prefix tree built from the router's own history, which **works with any engine, llama.cpp included**
  - the two-threshold switch between cache affinity and load
  - the circuit-breaker parameters
  - power-of-two choices

## 5. NVIDIA Dynamo

- **Cost function:** `prefill_blocks − overlap_blocks×overlap_score_credit + decode_blocks`. The worker with the lowest cost wins. https://docs.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/kv-aware-routing, https://docs.nvidia.com/dynamo/v-0-9-1/components/router/router-guide
- **KV state:** workers publish KV create and evict events. `--no-router-kv-events` switches to an **approximate mode** that predicts cache state from the router's own decisions, with a 120 s TTL or an experimental LRU. `--router-temperature` is deprecated for removal in v1.7. There are busy thresholds that queue requests when every worker is saturated. https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/router/configuration-and-tuning
- **Disaggregated mode:** decode routing turns overlap scoring off. https://docs.nvidia.com/dynamo/v1.4.2/knowledge-base/modular-components/router/disaggregated-serving
- **Backends:** vLLM, SGLang and TensorRT-LLM. llama.cpp is not listed. https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/router/configuration-and-tuning
- **Not verified:** I could not fetch the transport and discovery details (etcd or NATS).
- **What we can reuse:** the cost formula and the approximate-mode idea. The framework is overkill for 2–4 boxes. Its prefill/decode split pays off only within one engine family and needs a fast KV transport.

## 6. Ray Serve LLM

- **What it offers:** `LLMConfig` and `build_openai_app()`, multi-model and multi-LoRA, prefix-aware and custom request routers, PD disaggregation, autoscaling between `min_replicas` and `max_replicas`, and vLLM or SGLang engines. It **requires a Ray cluster**. https://docs.ray.io/en/latest/serve/llm/index.html
- **Verdict:** overkill. A Ray cluster spanning aarch64 Sparks, the x86 rig, and llama.cpp, which Ray does not manage, is a poor fit.

## 7. KServe and Envoy AI Gateway (now "Agent Router")

- **KServe:** LLMInferenceService integrates llm-d, InferenceGraph chains models, and autoscaling runs on token throughput, queue depth and GPU utilisation. Kubernetes only. https://kserve.github.io/website/docs/intro
- **Envoy AI Gateway:** renamed Agent Router (an Agentic AI Foundation project). The `aigw` CLI and the CRDs are unchanged. It offers:
  - routing on the `x-ai-eg-model` header
  - backend priority and failover
  - token-based rate limits
  - InferencePool / EPP integration
  - a **standalone `aigw` mode** for local machines
  
  Source: https://github.com/envoyproxy/ai-gateway
- **What we can reuse:** routing on the model header and token-budget rate limits. beast-gate already covers device-level limits. An Envoy dependency is overkill.

## 8. OpenRouter semantics

- **Default load balancing:** skip providers that have had outages in the last 30 s, then weight the rest by inverse-square price. https://openrouter.ai/docs/features/provider-routing
- **The `provider` object:**
  - `order`, `allow_fallbacks`, `only`, `ignore`
  - `sort`: price, throughput or latency
  - `preferred_min_throughput` and `preferred_max_latency` at p50, p75, p90 or p99
  - `require_parameters`: only use providers that support every request parameter
  - `quantizations`, `data_collection`, `zdr`, `max_price`
  - The shortcuts `:nitro` (throughput) and `:floor` (price)
  
  Source: https://openrouter.ai/docs/features/provider-routing
- **Model routing:**
  - A `models` array falls back on errors, context overflow, moderation or rate limits.
  - The response's `model` field reports the model that actually served the request.
  - `openrouter/auto` classifies the task. It supports `allowed_models`, `excluded_models` and `cost_tier`, and keeps a session sticky to one model.
  
  Source: https://openrouter.ai/docs/features/model-routing
- **What we can reuse:** this is the best **per-request API** to copy:
  - `order`, `only`, `ignore` and `allow_fallbacks` as request hints
  - `require_parameters`: only route to engines that support grammar, tools, or `reasoning_budget_tokens`
  - `quantizations` as a filter
  - reporting the served model in the `model` field
  - model suffixes (`:fast`, `:max`) as aliases for a routing policy

## 9. llama.cpp RPC backend, router mode and draft models; llama-swap

- **RPC backend:** `ggml-rpc-server` on the remote hosts and `--rpc host:port,…` on the main host. It spreads weights and KV cache across local and remote devices in proportion to their memory, and `--tensor-split` overrides the proportions. It has a local tensor cache (`-c`) and optional RDMA. Upstream warns that it is "fragile and insecure. **Never run the RPC server on an open network.**" It has no authentication. https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md
- **Server features:**
  - `--alias` takes comma-separated names.
  - `/metrics` (enabled with `--metrics`) exposes `llamacpp:requests_processing`, `llamacpp:requests_deferred`, token rates and speculative-decoding counters.
  - `/slots` accepts `?fail_on_no_slot=1`.
  - `/health` returns 503 "Loading model" while loading and 200 once ready.
  - `/props` exposes the context size and capabilities.
  - `--slot-save-path` saves and restores KV per slot.
  - Router mode: `--models-dir`, `--models-max` (default 4), `--models-preset`, and each model runs as a child process.
  
  Sources: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md, https://huggingface.co/blog/ggml-org/model-management-in-llamacpp
- **Draft models:** the draft model must be local. An open proposal, issue #23982, would add an HTTP proxy that orchestrates a draft server and a target server on separate machines using only `/completion`, with matching tokenizers as the constraint. It has no maintainer response yet. https://github.com/ggml-org/llama.cpp/issues/23982
- **llama-swap:** a Go proxy that picks the backend from the request's `model` field. Config keys:
  - `models.cmd` and `${PORT}`
  - `ttl`
  - `aliases`
  - `checkEndpoint`
  - `healthCheckTimeout` (15 s minimum)
  - `useModelName`, which rewrites the upstream model id
  - `groups` with `swap`, `exclusive` and `persistent`
  - **`peers`** with `proxy` and `apiKey`, for remote upstreams
  - `filters.stripParams`
  - `concurrencyLimit`, which returns 429
  - `sendLoadingState`
  
  It streams and works with any OpenAI-compatible engine. https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/config.example.yaml, https://github.com/mostlygeek/llama-swap
- **What we can reuse:**
  - llama-swap's config shape is closest to what we need: models → nodes, with aliases, `useModelName`, peers, stripParams, a concurrency limit, and exclusive groups.
  - RPC is realistic only on a private link, never on the tailnet. It becomes a "pool" mode for one oversized GGUF.

## 10. exo

- **What it is:** automatic peer discovery, topology-aware partitioning, tensor parallelism (up to 3.2× on 4 devices), and RDMA over Thunderbolt 5. MLX is the main backend. On Linux it is **CPU only** for now. It serves OpenAI, Claude, Responses and Ollama APIs. https://github.com/exo-explore/exo
- **Heterogeneous PD demo:** 2× DGX Spark for prefill with an M3 Ultra for decode, over 10 GbE, running Llama-3.1-8B FP16 with an 8k-token prompt.

  | Setup | Prefill | Decode | Total | Speedup vs M3 Ultra |
  |---|---|---|---|---|
  | M3 Ultra alone | 5.57 s | 0.85 s | 6.42 s | baseline |
  | Combined | 1.47 s | 0.85 s | 2.32 s | 2.8× |

  KV is streamed layer by layer. Overlap only hides the transfer when transfer time is shorter than compute time, which needs roughly 5k to 40k tokens of context depending on the model. https://blog.exolabs.net/nvidia-dgx-spark/
- **What we can reuse:** the concept (put compute-heavy prefill and bandwidth-heavy decode on the hardware suited to each) and the break-even formula. The runtime does not fit our CUDA engines.

## 11. GPUStack

- **What it is:** a heterogeneous cluster manager (NVIDIA, AMD, Ascend and others) with pluggable engines (vLLM, SGLang, TensorRT-LLM). It provides an OpenAI-compatible API, load balancing, failure recovery, authentication, metering and Grafana, and runs in Docker with Linux workers. https://github.com/gpustack/gpustack
- **Verdict:** it duplicates OpenBeast's control plane (weights, identity, dashboard). Worth mining for UX ideas: a node inventory, model placement, and usage metering.

## 12. Ollama clustering

- **Native support:** none. Ollama is a single instance with no load balancing. https://blog.runc.ai/ollama-distributed-inference/
- **Third-party options:**
  - OLOL: model-aware routing and session affinity. https://github.com/K2/olol
  - Hive: queues by model or node. https://www.sciencedirect.com/science/article/pii/S2352711025001505
  - Olla: a merged model catalog, health checks and failover. https://peira.dev/articles/olla-ollama-load-balancer/
  - Ollama Herd: auto-discovery plus a scoring engine. https://ollamaherd.com/guides/ollama-load-balancing
- **What we can reuse:** the lesson that "a **merged model catalog** plus health checks plus model-aware routing" covers most of the value for a homelab, and that true distributed inference needs runtime support.

## 13. Speculative decoding across devices

- **Research:**
  - DSD adds adaptive window control for draft and target running on separate devices. https://arxiv.org/abs/2511.21669
  - DSSD (ICML 2025). https://icml.cc/virtual/2025/47046
  - PipeSD puts the draft at the edge and verifies in the cloud. https://arxiv.org/pdf/2605.13319
- **Practice:** llama.cpp has no support yet (see issue #23982 above). TensorFold does exact speculative decoding inside one process. https://vramcalculator.com/tensorfold/
- **Verdict:** a future research mode. Each draft/verify round trip costs a network RTT, so on a tailnet it will usually lose to MTP running on one box, which is our shipped default. Measure before building.

## 14. Disaggregated prefill/decode on heterogeneous hardware

- **HexGen-2:** schedules PD across heterogeneous GPUs as a constraint-optimisation problem. https://arxiv.org/abs/2502.07903
- **Helix:** a max-flow / MILP placement, up to 3.3× throughput. https://dl.acm.org/doi/10.1145/3669940.3707215
- **A multi-vendor PD system.** https://arxiv.org/pdf/2509.17542
- **Hetis.** https://arxiv.org/pdf/2509.08309
- **Engine support:** SGLang PD (§4), Dynamo (§5) and exo (§10).
- **Verdict:** it needs one engine family on both sides and a compatible KV layout. vLLM↔vLLM on Spark↔Spark over ConnectX-7 is plausible later. 5090 llama.cpp → Spark vLLM is not possible, because the KV formats are incompatible.

## 15. Semantic and learned routers

- **RouteLLM:** mf, sw_ranking, bert or causal_llm routers between a strong and a weak model, with a calibrated threshold, served as an OpenAI-compatible model named `router-mf-0.116`. It claims up to 85% cost reduction while keeping 95% of GPT-4 quality. https://github.com/lm-sys/RouteLLM
- **vLLM Semantic Router:** an Envoy ext-proc that classifies requests with BERT or LoRA classifiers. It has a "MoM" virtual model, prompt guard, a semantic cache and LoRA routing. https://github.com/vllm-project/semantic-router. Its own vision paper warns that intent classification may not be the strongest signal for choosing among many models. https://www.emergentmind.com/topics/vllm-semantic-router
- **NotDiamond:** trained on your eval data, with a quality/cost/latency tradeoff setting. https://docs.notdiamond.ai/docs/what-is-model-routing
- **Martian:** "model mapping". https://techcrunch.com/2023/11/15/martians-tool-automatically-switches-between-llms-to-reduce-costs/
- **Verdict:** we already have the classifier infrastructure: `agents/router.py`'s grammar-constrained classify and the proposed CPU sidecar in `docs/ROUTER_SIDECAR_PLAN.md`. Our 137-task eval gives per-category scores for each model, which is exactly NotDiamond-style training data. Make it an opt-in `policy: classify`, and keep rules deterministic by default.

## 16. OpenAI-compatible model aliasing

- **vLLM:** `--served-model-name a b c` answers to any of the names, and the first one is returned in responses. https://docs.vllm.ai/en/latest/cli/serve.html
- **Others:** llama.cpp `--alias` (§9), llama-swap `aliases` and `useModelName` (§9), and LiteLLM `model_name` groups (§1).
- **Takeaway:** hydra should own a **virtual model namespace** that agents see (for example `beast`, `beast:fast`, `beast:max`, `qwen38-27b`), and rewrite the `model` field for each upstream. This keeps the era-locked `runner.py`, `tools.py` and `opencode.json` untouched, because they only need a URL and a model id.

---

## Synthesis: routing signals

| Signal | Where it comes from | Engine coverage | Realistic for 2–4 boxes? |
|---|---|---|---|
| Requested model id / alias | request body | all | **Yes, the core signal** |
| Capability requirements (tools, grammar/json_schema, vision, reasoning budget, context length) | request plus a per-node capability declaration or probe | all | **Yes** (OpenRouter `require_parameters`, LiteLLM pre-call checks) |
| Estimated prompt tokens vs node ctx (unified-KV `capacity.ctx_*`) | a char/4 heuristic or `/tokenize` | all | **Yes** (context-window routing and fallback) |
| Liveness/readiness (`/health`: llama 503 while loading, vLLM 200, TensorFold `{"ok":true}`) | a probe loop | all | **Yes** (`scripts/lib/backend.sh` already encodes these rules) |
| In-flight count at the router | router-local | all | **Yes** (least-busy, power-of-two) |
| Engine queue (`llamacpp:requests_deferred`/`processing`, `vllm:num_requests_waiting`/`running`) | a `/metrics` scrape | llama (needs `--metrics`), vLLM | **Yes** |
| KV pressure (`vllm:kv_cache_usage_perc`, llama `/slots`) | scrape | vLLM, llama | Yes, as a tiebreaker |
| Observed TTFT / tok/s EWMA | router-local measurement | all | **Yes** (latency strategy; `preferred_max_latency`) |
| Prefix affinity (approximate tree of the router's own history, as in SGLang/Dynamo approx mode) | router-local | all | **Yes**, but only matters when several replicas serve the same model |
| Session/conversation stickiness (header or hash of the first messages) | request | all | **Yes, cheap**; protects llama.cpp slot prefix reuse |
| Identity/tier (beast-gate device, X-OpenWebUI role) | existing headers | all | Yes (per-tier allowed nodes and priority) |
| Error rate / circuit state | router-local | all | **Yes** (cooldown, circuit breaker) |
| Task class (code/zig/chat/vision) via rules or a classifier | request content | all | Yes as opt-in; rules first, classifier later |
| Eval-derived quality per model per category | `evals/` scores | offline | Yes, a unique asset for "best model for task X" |
| Engine KV events (exact KV-aware routing) | engine event stream | vLLM/SGLang/TRT via Dynamo | No, overkill |
| Power/thermal/cost (watts per token, time of day, GPU lease held by a campaign) | nvidia-smi, the GPU lease | local | Yes (a "rig busy with a campaign → drain" flag) |

## Synthesis: routing strategies, ranked for us

**Tier A: build now (hydra v0/v1).** All of these are stateless or router-local, engine-agnostic, stdlib/httpx/FastAPI only, and testable hermetically with fake upstreams.

1. **Virtual model catalog and aliasing.** Map each alias to an ordered list of (node, upstream model id). Rewrite `model` for each upstream and report the served model in the response, as OpenRouter does.
2. **Capability and context filtering** before choosing a node: tools, grammar, vision, `max_ctx`, and a reasoning-budget passthrough.
3. **Priority order with fallback.** On connect error, timeout, 5xx or 503-loading, fall back before the first byte only. Never fail over mid-stream; that matches LiteLLM's documented behaviour.
4. **Health state machine per node.** Readiness probe, then LiteLLM-style cooldown, then an SGLang-style circuit breaker (5 failures to open, 30 s half-open, 2 successes to close).
5. **Load-aware choice within an alias group.** Router in-flight count plus scraped queue depth, with power-of-two or least-busy selection and an optional weight per node.
6. **Session stickiness** to keep llama.cpp prefix/slot reuse.
7. **A policy per request:** a header or model suffix (`beast:fast`, `beast:max`, `beast:long`) in the spirit of OpenRouter's `:nitro`/`:floor`, with `only`, `ignore` and `order` hints.
8. **Pass-through SSE streaming** with backpressure. Count usage from the final chunk or `usage`.
9. **Declarative YAML/INF config,** hot-reloaded as in vLLM's `--dynamic-config-yaml`, plus a `/hydra/nodes` status endpoint, a `/v1/models` merged catalog, and routing-decision audit lines.

**Tier B: next, once real nodes exist (all need measurement).**

- Latency/throughput EWMA routing (`sort: throughput|latency`).
- SGLang-style approximate prefix affinity when two or more replicas serve the same model (for example Spark A and Spark B each running a replica instead of TP2).
- Rule-based task routing (vision → the node with mmproj; zig or long-context → the big-ctx node).
- An opt-in classifier on the CPU sidecar; eval-score-driven model choice.
- Priority classes and draining of the "rig in campaign" node through the existing GPU lease.
- A `/api/slot` v3 that aggregates the nodes.

**Tier C: future research (overkill or blocked now).**

- llama.cpp RPC pooling to split one GGUF across the 5090 and the 3090 Tis. Private link only, because it has no authentication.
- vLLM PD disaggregation between the Sparks over ConnectX-7.
- Cross-machine speculative decoding, following llama.cpp issue #23982.
- Exact KV-event routing à la Dynamo.
- Learned routers (RouteLLM-style) trained on our eval data.
- Helix/HexGen-style automatic placement solving.

**Reject for OpenBeast:**

- Kubernetes-based stacks (llm-d, KServe, production-stack Helm, GPUStack's control plane) and Ray. They are too heavy for 2–4 boxes and duplicate our control plane.
- LiteLLM as a dependency (supply-chain history). Copy its semantics instead.
- Envoy/Agent Router as a dependency. The standalone `aigw` could be evaluated later, but it adds a Go/Envoy binary to pin.

**Constraints that fall out of the research for the hydra design:**

- One routing hop goes on the inference path behind beast-gate. Hydra authenticates to upstreams per node with keys like `LLAMA_API_KEY`.
- Tools still execute where the process runs; hydra only routes `/v1/*`.
- Unified KV means a node's context is the full `-c` value, never divided by slot count.
- The response `model` field and an `X-Hydra-Node` header make every routing decision auditable. That is required for measured claims and eval provenance, since evals must pin one node and model and never be silently rerouted.