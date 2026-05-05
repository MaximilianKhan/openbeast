# Technical Reference

## Directory layout

```
models/
├── llama.cpp/              # inference engine (built with CUDA) [gitignored]
├── weights/                # GGUF model files [gitignored]
│   ├── Qwen3.6-27B-Q4_K_M.gguf
│   ├── Qwen3.6-27B-UD-Q5_K_XL.gguf
│   ├── Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf
│   ├── Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
│   └── gemma-4-31B-it-UD-Q5_K_XL.gguf
├── start.sh                # launch full stack (server + MCPO + Open WebUI)
├── stop.sh                 # stop full stack
├── agent.sh                # run a local agent against a task
├── scripts/                # server, chat, and config scripts
│   ├── serve.sh            # generic OpenAI-compatible API server
│   ├── run.sh              # generic interactive chat launcher
│   ├── configure-webui.sh  # auto-configure Open WebUI
│   ├── serve-qwen-27b-q4.sh
│   ├── serve-qwen-27b-q5.sh
│   ├── serve-qwen-27b-uncensored-q5.sh
│   ├── serve-qwen-35b-a3b.sh
│   ├── serve-gemma-4-31b-q5.sh
│   ├── run-qwen-27b-q4.sh
│   ├── run-qwen-27b-q5.sh
│   ├── run-qwen-27b-uncensored-q5.sh
│   ├── run-qwen-35b-a3b.sh
│   └── run-gemma-4-31b-q5.sh
├── agents/                 # agent framework + MCP tool server
│   ├── runner.py           # standalone agent loop (LLM + tool use)
│   ├── tools.py            # tool definitions for standalone agent
│   ├── mcp_server.py       # MCP server (tools + long-running agent management)
│   ├── requirements.txt
│   └── logs/               # agent run logs (JSONL) [gitignored]
├── tests/                  # test suite
│   ├── run_tests.sh        # run all tests (unit + structure)
│   ├── test_tools.py       # tool unit tests
│   ├── test_scripts.sh     # script structure validation
│   └── test_smoke.sh       # end-to-end stack smoke test (requires running stack)
├── evals/                  # eval harness for model benchmarking
│   ├── run_eval.py         # eval runner
│   ├── tasks/              # task definitions (JSON)
│   └── results/            # eval results (JSON, gitignored)
├── system-prompt.md        # soul file — persona and principles (all frontends)
├── system-prompt-tools.md  # tool-use guidance (Open WebUI only)
├── opencode.json           # OpenCode config (local provider + models)
├── docker-compose.yml      # Open WebUI container config
├── README.md               # project overview (start here)
├── INSTALL.md              # step-by-step installation guide
├── REFERENCE.md            # this file — technical reference
└── TODO.md                 # roadmap and completed work
```

## VRAM estimates (RTX 5090 — 32GB)

All estimates use Q4_0 KV cache quantization (default).

**Important:** llama.cpp allocates KV cache for all layers, not just the attention layers
in the hybrid DeltaNet architecture. Theoretical savings from the hybrid design do not
apply to current llama.cpp KV allocation. All numbers below are based on real-world
measurements.

### Qwen3.6-27B — 64 layers, real-world KV cost: ~18 KB/token

**Q4_K_M (~16GB weights)**

| Context | Model | KV Cache | **Total** | Headroom |
|---------|-------|----------|-----------|----------|
| 64K     | 16 GB | 1.1 GB   | **17.1 GB** | 14.9 GB |
| 262K    | 16 GB | 4.5 GB   | **20.5 GB** | 11.5 GB |
| **512K** (default) | 16 GB | 9.0 GB | **25.0 GB** | 7.0 GB |
| 768K    | 16 GB | 13.5 GB  | **29.5 GB** | OOM    |

**Q5_K_XL (~19GB weights) — higher fidelity, tighter fit**

Re-measured 2026-05-05: actual KV cost runs denser than the original 18 KB/token
estimate. The 416K default still works but lands tight against the 2GB rule.

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| **416K** (default) | **30,711 MiB** | **2,057 MiB** | **measured — 9 MiB above 2GB rule (tight)** |
| 408K (suggested if OOMs) | ~30,560 MiB | ~2,200 MiB | safer alternative — comparable to other models' margin |

**Uncensored (HauhauCS Aggressive) Q5_K_P (~21GB weights)**

