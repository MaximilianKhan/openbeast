# Updating

## Updating OpenBeast itself

`scripts/update.sh` does not update OpenBeast. It has no `git pull` of this
repo. To move to a newer OpenBeast:

```bash
./stop.sh
git pull --ff-only
./scripts/pydeps.sh install   # Python deps at the lock this commit ships
./start.sh -d                 # compose pulls the image digests this commit pins
./start.sh doctor
```

llama.cpp is a separate clone under `llama.cpp/` and is not moved by the pull.
The commit OpenBeast builds is pinned in `scripts/llama.cpp.ref`, which the
pull *does* move: a fresh `./bootstrap.sh` fetches exactly that commit, while
an existing clone at another commit is built as it stands, with a warning
(`rm -rf llama.cpp && ./bootstrap.sh` for the pinned engine).
`./scripts/update.sh --llama` is the other direction — it moves the clone to
upstream `master`, see below.
`./bootstrap.sh --no-start` is idempotent and re-runs every setup step if you
would rather not pick. Clients update separately (below).

If `git pull` reports conflicts in `docker-compose.yml`,
`agents/requirements.txt`, `agents/requirements.lock`,
`scripts/client-searxng.compose.yml` or `scripts/llama.cpp.ref`, an earlier
`update.sh` run rewrote them.
`git status` shows which; `git checkout -- <file>` returns each to the shipped
pin before you pull.

## Updating the pulled-in components (`update.sh`)

`scripts/update.sh` moves the upstream pins forward: llama.cpp to upstream
`master`, the container images to their newest digests, the Python layer to
newer releases. It rewrites tracked files and asks nothing. That is a
maintainer action; on an installed rig it leaves the checkout ahead of what
OpenBeast was tested with.

OpenBeast orchestrates several upstream open source projects (full list and
credits: [`NOTICE`](../NOTICE) and the README credits section). Upstreams
move fast — llama.cpp alone lands performance work weekly — so keeping them
fresh is worth doing periodically.

## One command

```bash
./scripts/update.sh
```

That updates every upstream component: llama.cpp (git pull + CUDA rebuild), the Open WebUI
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
./scripts/update.sh --images      # just the container images: Open WebUI, SearXNG, extension fragments (re-pins digests)
./scripts/update.sh --python      # just mcp / openai / fastapi / uvicorn / PyJWT / huggingface_hub
                                  #   — and regenerates agents/requirements.lock to match
./scripts/update.sh --opencode    # just OpenCode
./scripts/update.sh --force       # rebuild llama.cpp even if HEAD has not moved
                                  #   (the only way to rebuild under OFFLINE=true, where there is no pull)
