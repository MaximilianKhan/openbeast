# OpenBeast client mode — thin client on the laptop, big rig does the thinking

**Status: SHIPPED (2026-07-17). Both deliverables landed:
`scripts/setup-mac-client.sh` (client, macOS/Linux — preflight, slim sparse
checkout or in-place clone, pinned venv, env file, non-clobbering
opencode.json merge, `--uninstall`) and
`scripts/setup-tailscale.sh --publish-searxng` / `--unpublish-searxng`
(rig, opt-in, tailnet-only :8889). Verified end-to-end on the reference box
(isolated $HOME/$XDG: install → venv MCP import → uninstall leaves foreign
config keys untouched). Structure tests: `tests/test_scripts.sh` §13.
Remaining: a live two-machine run from the actual laptop (the
three-localities check below).**

> **Not to be confused with TODO §5 "macOS/Metal + Docker Desktop."** That item
> is about running the *model server* on a Mac (Metal build, Darwin bootstrap
> branch). This is the opposite: the Mac runs **no model at all** — it's a thin
> client whose tools execute locally while inference and web search come from the
> rig over the tailnet. The two are independent and complementary.

## The feature (Max, 2026-07-13)

Let a laptop (the Mac) become a full OpenBeast **thick client**: the 15-tool
arsenal + the MCP server run **locally on the laptop** (so `bash`/`edit_file`
act on the laptop's own project files), while the **model** and **web search**
are served by the big rig (the 5090 box) over the tailnet. One install script
on the laptop; one opt-in flag on the rig. Nothing long-running to babysit on
the laptop — quit OpenCode and the client is gone.

This is the **client-side companion** to the already-shipped distributed-agents
split ([[DISTRIBUTED_AGENTS_PLAN]]): same spine — *local files, remote brains* —
but with the laptop as the "local" and no requirement that the laptop run any
inference of its own.

## Why this works (the load-bearing facts)

1. **Tools execute where the process runs, not where the model runs.**
   `agents/tools.py` uses local `subprocess`/`open()`. Run `mcp_server.py` on the
   laptop → its tools touch the laptop's filesystem. This is the correct locality
   for a coding agent (the workspace is on the laptop). Same fact
   [[DISTRIBUTED_AGENTS_PLAN]] leans on, applied one layer out.
2. **The MCP server needs no rig code and no GPU.** `agents/mcp_server.py` imports
   only `agents/tools.py` (+ `skills/` for the `skill` tool) and the pinned deps
   in `agents/requirements.txt` (`openai`, `mcp`, `fastapi`, `uvicorn`, `PyJWT`).
   No llama.cpp, no weights, no Docker. A slim checkout of `agents/` + `skills/`
   is the entire footprint.
3. **stdio transport = no daemon, no open port, auto-reaped.** OpenCode spawns
   the MCP server as a stdio subprocess (`"type": "local"`) and kills it on exit.
   In stdio mode the server binds **no socket** (the `0.0.0.0:3001` bind only
   applies to `--transport http`). So there is nothing to start/stop/expose on
   the laptop — "easily kill" is free.
4. **Every rig-side surface the client needs is one HTTP call.** Inference is
   already published (`setup-tailscale.sh` → `:8443/v1`). Web search is the only
   piece not yet reachable — see the gap below.

```
   LAPTOP (files here)                       RIG (beast.<tailnet>.ts.net)
   opencode + mcp_server.py ── HTTPS :8443 ─▶ llama-server (model)
   bash/read/write act on LAPTOP FS          (tokens only; never touches files)
   web_search ──────────── HTTPS :8889 ─────▶ SearXNG (private metasearch)
```

## What already works vs. what's missing (verified 2026-07-13)

- ✅ Inference over the tailnet: `https://beast.<tailnet>.ts.net:8443/v1`
  (`setup-tailscale.sh`, live). OpenCode consumes it via `opencode.json`
  `baseURL` — verified reachable (200 on `/health`).
- ✅ MCP server is transport-flexible and dependency-light (facts 2–3 above).
- ✅ `web_search` is already indirected through `SEARXNG_URL`
  (`agents/tools.py`, default `http://localhost:8888`) — so the client only needs
  that env var pointed at the rig; **no code change** in `tools.py`.
- ✅ `start_agent`/`start_skill_agent` already honor `OPENBEAST_AGENT_INFERENCE_URL`
  ([[DISTRIBUTED_AGENTS_PLAN]] Phase 1), so background agents spawned *from the
  laptop* execute on the laptop and borrow the rig's brain — for free.
- ❌ **The gap: SearXNG is loopback-only and deliberately unpublished.**
  `setup-tailscale.sh` publishes only WebUI (:443) and llama (:8443), calling
  SearXNG "internal plumbing, not human-facing." Correct default — but it means
  `web_search` from the laptop has nothing to call until we add an opt-in expose.
- ❌ **No client installer.** All setup scripts are rig-side. Nothing wires the
  laptop's `opencode.json` + env + slim checkout.

## Deliverable A — `scripts/setup-mac-client.sh` (runs on the laptop)

Idempotent; macOS-first (also fine on a Linux laptop). Flags: `--host <fqdn>`
(rig FQDN; auto-detect a tailnet peer named `beast` if omitted), `--no-search`
(skip `SEARXNG_URL` wiring), `--uninstall`.

**Steps**
1. **Preflight.** Require `python3` ≥3.10, `pip3`, `opencode`, and a live
   `tailscale status` that can see the rig. Resolve the rig FQDN (`--host` or
   auto-detect). Print the resolved `:8443` API and `:8889` search URLs and a
   read-only ✓/✗ report before touching anything (mirror
   `bootstrap.sh --preflight` ethos).
2. **Slim checkout** into `~/.openbeast-client/` — `agents/` + `skills/` only,
   preserving the sibling layout so `_REPO_SKILLS_DIR` (`agents/../skills`)
   resolves. If run from inside a full clone, use it in place instead.
3. **Isolated venv** at `~/.openbeast-client/venv`; `pip install -r
   agents/requirements.txt` (pinned — supply-chain anchored, same as the rig).
4. **Write `~/.openbeast-client.env`** — `AGENT_INFERENCE_URL=https://<rig>:8443/v1`,
   `SEARXNG_URL=https://<rig>:8889` (unless `--no-search`), `LLAMA_API_KEY`
   passthrough if the rig set one. Optional `OPENBEAST_MCP_TOOLS=` allowlist
   (default: all tools register).
5. **Merge `~/.config/opencode/opencode.json`** (never clobber — read-merge via a
   small Python/jq step): add/refresh the `llama-cpp` provider (`baseURL` :8443,
   dummy `apiKey`), the model list (copied from repo `opencode.json`), and the
   `mcp.local-tools` block pointing `command` at the venv python +
   `~/.openbeast-client/agents/mcp_server.py`, with `environment` carrying
   `SEARXNG_URL` + `AGENT_INFERENCE_URL`.
6. **Health check.** `curl` the rig `:8443/health`; if search wired, hit
   `:8889/search?q=test&format=json` and assert JSON; `python -c "import mcp,
   openai"` in the venv.
7. **Report + uninstall path.** Print how to launch (`opencode` → pick a
   `llama-cpp` model) and remind that nothing persists — quitting OpenCode reaps
   the stdio MCP subprocess. `--uninstall` removes the opencode block + the
   `~/.openbeast-client/` dir + the env file.

**macOS caveat:** stock macOS ships Bash 3.2. Keep the script POSIX-ish — no
`declare -A`, no `mapfile`, `#!/usr/bin/env bash`. (ShellCheck in PR-quality CI
will catch bashisms.)

## Deliverable B — `--publish-searxng` on `scripts/setup-tailscale.sh` (rig)

Opt-in, off by default, so the private-by-default posture is preserved.

- Adds one serve entry next to the existing two:
  `sudo tailscale serve --bg --https=8889 http://127.0.0.1:8888`
  (8889 chosen to sit beside 8443; no collision with 443/8443).
- Prints the `SEARXNG_URL=https://<fqdn>:8889` line to paste into the client (or
  that the client installer auto-detects).
- `--unpublish-searxng` → `sudo tailscale serve --https=8889 off`.
- **Security note to document at the flag:** SearXNG has no auth and its rate
  limiter is disabled (`searxng/settings.yml`, needed for the JSON API). Exposing
  it means *any tailnet device* can run searches through it. On a personal
  tailnet that's an acceptable boundary (same trust model as the llama endpoint);
  it is **not** public (never `tailscale funnel`). State this plainly, exactly as
  the remote-access docs state the WebUI/llama boundary.

## Verification

1. **Rig:** `setup-tailscale.sh --publish-searxng`; confirm `tailscale serve
   status` shows the `:8889 → :8888` line and `curl https://<fqdn>:8889/search?q=test&format=json`
   returns JSON.
2. **Laptop:** `setup-mac-client.sh --host beast.<tailnet>.ts.net`; then
   `opencode`, pick a `llama-cpp` model, and prove all three localities in one
   session:
   - ask it to **edit a file** in the current laptop project → change lands on
     **laptop** disk (tools local ✓);
   - ask it to **search the web** → `web_search` returns results, and the query
     shows up in the **rig's** SearXNG (search remote ✓);
   - `nvidia-smi` on the rig (over SSH) **spikes** while it generates (brain
     remote ✓).
3. **Cleanup:** quit OpenCode → confirm no `mcp_server.py` process survives on
   the laptop (stdio reaping ✓). `setup-mac-client.sh --uninstall` → opencode
   config clean.
4. **CI/tests:** add structure assertions for both scripts to
   `tests/test_scripts.sh` (flag parsing, idempotency guards, no bashisms).

## Prerequisites & caveats

- **Data flow (state it plainly, same as [[DISTRIBUTED_AGENTS_PLAN]]):** file
  contents the agent **reads** on the laptop are sent to the rig as model context
  — the model must see data to reason about it. On your own tailnet this is still
  your hardware, so the promise is *"nothing leaves your tailnet,"* not *"nothing
  leaves this machine."* Document it wherever client mode appears.
- **RBAC does not apply to the client path — by design.** RBAC lives in the
  `:3001` identity server (the WebUI surface). The laptop's local MCP server is
  single-user (it's your laptop); guest/admin profiles are meaningless there. If
  a shared laptop ever needs scoping, `OPENBEAST_MCP_TOOLS` can restrict which
  tools register (the same mechanism the guest WebUI instance uses).
- **Model label vs. loaded weights:** `llama-server` answers with whatever model
  the rig loaded, regardless of the `model` field OpenCode sends. Start the model
  you intend to use on the rig first.
- **Security:** the tailnet is the boundary for both new surfaces; reuse
  `LLAMA_API_KEY` if you want an extra check on the llama endpoint. Never
  `tailscale funnel` either service.

## Docs to update on ship

- New: this file, linked from README (Documentation list + the Remote-access
  "Distributed agents" neighborhood) and `docs/TODO.md`.
- `docs/TOOLS.md`: note `web_search` is `SEARXNG_URL`-indirected and works
  remotely in client mode.
- `openbeast.conf.example` / `docs/REFERENCE.md`: mention `SEARXNG_URL` and the
  `--publish-searxng` toggle.
- README "Remote access": a short "Use it as a coding client from your laptop"
  subsection + the 15-second video beat.
