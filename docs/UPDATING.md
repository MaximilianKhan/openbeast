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
                                  #   (read docs/LLAMACPP_WATCH.md first: upstream
                                  #   tripwires to re-check after every rebuild)
./scripts/update.sh --images      # just Open WebUI + SearXNG images (re-pins digests)
./scripts/update.sh --python      # just mcp / openai / fastapi / uvicorn / PyJWT / huggingface_hub
                                  #   — and regenerates agents/requirements.lock to match
./scripts/update.sh --opencode    # just OpenCode
./scripts/update.sh --force       # rebuild llama.cpp even if HEAD has not moved
                                  #   (the only way to rebuild under OFFLINE=true, where there is no pull)
```

Under `OFFLINE=true` (`openbeast.conf`) every step that needs the network is
skipped with a message saying what it would have done — no pull, no digest
bump, no index query — and `--check` reports what is on disk instead of
comparing against a remote.

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
| **MCP SDK / openai / fastapi / uvicorn / PyJWT** | `pip install --user -U <packages>`, then the **gate**: `agents/mcp_server.py` and `agents/openapi_tools.py` must import cleanly against the new versions or the upgrade is rolled back and no pin is rewritten (a 2026-08-14 mcp 1→2 bump deleted the class the MCP server is built on and took the next boot down). On success the `==` pins in `agents/requirements.txt` are rewritten to what is now installed — major jumps are shouted — and **`agents/requirements.lock` is regenerated** (`pydeps.sh lock`) so the hash-pinned closure moves with the pins. If the lock cannot be regenerated (pypi.org unreachable) it is left intact and reported STALE: bootstrap will then say so and use `requirements.txt`, and CI's `pydeps.sh verify` fails — run `./scripts/pydeps.sh lock` before committing. Commit `requirements.txt` and `requirements.lock` **together** | PEP-668 (Arch/newer Debian) handled automatically with `--break-system-packages` (touches `~/.local` only). A running MCP/tool server keeps the old code until restarted |
| **huggingface_hub (`hf` CLI)** | same pip upgrade | Not in `requirements.txt` (the CLI moved between majors) but pinned in the lock as an explicit extra |
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

## Dependabot bumps — the relock workflow and `land-dependabot.sh`

Dependabot bumps `agents/requirements.txt` and knows nothing about
`agents/requirements.lock`, the hash-pinned closure generated *from* it — so
on its own every `/agents` Dependabot PR fails CI's `pydeps.sh verify` ("the
lock is stale") and stays red. Two pieces close that:

- **`.github/workflows/dependabot-relock.yml`** runs on a pull request that
  touches `agents/requirements.txt` when the actor is `dependabot[bot]`,
  regenerates the lock on
  the branch and pushes it as a `deps: regenerate …` commit tagged
  `[dependabot skip]` (so Dependabot keeps rebasing the branch, and re-fires
  the relock when it does). The lock is resolved on CI's python (3.12) and
  **proven on 3.14**, the reference box's python, before it is pushed — a
  closure short one package on 3.14 would break bootstrap's hash-pinned
  install there. A push made with `GITHUB_TOKEN` does not run workflows: the
  PR's CI runs are created in `action_required` and wait for a maintainer to
  approve them (measured 2026-09-17: `workflow_dispatch` runs do *not*
  satisfy the PR's required checks; approval does).
- **`./scripts/land-dependabot.sh [PR …]`** does the whole chain from a
  maintainer's shell, one PR at a time, each step waiting on GitHub:
  `@dependabot rebase` (main moved when the previous PR merged, and branch
  protection wants an up-to-date branch) → wait for the relock push → approve
  the held runs → wait for CI (re-running a *cancelled* run once — a late
  force-push does that) → squash-merge. Sequential on purpose: every one of
  these PRs touches the same two files, so each merge invalidates the next
  PR's lock. It refuses to run twice at once (`flock`), needs `gh`
  authenticated as a maintainer, and touches nothing local — it does **not**
  pip-install the new versions; `./bootstrap.sh` or `update.sh --python`
  does that, on your schedule. Do not run that mid-campaign: `openai` is the
  eval client.

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
