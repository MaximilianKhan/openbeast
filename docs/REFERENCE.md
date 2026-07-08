# Technical Reference

## Directory layout

```
openbeast/
├── llama.cpp/              # inference engine (built with CUDA) [gitignored]
├── openbeast.conf.example  # config template — copy to openbeast.conf to relocate weights
├── scripts/lib/weights.sh  # resolves the weights dir (env / config / ./weights / ../weights)
├── weights/                # GGUF model files — default location, relocatable [gitignored]
│   ├── Qwen3.6-27B-UD-Q5_K_XL.gguf
│   ├── Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf
│   ├── Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
│   ├── Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
│   ├── Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf          # MTP variant
│   ├── Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf       # MTP variant
│   ├── Qwopus3.6-27B-v2-Q5_K_M.gguf             # Jackrong SFT fine-tune
│   ├── Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf         # Jackrong SFT + MTP heads
│   └── gemma-4-31B-it-UD-Q5_K_XL.gguf
├── start.sh                # launch full stack (server + MCPO + Open WebUI)
├── stop.sh                 # stop full stack
├── agent.sh                # run a local agent against a task
├── scripts/                # server, chat, and config scripts
│   ├── serve.sh            # generic OpenAI-compatible API server
│   ├── run.sh              # generic interactive chat launcher
│   ├── configure-webui.sh  # auto-configure Open WebUI
│   ├── serve-qwen-27b-q5.sh
│   ├── serve-qwen-27b-uncensored-q5.sh
│   ├── serve-qwen-35b-a3b.sh
│   ├── serve-qwen-35b-a3b-uncensored-q4.sh
│   ├── serve-qwen-27b-mtp-q5.sh
│   ├── serve-qwen-35b-a3b-mtp.sh
│   ├── serve-qwopus-27b-v2-q5.sh
│   ├── serve-qwopus-27b-v2-mtp-q5.sh
│   ├── serve-gemma-4-31b-q5.sh
│   ├── run-qwen-27b-q5.sh
│   ├── run-qwen-27b-uncensored-q5.sh
│   ├── run-qwen-35b-a3b.sh
│   ├── run-qwen-35b-a3b-uncensored-q4.sh
│   ├── run-qwen-27b-mtp-q5.sh
│   ├── run-qwen-35b-a3b-mtp.sh
│   ├── run-qwopus-27b-v2-q5.sh
│   ├── run-qwopus-27b-v2-mtp-q5.sh
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
├── skills/                 # curated expertise packages — discovered via MCP
│   ├── README.md           # skill schema + how to add new ones
│   ├── code-review/SKILL.md
│   ├── security-audit/SKILL.md
│   ├── debugging-methodology/SKILL.md
│   ├── deep-counsel/SKILL.md
│   ├── eval-task-author/SKILL.md
│   └── eval-variant-porter/SKILL.md
└── docs/
    ├── INSTALL.md          # step-by-step installation guide
    ├── REFERENCE.md        # this file — technical reference
    ├── RESULTS.md          # eval distribution + cross-host sweep results
    ├── SKILLS_PLAN.md      # skills system design + roadmap
    ├── WORK_PLAN.md        # active work plan / save state for eval suite work
    └── TODO.md             # roadmap and completed work
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
estimate. The 416K default landed tight against the 2GB rule and was crashing
under sustained OS+KV pressure, so the operational default was dropped to **350K**
on 2026-05-22. v3.5 benchmark accuracy (97.85%) was measured at 416K and stands.

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| **350K** (default, 2026-05-22) | **~29,500 MiB** (est.) | **~3,200 MiB** (est.) | **current default — comfortable headroom, removes crash mode** |
| 408K    | ~30,560 MiB    | ~2,200 MiB           | comparable to other models' margin |
| 416K (prior default, benchmarked at) | **30,711 MiB** | **2,057 MiB** | measured — 9 MiB above 2GB rule (tight); crashes observed under sustained load |

**Uncensored (HauhauCS Aggressive) Q5_K_P (~21GB weights)**

Re-measured 2026-05-05: actual KV cost runs denser than the original 18 KB/token
estimate (closer to ~20 KB at high context). The original 416K default was too
tight; an interim 380K default still crashed intermittently. Operational default
dropped to **350K** on 2026-05-22 to clear the crash mode. v3.5 benchmark accuracy
(96.16%) was measured at 380K and stands.

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| **350K** (default, 2026-05-22) | **~30,000 MiB** (est.) | **~2,800 MiB** (est.) | **current default — clears the crash mode at 380K** |
| 380K (prior default, benchmarked at) | **30,648 MiB** | **2,120 MiB** | measured — meets 2GB rule on paper; crashed intermittently under sustained load |
| 416K    | **31,405 MiB** | 1,363 MiB            | below 2GB rule — OOM risk on OS spikes |

### Gemma 4 31B-it — measured KV cost (non-linear, grows with context)

Different model family from Qwen (no DeltaNet, uses sliding-window attention).
Per-token KV starts close to Qwen 27B but rises with context length —
20 KB/token from 128K→200K, then 25 KB/token from 200K→250K. Measured 2026-05-05.

**Q5_K_XL (~20.4GB weights)**

| Context | **Total Used** | Headroom (32GB card) | Status |
|---------|----------------|----------------------|--------|
| 128K    | **28,680 MiB** | 4,088 MiB            | safe   |
| **192K** (default, 2026-05-08) | **~29,800 MiB** | **~2,800 MiB** | **current default — comfortable headroom under sustained KV pressure** |
| 200K    | **30,155 MiB** | 2,613 MiB            | safe at idle, tight under load |
| 220K    | **30,688 MiB** | 2,080 MiB            | meets 2GB rule on paper; crashed mid-eval 2026-05-08 |
| 250K    | **31,439 MiB** | 1,329 MiB            | below 2GB rule — OOM risk on OS spikes |

**Why default dropped from 220K to 192K (2026-05-08):** During the v3.5 eval
sweep, llama-server died between tasks 10–11 at the 220K default. Headroom
on paper was 2,080 MiB but sustained KV pressure (back-to-back 6-variant
const-time-compare tasks at 4-bit KV quant) appeared to exhaust the
allocator. Dropping to 192K (~13% reduction) cleared the crash; the same
task that took 164s and failed at 220K passed in 70.7s at 192K. Largest
observed eval prompt is ~47K, so 192K is plenty for sequential workloads.
Earlier KV-growth observation still holds: cost per token rises non-linearly
beyond 200K — same cliff that caused Qwen Q5_K_XL OOMs at 512K.

### Qwen3.6-35B-A3B — 40 layers, real-world KV cost: ~6.3 KB/token

| Context | Model | KV Cache | **Total** | Headroom |
|---------|-------|----------|-----------|----------|
| 64K     | 20 GB | 0.4 GB   | **20.4 GB** | 11.6 GB |
| 262K    | 20 GB | 1.6 GB   | **21.6 GB** | 10.4 GB |
| **512K** (default) | 20 GB | 3.1 GB + extra compute | **27.8 GB** | 4.3 GB |
| 768K    | 20 GB | 4.7 GB   | **24.7 GB** | 7.3 GB  |
| 1M      | 20 GB | 6.3 GB   | **26.3 GB** | 5.7 GB  |

### Qwen3.6 **MTP** variants (Multi-Token Prediction, scaffolded 2026-05-22)

Models: `unsloth/Qwen3.6-27B-MTP-GGUF` and `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`.
MTP draft heads are baked into the same GGUF — no sidecar file. Launched
with `--spec-type draft-mtp` plus per-model tuned draft parameters
(benchmarked empirically 2026-07-07 on greedy 640-token code/reasoning
generations; speculative decoding is lossless — the target model verifies
every draft token, so these knobs affect speed only):

| Model | Tuned config | tok/s | Baseline (no spec) | Speedup | Accept |
|-------|--------------|------:|-------------------:|--------:|-------:|
| 27B MTP Q5_K_XL | `n-max 8, p-min 0.0` | 184 | 66.8 | **2.75×** | 55% |
| 35B-A3B MTP Q4_K_M | `n-max 4, p-min 0.0` | 379 | 259 | **1.46×** | 65% |
| Qwopus 27B v2 MTP | `n-max 4, p-min 0.0` | 147 | 68.5 | **2.14×** | 62% |

Tuning findings: probability-gating drafts (`p-min` 0.5–0.9) always measured
slower than drafting unconditionally — MTP heads are nearly free to run, so
rejected drafts cost little. Dense models reward deep drafts (27B optimum
n8); the fast-decoding MoE inverts at n8+ (below baseline — verification
batches cost more than they save). The Qwopus SFT fine-tune has lower draft
acceptance than base Qwen (its output distribution shifted relative to the
MTP heads), so its optimum stays at n4.

**Upstream constraints (llama.cpp as of 2026-05-22, see commit log around
`#22673` and follow-ups through `#23461`):**
- `-np > 1` is not supported with MTP — serve scripts pin `-np 1`. Concurrent
  requests serialize.
