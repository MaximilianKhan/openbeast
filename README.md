# 🦁 OpenBeast

**Your own private AI workstation — frontier-class models, a full agent tool suite, and secure access from anywhere, running entirely on your hardware. No cloud, no API keys, no data ever leaving your machine.**

Most local-model tools stop at "chat with a model." OpenBeast is the whole
stack: an OpenAI-compatible model server, an autonomous agent with a
17-tool arsenal (shell, file editing, web search, background sub-agents), a
browser chat UI *and* a terminal coding agent, one-command encrypted remote
access, and family-grade multi-user permissions — all self-hosted, all yours.

## Install (one command)

```bash
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast
./bootstrap.sh
```

`bootstrap.sh` detects your GPU, builds llama.cpp, installs dependencies,
downloads the default model, and launches the full stack with **all tools
wired and no login wall** — the complete demo, out of the box. It checks the
heavy prerequisites (NVIDIA driver, CUDA, Docker) and tells you exactly what
to install if anything's missing.

**Just want to chat?** `./bootstrap.sh --minimal` sets up Tier 0 — the model
server only, no Docker, no tools — and you point any OpenAI-compatible client
at `http://localhost:8080/v1`.

**Want it on your phone, securely, from anywhere?** `./scripts/setup-tailscale.sh`
puts the stack on your private tailnet with automatic HTTPS in about five
minutes. See ["Remote access"](#remote-access-tailscale) below.

**Already installed?** `./scripts/update.sh` pulls the latest llama.cpp (and
rebuilds it), container images, and Python deps in one shot — details in
[`docs/UPDATING.md`](docs/UPDATING.md).

## Why OpenBeast

| | OpenBeast | Ollama | LM Studio | text-generation-webui |
|---|:---:|:---:|:---:|:---:|
| Fully local, no cloud | ✅ | ✅ | ✅ | ✅ |
| OpenAI-compatible API | ✅ | ✅ | ✅ | ✅ |
| **Agent tool suite** (shell, files, web, sub-agents) | ✅ | — | — | partial |
| **Terminal coding agent** (OpenCode) | ✅ | — | — | — |
| **One-command secure remote access** (Tailscale + HTTPS) | ✅ | — | — | — |
| **Multi-user roles / RBAC** (family-safe: guests get web, not your files) | ✅ | — | — | — |
| **Speculative decoding** (MTP, 1.5–2.75× tok/s) | ✅ | partial | partial | partial |
| **VRAM-measured context tuning** + reproducible eval leaderboard | ✅ | — | — | — |

Ollama and LM Studio are excellent *model runners*. OpenBeast is a *workstation*
built around one: it turns a local model into an agent you can actually work
with, reach from any device, and safely share with your household.

Built and tuned on an RTX 5090 (32 GB) running Arch Linux. Default model:
**Qwen3.6-27B Uncensored Q5_K_P** (#2 on the internal leaderboard, 96.16 %);
the dense **Qwen3.6-27B Q5_K_XL** tops raw accuracy at 97.85 %, and the
**35B-A3B MoE** variants run 30–50 % faster per token — each swaps in with one
argument to `start.sh`. Nine models are pre-configured, benchmarked against a
v3.5 eval suite of 159 base tasks (33 with multi-language variants, 323
effective test units). See [`docs/RESULTS.md`](docs/RESULTS.md) and
[`evals/README.md`](evals/README.md).

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
   │       bash · read · write · edit · grep · list_files                    │
   │       fetch · web_search                                                │
   │       start_agent · check_agent · tail_agent · list_agents · stop_agent │
   │       list_skills · load_skill · start_skill_agent · reload_skills      │
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
- 9 pre-configured models: 5 measured + benchmarked (Qwen 27B dense Q5_K_XL, **Qwen 27B uncensored Q5_K_P** as default, Qwen 35B-A3B MoE, Qwen 35B-A3B uncensored, Gemma 4 31B-it) and 4 VRAM-measured 2026-07-07, awaiting first benchmark sweep (Qwen 27B MTP, Qwen 35B-A3B MTP, Qwopus 27B v2, Qwopus 27B v2 MTP)
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
- Daemon mode: `./start.sh -d` returns when the model is loaded and keeps the stack running in a **memory-capped scope** (a runaway process can never take down the box); `./start.sh --status` shows what's up; `./stop.sh` shuts everything down gracefully any time
- Health monitor with auto-restart (`scripts/healthcheck.sh`)
- End-to-end smoke test (`tests/test_smoke.sh`)
- **323-unit eval suite** (159 base tasks · 33 variant'd across 6 languages · 80 easy / 123 medium / 120 hard units) with automated validation — full distribution in [`evals/README.md`](evals/README.md)
- **Multi-model benchmark** runner (`evals/benchmark_all.py`) — sweeps every model and produces an accuracy-ranked leaderboard
- Per-task tracking of accuracy, speed, prompt/completion tokens, and API-equivalent cost (`evals/scoring.py`)
- Multi-language variant support: a single task can have Python / Go / C / C++ / Rust / Zig versions (6 languages), scored fractionally
- Test suite covering scripts, tools, MCP server, and eval tasks (`tests/run_tests.sh`)

## Manual install (the long way)

`./bootstrap.sh` (above) automates all of this. These are the same steps by
hand, if you'd rather run them yourself or adapt them. Assumes NVIDIA driver,
CUDA, Docker, and Python 3.10+ are installed:

```bash
# 1. Clone and enter the repo
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast

# 2. Build llama.cpp with CUDA. CUDA_ARCH is auto-detected from your GPU
#    (5090=120, 4090=89, 3090=86). nvcc lives in /opt/cuda/bin on Arch —
#    add it to PATH so CMake finds it.
export PATH="/opt/cuda/bin:$PATH"
CUDA_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
git clone https://github.com/ggml-org/llama.cpp.git
(cd llama.cpp && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH:-120}" \
                 && cmake --build build --config Release -j$(nproc))

# 3. Install Python deps + Hugging Face CLI
pip install --user --break-system-packages huggingface-hub[cli] -r agents/requirements.txt

# 4. Download the default model (or pick another from docs/INSTALL.md)
#    --local-dir can be anywhere — see "Model weights location" below.
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

See **[docs/INSTALL.md](docs/INSTALL.md)** for prerequisites, GPU/driver setup, alternate models, and troubleshooting.

## Remote access (Tailscale)

The stack binds to `127.0.0.1` by default — nothing is reachable from the
network, not even the LAN. To use OpenBeast from your phone or laptop
anywhere (cellular included), one script puts it on your private tailnet
with automatic HTTPS:

```bash
./scripts/setup-tailscale.sh
```

Five minutes, verified end-to-end. It installs Tailscale, joins your tailnet
as `beast` (browser SSO login on first run), walks you through the two
one-time tailnet toggles (MagicDNS + HTTPS Certificates — it prints the
admin-console link and waits), and publishes exactly two services —
tailnet-only, never the public internet:

| URL | Service |
|---|---|
| `https://<host>.<tailnet>.ts.net` | Open WebUI (chat) |
| `https://<host>.<tailnet>.ts.net:8443/v1` | llama-server (OpenAI-compatible API) |

Every connecting device authenticates via its WireGuard key; the WebUI
additionally requires an account now (`WEBUI_AUTH=true` — first signup
becomes admin; mirror the credentials into `openbeast.conf` as
`WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` so `configure-webui.sh` can
keep working). MCPO and SearXNG stay loopback-only — they serve the model,
not humans.

- **Phone:** install the Tailscale app, sign in, open the chat URL, "Add to
  Home Screen" (the WebUI is a PWA).
- **Remote coding agent:** on any tailnet machine, point OpenCode's
  `baseURL` at `https://<host>.<tailnet>.ts.net:8443/v1` — full coding agent
  against the home GPU from anywhere.
- **Legacy LAN-open behavior:** set `BIND_HOST=0.0.0.0` in `openbeast.conf`
  (or `OPENBEAST_BIND=0.0.0.0`) — not recommended; every service becomes
  reachable unauthenticated on the LAN.
- Optional API key for the llama-server: set `LLAMA_API_KEY` in
  `openbeast.conf` (off by default; the tailnet is the boundary).

Design rationale, alternatives considered (Headscale, NetBird, plain
WireGuard), and the verification checklist live in
**[docs/REMOTE_ACCESS_PLAN.md](docs/REMOTE_ACCESS_PLAN.md)**.

## Model weights location

Weights are large (10s of GB each), so OpenBeast never requires you to store
them inside the repo. Every launch script resolves a weights directory through
`scripts/lib/weights.sh`, checking these in order (first match wins):

1. **`$OPENBEAST_WEIGHTS_DIR`** — environment variable, highest priority. Best
   for a one-off: `OPENBEAST_WEIGHTS_DIR=/mnt/nvme/gguf ./start.sh`.
2. **`WEIGHTS_DIR=` in `openbeast.conf`** — a repo-root config file for a
   persistent choice. Copy the template and edit it:
   ```bash
   cp openbeast.conf.example openbeast.conf
   # WEIGHTS_DIR=/mnt/nas/ai/weights   (NVMe, USB, NAS mount, ~ , or relative)
   ```
   `openbeast.conf` is gitignored, so your personal path is never committed.
3. **`./weights/`** — an in-repo folder, used automatically if it exists
   (this is what the Quick Start creates, and what long-time setups already use).
4. **`../weights/`** — the default for a fresh clone with no `./weights`: a
   sibling folder right next to the `openbeast` checkout.

Paths accept `~` and may be relative (resolved against the repo root). If the
resolved directory doesn't exist, the launch scripts print exactly how to point
OpenBeast at your weights instead of failing with a cryptic "model not found".

## Models

| Model | Quant | Weights | Context | VRAM (measured) | Notes |
|-------|-------|---------|---------|-----------------|-------|
| **Qwen3.6-27B** | **Q5_K_XL** | **19 GB** | **350K** | **~29.5 GB** | **Top accuracy** on v3.5 (97.85%, benchmarked at 416K); slower per-token than the MoEs |
| Qwen3.6-27B Uncensored | Q5_K_P | 21 GB | 350K | ~30.0 GB | Uncensored fine-tune (HauhauCS Aggressive); 96.16% on v3.5 (benchmarked at 380K) |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 27.8 GB | Fast MoE (3B active); 93.74% on v3.5; ~4.3 GB headroom (measured) |
| Qwen3.6-35B-A3B Uncensored | Q4_K_M | 20 GB | 512K | 27.1 GB | Fastest of the lineup but trails on accuracy (90.33% on v3.5) |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 192K | ~28.5 GB | Different family; KV cost rises with context (20→25 KB/token); reduced from 220K on 2026-05-08 after a sustained-load crash at the tight 2,080 MiB headroom |
| Qwen3.6-27B **MTP** | Q5_K_XL | 20.4 GB | 288K | 29.4 GB | MTP draft heads baked in; tuned `n-max 8 / p-min 0.0` measures **184 tok/s vs 66.8 baseline (2.75×)**. Forces `-np 1` (no parallel slots, no `--mmproj`). 2.5 GB headroom at the tuned config. Not yet benchmarked. |
| Qwen3.6-35B-A3B **MTP** (MoE) | Q4_K_M | 22.7 GB | 512K | 28.8 GB | Same as above for the MoE; tuned `n-max 4 / p-min 0.0` measures **379 tok/s vs 259 baseline (1.46×)**. Same `-np 1` constraint; matches the non-MTP MoE's 512K ceiling (3.1 GB headroom). Not yet benchmarked. |
| Qwopus3.6-27B-v2 | Q5_K_M | 19.2 GB | 416K | 29.3 GB | Jackrong SFT fine-tune of Qwen3.6-27B (Trace Inversion from Claude Opus 4.6/4.7); reasoning-enhanced. 2.6 GB headroom measured. YaRN config in this GGUF unverified — back off context if outputs degrade past ~128K. |
| Qwopus3.6-27B-v2 **MTP** | Q5_K_M | 19.5 GB | 336K | 29.3 GB | Same fine-tune with MTP heads; tuned `n-max 4 / p-min 0.0` measures **147 tok/s vs 68.5 baseline (2.14×)**. Same `-np 1` / no-`mmproj` MTP constraints. 2.5 GB headroom (352K lands at 2,132 MiB — the known sustained-load crash zone). Not yet benchmarked. |

All nine rows have their contexts and VRAM measured against the 2GB OS-headroom rule on a 32GB card (the four MTP/Qwopus rows measured 2026-07-07; VRAM column shows total GPU usage at max context, which includes ~1.3 GB of desktop baseline). See [`docs/REFERENCE.md`](docs/REFERENCE.md) for per-variant details and [`docs/TODO.md`](docs/TODO.md) "Speculative decoding — MTP variants" for the benchmark plan.

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
weights/                     # GGUF model files (default location; relocatable — see below) [gitignored]
openbeast.conf.example       # Config template — copy to openbeast.conf to set a custom weights dir
scripts/lib/weights.sh       # Resolves the weights directory (env / config / ./weights / ../weights)
llama.cpp/                   # Inference engine, built with CUDA [gitignored]
```

## Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — Step-by-step installation, prerequisites, troubleshooting
- **[docs/REFERENCE.md](docs/REFERENCE.md)** — VRAM tables (measured), architecture details, all configuration options
- **[docs/TOOLS.md](docs/TOOLS.md)** — Every tool a model can call: inventory, provenance (custom vs pulled-in), hardening, RBAC visibility
- **[docs/UPDATING.md](docs/UPDATING.md)** — Updating every pulled-in component (llama.cpp, images, Python deps) with one command
- **[docs/HARDWARE_PROFILES.md](docs/HARDWARE_PROFILES.md)** — GPU detection and recommended configs per hardware tier (5090 is the measured reference; 3090/4090/AMD/Intel advisory)
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

# Multi-model sweep — stops/starts each serve script in turn. Covers all 9
# configured models (5 benchmarked so far; those 5 take ~16-20h on the v3.5
# suite — budget roughly a day for all 9)
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

- NVIDIA GPU with CUDA (tested on RTX 5090; works on 3090/4090 — `bootstrap.sh` auto-detects the CUDA arch and prints a per-tier config recommendation; see [`docs/HARDWARE_PROFILES.md`](docs/HARDWARE_PROFILES.md))
- Linux with NVIDIA driver, CUDA toolkit, Docker, and Python 3.10+
- Disk: ~25 GB for llama.cpp + one model. Each model adds 16–21 GB.
- VRAM: 24 GB minimum for the smaller quants; 32 GB for the defaults

## Credits — standing on the shoulders of giants

OpenBeast is an orchestration layer. The heavy lifting below it is done by
outstanding open source projects, and each deserves the credit:

| Project | What it does in OpenBeast | Upstream |
|---|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT) | The inference engine — `llama-server` serves every model, OpenAI-compatible | ggml-org |
| [Open WebUI](https://github.com/open-webui/open-webui) (Open WebUI License, BSD-3-based) | The browser chat frontend, user accounts, and RBAC surface | open-webui |
| [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0) | Private metasearch — powers the `web_search` tool with no tracking | searxng |
| [MCPO](https://github.com/open-webui/mcpo) (MIT) | MCP→OpenAPI proxy that exposes our tool server to Open WebUI | open-webui |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (MIT) | The protocol layer our tool server (`agents/mcp_server.py`) is built on | modelcontextprotocol |
| [OpenCode](https://github.com/sst/opencode) (MIT) | The terminal coding agent frontend | sst |
| [openai-python](https://github.com/openai/openai-python) (Apache-2.0) | Client SDK the autonomous agent runner speaks to llama-server with | openai |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) (Apache-2.0) | The `hf` CLI that downloads model weights | huggingface |
| [Tailscale](https://github.com/tailscale/tailscale) (BSD-3-Clause) | Optional: encrypted remote access to the stack from anywhere | tailscale |

Model weights (Qwen, Gemma, and community finetunes) are downloaded from
Hugging Face and carry their own upstream licenses. License labels above are
as published at time of writing — always check upstream for current terms.

See [`docs/UPDATING.md`](docs/UPDATING.md) for how to pull the latest version
of every component with one command.

## License

[Apache License 2.0](LICENSE) — permissive, with an explicit patent grant.
Use it, fork it, build a business on it (on-prem, air-gapped, commercial — all
fair game). See [`NOTICE`](NOTICE) for the third-party components OpenBeast
orchestrates; model weights carry their own upstream licenses.

---

<!--
  A small Latin blessing to close. Translation:
  "Behold the Beast — but tamed. It brands your brow with no foreign lord's
  number; its mark stays in your own silicon, and the key is in your hands.
  Saint Michael the Archangel, guard our gates: defend our networks in
  battle, lest our data stray into the cloud. The local Beast roars for the
  people — and your data never leaves home."

  The joke: Revelation's "mark of the beast" (a foreign lord branding you) is
  inverted — OpenBeast's mark is a blessing that never leaves your machine,
  and the security layer (Tailscale, RBAC, sandboxing) is St. Michael at the
  gate. "Nube" = cloud, both the heavenly kind and the data-harvesting kind.
-->

<sub><i>Ecce Bestia — sed domita. Frontem tuam numero domini alieni non signat; signum eius in silicio tuo manet, et clavis penes te est. Sancte Michael Archangele, portas nostras custodi: retia nostra in proelio defende, ne data in nubem vagentur. Bestia localis pro populo rugit — nec datum tuum domo umquam exit.</i></sub>