Re-measured 2026-05-05: actual KV cost runs denser than the original 18 KB/token
estimate (closer to ~20 KB at high context). The original 416K default was too
tight. New default: **380K**, validated at 2,120 MiB headroom.

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| **380K** (default) | **30,648 MiB** | **2,120 MiB** | **measured — meets 2GB rule** |
| 416K    | **31,405 MiB** | 1,363 MiB            | below 2GB rule — OOM risk on OS spikes |

### Gemma 4 31B-it — measured KV cost (non-linear, grows with context)

Different model family from Qwen (no DeltaNet, uses sliding-window attention).
Per-token KV starts close to Qwen 27B but rises with context length —
20 KB/token from 128K→200K, then 25 KB/token from 200K→250K. Measured 2026-05-05.

**Q5_K_XL (~20.4GB weights)**

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| 128K    | **28,680 MiB** | 4,088 MiB            | safe   |
| 200K    | **30,155 MiB** | 2,613 MiB            | safe   |
| **220K** (default) | **30,688 MiB** | **2,080 MiB** | **measured ceiling — exactly meets 2GB rule** |
| 250K    | **31,439 MiB** | 1,329 MiB            | below 2GB rule — OOM risk on OS spikes |

**Why default is 220K:** Each step beyond 200K costs more per token (KV growth
appears non-linear, possibly due to compute buffer scaling). 220K is the highest
context that respects the 2GB headroom rule. 250K worked but left no margin
for browser/video GPU spikes — same cliff that caused Qwen Q5_K_XL OOMs at 512K.

### Qwen3.6-35B-A3B — 40 layers, real-world KV cost: ~6.3 KB/token

| Context | Model | KV Cache | **Total** | Headroom |
|---------|-------|----------|-----------|----------|
| 64K     | 20 GB | 0.4 GB   | **20.4 GB** | 11.6 GB |
| 262K    | 20 GB | 1.6 GB   | **21.6 GB** | 10.4 GB |
| **512K** (default) | 20 GB | 3.1 GB | **23.1 GB** | 8.9 GB |
| 768K    | 20 GB | 4.7 GB   | **24.7 GB** | 7.3 GB  |
| 1M      | 20 GB | 6.3 GB   | **26.3 GB** | 5.7 GB  |

> **Note:** Real-world measurement (2026-04-27): model=20,583 MiB, KV at 512K=3,131 MiB,
> compute=1,580 MiB. The MoE 35B-A3B is significantly more KV-efficient than the
> dense 27B (~6.3 vs ~18 KB/token). Could safely run at 1M context.

## 1. System packages

```bash
sudo pacman -S cuda cmake
```

- `cuda` (13.2.0) — installed to `/opt/cuda/`
- `cmake` (4.3.1)
- `gcc`/`g++` were already present
- NVIDIA driver (`nvidia-utils 595.58.03`) was already present

Note: the omarchy mirror choked on the cuda `.sig` file. Worked after retrying
or pulling from a different mirror.

## 2. Build llama.cpp with CUDA

```bash
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
export PATH=/opt/cuda/bin:$PATH
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build --config Release -j$(nproc)
```

- `DCMAKE_CUDA_ARCHITECTURES=120` targets RTX 5090 (Blackwell, SM 120)
- Binaries land in `llama.cpp/build/bin/` (`llama-cli`, `llama-server`, etc.)

## 3. Download models

All weights go into `weights/`.

```bash
pip install --user --break-system-packages huggingface-hub[cli]
```

