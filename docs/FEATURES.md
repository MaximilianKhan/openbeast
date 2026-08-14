# Features (complete)

The full capability breakdown. The README carries a condensed highlights
version; this is the exhaustive reference.

## Model Serving
- llama.cpp with CUDA (Blackwell SM 120), full GPU offload
- 6 parallel request slots with unified KV cache and continuous batching
- 23 pre-configured models (all VRAM/context-measured on the 5090), capability-ranked on the hardened v4 eval suite — default **Heretic v2 27B MTP Q5_K_M** (256K context); full lineup in [MODELS.md](MODELS.md), scores in the [leaderboard](RESULTS.md)
- Context lengths tuned to measured VRAM ceilings (192K–512K) on a 32GB card; MTP variants additionally pin `-np 1` per upstream constraint
- **Reasoning on by default.** The shipped Qwen models are "thinking" models — full chain-of-thought is on out of the box for maximum answer quality. It's a stateless *per-request* toggle (`chat_template_kwargs: {enable_thinking: false}`), so automated sub-calls (e.g. a JSON classification or routing step) can opt out for speed and clean output without touching your deployment or any other request. A global `REASONING` / `REASONING_BUDGET` (in `openbeast.conf`) caps or disables thinking per deployment — the over-reasoning "MAX" tunes ship with a sane `--reasoning-budget` default.
- **Fast boot** (opt-in, `FAST_BOOT`): serve the tiny Qwen3-0.6B bridge on :8080 for instant chat, then hot-swap to the configured model once the stack is up and its weights are warmed.
- **Model load-failure rollback** (on by default, `MODEL_ROLLBACK`): if a model fails to load (OOM, missing/corrupt weight), revert to the last model that loaded healthy rather than leaving the stack down.

## Tool Suite (15 tools, two surfaces)
- File operations: `read_file`, `write_file`, `edit_file`, `list_files`
- Code search: `grep` (regex), `list_files` (glob)
- Shell: `bash` with timeout and output capture
- Web: `fetch` (URL → readable text, SSRF-guarded), `web_search` (via local SearXNG)
- Agent management: `start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent`
- Skills (curated expertise packages): `skill` (one tool: index + load, rescans on every call), `start_skill_agent`
- **Identity-aware serving** (`agents/openapi_tools.py`, the WebUI surface): each WebUI account's files shard into `~/openbeast-files/users/<id>/` with a per-shard `.manifest.jsonl` write index (`FILES_SHARDING=user|chat|off`); per-profile RBAC keys checked on every call; per-call **audit trail** (`.run/tool-audit.jsonl` — who ran which tool when, argument digests only, never contents)
- MCP surface (`agents/mcp_server.py`, stdio) for OpenCode and any MCP client — same 15 functions, imported by the identity server so the surfaces can't drift
- Private workspace: created `0700` by `start.sh`; configurable via `FILES_DIR` in `openbeast.conf` — persistent and private, never world-readable `/tmp`

## Autonomous Agents
- **Agent-spawn router** (opt-in, `AGENT_ROUTER=true`): local models rarely call the "spawn a background agent" tool on their own judgment, so a grammar-constrained pre-flight classifier detects delegation requests ("do this in the background while we keep talking") and spawns the agent *deterministically*. Normal chat passes through untouched with thinking on. See [`RESEARCH_FINDINGS.md`](RESEARCH_FINDINGS.md) §8-11.
- Fire-and-forget background agents that code independently
- Context briefing from spawning model
- JSONL logging with full replay/resumption (`agents/logs/`)
- Token budget awareness (spawned agents are given a deliberately conservative ~85K budget — not a per-slot capacity; under unified KV slots share one pool, see [`BEAST_SLOT.md`](BEAST_SLOT.md))

