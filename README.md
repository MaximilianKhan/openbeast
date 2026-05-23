# Local AI Stack

A fully local, GPU-accelerated AI coding workstation. Run frontier-class language models on your own hardware with a complete tool suite, autonomous agents, web search, and multiple frontends — no cloud APIs, no API keys, no data leaving your machine.

Built and tuned on an RTX 5090 (32GB) running Arch Linux. Default model: **Qwen3.6-35B-A3B Uncensored Q4_K_M** — top of the internal leaderboard at 97.3 % accuracy / 86.7 speed on the 144-task sweep, with the fastest wall-clock among 5 benchmarked models. Eval suite is now at v3.5 — 159 base tasks, 33 of them with full 6-language variants (~313 effective test units), result cache for retryable sweeps, and tool-selection efficiency analyzer. See [`docs/RESULTS.md`](docs/RESULTS.md) and [`evals/README.md`](evals/README.md) for full distribution and methodology.

## Architecture

```
                             ┌────────────────────┐
                             │     Open WebUI     │
                             │     (port 3000)    │
                             └──────────┬─────────┘
                                        │
                                        ▼
   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
   │      OpenCode      │    │     MCPO Proxy     │    │      SearXNG       │
   │     (terminal)     │    │     (port 3001)    │    │     (port 8888)    │
   └──────────┬─────────┘    └──────────┬─────────┘    └──────────┬─────────┘
              │                         │                         │
              │ stdio                   │ HTTP                    │ web_search
              ▼                         ▼                         ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                            MCP Tool Server                              │
   │                         (agents/mcp_server.py)                          │
   │                                                                         │
   │       bash · read · write · edit · grep · fetch                         │
   │       web_search · start_agent · check_agent                            │
   │       tail_agent · list_agents · stop_agent                             │
   └─────────────────────────────────────┬───────────────────────────────────┘
                                         │
                                         ▼
                          ┌────────────────────────────┐
                          │      llama.cpp Server      │
                          │        (port 8080)         │
                          │      6 parallel slots      │
                          │      unified KV cache      │
                          │     continuous batching    │
                          ├────────────────────────────┤
                          │   RTX 5090 · 32 GB GDDR7   │
                          └────────────────────────────┘
```

## Features

**Model Serving**
- llama.cpp with CUDA (Blackwell SM 120) — full GPU offload
- 6 parallel request slots with unified KV cache and continuous batching
- 9 pre-configured models: 5 measured + benchmarked (Qwen 27B dense, Qwen 27B uncensored, Qwen 35B MoE, **Qwen 35B-A3B uncensored** as default, Gemma 4 31B-it) and 4 scaffolded 2026-05-22 awaiting first launch (Qwen 27B MTP, Qwen 35B-A3B MTP, Qwopus 27B v2, Qwopus 27B v2 MTP)
- Context lengths tuned to measured VRAM ceilings (192K–512K) on a 32GB card; MTP variants additionally pin `-np 1` per upstream constraint