### Qwen3.6-27B (hybrid DeltaNet + attention)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-Q4_K_M.gguf --local-dir weights/
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir weights/
```

- Source: https://huggingface.co/unsloth/Qwen3.6-27B-GGUF
- **Hybrid architecture:** 48 DeltaNet layers + 16 gated attention layers (64 total)
- Native max: 262K, extended via YaRN to ~1M
- Real-world KV cost: **~18 KB/token** (llama.cpp allocates KV for all 64 layers)
- **Q4_K_M** (~16GB): default **512K** context, ~25GB total
- **Q5_K_XL** (~19GB): default **416K** context, ~26.3GB total — higher weight fidelity

### Qwen3.6-27B Uncensored (HauhauCS Aggressive)

```bash
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/
```

- Source: https://huggingface.co/HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive
- Same base architecture as Qwen3.6-27B (64 layers, ~18 KB/token KV cost)
- Fine-tuned with safety filters removed
- **Q5_K_P** (~21GB): default **416K** context, ~28.3GB total

### Gemma 4 31B-it (Q5_K_XL — 20.4GB)

```bash
hf download unsloth/gemma-4-31B-it-GGUF gemma-4-31B-it-UD-Q5_K_XL.gguf --local-dir weights/
```

- Source: https://huggingface.co/unsloth/gemma-4-31B-it-GGUF
- Different family from Qwen — uses sliding-window attention
- KV cost not yet measured; default **128K** context (conservative)
- Raise `-c` after validating actual VRAM usage with a real launch

### Qwen3.6-35B-A3B (MoE + hybrid DeltaNet, Q4_K_M — 22GB)

```bash
hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --local-dir weights/
```

- Source: https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF
- **Hybrid architecture:** 30 DeltaNet layers + 10 gated attention layers (40 total)
- **MoE:** 256 experts, 9 active per token (8 routed + 1 shared). 35B total, 3B active — fast inference.
- Native max: 262K, extended via YaRN to ~1M
- Real-world KV cost: **~6.3 KB/token** (measured 2026-04-27, much lower than the 27B)
- Default **512K** context, ~23.1GB total — could safely run at 1M context

## 4. Frontends

### OpenCode (terminal coding agent)

Configured via `opencode.json`. Connects to the llama.cpp server on port 8080.
MCP tools (bash, read/write files, grep) are available via stdio transport —
OpenCode launches the MCP server automatically.

Run `opencode` in any project directory while the server is running.

### Open WebUI (browser chat interface)

Configured via `docker-compose.yml`. Runs as a Docker container on port 3000.
Connects to the llama.cpp server on port 8080 via `localhost`.

Tools are exposed via **MCPO** (MCP-to-OpenAPI proxy) on port 3001. Open WebUI's
native MCP (Streamable HTTP) support has a known bug, so we use MCPO to wrap
our MCP server as OpenAPI endpoints that Open WebUI consumes natively.

**Setup:** Automatic — `./start.sh` runs `configure-webui.sh` which registers the
MCPO tool server and sets native function calling for all detected models.
On a fresh install, the first `./start.sh` handles everything.

- Persistent conversation history (Docker named volume, survives stop/start)
- File upload and RAG
- Web search (configurable)
- Tool use via MCPO (bash, file I/O, edit, grep, fetch, agent management)

### System prompt (soul file)

The system prompt is split into two files:

- **`system-prompt.md`** — The "soul file." Persona, communication style, and
  operating principles. Shared across all frontends. Contains no tool references
  so it works cleanly with any frontend's own tool system.
- **`system-prompt-tools.md`** — Tool-use guidance for our MCP tools (`edit_file`,
  `web_search`, `start_agent`, etc.). Only appended for frontends that use our
  MCP tools directly.

**How it propagates to each frontend:**

| Frontend | Soul | Tool Guidance | Mechanism |
|----------|------|---------------|-----------|
| **Open WebUI** | `system-prompt.md` | `system-prompt-tools.md` | `configure-webui.sh` concatenates both into the model's DB entry |
| **OpenCode** | `system-prompt.md` | OpenCode's own built-in schemas | OpenCode injects its own tool descriptions — no overlap |
| **agent.sh / runner.py** | `system-prompt.md` | Inline `_AGENT_INSTRUCTIONS` | Runner builds its own prompt with soul + agent-specific guidance |
| **Interactive chat** | (not injected) | — | Pass manually with `--system-prompt-file system-prompt.md` |

The split exists because OpenCode has its own tool names (`edit` vs `edit_file`,
`view` vs `read_file`) and injects its own tool schemas. Mixing our tool
descriptions into OpenCode's prompt would create confusion and redundancy.

**To edit:** Change `system-prompt.md` (persona) or `system-prompt-tools.md`
(tool guidance) and re-run `./scripts/configure-webui.sh` (or restart the stack
with `./start.sh`). Changes take effect on the next new chat.

### MCP tool server + MCPO proxy

Exposes local tools (bash, read_file, write_file, edit_file, list_files, grep,
fetch, web_search) and long-running agent management (start_agent, check_agent,
tail_agent, list_agents, stop_agent) to any MCP-compatible client.

- **stdio** — used by OpenCode (launched automatically via `opencode.json`)
- **MCPO** — used by Open WebUI (`http://localhost:3001`, started by `./start.sh`).
  MCPO wraps the MCP server (via stdio) as OpenAPI endpoints. This works around
  Open WebUI's broken native MCP Streamable HTTP support.

