# Installation

This repo runs local LLMs via llama.cpp on NVIDIA GPUs, with OpenCode (terminal
agent), Open WebUI (browser chat), an autonomous agent runner, and an MCP tool
server providing 17 tools for file I/O, shell, web search, agent management,
and a curated skills system (14 specialized expertise packages loaded on
demand).

The inference engine, model weights, and Docker volumes are not checked in —
follow the steps below to set them up. Everything else (configs, scripts,
SearXNG settings) lives in the repo and is portable across Linux boxes.

## TL;DR — Fresh box bootstrap

For a working stack on a fresh Linux machine with NVIDIA + Docker:

```bash
# System packages (Arch shown; adjust for your distro)
sudo pacman -S cuda cmake docker git python python-pip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker

# Clone the repo
git clone <repo-url> models && cd models

# Build llama.cpp with CUDA (set CMAKE_CUDA_ARCHITECTURES for your GPU)
git clone https://github.com/ggml-org/llama.cpp.git
export PATH=/opt/cuda/bin:$PATH
(cd llama.cpp && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 \
                 && cmake --build build --config Release -j$(nproc))

# Python deps + Hugging Face CLI
pip install --user --break-system-packages huggingface-hub[cli] -r agents/requirements.txt

# Default model — top of internal leaderboard (97.3% accuracy / 86.7 speed)
hf download HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf --local-dir weights/

# Frontends
curl -fsSL https://opencode.ai/install | bash
docker pull ghcr.io/open-webui/open-webui:main
docker pull searxng/searxng

# Make scripts executable (in case clone didn't preserve the bit)
chmod +x start.sh stop.sh agent.sh scripts/*.sh tests/*.sh

# Launch
./start.sh
```

The detailed walkthrough below explains each step and lists alternate models.

## Prerequisites

### Core
- NVIDIA GPU with CUDA support (tested on RTX 5090, Blackwell SM 120)
- NVIDIA driver installed (`nvidia-smi` should work)
- `cuda` and `cmake` installed
- `gcc`/`g++` installed
- Docker installed (for Open WebUI + SearXNG); user in `docker` group
- Python 3.10+ with `pip`

### Compiler toolchains for the eval suite (multi-language variants)

The 159-task eval suite includes 33 base tasks with multi-language variants
(Python / Go / C / C++ / Rust / Zig — ~187 variant entries; suite now ~313
effective test units total). To run those variants, all six toolchains need
to be available:

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
pip install --user --break-system-packages huggingface-hub[cli]
```

(The `--break-system-packages` flag is Arch-specific; on Debian/Ubuntu use a
venv or `pipx` instead.)

Create the weights directory and download whichever models you plan to use:

```bash
mkdir -p weights
```

### Qwen3.6-35B-A3B Uncensored (HauhauCS Aggressive) -- Q4_K_M (~20GB) — DEFAULT

```bash
hf download HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf --local-dir weights/
```

### Qwen3.6-35B-A3B (standard MoE) -- Q4_K_M (~20GB)

```bash
hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --local-dir weights/
```

### Qwen3.6-27B Uncensored (HauhauCS Aggressive) -- Q5_K_P (~21GB)

```bash
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive \
   Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/
```

### Qwen3.6-27B (standard) -- Q5_K_XL (~19GB)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir weights/
```

### Gemma 4 31B-it -- Q5_K_XL (~20.4GB)

```bash
hf download unsloth/gemma-4-31B-it-GGUF gemma-4-31B-it-UD-Q5_K_XL.gguf --local-dir weights/
```

You don't need all of these — download whichever models you plan to use.

## 3. Install Python dependencies

```bash
pip install --user --break-system-packages -r agents/requirements.txt
```

Installs `openai`, `mcp`, and `mcpo` — needed by the agent runner, the MCP
server (including long-running agent management), and Open WebUI tool integration.

## 4. Install frontends

### OpenCode (terminal coding agent)

```bash
curl -fsSL https://opencode.ai/install | bash
```

OpenCode reads `opencode.json` from the directory you launch it in. Our
`opencode.json` (committed in the repo root) wires up:
- The local llama.cpp server as an OpenAI-compatible provider on `localhost:8080`
- All 5 models with their tuned context limits
- The MCP tool server via stdio (auto-launched as a subprocess by OpenCode)

Run `opencode` from the repo root, or copy `opencode.json` to any project
where you want to use the local stack.

### Open WebUI (browser chat interface)

```bash
docker pull ghcr.io/open-webui/open-webui:main
```

Open WebUI is configured by `docker-compose.yml` (in repo root). It:
- Runs in `network_mode: host` so it can reach `localhost:8080` (llama.cpp)
- Auto-disables auth (`WEBUI_AUTH=false`) for local-only use
- Persists chat history to a Docker named volume (`open-webui-data`)