- `--mmproj` is not supported with MTP — no vision input on these builds.

**VRAM (measured 2026-07-07 via `scripts/measure-vram.sh`):** both GGUFs are
~0.7–1.4 GB heavier than the non-MTP builds because the MTP head tensors are
loaded into VRAM alongside the main weights, and the MTP draft path allocates
additional scratch buffers. KV is allocated up-front at load, so these peaks
are reachable without sending traffic. Totals include ~1.3 GB of desktop
baseline on the shared card.

Measurements below are at each model's tuned draft config (draft buffers
scale with `n-max`: the 27B's n8 costs ~600 MiB more than n4):

| Variant | Max context (default) | Measured | Notes |
|---------|----------------------|----------|-------|
| 27B MTP Q5_K_XL | **288K** (-c 294912) | 30,063 MiB / 2,544 MiB headroom | At the tuned n-max 8. 320K went TIGHT at n8 (30,959 MiB / 1,648 MiB) — 32K of context traded for the 2.75× decode speed. ~28 KB/token KV. |
| 35B-A3B MTP Q4_K_M | **512K** (-c 524288) | 29,449 MiB / 3,158 MiB headroom | Matches the non-MTP MoE ceiling; light ~12 KB/token KV absorbs the MTP heads. 384K = 27,833 MiB / 4,774 MiB. |
| Qwopus 27B v2 MTP Q5_K_M | **336K** (-c 344064) | 30,027 MiB / 2,580 MiB headroom | 352K = 30,475 MiB / 2,132 MiB — passes the 2 GB rule on paper but matches the uncensored 27B's sustained-load crash zone, so backed off one notch. ~27 KB/token KV. |

