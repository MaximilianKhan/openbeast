# Installation

OpenBeast runs local LLMs via llama.cpp on NVIDIA GPUs, with OpenCode (terminal
agent), Open WebUI (browser chat), an autonomous agent runner, and an MCP tool
server providing 15 tools for file I/O, shell, web search, agent management,
and a curated skills system (14 specialized expertise packages loaded on
demand).

The inference engine, model weights, and Docker volumes are not checked in —
follow the steps below to set them up. Everything else (configs, scripts,
SearXNG settings) lives in the repo and is portable across Linux boxes.

## TL;DR — Fresh box bootstrap

After cloning, start with the read-only environment check — it runs every
prerequisite probe (compiler toolchain, GPU + CUDA, Docker, disk space for
weights), prints a ✓/✗ report with per-distro install hints, and installs,
builds, downloads and writes **nothing**:

```bash
./bootstrap.sh --preflight   # exit 0 = ready, exit 1 = missing prereqs
```

Want the stack to start at boot? `scripts/openbeast.service` is a ready
systemd user unit — install instructions are in its header comment.

Then `./bootstrap.sh` automates everything in this TL;DR (recommended); the
steps below are the manual equivalent.

For a working stack on a fresh Linux machine with NVIDIA + Docker:

```bash
# System packages (Arch shown; adjust for your distro)
sudo pacman -S cuda cmake docker git python python-pip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker

# Clone the repo
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast

# Build llama.cpp with CUDA (set CMAKE_CUDA_ARCHITECTURES for your GPU)
git clone https://github.com/ggml-org/llama.cpp.git
export PATH=/opt/cuda/bin:$PATH
(cd llama.cpp && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 \
                 && cmake --build build --config Release -j$(nproc))

# Python deps + Hugging Face CLI
pip install --user --break-system-packages huggingface-hub -r agents/requirements.txt
# pip installs the `hf` CLI to ~/.local/bin — make sure it's on PATH:
export PATH="$HOME/.local/bin:$PATH"   # add to your shell rc to persist

# Default model — uncensored 27B fine-tune (#2 on the v3.5 leaderboard, 96.16%)
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/

# Frontends
curl -fsSL https://opencode.ai/install | bash
docker pull ghcr.io/open-webui/open-webui:main
docker pull searxng/searxng:latest

# Make scripts executable (in case clone didn't preserve the bit)
chmod +x start.sh stop.sh agent.sh scripts/*.sh tests/*.sh

# Launch
./start.sh
```

The detailed walkthrough below explains each step and lists alternate models.

## Prerequisites

### Core
- NVIDIA GPU with CUDA support and **≥ 11 GB VRAM** — the enforced floor
  (1080 Ti / 2080 Ti class; see docs/HARDWARE_PROFILES.md). Tested on RTX
  5090 (Blackwell SM 120)
- NVIDIA driver installed (`nvidia-smi` should work)
- `cuda` and `cmake` installed
- `gcc`/`g++` installed
- Docker installed (for Open WebUI + SearXNG); user in `docker` group
- Python 3.10+ with `pip`

### Compiler toolchains for the eval suite (multi-language variants)

The eval suite (v4: 137 base tasks / 291 effective test units — see
[`evals/README.md`](../evals/README.md) for the current distribution)
includes multi-language variant tasks
(Python / Go / C / C++ / Rust / Zig). To run those variants, all six
toolchains need to be available:

| Language | Used for | Install (Arch) |
|---|---|---|
| Python 3.10+ | always | `sudo pacman -S python` |
| Go ≥1.21 | variant `b` | `mise use -g go@latest` (or `sudo pacman -S go`) |
| C (gcc) | variant `c` | (already required for llama.cpp) |
| C++ (g++) | variant `d` | (already required for llama.cpp) |
| Rust (rustc) | variant `e` | `sudo pacman -S rust` |
| Zig | variant `f` | `mise use -g zig@latest` (no sudo needed) |

If you skip a toolchain, the variant tasks for that language will fail at
the build step — their pass/fail entries in the leaderboard will record
as failures. The other variants of the same task still run normally.

### Arch Linux quick install

```bash
# Core
sudo pacman -S cuda cmake docker gcc

# Toolchains (skip any you already have)
sudo pacman -S go rust         # if installing system-wide
mise install zig@latest && mise use -g zig@latest    # via mise (no sudo)

# Daemons
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out/in or run `newgrp docker`
```

