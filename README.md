# Local AI Stack

A fully local, GPU-accelerated AI coding workstation. Run frontier-class language models on your own hardware with a complete tool suite, autonomous agents, web search, and multiple frontends — no cloud APIs, no API keys, no data leaving your machine.

Built on an RTX 5090 (32GB) running Arch Linux. Default model: **Qwen3.6-27B Uncensored** (Q5_K_P) — scores within 4 points of Claude Opus on SWE-bench. 10/10 on our internal eval harness.

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
- 6 parallel request slots with unified KV cache
- 4 pre-configured Qwen 3.6 models (27B dense, 35B MoE, uncensored variants)
- Up to 512K context (1M on the MoE model)

**Tool Suite (15 MCP tools)**
- File operations: `read_file`, `write_file`, `edit_file`, `list_files`
- Code search: `grep` (regex), `list_files` (glob)
- Shell: `bash` with timeout and output capture
- Web: `fetch` (URL content), `web_search` (SearXNG)
- Agent management: `start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent`

**Autonomous Agents**
- Fire-and-forget background agents that code independently
- Context briefing from spawning model
- JSONL logging with full replay/resumption
- Token budget awareness (~85K per slot)

**Frontends**
- [Open WebUI](https://github.com/open-webui/open-webui) — browser chat with persistent history, file upload, tool use
- [OpenCode](https://opencode.ai) — terminal coding agent with built-in tools
- `agent.sh` — headless autonomous agent for scripted tasks

**Operations**
- Health monitor with auto-restart (`scripts/healthcheck.sh`)
- End-to-end smoke test (`tests/test_smoke.sh`)
- 10-task eval harness with automated validation (`evals/run_eval.py`)
- 79-test suite covering scripts, tools, and MCP server

## Quick Start

```bash
# 1. Install (see INSTALL.md for full steps)
git clone <repo> && cd models
# Build llama.cpp, download models, install deps...

# 2. Start the full stack
./start.sh

# 3. Use it
open http://localhost:3000          # Open WebUI
opencode                           # Terminal agent (from any directory)
./agent.sh "add tests for auth.py" # Autonomous agent
```

## Models

| Model | Quant | Size | Context | VRAM | Notes |
|-------|-------|------|---------|------|-------|
| Qwen3.6-27B | Q4_K_M | 16 GB | 512K | 25 GB | Fast, good quality |
| Qwen3.6-27B | Q5_K_XL | 19 GB | 416K | 30.7 GB | Higher fidelity (margin only 9 MiB above 2GB rule) |
| **Qwen3.6-27B Uncensored** | **Q5_K_P** | **21 GB** | **380K** | **30.7 GB** | **Default model** |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 23 GB | Fast, KV-efficient, 1M capable |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 220K | 30.7 GB | Different family; KV cost rises with context (20→25 KB/token) |

## Project Structure

```
start.sh                    # Launch full stack
stop.sh                     # Stop everything
agent.sh                    # Run autonomous agent

scripts/                    # Server, chat, and config scripts
  serve.sh / run.sh         # Generic launchers
  serve-qwen-*.sh           # Model-specific API servers
  run-qwen-*.sh             # Model-specific chat
  configure-webui.sh        # Auto-configure Open WebUI
  healthcheck.sh            # Service health monitor

agents/                     # Agent framework + MCP tools
  mcp_server.py             # MCP tool server (15 tools)
  runner.py                 # Autonomous agent loop
  tools.py                  # Tool schemas and handlers

tests/                      # Test suite (79 tests)
evals/                      # Eval harness (10 coding tasks)

system-prompt.md            # Soul file (persona, all frontends)
system-prompt-tools.md      # Tool guidance (Open WebUI only)
docker-compose.yml          # Open WebUI + SearXNG containers
opencode.json               # OpenCode project config
```

## Documentation

- **[INSTALL.md](INSTALL.md)** — Step-by-step installation, prerequisites, troubleshooting
- **[REFERENCE.md](REFERENCE.md)** — VRAM tables, architecture details, all configuration options
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

- NVIDIA GPU with CUDA (tested on RTX 5090, works on 3090/4090)
- Arch Linux (or any Linux with CUDA + Docker)
- Python 3.10+, Docker, cmake, gcc
