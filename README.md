# Local AI Stack

A fully local, GPU-accelerated AI coding workstation. Run frontier-class language models on your own hardware with a complete tool suite, autonomous agents, web search, and multiple frontends — no cloud APIs, no API keys, no data leaving your machine.

Built and tuned on an RTX 5090 (32GB) running Arch Linux. Default model: **Qwen3.6-27B Uncensored** (Q5_K_P) — scores within 4 points of Claude Opus on SWE-bench. 10/10 on our internal eval harness.

## Architecture

```
                            +------------------+
                            |   Open WebUI     |  Browser chat (port 3000)
                            |   (Docker)       |
                            +--------+---------+
                                     |
                                     v
+----------------+          +--------+---------+          +----------------+
|   OpenCode     |          |   MCPO Proxy     |          |   SearXNG      |
|   (terminal)   |          |   (port 3001)    |          |   (port 8888)  |
+-------+--------+          +--------+---------+          +-------+--------+
        |                            |                            |
        | stdio                      | stdio                      |
        v                            v                            |
+-------+----------------------------+---------+                  |
|              MCP Tool Server                 | <----------------+
|         (agents/mcp_server.py)               |    web_search
|                                              |
|  bash | read | write | edit | grep | fetch   |
|  web_search | start_agent | check_agent      |
|  tail_agent | list_agents | stop_agent       |
+-----------------------+----------------------+
                        |
                        v
          +-------------+-------------+
          |     llama.cpp Server      |
          |      (port 8080)          |
          |  6 parallel slots         |
          |  unified KV cache         |
          |  continuous batching      |
          +---------------------------+
          |  RTX 5090 (32GB GDDR7)    |
          +---------------------------+
```

## Features

**Model Serving**
- llama.cpp with CUDA (Blackwell SM 120) — full GPU offload
- 6 parallel request slots with unified KV cache and continuous batching
- 5 pre-configured models (Qwen 27B dense, Qwen 35B MoE, Qwen 27B uncensored, Gemma 4 31B-it)
- Context lengths tuned to measured VRAM ceilings (380K–512K) on a 32GB card

**Tool Suite (13 MCP tools)**
- File operations: `read_file`, `write_file`, `edit_file`, `list_files`
- Code search: `grep` (regex), `list_files` (glob)
- Shell: `bash` with timeout and output capture
- Web: `fetch` (URL → readable text), `web_search` (via local SearXNG)
- Agent management: `start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent`

**Autonomous Agents**
- Fire-and-forget background agents that code independently
- Context briefing from spawning model
- JSONL logging with full replay/resumption (`agents/logs/`)
- Token budget awareness (~85K per slot)