`mise` is a multi-language version manager (think `asdf` / `nvm`); already
on the user's box for Go and Node. Using mise for Zig avoids needing sudo
and lets you switch versions easily.

## 1. Build llama.cpp

Clone and build llama.cpp with CUDA support from the repo root:

```bash
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
export PATH=/opt/cuda/bin:$PATH
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build --config Release -j$(nproc)
cd ..
```

Adjust `CMAKE_CUDA_ARCHITECTURES` for your GPU:
- RTX 5090 (Blackwell): `120`
- RTX 4090 (Ada Lovelace): `89`
- RTX 3090 (Ampere): `86`

After building, verify the binaries exist:

```bash
ls llama.cpp/build/bin/llama-cli llama.cpp/build/bin/llama-server
```

## 2. Download model weights

Install the Hugging Face CLI if you don't have it:

```bash
pip install --user --break-system-packages huggingface-hub
export PATH="$HOME/.local/bin:$PATH"   # pip puts `hf` here — persist in your shell rc
```

(The `--break-system-packages` flag is Arch-specific; on Debian/Ubuntu use a
venv or `pipx` instead. If you later see `hf: command not found`, the PATH
line above is what's missing.)

Weights do not have to live inside the repo. OpenBeast resolves the weights
directory (env var → `openbeast.conf` → `./weights` → `../weights`) — see
**[§ Where weights live](#where-weights-live)** below to put them on an NVMe,
USB drive, or NAS. The examples below use `--local-dir weights/` (the in-repo
default); substitute your chosen directory if different.

```bash
mkdir -p weights
```

### Qwen3.6-27B Uncensored (HauhauCS Aggressive) -- Q5_K_P (~21GB) — DEFAULT

```bash
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/
```

### Qwen3.6-27B (standard) -- Q5_K_XL (~19GB) — top accuracy (97.85%)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir weights/
```

### Qwen3.6-35B-A3B Uncensored (HauhauCS Aggressive) -- Q4_K_M (~20GB)

```bash
hf download HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf --local-dir weights/
```

### Qwen3.6-35B-A3B (standard MoE) -- Q4_K_M (~20GB)

```bash
hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --local-dir weights/
```

### Gemma 4 31B-it -- Q5_K_XL (~20.4GB)

```bash
hf download unsloth/gemma-4-31B-it-GGUF gemma-4-31B-it-UD-Q5_K_XL.gguf --local-dir weights/
```

### Qwen3.6-27B **MTP** -- Q5_K_XL (~20.4GB)

Builds with Multi-Token Prediction (MTP) draft heads baked in. Pairs with
llama.cpp's `--spec-type draft-mtp` for faster inference — unsloth claims
~1.5–2×; we measured 1.46–2.75× depending on the model (up to 2.75× on this
dense 27B; see `docs/REFERENCE.md` "MTP variants").
Slightly heavier than the non-MTP build because of the embedded MTP head tensors.

```bash
hf download unsloth/Qwen3.6-27B-MTP-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf \
   --local-dir weights/.mtp-staging-27b
mv weights/.mtp-staging-27b/Qwen3.6-27B-UD-Q5_K_XL.gguf \
   weights/Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf
rm -rf weights/.mtp-staging-27b   # hf leaves a .cache/ subdir behind
```

The rename keeps the MTP and non-MTP builds side-by-side under distinct names.

### Qwen3.6-35B-A3B **MTP** (MoE) -- Q4_K_M (~22.7GB)

Same idea — MTP heads embedded for `--spec-type draft-mtp` use.

```bash
hf download unsloth/Qwen3.6-35B-A3B-MTP-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
   --local-dir weights/.mtp-staging-35b
mv weights/.mtp-staging-35b/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
   weights/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf
rm -rf weights/.mtp-staging-35b   # hf leaves a .cache/ subdir behind
```

**MTP launch constraints (upstream llama.cpp limitations as of 2026-05-22):**
- `-np 1` is forced — MTP doesn't yet support more than one parallel slot.
  The MTP serve scripts pin this; concurrent requests serialize.
- `--mmproj` is not yet supported with MTP — no vision input on these builds.

### Qwopus3.6-27B-v2 (Jackrong SFT) -- Q5_K_M (~19.2GB)

Reasoning-enhanced fine-tune of Qwen3.6-27B trained on Trace Inversion
datasets distilled from Claude Opus 4.6/4.7 reasoning traces. Standard
(non-MTP) build.

```bash
hf download Jackrong/Qwopus3.6-27B-v2-GGUF Qwopus3.6-27B-v2-Q5_K_M.gguf \
   --local-dir weights/
rm -rf weights/.cache   # hf leaves a cache subdir behind
```

### Qwopus3.6-27B-v2 **MTP** (Jackrong SFT) -- Q5_K_M (~19.5GB)

Same fine-tune with MTP draft heads embedded — pairs with `--spec-type
draft-mtp`. Inherits the same `-np 1` / no-`mmproj` MTP constraints.

```bash
hf download Jackrong/Qwopus3.6-27B-v2-MTP-GGUF Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf \
   --local-dir weights/
rm -rf weights/.cache   # hf leaves a cache subdir behind
```

**Context caveat for both Qwopus variants:** Jackrong's README cites
"32K/128K native context" — the YaRN extension that ships in the unsloth
Qwen3.6 GGUFs may or may not be intact in their conversion. The serve
scripts ship at our standard 416K (non-MTP) / 336K (MTP) contexts;
if outputs degrade past ~128K in practice, back the contexts down to
something within native limits.

### Fable-Fusion 711 (DavidAU) -- Q5_K_M / Q6_K, regular + MTP (~20.7–24GB each)

Community fine-tune: [DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF](https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF)
— Qwen3.6-27B dense (reasoning ON), Heretic-uncensored, NEO imatrix quants.
The serve scripts expect DavidAU's **exact upstream filenames** (don't rename),
so download straight into `weights/`:

```bash
# Pick any subset. Regular Q5_K_M and Q6_K + their MTP twins:
hf download DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF \
   Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q5_K_M.gguf \
   Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M.gguf \
   Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-Q6_K.gguf \
   Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q6_K.gguf \
   --local-dir weights/
rm -rf weights/.cache   # hf leaves a cache subdir behind
```

Serve with `serve-fable-fusion-27b-q5.sh` / `-mtp-q5` / `-q6` / `-mtp-q6`.
**Contexts, VRAM, and MTP draft depth are measured on the 5090** (2026-07-17 —
see `docs/REFERENCE.md`): Q5 both hold native 262K; Q6 ships at 240K, Q6 MTP at
176K; both MTP builds peak at `--spec-draft-n-max 2` (~108/103 tok/s, a
1.6–1.8× speedup). To re-profile on different hardware: tune draft depth with
`./scripts/profile-fable-fusion-mtp.sh {q5,q6}` and find the largest safe
context with `./scripts/measure-vram.sh`. DavidAU's MTP rules: keep temperature
≤ 1.0 and repetition_penalty = 1.0, or switch to the non-MTP quant if draft
acceptance stays under ~50%.

### Heretic v2 (llmfan46) -- Q5_K_M / Q6_K, both MTP (~19.7 / 22.8GB)

Community fine-tune: [llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF)
— Qwen3.6-27B, uncensored (Heretic v1.3.0 + MPOA), with the native MTP heads
preserved. Two MTP variants prepared; the serve scripts expect the exact
upstream filenames:

```bash
hf download llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF \
   Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf \
   Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q6_K.gguf \
   --local-dir weights/
rm -rf weights/.cache   # hf leaves a cache subdir behind
```

Serve with `serve-heretic-v2-27b-mtp-q5.sh` / `-q6`. **Measured on the 5090**
(2026-07-17 — see `docs/REFERENCE.md`): Q5 holds native 262K at n-max 8
(~136 tok/s), Q6 ships at 208K at n-max 4 (~139 tok/s) — the fastest MTP builds
in the lineup. To re-profile on different hardware: draft depth with
`./scripts/profile-heretic-v2-mtp.sh {q5,q6}`, context ceiling with
`./scripts/measure-vram.sh`. MTP rules: temp ≤ 1.0, repetition_penalty = 1.0;
use a non-MTP quant if draft acceptance stays under ~50%.

You don't need all of these — download whichever models you plan to use.

### Where weights live

You do **not** have to keep weights inside the repo. Every launch script
resolves a weights directory via `scripts/lib/weights.sh`, checking these in
order (first match wins):

1. **`$OPENBEAST_WEIGHTS_DIR`** — environment variable (per-shell override):
   ```bash
   OPENBEAST_WEIGHTS_DIR=/mnt/nvme/gguf ./start.sh
   ```
2. **`WEIGHTS_DIR=` in `openbeast.conf`** — repo-root config, for a persistent
   choice. Gitignored, so your personal path is never committed:
   ```bash
   cp openbeast.conf.example openbeast.conf
   # edit: WEIGHTS_DIR=/mnt/nas/ai/weights   (NVMe, USB, NAS, ~ , or relative)
   ```
3. **`./weights/`** — an in-repo folder, used automatically if it exists (what
   the download commands above create).
4. **`../weights/`** — the default for a fresh clone with no `./weights`: a
   sibling folder next to the `openbeast` checkout.

Paths accept `~` and may be relative (resolved against the repo root). If the
resolved directory is missing, the launch scripts tell you exactly how to set it.

## 3. Install Python dependencies

```bash
pip install --user --break-system-packages -r agents/requirements.txt
```

Installs `openai`, `mcp`, `fastapi`, and `uvicorn` (all pinned) — needed by
the agent runner, the MCP server (including long-running agent management),
and the identity tool server (`agents/openapi_tools.py`) that backs Open
WebUI tool integration.

## 4. Install frontends

### OpenCode (terminal coding agent)

```bash
curl -fsSL https://opencode.ai/install | bash
```

OpenCode reads `opencode.json` from the directory you launch it in. Our
`opencode.json` (committed in the repo root) wires up:
- The local llama.cpp server as an OpenAI-compatible provider on `localhost:8080`
- All 9 configured models with their tuned context limits
- The MCP tool server via stdio (auto-launched as a subprocess by OpenCode)

Run `opencode` from the repo root, or copy `opencode.json` to any project
where you want to use the local stack.

### Open WebUI (browser chat interface)

```bash
docker pull ghcr.io/open-webui/open-webui:main
```

Open WebUI is configured by `docker-compose.yml` (in repo root). It:
- Runs in `network_mode: host` so it can reach `localhost:8080` (llama.cpp)
- Defaults to no login wall (`WEBUI_AUTH=false`) for local-only use — the
  full-tools demo works immediately. `scripts/setup-tailscale.sh` turns auth
  on (and RBAC tiers apply) when you expose the WebUI to your tailnet; see §7.
- Persists chat history to a Docker named volume (`open-webui-data`)

Available at http://localhost:3000 once the stack is running.

### SearXNG (private web search backend)

```bash
docker pull searxng/searxng:latest
```

Used by the `web_search` MCP tool. Our `docker-compose.yml` mounts
`searxng/settings.yml` (committed in the repo) into the container with two
non-default settings:
- JSON format enabled (the stock image has only HTML, which breaks the MCP tool)
- Rate limiter disabled (for local use)
- Granian server bound to port 8888 (stock image defaults to 8080, which
  collides with llama.cpp)

The session-signing key is NOT in the mounted file — it's a per-install
random secret that the first `./start.sh` generates into `openbeast.conf`
(gitignored, mode 600) and injects via the `SEARXNG_SECRET` env var. If you
run `docker compose up` by hand, source `scripts/lib/conf.sh` first (with
`REPO_DIR` set to the repo root) or compose will refuse to start SearXNG.

No other manual config needed — the mounted file handles the rest.

## 5. Start the stack

The default model is **Qwen3.6-27B Uncensored Q5_K_P** (HauhauCS Aggressive) — an
uncensored fine-tune that scores 96.16 % on the v3.5 sweep (#2 overall). Swap in
another model with a single arg (below): the dense 27B Q5 for top accuracy, or a
35B-A3B MoE when interactive speed matters more.

```bash
./start.sh                                       # default model (27B Uncensored Q5) + identity tool server + Open WebUI + SearXNG
./start.sh serve-qwen-27b-q5.sh                  # dense 27B Q5 — top accuracy (97.85%)
./start.sh serve-qwen-35b-a3b.sh                 # standard 35B-A3B MoE (30–50% faster tokens)
./start.sh serve-qwen-35b-a3b-uncensored-q4.sh   # 35B-A3B Uncensored MoE (fastest wall-clock)
./start.sh serve-gemma-4-31b-q5.sh               # Gemma 4 31B
```

This launches:
1. **llama.cpp server** on port 8080 (OpenAI-compatible API)
2. **Identity tool server** on port 3001 (`agents/openapi_tools.py` — serves the
   15 tools as OpenAPI for Open WebUI, with per-user file shards, RBAC keys,
   and an audit trail)
3. **Open WebUI** on port 3000 (Docker container)
4. **SearXNG** on port 8888 (Docker container, used by `web_search`)
5. **configure-webui.sh** (auto-configures tool server + native function calling
   + system prompt for every detected model)
6. *(opt-in)* **agent-spawn router** on port 8088 — only when `AGENT_ROUTER=true`
   in `openbeast.conf`; the frontends then send chat through it

`start.sh` also creates `~/openbeast-files` (mode `0700`) — the private
workspace where files the chat model writes via the direct tools land
(configurable via `FILES_DIR` in `openbeast.conf`).

On first run, `configure-webui.sh` populates Open WebUI with:
- The identity tool server registered as an OpenAPI endpoint
- Native function calling mode for every model (required for Qwen tool use —
  the prompt-based default breaks with `<think>` tags)
- The system prompt (`system-prompt.md` + `system-prompt-tools.md`) written
  into each model's DB entry

OpenCode runs separately from the stack:

```bash
cd ~/my-project        # any directory, but opencode.json must be reachable
opencode
```

To stop everything:

```bash
./stop.sh
```

## 6. Verify

- **Model server:** `curl http://localhost:8080/health` (returns `{"status":"ok"}`)
- **Identity tool server:** `curl http://localhost:3001/openapi.json | python3 -m json.tool | head` (lists all 15 tools; `curl http://localhost:3001/health` for liveness)
- **Open WebUI:** open http://localhost:3000 in a browser
- **SearXNG:** `curl 'http://localhost:8888/search?q=test&format=json' | head -c 200` (returns JSON results, not 403)
- **OpenCode:** run `opencode` in a project directory, select a `qwen-*` or `gemma-*` model
- **Tool use:** in Open WebUI, click the wrench icon in the chat input and toggle on "Local Tools (privileged)"
- **Long-running agents:** ask the model to use `start_agent` to spawn a background agent, then `check_agent` to monitor
- **Health check:** `./scripts/healthcheck.sh` (services + GPU VRAM + slot usage; `--restart` to auto-recover)
- **Smoke test:** `./tests/test_smoke.sh` (end-to-end stack validation)
- **Eval harness:** `python3 evals/run_eval.py` (v4 suite — 137 base tasks / 291 units; see evals/README.md)

## 7. Remote access (optional, recommended)

Everything binds `127.0.0.1` by default, so the stack is unreachable from
other devices until you join a tailnet (or explicitly opt back into LAN-open
binds with `BIND_HOST=0.0.0.0`). Verified end-to-end 2026-07-07; the whole
flow is ~5 minutes.

**You need:** any Google/GitHub/Microsoft account for Tailscale's free plan
(100 devices, 3 users — plenty).

```bash
./scripts/setup-tailscale.sh
```

The script is idempotent (safe to re-run anytime) and stops for exactly two
browser moments, telling you precisely what to do at each:

1. **Tailnet login** — it prints a login URL; sign in with your SSO account.
   The machine joins as `beast` (override with `TS_HOSTNAME=<name>`).
2. **Two one-time tailnet toggles** — on first setup it sends you to
   <https://login.tailscale.com/admin/dns> to enable **MagicDNS** and
   **HTTPS Certificates**, then waits and continues automatically once you
   flip them. (The cert-transparency warning on the HTTPS toggle is
   expected: machine *names* become publicly logged, the services behind
   them stay tailnet-only.)

It finishes by printing your two permanent URLs:

| URL | What |
|---|---|
| `https://beast.<tailnet>.ts.net` | Open WebUI (chat) |
| `https://beast.<tailnet>.ts.net:8443/v1` | OpenAI-compatible API |

### Post-setup (one time, ~3 minutes)

1. Restart the stack so the loopback binds + WebUI auth take effect:
   `./stop.sh && ./start.sh`
2. Open the WebUI URL and **create the admin account immediately** —
   `WEBUI_AUTH=true` now, and the *first* signup becomes admin.
3. Mirror those credentials into `openbeast.conf` (`WEBUI_ADMIN_EMAIL` /
   `WEBUI_ADMIN_PASSWORD`) so `scripts/configure-webui.sh` can keep applying
   tool-server config on restarts.

### Add your devices (each ~1 minute)

- **Phone:** install the Tailscale app → sign in with the same account →
  open `https://beast.<tailnet>.ts.net` → browser menu → "Add to Home
  Screen". Open WebUI is a PWA — it installs like a native chat app and
  works anywhere you have signal, home or abroad.
- **Laptop:** install Tailscale ([tailscale.com/download](https://tailscale.com/download)),
  sign in, done — both URLs work in any browser.
- **Coding agent from anywhere:** point OpenCode (or any OpenAI-compatible
  client) at the API URL. In `opencode.json`, use
  `"baseURL": "https://beast.<tailnet>.ts.net:8443/v1"` — full agent
  against your home GPU from a cafe.

### Verify the security boundary

- From a device **off** your home network (phone hotspot):
  `curl https://beast.<tailnet>.ts.net:8443/v1/models` → model list.
- Same URL with Tailscale disconnected on that device → connection fails.
  That failure is the proof the perimeter works.
- `./scripts/healthcheck.sh` — the report includes a Tailscale row.
- Optional: `nmap <this-machine's-LAN-IP>` from a LAN device — ports 3000,
  8080, 3001, 8888 all closed.

See [REMOTE_ACCESS_PLAN.md](REMOTE_ACCESS_PLAN.md) for the full design
rationale (why Tailscale, the Headscale escape hatch, what's deliberately
out of scope) and the README "Remote access" section for day-to-day usage.

## Architecture notes

### Why an OpenAPI tool server instead of direct MCP?

Open WebUI (as of v0.9.x) has native MCP Streamable HTTP support, but it has a
known bug — it sends incorrect HTTP requests to MCP endpoints — so tools must
be offered as standard OpenAPI endpoints, which Open WebUI consumes natively
as "External Tools." Our **identity tool server** (`agents/openapi_tools.py`,
port 3001) does that job. It replaced the generic MCPO proxy (v1.1,
2026-07-09) because MCPO dropped the identity headers Open WebUI forwards;
our server reads them to shard each user's files into their own workspace,
enforce the RBAC profile keys, and write an audit trail. It imports the same
15 tool functions as `agents/mcp_server.py`, so the two surfaces can't drift.

### Why native function calling?

Open WebUI has two function calling modes:
- **Default (prompt-based):** Injects tool descriptions as XML in the prompt and
  asks the model to output JSON. Breaks with Qwen's thinking mode (`<think>` tags
  interfere with JSON parsing), causing the UI to hang after tool execution.
- **Native:** Sends the standard `tools` array in the API request. llama.cpp
  returns proper `tool_calls` in the response, and tool results go back as
  `role: tool` messages. This is the correct flow for Qwen on llama.cpp.

`configure-webui.sh` sets native mode automatically for all models.

### System prompt (soul file)

The system prompt is split into two files to support different frontends cleanly:

- **`system-prompt.md`** — Persona and operating principles (the "soul"). Applied
  to all frontends. Contains no tool references.
- **`system-prompt-tools.md`** — Tool-use guidance for our MCP tools. Only used
  by Open WebUI, where the model needs to know about `edit_file`, `web_search`,
  `start_agent`, etc. by name.

OpenCode has its own built-in tool system with different names (`edit` vs
`edit_file`, `view` vs `read_file`), so it only gets the soul file — no
conflicting tool descriptions.

How each frontend uses them:

- **Open WebUI:** `configure-webui.sh` concatenates both files and writes the
  result into each model's database entry. Every new chat inherits it.
- **OpenCode:** Reads `system-prompt.md` only (via global config). OpenCode
  injects its own tool schemas separately.
- **agent.sh / runner.py:** Reads `system-prompt.md` and appends its own
  inline agent instructions with tool guidance.
- **Interactive chat:** Not injected automatically — pass it manually with
  `--system-prompt-file system-prompt.md` if desired.

To change the prompt, edit `system-prompt.md` and/or `system-prompt-tools.md`
and re-run `./scripts/configure-webui.sh` (or restart the stack). Changes take
effect on the next new chat.

### Why OpenCode uses stdio, not the identity tool server

OpenCode runs locally and launches the MCP server as a child process via stdio
(configured in `opencode.json`). This is simpler and faster than HTTP — no
intermediary needed. The identity tool server only exists to bridge the gap for
Open WebUI's HTTP-based tool server integration (and to add the per-user
identity layer WebUI accounts need).

## Troubleshooting

**Renamed the repo directory → `start.sh` dies with `Conflict. The container
name "/open-webui" is already in use`** — Docker Compose derives its project
name from the directory, so containers created under the old name conflict
with the new project (and `start.sh`'s cleanup then stops llama/the tool server too,
leaving only the old container running). The compose file now pins
`name: openbeast`, so this can't recur — but if you're recovering from a
rename that predates the pin:

1. `docker rm -f open-webui searxng` — containers are disposable; data
   lives in volumes.
2. Check for a forked data volume: `docker volume ls | grep open-webui-data`.
   If both `<oldname>_open-webui-data` and `openbeast_open-webui-data`
   exist, your chats/config are in the old one. Migrate:
   ```bash
   docker stop open-webui
   docker run --rm -v <oldname>_open-webui-data:/old:ro \
     -v openbeast_open-webui-data:/new alpine \
     sh -c 'rm -rf /new/* ; cp -a /old/. /new/'
   ```
3. Rerun `./start.sh`. Note: a database from the `WEBUI_AUTH=false` era has
   an `admin@localhost` account with no usable password — set one directly
   before logging in (bcrypt-hash a password into the `auth` table of
   `webui.db`) or you'll be locked out under `WEBUI_AUTH=true`.

**`llama-cli not found`** — llama.cpp isn't built or the build directory structure
changed. Rebuild and check that `llama.cpp/build/bin/llama-cli` exists.

**`cudaMalloc failed: out of memory`** — not enough VRAM. Either another process is
using the GPU, or the context length is too high. Override with a smaller context:

```bash
./scripts/run-qwen-27b-q5.sh -c 262144
```

See `REFERENCE.md` for measured VRAM at different context lengths. The OS/desktop
compositor uses ~2GB of GPU VRAM — always leave at least 2GB headroom.

**`hf: command not found`** — two causes: the CLI isn't installed (step 2
above), or — most common — pip installed it to `~/.local/bin`, which isn't on
your PATH. Fix: `export PATH="$HOME/.local/bin:$PATH"` (add to your shell rc).
Note: `huggingface-cli` is deprecated, use `hf` instead.

**`permission denied: ./start.sh`** — clone didn't preserve the executable bit.
Fix: `chmod +x start.sh stop.sh agent.sh scripts/*.sh tests/*.sh`.

**Open WebUI can't connect to model** — make sure the llama.cpp server is running
first (`./start.sh` handles this automatically). The Docker container uses host
network mode, so it reaches llama.cpp via `localhost:8080`.

**Tools don't work in Open WebUI** — check these in order:
1. Is the identity tool server running? `curl http://localhost:3001/openapi.json`
   should return JSON (`curl http://localhost:3001/health` for liveness).
2. Is the tool server configured? Admin Settings > External Tools should show
   "Local Tools (privileged)" and "Web Search (all users)" with URL
   `http://localhost:3001`.
3. Is native function calling enabled? Admin Settings > Models > [your model] >
   Advanced > Function Calling should be set to "Native."
4. Did you enable tools in the chat? Click the wrench icon in the chat input
   area and toggle on the Local Tools.

If all else fails, re-run `./scripts/configure-webui.sh` and restart Open WebUI
(`docker restart open-webui`).

**`web_search` returns "SearXNG is not running"** — the SearXNG container is
crashing (check `docker ps` for restart count). Two known causes, both already
patched in our `docker-compose.yml` and `searxng/settings.yml`:
1. Granian binds to its default port 8080 (clashes with llama.cpp) unless
   `GRANIAN_PORT=8888` is set.
2. The stock `settings.yml` only enables `formats: [html]`, so JSON queries get
   403 Forbidden. We mount our own with `formats: [html, json]`.

If you ever upgrade the SearXNG image and these break again, see
`searxng/settings.yml` and the `searxng` service in `docker-compose.yml`.

**OpenCode shows no local models** — make sure you're running `opencode` from a
directory that can find the `opencode.json` config (the repo root), or copy
`opencode.json` to your project. The llama.cpp server must be running on port 8080.

**Docker permission denied** — your user isn't in the `docker` group. Run
`sudo usermod -aG docker "$USER"` and either log out/in or run `newgrp docker`.