#### Long-running agents via MCP

The MCP server can spawn autonomous background agents that iterate independently
using `runner.py`. The model in a chat session can dispatch complex tasks to an
agent, continue the conversation, and check back for results later.

- **`start_agent(task, workdir, max_iter, context)`** — spawn a background agent
  with optional context briefing, returns an agent ID immediately (non-blocking)
- **`check_agent(agent_id)`** — returns status, iteration count, recent tool calls,
  last model reasoning, and the final summary if complete
- **`tail_agent(agent_id, lines)`** — raw JSONL log tail for detailed debugging
- **`list_agents()`** — tabular overview of all tracked agents
- **`stop_agent(agent_id)`** — graceful SIGTERM, escalates to SIGKILL after 10s

Agents are context-aware: they receive an approximate token budget (~85K per slot)
and briefing context from the spawning model. Failed agents can be resumed from
their JSONL log with `./agent.sh --resume agents/logs/agent-{id}.jsonl "continue"`.

Agents run as detached processes in their own process group. The MCP server
cleans up any running agents on shutdown via `atexit`. Agent logs are written to
`agents/logs/agent-{id}.jsonl` — the same JSONL format used by `agent.sh`.

If the MCP server restarts, `check_agent` can still read status from log files
on disk (no live process control, but full history is available).

## 5. Run

### Full stack

The default model is **Qwen3.6-27B Uncensored (HauhauCS Aggressive) Q5_K_P**.

```bash
./start.sh                          # default model + MCP tools + Open WebUI + SearXNG
./start.sh serve-qwen-27b-q4.sh     # use a different model
./stop.sh                           # stop everything (server, MCP, Open WebUI, SearXNG)
```

### Web search (SearXNG)

Local web search is provided by SearXNG, running as a Docker container on port 8888.
It's included in `docker-compose.yml` and starts automatically with `./start.sh`.
No API keys needed — SearXNG aggregates results from public search engines.

The `web_search` MCP tool uses SearXNG to search the web and return titles, URLs,
and snippets. Models can also use `fetch` to read full page content from search results.

### Interactive chat (standalone)

```bash
./scripts/run-qwen-27b-q4.sh       # Qwen 27B Q4_K_M  (512K ctx, ~25GB VRAM)
./scripts/run-qwen-27b-q5.sh       # Qwen 27B Q5_K_XL (416K ctx, ~26.3GB VRAM)
./scripts/run-qwen-27b-uncensored-q5.sh  # Qwen 27B Uncensored Q5_K_P (416K ctx, ~28.3GB VRAM)
./scripts/run-qwen-35b-a3b.sh      # Qwen 35B-A3B MoE (512K ctx, ~23.1GB VRAM)
./scripts/run-gemma-4-31b-q5.sh    # Gemma 4 31B-it Q5_K_XL (128K ctx, ~20.4GB model + KV TBD)
```

### OpenAI-compatible API server

```bash
./scripts/serve-qwen-27b-q4.sh     # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-q5.sh     # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-uncensored-q5.sh  # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-35b-a3b.sh    # http://localhost:8080/v1/chat/completions
./scripts/serve-gemma-4-31b-q5.sh  # http://localhost:8080/v1/chat/completions
```

### Agent (autonomous long-running tasks)

```bash
./agent.sh "add unit tests for the auth module"
./agent.sh -w ~/projects/myapp "refactor logging to use structured output"
./agent.sh -f tasks/my-task.md                    # read task from file
./agent.sh --max-iter 50 "fix the failing tests"  # limit iterations
```

The agent loops the model with tool use (bash, file I/O, grep) until the task is
done or max iterations are reached. Logs are written to `agents/logs/`.

Agents can also be spawned from within a chat session (Open WebUI or OpenCode)
using the `start_agent` MCP tool — the model dispatches work to a background
agent and checks on it with `check_agent`.

### Custom flags

All model scripts forward extra args to `run.sh`/`serve.sh`, which forward to llama.cpp:

```bash
./scripts/run-qwen-27b-q5.sh -c 524288       # override context length
./scripts/serve-qwen-35b-a3b.sh -p 9090      # override port
./scripts/serve-qwen-27b-q4.sh -np 10        # override parallel slots (default 6)
./scripts/run.sh -m weights/some-model.gguf   # use generic script directly
```

