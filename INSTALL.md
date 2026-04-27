# Installation

This repo contains scripts for running local LLMs via llama.cpp on NVIDIA GPUs,
with OpenCode (terminal coding agent) and Open WebUI (browser chat interface) as
frontends.

The inference engine, model weights, and Docker volumes are not checked in —
follow the steps below to set them up.

## Prerequisites

- NVIDIA GPU with CUDA support (tested on RTX 5090, Blackwell SM 120)
- NVIDIA driver installed (`nvidia-smi` should work)
- `cuda` and `cmake` installed
- `gcc`/`g++` installed
- Docker installed (for Open WebUI)

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

### Qwen3.6-35B-A3B -- Q4_K_M (~22GB)

```bash
hf download unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --local-dir weights/
```

You don't need all three -- download whichever models you plan to use.

## 3. Install frontends

### OpenCode (terminal coding agent)

```bash
curl -fsSL https://opencode.ai/install | bash
```

OpenCode is configured via `opencode.json` in the repo root. It will automatically
detect the local llama.cpp server when you run `opencode` in any project directory
while the server is running.

### Open WebUI (browser chat interface)

Pull the Docker image:

```bash
docker pull ghcr.io/open-webui/open-webui:main
```

Open WebUI is configured via `docker-compose.yml` and will be available at
http://localhost:3000 when the stack is running.

## 4. Start the stack

The easiest way to run everything:

```bash
./start.sh                          # starts 27B Q5 server + Open WebUI
./start.sh serve-qwen-27b-q4.sh     # use a different model
```

This launches the llama.cpp server and Open WebUI. Then use OpenCode separately
in any project:

```bash
cd ~/my-project
opencode
```

To stop everything:

```bash
./stop.sh
```

## 5. Verify

- **Model server:** `curl http://localhost:8080/health`
- **Open WebUI:** open http://localhost:3000 in a browser
- **OpenCode:** run `opencode` in a project directory, select the local model

## Troubleshooting

**`llama-cli not found`** -- llama.cpp isn't built or the build directory structure
changed. Rebuild and check that `llama.cpp/build/bin/llama-cli` exists.

**`cudaMalloc failed: out of memory`** -- not enough VRAM. Either another process is
using the GPU, or the context length is too high. Override with a smaller context:

```bash
./run-qwen-27b-q4.sh -c 262144
```

See `SETUP.md` for VRAM estimates at different context lengths.

**`hf: command not found`** -- install the Hugging Face CLI (step 2 above).
Note: `huggingface-cli` is deprecated, use `hf` instead.

**Open WebUI can't connect to model** -- make sure the llama.cpp server is running
first (`./start.sh` handles this automatically). The Docker container reaches the
host via `host.docker.internal`.

**OpenCode shows no local models** -- make sure you're running `opencode` from a
directory that can find the `opencode.json` config (the repo root), or copy
`opencode.json` to your project. The llama.cpp server must be running on port 8080.
