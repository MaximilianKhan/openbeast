# Installation

This repo contains scripts for running local LLMs via llama.cpp on NVIDIA GPUs,
with OpenCode (terminal coding agent) and Open WebUI (browser chat interface) as
frontends, plus an autonomous agent runner and MCP tool server for tool use.

The inference engine, model weights, and Docker volumes are not checked in —
follow the steps below to set them up.

## Prerequisites

- NVIDIA GPU with CUDA support (tested on RTX 5090, Blackwell SM 120)
- NVIDIA driver installed (`nvidia-smi` should work)
- `cuda` and `cmake` installed
- `gcc`/`g++` installed
- Docker installed (for Open WebUI)
- Python 3.10+ with `pip`

On Arch Linux:

```bash
sudo pacman -S cuda cmake docker
sudo systemctl enable --now docker
```

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

Create the weights directory and download models:

```bash
mkdir -p weights
```

### Qwen3.6-27B -- Q4_K_M (~16GB)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-Q4_K_M.gguf --local-dir weights/
```

### Qwen3.6-27B -- Q5_K_XL (~19GB)

```bash
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir weights/
```

### Qwen3.6-27B Uncensored (HauhauCS Aggressive) -- Q5_K_P (~21GB)

```bash
hf download HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf --local-dir weights/
```

### Qwen3.6-35B-A3B -- Q4_K_M (~20GB)

```bash
hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --local-dir weights/
```

You don't need all of these -- download whichever models you plan to use.

## 3. Install Python dependencies

```bash
pip install --user --break-system-packages -r agents/requirements.txt
```

This installs the OpenAI SDK, MCP SDK, and MCPO (MCP-to-OpenAPI proxy) needed
by the agent runner, MCP server (including long-running agent management), and
Open WebUI tool integration.

## 4. Install frontends

### OpenCode (terminal coding agent)

```bash
curl -fsSL https://opencode.ai/install | bash
```

OpenCode is configured via `opencode.json` in the repo root. MCP tools (bash,
file I/O, grep) are wired via stdio transport — OpenCode launches the MCP server
automatically. Run `opencode` in any project directory while the server is running.

### Open WebUI (browser chat interface)

Pull the Docker image:

```bash
docker pull ghcr.io/open-webui/open-webui:main
```

Open WebUI is configured via `docker-compose.yml` and will be available at
http://localhost:3000 when the stack is running.

## 5. Start the stack

```bash
./start.sh                          # starts model server + MCPO tools + Open WebUI
./start.sh serve-qwen-27b-q4.sh     # use a different model
```

This launches:
1. **llama.cpp server** on port 8080 (OpenAI-compatible API)
2. **MCPO proxy** on port 3001 (wraps MCP tools as OpenAPI for Open WebUI)
3. **Open WebUI** on port 3000 (Docker container)
4. **configure-webui.sh** (auto-configures tool server + native function calling)

On a fresh install, `configure-webui.sh` runs automatically and sets up:
- The MCPO tool server as an OpenAPI endpoint in Open WebUI
- Native function calling mode for all detected models (required for Qwen tool use)
- The system prompt from `system-prompt.md` (applied to all models)

Then use OpenCode separately in any project:

```bash
cd ~/my-project
opencode
```

To stop everything:

```bash
./stop.sh
```

## 6. Verify

- **Model server:** `curl http://localhost:8080/health`
- **MCPO tools:** `curl http://localhost:3001/docs` (should show OpenAPI docs)
- **Open WebUI:** open http://localhost:3000 in a browser
- **OpenCode:** run `opencode` in a project directory, select the local model
- **Tool use:** in Open WebUI, enable the "Local Tools (MCPO)" tool in the chat
  input area (wrench icon), then ask the model to run a command
- **Long-running agents:** ask the model to use `start_agent` to spawn a
  background agent, then `check_agent` to monitor it

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

`system-prompt.md` is the shared system prompt applied to all models across
all frontends. It defines the model's persona, communication style, and
operating principles — the local equivalent of Claude's CLAUDE.md.

The file lives in the repo root so it's version-controlled and portable. It
propagates to each frontend differently:

- **Open WebUI:** `configure-webui.sh` writes the contents into each model's
  database entry (`meta.system`). Every new chat inherits it automatically.
- **agent.sh:** `runner.py` reads the file at startup and prepends it to the
  agent's task-specific instructions.
- **Interactive chat:** Not injected automatically by `scripts/run.sh` — pass it
  manually with `--system-prompt-file system-prompt.md` if needed.

To change the prompt, edit `system-prompt.md` and re-run `./scripts/configure-webui.sh`
(or restart the stack). Changes take effect on the next new chat.

### Why OpenCode uses stdio, not MCPO

OpenCode runs locally and launches the MCP server as a child process via stdio
(configured in `opencode.json`). This is simpler and faster than HTTP — no proxy
needed. MCPO only exists to bridge the gap for Open WebUI's HTTP-based tool
server integration.

## Troubleshooting

**`llama-cli not found`** -- llama.cpp isn't built or the build directory structure
changed. Rebuild and check that `llama.cpp/build/bin/llama-cli` exists.

**`cudaMalloc failed: out of memory`** -- not enough VRAM. Either another process is
using the GPU, or the context length is too high. Override with a smaller context:

```bash
./scripts/run-qwen-27b-q4.sh -c 262144
```

See `SETUP.md` for VRAM estimates at different context lengths. The OS/desktop
compositor uses ~2GB of GPU VRAM — always leave at least 2GB headroom.

**`hf: command not found`** -- install the Hugging Face CLI (step 2 above).
Note: `huggingface-cli` is deprecated, use `hf` instead.

**Open WebUI can't connect to model** -- make sure the llama.cpp server is running
first (`./start.sh` handles this automatically). The Docker container uses host
network mode, so it reaches llama.cpp via `localhost:8080`.

**Tools don't work in Open WebUI** -- check these in order:
1. Is MCPO running? `curl http://localhost:3001/docs` should show the Swagger UI.
2. Is the tool server configured? Admin Settings > External Tools should show
   "Local Tools (MCPO)" with URL `http://localhost:3001`.
3. Is native function calling enabled? Admin Settings > Models > [your model] >
   Advanced > Function Calling should be set to "Native."
4. Did you enable tools in the chat? Click the wrench icon in the chat input
   area and toggle on the Local Tools.

If all else fails, re-run `./scripts/configure-webui.sh` and restart Open WebUI
(`docker restart open-webui`).

**OpenCode shows no local models** -- make sure you're running `opencode` from a
directory that can find the `opencode.json` config (the repo root), or copy
`opencode.json` to your project. The llama.cpp server must be running on port 8080.