**Frontends**
- [Open WebUI](https://github.com/open-webui/open-webui) — browser chat with persistent history, file upload, tool use
- [OpenCode](https://opencode.ai) — terminal coding agent with built-in tools
- `agent.sh` — headless autonomous agent for scripted/scheduled tasks

**Operations**
- Health monitor with auto-restart (`scripts/healthcheck.sh`)
- End-to-end smoke test (`tests/test_smoke.sh`)
- 10-task eval harness with automated validation (`evals/run_eval.py`)
- Test suite covering scripts, tools, and MCP server (`tests/run_tests.sh`)

## Quick Start (fresh Linux box)

Assuming NVIDIA driver, CUDA, Docker, and Python 3.10+ are installed:

```bash
# 1. Clone and enter the repo
git clone <repo-url> models && cd models

# 2. Build llama.cpp with CUDA (set CMAKE_CUDA_ARCHITECTURES for your GPU)
git clone https://github.com/ggml-org/llama.cpp.git
(cd llama.cpp && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 \
                 && cmake --build build --config Release -j$(nproc))

# 3. Install Python deps + Hugging Face CLI
pip install --user --break-system-packages huggingface-hub[cli] -r agents/requirements.txt

# 4. Download the default model (or pick another from INSTALL.md)
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/

# 5. Install OpenCode (terminal frontend)
curl -fsSL https://opencode.ai/install | bash

# 6. Pull Open WebUI image (browser frontend)
docker pull ghcr.io/open-webui/open-webui:main

# 7. Launch the full stack
chmod +x start.sh stop.sh agent.sh scripts/*.sh tests/*.sh
./start.sh
```

Then:
```bash
# Browser chat
xdg-open http://localhost:3000

# Terminal agent (from any project directory)
opencode

# Autonomous background agent
./agent.sh "add tests for auth.py"
```

See **[INSTALL.md](INSTALL.md)** for prerequisites, GPU/driver setup, alternate models, and troubleshooting.

## Models

| Model | Quant | Weights | Context | VRAM (measured) | Notes |
|-------|-------|---------|---------|-----------------|-------|
| Qwen3.6-27B | Q4_K_M | 16 GB | 512K | ~25 GB | Fast, good quality |
| Qwen3.6-27B | Q5_K_XL | 19 GB | 416K | 30.7 GB | Higher fidelity (margin only 9 MiB above 2GB rule) |
| **Qwen3.6-27B Uncensored** | **Q5_K_P** | **21 GB** | **380K** | **30.7 GB** | **Default model** |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | ~23 GB | Fast, KV-efficient, 1M capable |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 220K | 30.7 GB | Different family; KV cost rises with context (20→25 KB/token) |

All context lengths validated against the 2GB OS-headroom rule on a 32GB card. See `REFERENCE.md` for the full measurement curve.

## Project Structure

```
start.sh                     # Launch full stack (llama.cpp + MCPO + Open WebUI + SearXNG)
stop.sh                      # Stop everything
agent.sh                     # Run an autonomous agent

scripts/                     # Server, chat, and ops scripts
  serve.sh / run.sh          # Generic launchers (pick model with -m)
  serve-<model>.sh           # Model-specific API servers
  run-<model>.sh             # Model-specific interactive chat
  configure-webui.sh         # Auto-configure Open WebUI (tools + system prompt)
  healthcheck.sh             # Service health monitor (--restart to auto-recover)

agents/                      # Agent framework + MCP tool server
  mcp_server.py              # MCP tool server (13 tools, stdio + HTTP transports)
  runner.py                  # Autonomous agent loop (LLM + tool use)
  tools.py                   # Tool schemas/handlers for the standalone runner
  requirements.txt           # openai, mcp, mcpo
  logs/                      # Agent run logs (JSONL) [gitignored]

searxng/
  settings.yml               # Custom config: enables JSON format + disables limiter

tests/                       # Test suite
  run_tests.sh               # Run all tests
  test_tools.py              # MCP tool unit tests
  test_scripts.sh            # Script structure validation
  test_smoke.sh              # End-to-end stack smoke test (requires running stack)

evals/                       # Eval harness (10 coding tasks with automated validation)

system-prompt.md             # Soul file (persona, applied to all frontends)
system-prompt-tools.md       # Tool guidance (Open WebUI only)
docker-compose.yml           # Open WebUI + SearXNG containers
opencode.json                # OpenCode project config (MCP wiring + model list)
weights/                     # GGUF model files [gitignored]
llama.cpp/                   # Inference engine, built with CUDA [gitignored]
```

## Documentation

- **[INSTALL.md](INSTALL.md)** — Step-by-step installation, prerequisites, troubleshooting
- **[REFERENCE.md](REFERENCE.md)** — VRAM tables (measured), architecture details, all configuration options
- **[TODO.md](TODO.md)** — Roadmap and completed work

## Eval Results

```
Task                              Difficulty   Time     Result
Create a Python file              easy          6.6s    PASS
Edit an existing file             easy         10.1s    PASS
Find and fix a bug across files   medium       17.9s    PASS
Write unit tests                  medium       25.5s    PASS
Multi-file refactor               medium       12.3s    PASS
Debug a runtime error             medium       26.7s    PASS
Build a CLI tool from scratch     hard         13.0s    PASS
Add REST API endpoints            hard         31.4s    PASS
Write bash script w/ errors       medium       14.9s    PASS
Transform data between formats    hard         11.0s    PASS

Total: 10/10 passed              Avg: 17.0s
```

Run evals yourself: `python3 evals/run_eval.py`

## Requirements

- NVIDIA GPU with CUDA (tested on RTX 5090; works on 3090/4090 — set `CMAKE_CUDA_ARCHITECTURES` accordingly)
- Linux with NVIDIA driver, CUDA toolkit, Docker, and Python 3.10+
- Disk: ~25 GB for llama.cpp + one model. Each model adds 16–21 GB.
- VRAM: 24 GB minimum for the smaller quants; 32 GB for the defaults
