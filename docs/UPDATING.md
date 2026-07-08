# Updating OpenBeast's pulled-in components

OpenBeast orchestrates several upstream open source projects (full list and
credits: [`NOTICE`](../NOTICE) and the README credits section). Upstreams
move fast — llama.cpp alone lands performance work weekly — so keeping them
fresh is worth doing periodically.

## One command

```bash
./scripts/update.sh
```

That updates everything: llama.cpp (git pull + CUDA rebuild), the Open WebUI
and SearXNG container images, the Python layer (MCPO, MCP SDK, openai,
huggingface_hub), and OpenCode. Then restart to pick it all up:

```bash
./stop.sh && ./start.sh
```

Preview what would change without touching anything:

```bash
./scripts/update.sh --check
```

Update a single component (flags compose):

```bash
./scripts/update.sh --llama       # just llama.cpp — the usual reason to update
./scripts/update.sh --images      # just Open WebUI + SearXNG images
./scripts/update.sh --python      # just mcpo / mcp / openai / huggingface_hub
./scripts/update.sh --opencode    # just OpenCode
```

## What each update actually does

| Component | Mechanism | Notes |
|---|---|---|
| **llama.cpp** | `git pull --ff-only` in `llama.cpp/`, then a rebuild of `llama-server` with the same backend bootstrap used — `GPU_BACKEND` from `openbeast.conf` (cuda / hip / sycl / cpu, auto-detected flags via `scripts/lib/hardware.sh`; see `docs/HARDWARE_PROFILES.md`) | Skips the rebuild when already at HEAD and built. A running server keeps the old binary until restarted. If the repo directory was ever moved/renamed, the stale CMake cache is detected and the build dir wiped automatically |
| **Open WebUI** | `docker compose pull` (`ghcr.io/open-webui/open-webui:main`) | Running containers are recreated on the new image; your data lives in the `open-webui-data` volume and survives. A stopped stack is left stopped |
| **SearXNG** | `docker compose pull` (`searxng/searxng:latest`) | Same recreate semantics. Our `searxng/settings.yml` override is bind-mounted, so local settings survive image updates |
| **MCPO / MCP SDK / openai** | `pip install --user -U -r agents/requirements.txt` | PEP-668 (Arch/newer Debian) handled automatically with `--break-system-packages` (touches `~/.local` only) |
| **huggingface_hub (`hf` CLI)** | same pip upgrade | |
| **OpenCode** | `opencode upgrade` | Falls back to telling you the reinstall one-liner if the self-upgrader fails |

## Not covered by the script (deliberately)

- **Tailscale** — a system package; update it with your distro's package
  manager (`sudo pacman -Syu tailscale`, `sudo apt upgrade tailscale`).
- **Model weights** — GGUF files are versionless snapshots, not something
  you "update." Re-download only when a model repo publishes improved
  quants: `hf download <repo> <file> --local-dir "$WEIGHTS_DIR"` (see
  "Model weights location" in the README).
- **NVIDIA driver / CUDA / Docker** — system-level; distro package manager
  territory, same reasoning as bootstrap: nothing should touch your GPU
  driver behind your back.

## After a llama.cpp update

llama.cpp occasionally changes server flags or default behaviors. If a serve
script fails after an update:

1. `./scripts/healthcheck.sh` for a quick triage.
2. Check `llama-server --help` for renamed flags against the flags in
   `scripts/serve-*.sh`.
3. Worst case, pin back: `git -C llama.cpp checkout <last-good-sha>` and
   re-run `./scripts/update.sh --llama` — the script detects the pinned
   (detached HEAD) checkout, skips the pull, and rebuilds exactly that SHA.
   `git -C llama.cpp checkout master` later to resume tracking upstream.

The eval suite is the deep verification: `python3 evals/run_eval.py` against
a known model should reproduce prior scores within noise.