./scripts/update.sh --llama --ignore-lease   # update llama.cpp even while a job holds the GPU lease
```

Under `OFFLINE=true` (`openbeast.conf`) every step that needs the network is
skipped with a message saying what it would have done — no pull, no digest
bump, no index query — and `--check` reports what is on disk instead of
comparing against a remote.

## Upgrading past v1.6.0: beast-artifact and beast-chat

Nothing here needs a manual step unless you run more than one operator.
Restart the stack (`./stop.sh && ./start.sh -d`) and read on.

- **Rig-published pages change owner, once.** Pages owned by `local` (what a
  rig with no allowlist published) are re-owned to the new `rig` principal
  when the artifact server starts: idempotent, with an `index.jsonl` row per
  page. Pages the CLI published on a rig **with** `ARTIFACT_OPERATORS` set
  are owned by the first operator and stay that way; `artifact.sh publish
  <f> --id <id>` can still update them in place, and `artifact.sh chown <id>
  rig` hands one to the rig for good. Details:
  [BEAST_ARTIFACT.md § Upgrading](BEAST_ARTIFACT.md#upgrading-local-pages-and-operator-owned-pages).
- **The first operator becomes the artifact admin.** With `ARTIFACT_ADMINS`
  unset, the first `ARTIFACT_OPERATORS` login (else the first
  `CHAT_OPERATORS` login) can read, re-share, hand over and delete the other
  operators' private pages; in v1.6.0 those stayed owner-only. A rig with more
  than one operator says so at every start, on stderr and as an
  `admin-default` row in the artifact audit log, until `ARTIFACT_ADMINS` is
  set. On a single-operator rig nothing changes in practice. Details:
  [BEAST_ARTIFACT.md § Who administers](BEAST_ARTIFACT.md#who-administers).
- **No operator configured?** The rig's private pages open for nobody from a
  phone. Every rig publish now says so, and `doctor` warns. Set
  `ARTIFACT_OPERATORS=you@example.com` (or `CHAT_OPERATORS`).
- **Managing pages from a phone is new and opt-in**: enroll a device with
  `--scope artifact`. Existing keys have no such scope and gain nothing.
- **Console jobs run under `job.sh`'s supervisor** and no longer inherit the
  stack's secret environment (`HF_TOKEN`, `GH_TOKEN`, stack keys…). A preset
  that relied on one must read it from a file. A job killed by a signal
  nobody in beast-chat sent is now `failed` with its exit status, as under
  `job.sh run`.
- **`healthcheck.sh` can now exit non-zero with a `FOREIGN` row** when a
  process the stack did not start (a sibling worktree's server) answers on
  `:3003` or `:3004`. It kills nothing.
- **The daily logrotate run sweeps the session ledger** (terminal records
  older than 30 days) even with `BEAST_CHAT=false`, and rotates
  `.run/sessions/*.log`.
- **Notifications are new and off** until you set `CHAT_NOTIFY_URL`; the
  `ntfy` extension's image is carried by `bundle.sh` and bumped by
  `update.sh --images` like the core ones.

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
checkout of `agents/ scripts/ skills/ searxng/`), then re-install the
hash-pinned closure (`agents/requirements.lock`, via `pydeps.sh`) into the
client's venv. A client installed from a
full clone is told to pull that clone itself.

Worth doing after any rig-side change under `agents/` — the client runs its
*own* copy of `mcp_server.py` and `tools.py`. `openbeast-client status`
compares the client's understood contract version against the rig's
`beast_slot` / `min_client` and says when an update is actually required.

## What each update actually does

| Component | Mechanism | Notes |
|---|---|---|
| **llama.cpp** | `git pull --ff-only` in `llama.cpp/`, then a rebuild of `llama-server`; **after `llama-server` has built**, `scripts/llama.cpp.ref` (a tracked file) is rewritten to the commit just pulled, so the pin follows only an engine that compiled — smoke-test, then commit it. The rebuild uses the same backend bootstrap used — `GPU_BACKEND` from `openbeast.conf` (cuda / hip / sycl / cpu, auto-detected flags via `scripts/lib/hardware.sh`; see `docs/HARDWARE_PROFILES.md`) | Skips the rebuild when already at HEAD and built. **Refuses while another job holds the GPU lease** (`scripts/gpu-lease.sh status`): the binary is the one every campaign cell execs and the eval era does not hash the engine, so a mid-campaign rebuild would split paired cells across two builds — wait, or pass `--ignore-lease`. A running server keeps the old binary until restarted. If the repo directory was ever moved/renamed, the stale CMake cache is detected and the build dir wiped automatically |
| **Open WebUI** | Pull the moving `:main` tag, read its new digest, rewrite the `@sha256:` pin in `docker-compose.yml`, recreate. On a box installed from an offline bundle (`image: sha256:<content id>` lines), the service is found in `docker-compose.yml.pre-bundle` and re-pinned to the new registry digest; an image line it cannot pin is warned about, never reported as updated | Images are **digest-pinned** for supply-chain safety — a plain `compose pull` would just re-fetch the pin, so `--images` is the sanctioned bump. Commit the compose digest change after verifying. Your data lives in the `open-webui-data` volume and survives. A stopped stack is left stopped |
| **SearXNG** | Same digest-bump for `searxng/searxng:latest` — **in `docker-compose.yml` only** | Our `searxng/settings.yml` override is bind-mounted, so local settings survive image updates. See the manual second bump below |
| **Extension images** (`extensions/*/compose.yaml`, e.g. ntfy) | Each fragment's pinned `<repo>:<tag>` is re-pulled and its `@sha256:` rewritten on the `image:` line — the tag is a deliberate version, so a new version stays a reviewed edit to the fragment. Every fragment on disk is checked, enabled or not; an unpinned or bundle-content-ID line is warned about. Running containers are recreated with the ENABLED fragments merged, as `start.sh` composes them | Commit the fragment's digest change with the core's |
| **MCP SDK / openai / fastapi / uvicorn / PyJWT** | `pip install --user -U <packages>`, then the **gate**: `agents/mcp_server.py` and `agents/openapi_tools.py` must import cleanly against the new versions or the upgrade is rolled back and no pin is rewritten (a 2026-08-14 mcp 1→2 bump deleted the class the MCP server is built on and took the next boot down). On success the `==` pins in `agents/requirements.txt` are rewritten to what is now installed — major jumps are shouted — and **`agents/requirements.lock` is regenerated** (`pydeps.sh lock`) so the hash-pinned closure moves with the pins. If the lock cannot be regenerated (pypi.org unreachable) it is left intact and reported STALE: bootstrap will then say so and use `requirements.txt`, and CI's `pydeps.sh verify` fails — run `./scripts/pydeps.sh lock` before committing. Commit `requirements.txt` and `requirements.lock` **together** | PEP-668 (Arch/newer Debian) handled automatically with `--break-system-packages` (touches `~/.local` only). A running MCP/tool server keeps the old code until restarted |
| **huggingface_hub (`hf` CLI)** | same pip upgrade | Not in `requirements.txt` (the CLI moved between majors) but pinned in the lock as an explicit extra |
| **OpenCode** | `opencode upgrade` | Falls back to telling you the reinstall one-liner if the self-upgrader fails |

## The client SearXNG pin — bumped together with the rig's

`scripts/client-searxng.compose.yml` — the optional local SearXNG that
`setup-client.sh --local-search` installs on a client — carries the *same*
digest-pinned `searxng/searxng:latest` image as `docker-compose.yml`.
`--images` rewrites **both**: whenever it pins a new searxng digest in
`docker-compose.yml` (or finds the client file already drifted from it), it
writes the same `image@sha256:` into the client compose file, and its closing
reminder says to commit the two together. To check by hand:

```bash
grep -n 'searxng/searxng.*@sha256:' docker-compose.yml scripts/client-searxng.compose.yml
```

Clients never silently follow `:latest`, so a missed mirror is drift, not a
break — and it is not *silent* drift: `tests/test_supply_chain.sh` (run in CI)
fails while the two digests differ.

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
  install there. It is **two jobs**: `resolve` (read-only token — resolving a
  bumped sdist runs its build backend, i.e. third-party code) hands the lock
  over as a one-file artifact, and `push` (write token) starts on a fresh
  runner, checks the file, commits with hooks disabled, and gives the token
  only to the push command. A push made with `GITHUB_TOKEN` does not run workflows: the
  PR's CI runs are created in `action_required` and wait for a maintainer to
  approve them (measured 2026-09-17: `workflow_dispatch` runs do *not*
  satisfy the PR's required checks; approval does).
- **`./scripts/land-dependabot.sh [PR …]`** does the whole chain from a
  maintainer's shell, one PR at a time, each step waiting on GitHub. With no
  PR numbers it takes only the open Dependabot PRs that touch
  `agents/requirements.txt` — a github-actions or docker bump never triggers
  the relock, so it is skipped (and counted in the output) rather than waited
  on; name such a PR explicitly to land it. Checks that have not appeared
  are waited on for up to 10 minutes, then the run stops without merging.
  The chain:
  `@dependabot rebase` (main moved when the previous PR merged, and branch
  protection wants an up-to-date branch) → wait for the relock push → approve
  the held runs (only this repo's, for the PR's head commit — never a fork's run
  on a same-named branch) → wait for CI (re-running a *cancelled* run once — a late
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
  [INSTALL.md § Where weights live](INSTALL.md#where-weights-live)).
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
