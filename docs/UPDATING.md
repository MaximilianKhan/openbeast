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
and SearXNG container images, the Python layer (MCP SDK, openai, fastapi,
uvicorn, huggingface_hub), and OpenCode. Then restart to pick it all up:

```bash
./stop.sh && ./start.sh
```

The restart covers every process the stack owns a pidfile for in `.run/` —
llama-server, the identity tool server, the agent router, and **beast-gate**
(`.run/edge.pid`), plus the compose containers.

**Toggling `EDGE_GATE` needs one more step.** A restart starts or stops
`agents/edge.py`, but it does **not** move the tailnet mapping: `tailscale
serve --https=8443` still points wherever it was last pointed. Re-run
`./scripts/setup-tailscale.sh` after flipping the key, or remote clients keep
hitting whatever `:8443` mapped to before (raw llama-server after enabling the
gate; a dead port after disabling it). `./scripts/doctor.sh` flags exactly
this mismatch — heed it.

Preview what would change without touching anything:

```bash
./scripts/update.sh --check
```

Update a single component (flags compose):

```bash
./scripts/update.sh --llama       # just llama.cpp — the usual reason to update
./scripts/update.sh --images      # just Open WebUI + SearXNG images (re-pins digests)
./scripts/update.sh --python      # just mcp / openai / fastapi / uvicorn / huggingface_hub
./scripts/update.sh --opencode    # just OpenCode
```

## Clients update themselves

`update.sh` updates **the rig only**. A beast-slot client
([`BEAST_SLOT.md`](BEAST_SLOT.md)) is a separate install — its own sparse
checkout plus an isolated venv under `~/.openbeast-client` — and nothing on the
rig reaches into it. Updating the rig does not update any client.

Run this **on each client**:

```bash
openbeast-client update      # = scripts/client.sh update
```

Two steps: `git pull --ff-only` in `~/.openbeast-client/repo` (the slim
checkout of `agents/ scripts/ skills/ searxng/`), then re-install the pinned
`agents/requirements.txt` into the client's venv. A client installed from a
full clone is told to pull that clone itself.

Worth doing after any rig-side change under `agents/` — the client runs its
*own* copy of `mcp_server.py` and `tools.py`. `openbeast-client status`
compares the client's understood contract version against the rig's
`beast_slot` / `min_client` and says when an update is actually required.

## What each update actually does

| Component | Mechanism | Notes |
|---|---|---|
| **llama.cpp** | `git pull --ff-only` in `llama.cpp/`, then a rebuild of `llama-server` with the same backend bootstrap used — `GPU_BACKEND` from `openbeast.conf` (cuda / hip / sycl / cpu, auto-detected flags via `scripts/lib/hardware.sh`; see `docs/HARDWARE_PROFILES.md`) | Skips the rebuild when already at HEAD and built. A running server keeps the old binary until restarted. If the repo directory was ever moved/renamed, the stale CMake cache is detected and the build dir wiped automatically |
| **Open WebUI** | Pull the moving `:main` tag, read its new digest, rewrite the `@sha256:` pin in `docker-compose.yml`, recreate | Images are **digest-pinned** for supply-chain safety — a plain `compose pull` would just re-fetch the pin, so `--images` is the sanctioned bump. Commit the compose digest change after verifying. Your data lives in the `open-webui-data` volume and survives. A stopped stack is left stopped |
| **SearXNG** | Same digest-bump for `searxng/searxng:latest` — **in `docker-compose.yml` only** | Our `searxng/settings.yml` override is bind-mounted, so local settings survive image updates. See the manual second bump below |
| **MCP SDK / openai / fastapi / uvicorn** | `pip install --user -U -r agents/requirements.txt` | PEP-668 (Arch/newer Debian) handled automatically with `--break-system-packages` (touches `~/.local` only) |
| **huggingface_hub (`hf` CLI)** | same pip upgrade | |
| **OpenCode** | `opencode upgrade` | Falls back to telling you the reinstall one-liner if the self-upgrader fails |

## The client SearXNG pin — a manual second bump

`--images` rewrites the `@sha256:` pin in **`docker-compose.yml` and nothing
else**. But `scripts/client-searxng.compose.yml` — the optional local SearXNG
that `setup-client.sh --local-search` installs on a client — carries the *same*
digest-pinned `searxng/searxng:latest` image and a comment saying to "bump both
together". Nothing automates that second bump. After running `--images`, mirror
the new digest by hand:

```bash
grep -n 'searxng/searxng.*@sha256:' docker-compose.yml scripts/client-searxng.compose.yml
# copy the fresh digest from docker-compose.yml into the client compose file,
# then commit both in the same change
```

Left un-mirrored, clients simply keep running the older pinned image — they
never silently follow `:latest`, so this is drift, not a break.

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