**Tool Suite (17 MCP tools)**
- File operations: `read_file`, `write_file`, `edit_file`, `list_files`
- Code search: `grep` (regex), `list_files` (glob)
- Shell: `bash` with timeout and output capture
- Web: `fetch` (URL → readable text), `web_search` (via local SearXNG)
- Agent management: `start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent`
- Skills (curated expertise packages): `list_skills`, `load_skill`, `start_skill_agent`, `reload_skills`

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
- **323-unit eval suite** (159 base tasks · 33 variant'd across 6 languages · 80 easy / 123 medium / 120 hard) with automated validation — full distribution in [`evals/README.md`](evals/README.md)
- **Multi-model benchmark** runner (`evals/benchmark_all.py`) — sweeps every model and produces an accuracy-ranked leaderboard
- Per-task tracking of accuracy, speed, prompt/completion tokens, and API-equivalent cost (`evals/scoring.py`)
- Multi-language variant support: a single task can have Python / Go / C / C++ / Rust / Zig versions (6 languages), scored fractionally
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

# 4. Download the default model (or pick another from docs/INSTALL.md)
hf download HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf --local-dir weights/

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

See **[docs/INSTALL.md](docs/INSTALL.md)** for prerequisites, GPU/driver setup, alternate models, and troubleshooting.

## Models

| Model | Quant | Weights | Context | VRAM (measured) | Notes |
|-------|-------|---------|---------|-----------------|-------|
| **Qwen3.6-27B** | **Q5_K_XL** | **19 GB** | **350K** | **~29.5 GB** | **Top accuracy** on v3.5 (97.85%, benchmarked at 416K); slower per-token than the MoEs |
| Qwen3.6-27B Uncensored | Q5_K_P | 21 GB | 350K | ~30.0 GB | Uncensored fine-tune (HauhauCS Aggressive); 96.16% on v3.5 (benchmarked at 380K) |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 27.8 GB | Fast MoE (3B active); 93.74% on v3.5; ~4.3 GB headroom (measured) |
| Qwen3.6-35B-A3B Uncensored | Q4_K_M | 20 GB | 512K | 27.1 GB | Fastest of the lineup but trails on accuracy (90.33% on v3.5) |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 192K | ~28.5 GB | Different family; KV cost rises with context (20→25 KB/token); reduced from 220K on 2026-05-08 after a sustained-load crash at the tight 2,080 MiB headroom |
| Qwen3.6-27B **MTP** | Q5_K_XL | 20.4 GB | 256K (TBD) | TBD | MTP draft heads baked in; `--spec-type draft-mtp` for ~1.5–2× speedup. Forces `-np 1` (no parallel slots, no `--mmproj`). Context conservative pending measurement. |
| Qwen3.6-35B-A3B **MTP** (MoE) | Q4_K_M | 22.7 GB | 384K (TBD) | TBD | Same as above for the MoE; same `-np 1` constraint. Not yet benchmarked. |
| Qwopus3.6-27B-v2 | Q5_K_M | 19.2 GB | 350K (TBD) | TBD | Jackrong SFT fine-tune of Qwen3.6-27B (Trace Inversion from Claude Opus 4.6/4.7); reasoning-enhanced. YaRN config in this GGUF unverified — back off context if outputs degrade past ~128K. |
| Qwopus3.6-27B-v2 **MTP** | Q5_K_M | 19.5 GB | 256K (TBD) | TBD | Same fine-tune with MTP heads; same `-np 1` / no-`mmproj` MTP constraints. Not yet benchmarked. |

The first five rows have their contexts and VRAM measured against the 2GB OS-headroom rule on a 32GB card. The four `(TBD)` rows (scaffolded 2026-05-22) ship with conservative starting contexts pending real `nvidia-smi` measurement under load — see [`docs/REFERENCE.md`](docs/REFERENCE.md) for the rationale on each and [`docs/TODO.md`](docs/TODO.md) "Speculative decoding — MTP variants" for the benchmark plan.

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
  mcp_server.py              # MCP tool server (17 tools, stdio + HTTP transports)
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

evals/                       # Eval harness — 159 tasks + multi-model benchmark
  README.md                  # Distribution table, schema, scoring (start here)
  run_eval.py                # Single-model eval runner (model-tagged results)
  scoring.py                 # Accuracy / speed / tokens + per-category & per-language breakdown
  benchmark_all.py           # Multi-model sweep orchestration
  tasks/                     # Per-task JSON definitions (01–159) with category tags
  results/                   # Per-run results (kept all, model-tagged) [gitignored]
  leaderboard.json           # Latest score per model + per-category drilldown (auto-updated)

docs/                        # All technical documentation
  INSTALL.md                 # Step-by-step installation guide
  REFERENCE.md               # VRAM tables, architecture, configuration
  RESULTS.md                 # Eval distribution + cross-host sweep results
  SKILLS_PLAN.md             # Skills system design + roadmap
  WORK_PLAN.md               # Active work plan / save state for eval suite work
  TODO.md                    # Roadmap and completed work

skills/                      # Curated expertise packages — loaded on-demand by the model (14 total)
  README.md                  # Skill schema + how to add new ones
  codebase-onboarding/       # Orient before editing — Tier 1
  spec-extraction/           # Extract precise spec from vague request — Tier 1
  git-discipline/            # Atomic commits + meaningful messages — Tier 1
  long-context-synthesis/    # Process huge inputs via chunked passes — Tier 1
  test-driven-development/   # Real TDD — red, green, refactor — Tier 2
  architecture-proposal/     # Design doc before code — Tier 2
  performance-optimization/  # Measure-driven perf work — Tier 2
  api-design/                # Signature + types + examples first — Tier 2
  code-review/               # Multi-pass code review
  security-audit/            # Threat-model-driven security review
  debugging-methodology/     # Hypothesis-driven root-cause analysis
  deep-counsel/              # Slow-mode reasoning for intractable problems
  eval-task-author/          # Authoring eval tasks (encodes the 6 pitfalls)
  eval-variant-porter/       # Adding multi-language variants to existing tasks

system-prompt.md             # Soul file (persona, applied to all frontends)
system-prompt-tools.md       # Tool guidance (Open WebUI only)
docker-compose.yml           # Open WebUI + SearXNG containers
opencode.json                # OpenCode project config (MCP wiring + model list)
weights/                     # GGUF model files [gitignored]
llama.cpp/                   # Inference engine, built with CUDA [gitignored]
```

## Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — Step-by-step installation, prerequisites, troubleshooting
- **[docs/REFERENCE.md](docs/REFERENCE.md)** — VRAM tables (measured), architecture details, all configuration options
- **[docs/RESULTS.md](docs/RESULTS.md)** — Eval distribution, sweep results, multi-host comparison
- **[docs/WORK_PLAN.md](docs/WORK_PLAN.md)** — Active work plan and save state for ongoing eval suite work
- **[docs/SKILLS_PLAN.md](docs/SKILLS_PLAN.md)** — Skills system design (Pattern A progressive disclosure via MCP)
- **[docs/WEAK_SPOT_ASSESSMENT.md](docs/WEAK_SPOT_ASSESSMENT.md)** — What other axes could surface model weaknesses; recommended priority for next eval expansions
- **[docs/TODO.md](docs/TODO.md)** — Roadmap and completed work
- **[evals/README.md](evals/README.md)** — Eval suite specifics: schema, scoring, pitfalls
- **[skills/README.md](skills/README.md)** — Skills schema + how to add new ones

## Evals & Benchmarking

The 159-task eval suite covers 12 categories spanning core software engineering,
math, physics, ML/LLM internals, distributed systems, security, signal processing,
and more — every task is self-contained (setup + validation + cleanup) with
deterministic checks. **Distribution table, schema, and scoring methodology in
[`evals/README.md`](evals/README.md)**.

**Latest sweep leaderboard** (NVIDIA GeForce RTX 5090 ×1, v3.5 — 323 effective units, 2026-05-08; Gemma is mid re-run, see [`docs/RESULTS.md`](docs/RESULTS.md) for the full report including per-category and per-language tables). **Not yet on the leaderboard:** the four 2026-05-22 additions (Qwen 27B MTP, Qwen 35B-A3B MTP, Qwopus 27B v2, Qwopus 27B v2 MTP) — they need a clean launch + VRAM measurement first, then the next sweep will fold them in. The three MTP rows will measure noticeably slower wall-clock per the `-np 1` constraint even if per-request speed improves; see [`docs/TODO.md`](docs/TODO.md) for the full plan.

| # | Model | Acc | Speed | Pass | Hard | Tokens | Cost ≈ | Wall |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **Qwen 27B Q5_K_XL** | **97.85** | 53.74 | **301/323** | **114/120** | 17.24 M | $70.27 | 8h 50m |
| 2 | Qwen 27B Uncensored Q5_K_P | 96.16 | 57.29 | 298/323 | 110/120 | 17.97 M | $70.89 | 8h 24m |
| 3 | Qwen 35B-A3B MoE Q4_K_M | 93.74 | 74.30 | 278/323 | 97/120 | 26.70 M | $111.37 | 6h 53m |
| 4 | Gemma 4 31B-it Q5_K_XL | 92.39 | 41.58 | 288/323 | 104/120 | 12.52 M | $54.23 | 9h 53m |
| 5 | Qwen 35B-A3B Uncensored Q4_K_M | 90.33 | 79.92 | 271/323 | 93/120 | 26.95 M | $107.12 | 5h 44m |

Cost is the API-equivalent on Anthropic Sonnet 4.6 ($3/M input, $15/M output) — sense-of-scale only; these all ran locally on the 5090.

**At-a-glance: per-language accuracy** (variant tasks, % accuracy; bold = top, italic = floor):

| Model | Python | C | C++ | Go | Rust | Zig | Best at |
|---|---:|---:|---:|---:|---:|---:|---|
| Qwen 27B Q5_K_XL | **99.9** | **93.2** | 90.3 | 93.2 | **96.1** | **66.9** | Python, C, Rust, Zig |
| Qwen 27B Uncensored Q5_K_P | 98.3 | 90.3 | **93.2** | **96.1** | 93.2 | 55.2 | C++, Go |
| Qwen 35B-A3B MoE Q4_K_M | 97.8 | _82.5_ | _83.5_ | 80.5 | _83.5_ | 40.9 | — |
| Gemma 4 31B-it Q5_K_XL | 94.3 | 88.3 | 86.4 | 94.2 | 91.2 | 54.5 | — |
| Qwen 35B-A3B Uncensored Q4_K_M | _94.2_ | _82.5_ | 85.4 | _78.6_ | **96.1** | _15.6_ | Rust |

The **Zig spread is enormous** (66.9 → 15.6) and the strongest discriminator on the suite. Python is saturated across the board — pick a smaller model and a faster one if you only ship Python. **Use 27B Q5_K_XL for Python, C, and Zig**; **27B Uncensored for Go and C++**; the MoE variants are useful when raw speed matters more than top-end accuracy.

**Ranking: accuracy is primary.** Tie-breakers: total pass count → hard-pass count → speed. Speed and tokens are surfaced as separate columns rather than collapsed into a composite — they reveal a real tradeoff (the MoE 35B variants are 30–50% faster but trail the dense 27B models by 4–7 accuracy points).
- **Accuracy**: difficulty-weighted pass rate (easy=1, medium=1.5, hard=2)
- **Speed**: average speed factor on passed tasks (budget 30s/90s/300s by difficulty)
- **Per-category breakdown**: every score is also reported per-category (Algorithms & DS, SWE / DevOps, Math Finance, Probability & Stats, Pure & Abstract Math, LLM / ML, Distributed / SysDesign, Concurrency & Systems, Physics, Performance & HW Opt, Security, Signal Processing & DSP) with subcategory drilldown — see `evals/scoring.py --by-category`
- **Multi-host comparison**: leaderboard is keyed by `(host_id, model_slug)`, so the same model run on different machines (e.g. RTX 5090 vs. 2×3090 Ti) coexist. See `evals/scoring.py --compare-hosts`.

```bash
# Single-model eval (server must already be running)
python3 evals/run_eval.py                          # all 159 tasks
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