Available at http://localhost:3000 once the stack is running.

### SearXNG (private web search backend)

```bash
docker pull searxng/searxng
```

Used by the `web_search` MCP tool. Our `docker-compose.yml` mounts
`searxng/settings.yml` (committed in the repo) into the container with two
non-default settings:
- JSON format enabled (the stock image has only HTML, which breaks the MCP tool)
- Rate limiter disabled (for local use)
- Granian server bound to port 8888 (stock image defaults to 8080, which
  collides with llama.cpp)

No manual config needed — the mounted file handles all of it.

## 5. Start the stack

The default model is **Qwen3.6-35B-A3B Uncensored (HauhauCS Aggressive) Q4_K_M**
— top of our internal leaderboard at 97.3 % accuracy with the fastest sweep
time among the 5 models we benchmarked.

```bash
./start.sh                                       # default model + MCPO + Open WebUI + SearXNG
./start.sh serve-qwen-27b-uncensored-q5.sh       # 27B Uncensored Q5 (slower but tighter quant)
./start.sh serve-qwen-35b-a3b.sh                 # standard 35B MoE
./start.sh serve-gemma-4-31b-q5.sh               # Gemma 4 31B
```

This launches:
1. **llama.cpp server** on port 8080 (OpenAI-compatible API)
2. **MCPO proxy** on port 3001 (wraps MCP tools as OpenAPI for Open WebUI)
3. **Open WebUI** on port 3000 (Docker container)
4. **SearXNG** on port 8888 (Docker container, used by `web_search`)
5. **configure-webui.sh** (auto-configures tool server + native function calling
   + system prompt for every detected model)

On first run, `configure-webui.sh` populates Open WebUI with:
- The MCPO tool server registered as an OpenAPI endpoint
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
- **MCPO tools:** `curl http://localhost:3001/openapi.json | python3 -m json.tool | head` (lists all 17 tools)
- **Open WebUI:** open http://localhost:3000 in a browser
- **SearXNG:** `curl 'http://localhost:8888/search?q=test&format=json' | head -c 200` (returns JSON results, not 403)
- **OpenCode:** run `opencode` in a project directory, select a `qwen-*` or `gemma-*` model
- **Tool use:** in Open WebUI, click the wrench icon in the chat input and toggle on "Local Tools (MCPO)"
- **Long-running agents:** ask the model to use `start_agent` to spawn a background agent, then `check_agent` to monitor
- **Health check:** `./scripts/healthcheck.sh` (services + GPU VRAM + slot usage; `--restart` to auto-recover)
- **Smoke test:** `./tests/test_smoke.sh` (end-to-end stack validation)
- **Eval harness:** `python3 evals/run_eval.py` (benchmark on 10 coding tasks)

## Architecture notes

### Why MCPO instead of direct MCP?

Open WebUI (as of v0.9.x) has native MCP Streamable HTTP support, but it has a
known bug — it sends incorrect HTTP requests to MCP endpoints. MCPO is the
officially recommended workaround from the Open WebUI team. It wraps our MCP
server (launched via stdio) as standard OpenAPI endpoints, which Open WebUI
consumes natively as "External Tools."

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

### Why OpenCode uses stdio, not MCPO

OpenCode runs locally and launches the MCP server as a child process via stdio
(configured in `opencode.json`). This is simpler and faster than HTTP — no proxy
needed. MCPO only exists to bridge the gap for Open WebUI's HTTP-based tool
server integration.

## Troubleshooting

**`llama-cli not found`** — llama.cpp isn't built or the build directory structure
changed. Rebuild and check that `llama.cpp/build/bin/llama-cli` exists.

**`cudaMalloc failed: out of memory`** — not enough VRAM. Either another process is
using the GPU, or the context length is too high. Override with a smaller context:

```bash
./scripts/run-qwen-27b-q5.sh -c 262144
```

See `REFERENCE.md` for measured VRAM at different context lengths. The OS/desktop
compositor uses ~2GB of GPU VRAM — always leave at least 2GB headroom.

**`hf: command not found`** — install the Hugging Face CLI (step 2 above).
Note: `huggingface-cli` is deprecated, use `hf` instead.

**`permission denied: ./start.sh`** — clone didn't preserve the executable bit.
Fix: `chmod +x start.sh stop.sh agent.sh scripts/*.sh tests/*.sh`.

**Open WebUI can't connect to model** — make sure the llama.cpp server is running
first (`./start.sh` handles this automatically). The Docker container uses host
network mode, so it reaches llama.cpp via `localhost:8080`.

**Tools don't work in Open WebUI** — check these in order:
1. Is MCPO running? `curl http://localhost:3001/openapi.json` should return JSON.
2. Is the tool server configured? Admin Settings > External Tools should show
   "Local Tools (MCPO)" with URL `http://localhost:3001`.
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