### Qwopus3.6-27B-v2 (Jackrong SFT, added 2026-05-22)

Models: `Jackrong/Qwopus3.6-27B-v2-GGUF` (non-MTP) and
`Jackrong/Qwopus3.6-27B-v2-MTP-GGUF` (MTP). Both are SFT fine-tunes of
Qwen3.6-27B trained on Trace Inversion datasets distilled from Claude
Opus 4.6/4.7 reasoning traces — reasoning-quality oriented rather than a
speed or uncensored play.

Architecturally identical to Qwen3.6-27B (64 layers, dense) — non-MTP at
Q5_K_M is 19.2 GB on disk vs 19 GB for our unsloth UD-Q5_K_XL.

**VRAM (measured 2026-07-07):** non-MTP runs **416K** (-c 425984) at
29,980 MiB / 2,627 MiB headroom (350K = 28,460 MiB / 4,147 MiB; 440K would
land right at the 2 GB rule). ~23 KB/token KV. The MTP build's numbers are
in the MTP table above.

**Context caveat:** Jackrong's README cites "32K/128K native context."
The YaRN extension that ships in the unsloth Qwen3.6 GGUFs (extending
the 262K native to ~1M) may or may not be intact in this conversion. The
serve scripts ship at the VRAM ceilings above (416K non-MTP / 352K MTP),
but if outputs degrade past ~128K under real use, back off the context.
Validate by running the same long-context probe against both Qwopus and
the unsloth Qwen3.6-27B build.

> **Note (2026-05-05 re-measurement):** at 512K total VRAM is **27,807 MiB / 4,271 MiB
> headroom** — substantially higher than the 2026-04-27 figure (23.1 GB) due to
> compute/scratch overhead at full context that wasn't captured in the original
> per-component breakdown. The MoE 35B-A3B Uncensored variant measured the same
> day at the same 512K setting reported 27,139 MiB / 4,939 MiB. The 64K-262K rows
> above haven't been re-measured and may also drift; treat them as ceilings.
> The "1M context safe" claim from the original note is no longer accurate at
> default slot counts — would need to drop slots to fit.

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

