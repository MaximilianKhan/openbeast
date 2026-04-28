# Local LLM Setup (RTX 5090, Arch Linux)

Setup completed 2026-04-22. Restructured 2026-04-26.

## Directory layout

```
models/
├── llama.cpp/              # inference engine (built with CUDA) [gitignored]
├── weights/                # GGUF model files [gitignored]
│   ├── Qwen3.6-27B-Q4_K_M.gguf
│   ├── Qwen3.6-27B-UD-Q5_K_XL.gguf
│   ├── Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf
│   └── Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
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
│   ├── run-qwen-27b-q4.sh
│   ├── run-qwen-27b-q5.sh
│   ├── run-qwen-27b-uncensored-q5.sh
│   └── run-qwen-35b-a3b.sh
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
├── system-prompt.md        # system prompt ("soul file") applied to all models
├── opencode.json           # OpenCode config (local provider + models)
├── docker-compose.yml      # Open WebUI container config
├── SETUP.md
└── INSTALL.md
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

| Context | Model | KV Cache | **Total** | Headroom |
|---------|-------|----------|-----------|----------|
| 64K     | 19 GB | 1.1 GB   | **20.1 GB** | 11.9 GB |
| 262K    | 19 GB | 4.5 GB   | **23.5 GB** | 8.5 GB  |
| **416K** (default) | 19 GB | 7.3 GB | **26.3 GB** | ~2 GB (after OS) |
| 512K    | 19 GB | 9.0 GB   | **28.0 GB** | OOM (OS VRAM) |
| 768K    | 19 GB | 13.5 GB  | **32.5 GB** | OOM     |

**Uncensored (HauhauCS Aggressive) Q5_K_P (~21GB weights)**

| Context | Model | KV Cache | **Total** | Headroom |
|---------|-------|----------|-----------|----------|
| 64K     | 21 GB | 1.1 GB   | **22.1 GB** | 9.9 GB  |
| 262K    | 21 GB | 4.5 GB   | **25.5 GB** | 6.5 GB  |
| **416K** (default) | 21 GB | 7.3 GB | **28.3 GB** | ~2 GB (after OS) |
| 512K    | 21 GB | 9.0 GB   | **30.0 GB** | OOM (OS VRAM) |

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

All models share a system prompt defined in `system-prompt.md` at the repo root.
This is the local equivalent of Claude's CLAUDE.md — it sets the model's persona,
communication style, and operating principles.

**How it propagates:**

- **Open WebUI:** `configure-webui.sh` reads `system-prompt.md` and writes it to
  each model's `meta.system` field in the database. This is applied automatically
  to every new chat with that model. Runs on every `./start.sh`.
- **agent.sh / runner.py:** The agent runner loads `system-prompt.md` at startup
  and prepends it to the agent's task-specific system prompt.
- **Interactive chat (run scripts):** llama.cpp `run.sh` does not inject a system
  prompt — the chat template handles it. You can pass one manually with
  `--system-prompt-file system-prompt.md` if desired.

**To edit:** Change `system-prompt.md` and re-run `./configure-webui.sh` (or
restart the stack with `./start.sh`). The new prompt takes effect on the next chat.

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

Agents are context-aware: they receive an approximate token budget (~73K per slot)
and briefing context from the spawning model. Failed agents can be resumed from
their JSONL log with `./agent.sh --resume agents/logs/agent-{id}.jsonl "continue"`.

Agents run as detached processes in their own process group. The MCP server
cleans up any running agents on shutdown via `atexit`. Agent logs are written to
`agents/logs/agent-{id}.jsonl` — the same JSONL format used by `agent.sh`.

If the MCP server restarts, `check_agent` can still read status from log files
on disk (no live process control, but full history is available).

## 5. Run

### Full stack

```bash
./start.sh                          # 27B Uncensored Q5 + MCP tools + Open WebUI + SearXNG
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
```

### OpenAI-compatible API server

```bash
./scripts/serve-qwen-27b-q4.sh     # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-q5.sh     # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-uncensored-q5.sh  # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-35b-a3b.sh    # http://localhost:8080/v1/chat/completions
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
./scripts/serve-qwen-27b-q4.sh -np 14        # override parallel slots (default 7)
./scripts/run.sh -m weights/some-model.gguf   # use generic script directly
```

### Parallel request handling

The server runs **7 parallel slots** by default with **unified KV cache** and
**continuous batching**. This means:

- Up to 7 concurrent requests are processed simultaneously (chat + background agents)
- Unified KV cache shares the context budget across all slots — **no extra VRAM**
  compared to a single-slot configuration
- Idle slots are freed automatically, so a single deep conversation can use more
  context while other slots are inactive
- Per-slot context: total context / slots (e.g. 512K / 7 = ~73K per slot)

Override with `-np` for more or fewer slots:

```bash
./scripts/serve-qwen-35b-a3b.sh -np 14   # 14 slots (~36K context per slot)
./scripts/serve-qwen-27b-q4.sh -np 3     # 3 slots (~170K context per slot)
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

Benchmark model performance on standardized coding tasks:

```bash
python3 evals/run_eval.py --list                     # list available tasks
python3 evals/run_eval.py                             # run all 10 tasks
python3 evals/run_eval.py --tasks 01,02,03            # run specific tasks
python3 evals/run_eval.py --max-iter 5                # limit iterations
```

Tasks range from easy (create a file) to hard (build a REST API endpoint).
Each task has automated validation — the harness checks the agent's work
against expected output. Results are saved to `evals/results/` as JSON for
comparison across models, quantizations, and prompt changes.

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
