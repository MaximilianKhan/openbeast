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
- **144-task eval harness** (40 easy / 53 medium / 51 hard) across 12 categories with automated validation (`evals/run_eval.py`)
- **Multi-model benchmark** runner (`evals/benchmark_all.py`) — sweeps every model and produces a ranked leaderboard
- Per-category accuracy breakdown across 12 domains (Algorithms, SWE, Math Finance, Stats, Pure Math, LLM/ML, SysDesign, Concurrency, Physics, Performance, Security, Signal Processing & DSP)
- Composite scoring with separate correctness + speed columns (`evals/scoring.py`)
- Test suite covering scripts, tools, MCP server, and eval tasks (`tests/run_tests.sh`)

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
| Qwen3.6-27B | Q5_K_XL | 19 GB | 416K | 30.7 GB | Higher fidelity (margin only 9 MiB above 2GB rule) |
| **Qwen3.6-27B Uncensored** | **Q5_K_P** | **21 GB** | **380K** | **30.7 GB** | **Default model** |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 27.8 GB | Fast, KV-efficient, 1M capable; ~4.3 GB headroom (measured) |
| Qwen3.6-35B-A3B Uncensored | Q4_K_M | 20 GB | 512K | 27.1 GB | Uncensored fine-tune (HauhauCS Aggressive); ~4.9 GB headroom (measured) |
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

evals/                       # Eval harness — 144 tasks + multi-model benchmark
  run_eval.py                # Single-model eval runner (model-tagged results)
  scoring.py                 # Accuracy/speed/composite + per-category breakdown
  benchmark_all.py           # Multi-model sweep orchestration
  tasks/                     # Per-task JSON definitions (01–144) with category tags
  results/                   # Per-run results (kept all, model-tagged) [gitignored]
  leaderboard.json           # Latest score per model + per-category drilldown (auto-updated)

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

## Evals & Benchmarking

The 144-task eval harness covers 12 categories spanning core software engineering,
math, physics, ML/LLM internals, distributed systems, security, signal processing,
and more — every
task is self-contained (setup + validation + cleanup) with deterministic checks.

**Latest leaderboard** (NVIDIA GeForce RTX 5090 ×1, sweep 2026-05-05/06):

| # | Model | Acc | Speed | Pass | Hard | Time |
|---:|---|---:|---:|---:|---:|---:|
| 1 | **Qwen 35B-A3B Uncensored Q4_K_M** | **97.3** | 86.7 | **140/144** | **49/51** | 50 min |
| 2 | Qwen 27B Uncensored Q5_K_P | 96.4 | 72.5 | 139/144 | 49/51 | 97 min |
| 3 | Qwen 27B Q5_K_XL | 95.5 | 68.2 | 138/144 | 48/51 | 116 min |
| 4 | Gemma 4 31B-it Q5_K_XL | 94.6 | 57.4 | 137/144 | 47/51 | 127 min |
| 5 | Qwen 35B-A3B MoE Q4_K_M | 93.5 | 86.3 | 136/144 | 46/51 | 49 min |

Full report with per-category breakdowns and cross-system comparison guide: **[RESULTS.md](RESULTS.md)**.

**Ranking: accuracy is primary, speed is the tie-breaker.** Composite (`0.75 × accuracy + 0.25 × speed`) is shown for backwards compatibility but is no longer the sort key.
- **Accuracy**: difficulty-weighted pass rate (easy=1, medium=1.5, hard=2)
- **Speed**: average speed factor on passed tasks (budget 30s/90s/300s by difficulty)
- **Per-category breakdown**: every score is also reported per-category (Algorithms & DS, SWE / DevOps, Math Finance, Probability & Stats, Pure & Abstract Math, LLM / ML, Distributed / SysDesign, Concurrency & Systems, Physics, Performance & HW Opt, Security, Signal Processing & DSP) with subcategory drilldown — see `evals/scoring.py --by-category`
- **Multi-host comparison**: leaderboard is keyed by `(host_id, model_slug)`, so the same model run on different machines (e.g. RTX 5090 vs. 2×3090 Ti) coexist. See `evals/scoring.py --compare-hosts`.

```bash
# Single-model eval (server must already be running)
python3 evals/run_eval.py                          # all 144 tasks
python3 evals/run_eval.py --tasks 21,22,23         # subset
python3 evals/run_eval.py --model-name custom-name # override auto-detected name

# Multi-model sweep — stops/starts each serve script in turn, ~7-9 hours for all 5
python3 evals/benchmark_all.py                     # full sweep
python3 evals/benchmark_all.py --models gemma-4-31b-q5,qwen-27b-q5
python3 evals/benchmark_all.py --list              # show configured models

# Scoring + leaderboard
python3 evals/scoring.py --show                    # current leaderboard
python3 evals/scoring.py --by-category             # per-category accuracy table
python3 evals/scoring.py --compare-hosts           # side-by-side across systems
python3 evals/scoring.py --host "NVIDIA GeForce RTX 5090 ×1"   # filter to one host
python3 evals/scoring.py --rebuild                 # regenerate from results/
python3 evals/scoring.py --score evals/results/eval-*.json  # score one file
```

Results land in `evals/results/eval-{model_slug}-{timestamp}.json` (one per run,
all kept). Each result file embeds the model name and a snapshot of the GPU
config (`nvidia-smi` — name, driver, total VRAM, compute capability). The
leaderboard at `evals/leaderboard.json` keeps the latest score per model
including the per-category drilldown.

## Requirements

- NVIDIA GPU with CUDA (tested on RTX 5090; works on 3090/4090 — set `CMAKE_CUDA_ARCHITECTURES` accordingly)
- Linux with NVIDIA driver, CUDA toolkit, Docker, and Python 3.10+
- Disk: ~25 GB for llama.cpp + one model. Each model adds 16–21 GB.
- VRAM: 24 GB minimum for the smaller quants; 32 GB for the defaults
