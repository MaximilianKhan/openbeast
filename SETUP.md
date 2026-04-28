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
├── run.sh                  # generic interactive chat launcher
├── serve.sh                # generic OpenAI-compatible API server
├── run-qwen-27b-q4.sh      # Qwen 27B Q4_K_M — chat
├── serve-qwen-27b-q4.sh    # Qwen 27B Q4_K_M — API
├── run-qwen-27b-q5.sh      # Qwen 27B Q5_K_XL — chat
├── serve-qwen-27b-q5.sh    # Qwen 27B Q5_K_XL — API
├── run-qwen-27b-uncensored-q5.sh  # Qwen 27B Uncensored Q5_K_P — chat
├── serve-qwen-27b-uncensored-q5.sh # Qwen 27B Uncensored Q5_K_P — API
├── run-qwen-35b-a3b.sh     # Qwen 35B-A3B (MoE) — chat
├── serve-qwen-35b-a3b.sh   # Qwen 35B-A3B (MoE) — API
├── agent.sh                # run a local agent against a task
├── agents/                 # agent framework + MCP tool server
│   ├── runner.py           # standalone agent loop (LLM + tool use)
│   ├── tools.py            # tool definitions for standalone agent
│   ├── mcp_server.py       # MCP server (exposes tools to OpenCode + Open WebUI)
│   ├── requirements.txt
│   └── logs/               # agent run logs (JSONL) [gitignored]
├── start.sh                # launch full stack (server + MCPO + Open WebUI)
├── stop.sh                 # stop full stack
├── configure-webui.sh      # auto-configure Open WebUI (tool server + native FC)
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
- Estimated KV cost: **~11 KB/token** (extrapolated from 27B, needs real-world validation)
- Default **512K** context, ~27.6GB total

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
- Tool use via MCPO (bash, file I/O, grep)

### MCP tool server + MCPO proxy

Exposes local tools (bash, read_file, write_file, list_files, grep) to any
MCP-compatible client.

- **stdio** — used by OpenCode (launched automatically via `opencode.json`)
- **MCPO** — used by Open WebUI (`http://localhost:3001`, started by `./start.sh`).
  MCPO wraps the MCP server (via stdio) as OpenAPI endpoints. This works around
  Open WebUI's broken native MCP Streamable HTTP support.

## 5. Run

### Full stack

```bash
./start.sh                          # 27B Uncensored Q5 + MCP tools + Open WebUI
./start.sh serve-qwen-27b-q4.sh     # use a different model
./stop.sh                           # stop everything (server, MCP, Open WebUI)
```

### Interactive chat (standalone)

```bash
./run-qwen-27b-q4.sh       # Qwen 27B Q4_K_M  (512K ctx, ~25GB VRAM)
./run-qwen-27b-q5.sh       # Qwen 27B Q5_K_XL (416K ctx, ~26.3GB VRAM)
./run-qwen-27b-uncensored-q5.sh  # Qwen 27B Uncensored Q5_K_P (416K ctx, ~28.3GB VRAM)
./run-qwen-35b-a3b.sh      # Qwen 35B-A3B MoE (512K ctx, ~27.6GB VRAM)
```

### OpenAI-compatible API server

```bash
./serve-qwen-27b-q4.sh     # http://localhost:8080/v1/chat/completions
./serve-qwen-27b-q5.sh     # http://localhost:8080/v1/chat/completions
./serve-qwen-27b-uncensored-q5.sh  # http://localhost:8080/v1/chat/completions
./serve-qwen-35b-a3b.sh    # http://localhost:8080/v1/chat/completions
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

### Custom flags

All model scripts forward extra args to `run.sh`/`serve.sh`, which forward to llama.cpp:

```bash
./run-qwen-27b-q5.sh -c 524288       # override context length
./serve-qwen-35b-a3b.sh -p 9090      # override port
./run.sh -m weights/some-model.gguf   # use generic script directly
```

## Adding a new model

1. Download the GGUF to `weights/`
2. Create `run-<name>.sh` and `serve-<name>.sh` that call `run.sh`/`serve.sh` with `-m` and appropriate `-c`
3. `chmod +x` the new scripts
4. Document above
