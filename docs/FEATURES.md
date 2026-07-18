# Features (complete)

The full capability breakdown. The README carries a condensed highlights
version; this is the exhaustive reference.

## Model Serving
- llama.cpp with CUDA (Blackwell SM 120), full GPU offload
- 6 parallel request slots with unified KV cache and continuous batching
- 17 pre-configured models (all VRAM/context-measured on the 5090), capability-ranked on the hardened v4 eval suite — default **Qwen 27B Uncensored Q5_K_P**; full lineup in [MODELS.md](MODELS.md), scores in the [leaderboard](RESULTS.md)
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
- Token budget awareness (~85K per slot)

## Frontends
- [Open WebUI](https://github.com/open-webui/open-webui): browser chat with persistent history, file upload, tool use
- [OpenCode](https://opencode.ai): terminal coding agent with built-in tools
- `agent.sh`: headless autonomous agent for scripted/scheduled tasks

## Operations
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