## Frontends
- [Open WebUI](https://github.com/open-webui/open-webui): browser chat with persistent history, file upload, tool use
- [OpenCode](https://opencode.ai): terminal coding agent with built-in tools
- `agent.sh`: headless autonomous agent for scripted/scheduled tasks

## Remote access & beast-slot (client/server)
- **Tailscale remote access** (`scripts/setup-tailscale.sh`): the whole stack stays loopback-bound; your private tailnet gets HTTPS access to the WebUI (`:443`) and the OpenAI-compatible API (`:8443`) — WireGuard device identity, never the public internet (funnel is deliberately not offered)
- **beast-slot**: the rig remains the full command center AND publishes its intelligence for other devices — `--publish-slot` adds a read-only discovery API (`:8444/api/slot`: the *actually loaded* model, slot busy/total, context, health) so clients know what they're talking to; slot-count-agnostic contract, ready for future multi-slot/fleet serving. See [`BEAST_SLOT.md`](BEAST_SLOT.md)
- **OpenBeast client** (`scripts/setup-client.sh`, macOS + Linux): any device runs OpenCode + the complete 15-tool arsenal locally — files, shell, and spawned agents act on the *client's* disk — while inference streams from the rig; `openbeast-client` CLI (status/agent/search/update/uninstall), optional client-local SearXNG (`--local-search`), optional bearer auth (`--api-key`). Data-flow promise: nothing leaves your tailnet
- **Keyed mode** (`LLAMA_API_KEY`, off by default): when enabled, the entire stack presents the bearer — WebUI, healthcheck, agents, evals, router, dashboard, clients

## Multi-device / multi-user (beast-gate)
Local inference servers have no concept of a *user*: llama.cpp offers one flat API key, no per-caller accounting, opportunistic slot assignment, and an unbounded queue with no timeout or preemption — and publishing it exposes its entire route table. `agents/edge.py` is the missing hop, and the only place on the inference path where identity exists. Opt-in (`EDGE_GATE=true`), fails closed, and it does not sit in front of your local frontends — enabling it changes what the *tailnet* sees, not what the rig does.
- **Per-device bearer keys**, hot-reloaded from `.run/clients.json` — revoking a lost laptop blocks its next request within seconds, with no llama-server restart (which would destroy the KV cache and every live conversation)
- **Strict path allowlist**: remote callers reach `/health`, `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` and nothing else. `/lora-adapters` (global model mutation), `/slots`, `/props`, `/v1/stream/<id>` (read or cancel another generation) and `/infill` return **404** — not 403, so a caller learns nothing about what exists
- **Tenancy hardening**: client-supplied `id_slot` is stripped (upstream it is unauthenticated, wraps onto another tenant's slot, and jumps the deferred queue) and re-injected only from the server-side device→slot map; `X-Conversation-Id` is namespaced per device; client-asserted identity headers are stripped and re-asserted from the authenticated device
- **Admission control** the model server cannot provide: token bucket (`EDGE_RATE_LIMIT`) plus a concurrent-generation cap (`EDGE_MAX_INFLIGHT`) per device, returning 429 with `Retry-After`
- **Inference audit + metering**: one row per completion in `.run/inference-audit.jsonl` — `request_id` (echoed to the caller as `X-OpenBeast-Request-Id`), `device`, enrollment-stable `device_uid`, `user_claimed`, model, status, duration, prompt/completion tokens. Never content, never key material. Prometheus at `/gate/metrics` (requires a device key or the rig-local token — it exposes per-device usage); rotated by logrotate
- **Device lifecycle** (`scripts/clients.sh`): `enroll` (key printed once; only its sha256 is stored) · `list` · `show` · `revoke` / `unrevoke` · `rotate` · `remove`. Optional `--slot N` pins a device to a llama-server slot, `--rate N` overrides its limit

## Model governance
- **Weight-registry enforcement** (`WEIGHT_ENFORCE`): `scripts/weights.registry` pins sha256 + byte size for every shipped GGUF, and `serve.sh` now checks the weight it is about to load — closing the last gap in a supply chain where images are digest-pinned and Python deps are hash-pinned. `warn` (default) logs and starts; `strict` refuses; `off` disables. Size-only per launch — a 20 GB sha256 on every start would cost minutes; `verify-weights.sh --deep` is the hash-level check
- **Strict refusals are not "crashes."** A `strict` rejection exits **3**, and `start.sh` refuses to apply `MODEL_ROLLBACK` to it — rolling back would silently serve a *different* model than you configured, defeating the check entirely. `doctor` reports when `strict` is safe to enable on your rig
- **Eval quality gate** — promotion by evidence: `doctor` resolves the default serve script through `evals/benchmark_all.py`'s registry and warns when that model has no leaderboard row **on this host**, or when the script isn't registered for benchmarking at all. A warning, not a refusal — running an unevaluated model knowingly is legitimate; running one unknowingly is the thing worth catching

## Operations
- **Drive wear tracking** (`scripts/ssd-wear.sh`, plus a `doctor` row): agent workloads write hard — model loads, eval-cache and KV churn, log growth, swap storms — and NAND dies by writes. Reads SMART **read-only**, resolves the drives actually backing your weights and repo mounts (through `lsblk --inverse`, so LUKS/LVM layouts work), and reports GB-written/day plus projected days to 100% wear from a bounded history in `.run/ssd-wear.json`. Advisory by design: always exits 0, and **warns rather than staying silent** when it can't read — a green row for health nobody measured is the worst outcome such a tool can produce. `--json` for machines, `--snapshot` to record a datapoint
- Daemon mode: `./start.sh -d` returns when the model is loaded and keeps the stack running in a **memory-capped scope** (a runaway process can never take down the box); `./start.sh --status` shows what's up, and `./stop.sh` shuts everything down gracefully any time
- Health monitor with auto-restart (`scripts/healthcheck.sh`)
- **`./start.sh doctor`** — one-shot diagnosis of a configured/running stack: GPU floor + VRAM headroom, disk, file modes and secret hygiene, pinned dependency drift, digest-pinned images, and per-service health, each with a fix hint (exit 1 on any failure). Where `bootstrap --preflight` checks "can I install here", doctor checks "is what I installed healthy and secure"
- **Extension system** (`scripts/ext.sh`): hot-pluggable optional services (compose fragments or background processes) that attach without editing core files — ships with a read-only status dashboard. See [`extensions/README.md`](../extensions/README.md).
- End-to-end smoke test (`tests/test_smoke.sh`)
- **291-unit eval suite** (137 base tasks, 31 with multi-language variants across 6 languages; hardened v4) with automated validation; full distribution in [`evals/README.md`](../evals/README.md)
- **Multi-model benchmark** runner (`evals/benchmark_all.py`) that sweeps every model and produces a **capability-ranked** leaderboard (scoring v2: problem-solving + language breadth)
- Per-task tracking of accuracy, speed, prompt/completion tokens, and API-equivalent cost (`evals/scoring.py`)
- Multi-language variant support: a single task can have Python / Go / C / C++ / Rust / Zig versions (6 languages), scored fractionally
- Test suite covering scripts, tools, MCP server, and eval tasks (`tests/run_tests.sh`)