Weights go into the OpenBeast weights directory. By default that is `weights/`
inside the repo, but it is relocatable — set `$OPENBEAST_WEIGHTS_DIR` or
`WEIGHTS_DIR=` in `openbeast.conf` to point at an NVMe/USB/NAS path instead.
See [INSTALL.md § Where weights live](INSTALL.md#where-weights-live). The
`--local-dir weights/` in the commands below just targets the in-repo default;
substitute your directory if different.

```bash
pip install --user --break-system-packages huggingface-hub[cli]
```

### Qwen3.6-27B (hybrid DeltaNet + attention)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir weights/
```

- Source: https://huggingface.co/unsloth/Qwen3.6-27B-GGUF
- **Hybrid architecture:** 48 DeltaNet layers + 16 gated attention layers (64 total)
- Native max: 262K, extended via YaRN to ~1M
- Real-world KV cost: **~18 KB/token** (llama.cpp allocates KV for all 64 layers)
- **Q5_K_XL** (~19GB): default **350K** context, ~29.5GB total — higher weight fidelity (reduced from 416K on 2026-05-22 after crashes; v3.5 benchmark numbers were measured at 416K)

### Qwen3.6-27B Uncensored (HauhauCS Aggressive)

```bash
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/
```

- Source: https://huggingface.co/HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive
- Same base architecture as Qwen3.6-27B (64 layers, ~18 KB/token KV cost)
- Fine-tuned with safety filters removed
- **Q5_K_P** (~21GB): default **350K** context, ~30.0GB total (reduced from 380K on 2026-05-22 after intermittent crashes; v3.5 benchmark numbers were measured at 380K)

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
- Default **512K** context, ~27.8 GB total — re-measured 2026-05-05 (was claimed ~23.1 GB before; compute overhead drove up the total)

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

**Enabling tools in a chat:** tool access is per-conversation by design —
click the **＋ (integrations) icon in the message input** and toggle on the
**local-mcp / Local Tools (MCPO)** tool server, then ask something that
needs a tool ("search the web for…"). Without the toggle the model chats
bare, which is why a fresh conversation can't search the web even though
the server is configured. Native function calling + the soul-file system
prompt are already set per model by `configure-webui.sh`; if a model ever
shows up without them (e.g. a brand-new alias), re-run
`./scripts/configure-webui.sh` — it's idempotent.

### Open WebUI login & accounts (since the 2026-07-07 Tailscale rollout)

**How to log in.** `WEBUI_AUTH=true` is the default now that the WebUI is
reachable from the whole tailnet — the login screen appears everywhere,
localhost included. The admin account is `admin@localhost`; its password
lives in the gitignored `openbeast.conf` (`WEBUI_ADMIN_EMAIL` /
`WEBUI_ADMIN_PASSWORD`). You log in once per device/browser; a session
token persists after that. If you change the password in the UI
(Settings → Account), update `openbeast.conf` to match — that's what
`configure-webui.sh` uses to re-apply tool config on restarts.

**How to turn auth off.** Set `OPENBEAST_WEBUI_AUTH=false` in the
environment before `./start.sh` (or edit the default in
`docker-compose.yml`). This restores the old zero-login single-user mode.
Trade-off: anyone holding any device on your tailnet — including a lost
phone — gets the full admin UI. Layered defense says leave it on; the cost
is one login per device.

**Accounts and history — it's real multi-user, not one shared login:**
- Every account has its **own separate chat history**, settings, and
  prompts, stored server-side in the Docker volume.
- **Same account on many devices = same history everywhere** — start a chat
  on the desktop, continue it on your phone. That's the normal
  single-owner setup: one account, all your devices.
- **More people = more accounts**, each with private history. Create them
  in Admin Panel → Users, or let people self-register at the login screen
  (signup is enabled by default; new signups land as role `pending` until
  you approve them in the admin panel — nobody gets in without your nod).
- Roles: `admin` (settings, models, users) vs `user` (chat only) vs
  `pending` (no access yet). Guests get `user`, not `admin`.

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

Exposes 17 tools to any MCP-compatible client, in four groups:

- **Code & files** (5): `read_file`, `write_file`, `edit_file`, `list_files`, `grep`
- **Shell + web** (3): `bash`, `fetch`, `web_search`
- **Long-running agent management** (5): `start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent`
- **Skills** (4): `list_skills`, `load_skill`, `start_skill_agent`, `reload_skills`

All 17 are custom OpenBeast code — full inventory, provenance, hardening
notes, and RBAC visibility in [`docs/TOOLS.md`](TOOLS.md).

Transports:

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

#### Skills via MCP

Skills are curated expertise packages — markdown files with frontmatter that
encode hard-won lessons for specific kinds of work (code review, security
audit, eval-task authoring, deep counsel, debugging methodology, etc.). They
exist outside the MCP system as files on disk; the MCP exposes them via four
tools:

- **`list_skills()`** — returns name + description for every skill, repo and
  global combined. Cheap; pay only for the index.
- **`load_skill(name)`** — returns the full SKILL.md body for one skill.
  Used after `list_skills` identifies a relevant one. Frontmatter stripped.
- **`start_skill_agent(skill, task, ...)`** — spawn a sub-agent with the
  skill activated as authoritative context. The runner inherits the soul
  file + agent instructions + the activated skill body + the user task.
  Returns an agent_id usable with `check_agent`.
- **`reload_skills()`** — re-scan the skills directories without restarting
  the MCP server (useful after editing a SKILL.md).

**Discovery order:** repo `skills/` first, then `~/.local/share/local-llm-skills/`.
Repo wins on name collision. Cached at first call; refresh via `reload_skills()`.

**Currently shipped (14 skills):** see `skills/README.md` for the full table.
Tier 1 (universal): codebase-onboarding, spec-extraction, git-discipline,
long-context-synthesis. Tier 2 (situational): test-driven-development,
architecture-proposal, performance-optimization, api-design. Plus
code-review, security-audit, debugging-methodology, deep-counsel,
eval-task-author, eval-variant-porter.

**Adding a skill:** create `skills/<name>/SKILL.md` with required frontmatter
(`name`, `description`); call `reload_skills()`. Test via
`bash tests/test_scripts.sh` — the validator checks every SKILL.md parses
cleanly. See `docs/SKILLS_PLAN.md` for the full design rationale and the
deferred Phase 5 (auto-routing layer).

**AGENTS.md** (project root) is the project-wide instructions file
auto-loaded by OpenCode. It contains the task→skill mapping that nudges the
model to invoke `list_skills` for non-trivial work.

## 5. Run

### Full stack

The default model is **Qwen3.6-27B Uncensored Q5_K_P** (HauhauCS Aggressive
uncensored fine-tune; #2 on the internal leaderboard at 96.16 % on v3.5). The
dense 27B Q5 leads on raw accuracy (97.85 %) and the 35B-A3B MoEs are faster —
each is one `./start.sh <serve-script>` away.

```bash
./start.sh                                      # default model + MCP tools + Open WebUI + SearXNG
./start.sh serve-qwen-27b-q5.sh                 # use a different model (dense 27B Q5, top accuracy)
./stop.sh                                       # stop everything (server, MCP, Open WebUI, SearXNG)
```

### Web search (SearXNG)

Local web search is provided by SearXNG, running as a Docker container on port 8888.
It's included in `docker-compose.yml` and starts automatically with `./start.sh`.
No API keys needed — SearXNG aggregates results from public search engines.

The `web_search` MCP tool uses SearXNG to search the web and return titles, URLs,
and snippets. Models can also use `fetch` to read full page content from search results.

### Interactive chat (standalone)

```bash
./scripts/run-qwen-27b-q5.sh       # Qwen 27B Q5_K_XL (350K ctx, ~29.5GB VRAM)
./scripts/run-qwen-27b-uncensored-q5.sh  # Qwen 27B Uncensored Q5_K_P (350K ctx, ~30.0GB VRAM)
./scripts/run-qwen-35b-a3b.sh      # Qwen 35B-A3B MoE (512K ctx, ~27.8 GB VRAM)
./scripts/run-qwen-27b-mtp-q5.sh   # Qwen 27B MTP Q5_K_XL (288K ctx, single-slot speculative)
./scripts/run-qwen-35b-a3b-mtp.sh  # Qwen 35B-A3B MTP MoE (512K ctx, single-slot speculative)
./scripts/run-qwopus-27b-v2-q5.sh       # Qwopus 27B v2 Q5_K_M (Jackrong SFT, 416K ctx)
./scripts/run-qwopus-27b-v2-mtp-q5.sh   # Qwopus 27B v2 MTP Q5_K_M (single-slot speculative, 336K ctx)
./scripts/run-gemma-4-31b-q5.sh    # Gemma 4 31B-it Q5_K_XL (128K ctx, ~20.4GB model + KV TBD)
```

### OpenAI-compatible API server

```bash
./scripts/serve-qwen-27b-q5.sh     # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-uncensored-q5.sh  # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-35b-a3b.sh    # http://localhost:8080/v1/chat/completions
./scripts/serve-qwen-27b-mtp-q5.sh   # MTP variant — single-slot, --spec-type draft-mtp
./scripts/serve-qwen-35b-a3b-mtp.sh  # MTP variant — single-slot, --spec-type draft-mtp
./scripts/serve-qwopus-27b-v2-q5.sh       # http://localhost:8080/v1/chat/completions
./scripts/serve-qwopus-27b-v2-mtp-q5.sh   # MTP variant — single-slot, --spec-type draft-mtp
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
./scripts/serve-qwen-27b-q5.sh -np 10        # override parallel slots (default 6)
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
./scripts/serve-qwen-27b-q5.sh -np 3    # 3 slots (~138K context per slot)
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

159 tasks across three difficulty tiers (40 easy / 53 medium / 66 hard) and 12
categories. Full distribution table, schema, and scoring methodology in
[**evals/README.md**](evals/README.md) — start there for anything eval-related.
This section covers operational details specific to the multi-model sweep and
cross-host comparison.

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

Total runtime estimate: ~159 tasks × ~90s avg × 5 models ≈ **8-10 hours**.
Plan to run overnight. Sweep summaries are saved to
`evals/results/sweep-{ts}.json`. The 2026-05-05/06 sweep on the RTX 5090 took
7h 21m wall-clock (26,489 s) on the prior 144-task suite — see
[RESULTS.md](RESULTS.md).

#### Scoring + leaderboard

Ranking is **accuracy-primary** (tie-break: pass count → hard pass count →
speed). **Tokens** (prompt + completion) and **API-equivalent cost** (Sonnet
4.6 pricing as a sense-of-scale baseline: $3/M input, $15/M output) are
tracked per task and summed per run, surfaced as separate columns so you can
see how chatty a model is on the way to the same answer. There is
intentionally no composite score — speed and accuracy trade off in opposite
directions on this suite, and a weighted average hides the signal. See
`evals/scoring.py`:

```
accuracy    = 100 × Σ(weight × passed) / Σ(weight)
              weights: easy=1, medium=1.5, hard=2 (per-variant: weight / num_variants)

speed       = 100 × mean(max(0, 1 - elapsed/budget)) over passed tasks
              budgets: easy=30s, medium=90s, hard=300s

tokens      = Σ(prompt + completion) across all task runs
              (parsed from runner stdout; 0 if the server didn't return usage)
```

`scoring.py --by-category` produces a per-model × per-category accuracy table
with subcategory drilldown.

`scoring.py --by-language` produces a per-language accuracy table across the
33 base tasks that have multi-language variants (Python / Go / C / C++ / Rust /
Zig — 187 total variant entries; one task, `122_gemm_blocked`, has 5
variants without a Python entry since it's perf-flavored). Use this view to
surface cross-language failure modes the Python-only suite cannot.

`scoring.py --compare-hosts` produces a side-by-side comparison across hosts.
The leaderboard is keyed by `(host_id, model_slug)`, so the same model
evaluated on a 5090 vs. a 2×3090 Ti host coexists in the same
`evals/leaderboard.json`. The `host_id` is auto-derived from `nvidia-smi` (e.g.
`NVIDIA GeForce RTX 5090 ×1` or `NVIDIA GeForce RTX 3090 Ti ×2`).

```bash
python3 evals/scoring.py --show                      # current leaderboard (with tokens column)
python3 evals/scoring.py --rebuild                   # rescore from results/
python3 evals/scoring.py --score evals/results/eval-foo.json
python3 evals/scoring.py --by-language               # per-language drilldown
```

`evals/leaderboard.json` is auto-maintained — one entry per `(host_id, model_slug)`,
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