### Parallel request handling

The server runs **6 parallel slots** by default with **unified KV cache** and
**continuous batching**. This means:

- Up to 6 concurrent requests are processed simultaneously (chat + background agents)
- Unified KV cache shares the context budget across all slots — **no extra VRAM**
  compared to a single-slot configuration
- Idle slots are freed automatically, so a single deep conversation can use more
  context while other slots are inactive
- Per-slot context: total context / slots (e.g. 512K / 6 = ~85K per slot)

Override with `-np` for more or fewer slots:

```bash
./scripts/serve-qwen-35b-a3b.sh -np 12   # 12 slots (~42K context per slot)
./scripts/serve-qwen-27b-q4.sh -np 3    # 3 slots (~170K context per slot)
```

Monitor active slots at `http://localhost:8080/slots` and KV cache usage at
`http://localhost:8080/metrics`.

### Health monitoring

```bash
./scripts/healthcheck.sh              # check all services + GPU VRAM + slot usage
./scripts/healthcheck.sh --restart    # check and auto-restart failed services
```

Reports status of llama.cpp, MCPO, Open WebUI, SearXNG, GPU VRAM utilization, and
active slot count. With `--restart`, automatically restarts any service that's down.

### Eval harness

30 tasks across three difficulty tiers (2 easy / 15 medium / 13 hard). Each
task has deterministic validation (Python or bash returning exit 0/1). Results
are tagged with the model name (auto-detected via `/v1/models`) and a snapshot
of the GPU config (`nvidia-smi`).

#### Single-model eval

```bash
python3 evals/run_eval.py --list                     # list all 30 tasks
python3 evals/run_eval.py                             # run everything
python3 evals/run_eval.py --tasks 21,22,23            # subset
python3 evals/run_eval.py --max-iter 5                # cap iterations
python3 evals/run_eval.py --model-name custom        # override auto-detected name
```

Results land in `evals/results/eval-{model_slug}-{timestamp}.json` (all kept).

#### Multi-model benchmark

`evals/benchmark_all.py` runs the full suite against every configured model in
turn. For each: stops llama-server, starts the model's serve script, waits for
`/health`, runs the eval, kills the server, scores the run, updates the
leaderboard. If a model fails to launch or crashes mid-run, it's skipped and
flagged in the sweep summary.

```bash
python3 evals/benchmark_all.py                       # all 5 models, full suite
python3 evals/benchmark_all.py --models gemma-4-31b-q5,qwen-27b-q5
python3 evals/benchmark_all.py --tasks 21,22,23      # subset of tasks
python3 evals/benchmark_all.py --list                # show configured models
```

Total runtime estimate: ~30 tasks × ~90s avg × 5 models ≈ **3-4 hours**. Plan
to run overnight. Sweep summaries are saved to `evals/results/sweep-{ts}.json`.

#### Scoring + leaderboard

The composite score formula (see `evals/scoring.py`):

```
correctness = 100 × Σ(weight × passed) / Σ(weight)
              where weights: easy=1, medium=3, hard=5

speed       = 100 × mean(max(0, 1 - elapsed/budget)) over passed tasks
              where budgets: easy=30s, medium=90s, hard=300s

composite   = 0.75 × correctness + 0.25 × speed
```

Tie-breakers (in order): raw pass count → hard-task pass count → total elapsed.

```bash
python3 evals/scoring.py --show                      # current leaderboard
python3 evals/scoring.py --rebuild                   # rescore from results/
python3 evals/scoring.py --score evals/results/eval-foo.json
```

`evals/leaderboard.json` is auto-maintained — one entry per `model_slug`,
latest run wins. Result files in `evals/results/` are never deleted, so full
history is available for trend analysis.

### Smoke test (end-to-end)

Requires the full stack running (`./start.sh`):

```bash
./tests/test_smoke.sh
```

Validates: llama.cpp health, parallel slots, MCPO tool exposure, Open WebUI,
SearXNG, chat completion, native function calling, and direct tool invocation.

## Adding a new model

1. Download the GGUF to `weights/`
2. Create `run-<name>.sh` and `serve-<name>.sh` that call `run.sh`/`serve.sh` with `-m` and appropriate `-c`
3. `chmod +x` the new scripts
4. Document above
