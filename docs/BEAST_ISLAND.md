# beast-island 🏝️ — the rig keeps thinking when the internet dies

> **Islanding** is the power-grid term for a microgrid that disconnects when
> the grid fails and keeps serving its local load from its own battery.
> beast-island does the same for the rig. When an outage takes the router
> down and the rig rides its UPS, the model, the tools, OpenCode and Open WebUI
> keep working. Only the internet is gone.

## Why this exists

Everything OpenBeast does is local except two tools, `web_search` and `fetch`.
So an installed rig already *works* with no internet. What it did badly was
**waiting**:

| Without beast-island | With it |
|---|---|
| Each `web_search` / `fetch` stalls for seconds per call: SearXNG engine timeouts, or DNS followed by a 30 s socket timeout. The model then retries | They answer at once: *"ISLAND MODE … do not retry"* |
| Booting with no internet: Open WebUI auto-updates its embedding models from Hugging Face on every start and stalls | A launch with no route boots islanded: Open WebUI starts in `OFFLINE_MODE`, compose never pulls |
| `healthcheck.sh` counts an offline Tailscale as DOWN and, under `--restart` with passwordless sudo, restarts `tailscaled` on every run | With no internet it's reported as *expected in island mode*: not counted, not restarted |
| Nothing tells you what state you are in | `./start.sh --status`, `healthcheck.sh` and `doctor` all say it |

## Using it

```bash
./start.sh -d                      # ISLAND=auto (default): islands by itself, rejoins by itself
./start.sh -d --offline            # islanded from launch, stays islanded until the stack stops
./start.sh restart --offline       # restart keeps nothing from the last launch: say it again
python3 agents/island.py status    # what state are we in?
```

Shell aliases make these one word. Define them yourself if you want them;
the repo does not install any:

```bash
alias beastup-offline='~/Documents/openbeast/start.sh -d --offline'
alias beastisland='python3 ~/Documents/openbeast/agents/island.py status'
```

**Auto (the default).** A watcher process probes the internet every 15 s. It is
started and reaped by `start.sh`, but nothing restarts it if it dies; if that
happens its state goes stale and the rig reads as online. After `ISLAND_GRACE`
seconds (default 90) of failed probes, the rig enters island mode. Counting
from the moment the route dies, that is roughly 90 to 120 s. It leaves island
mode once two probes in a row succeed, about 15 to 35 s after the route is
back.

**A single good probe restarts the grace.** So a link that comes and goes
keeps the rig online, and web tools still stall on that link. Set
`ISLAND=force` by hand if your uplink is flapping.

**At launch, auto skips the grace.** If `./start.sh` finds no route, the stack
boots islanded right away. Waiting would mean Open WebUI stalling on its
downloads. This covers the case where power comes back to the rig before the
router.

**Forced** (`--offline`, `ISLAND=force`) islands from launch and stays
islanded until the stack stops. This holds even if the launch decision could
not be written. The watcher still probes: when the internet becomes reachable
it logs *"the internet is reachable — staying islanded because island mode was
forced. Rejoin with: ./stop.sh && ./start.sh -d"*.

## What changes in island mode

| Piece | Behaviour |
|---|---|
| `web_search`, `fetch` (OpenCode MCP and the Open WebUI `:3001` tool server, one code path) | Return the island refusal immediately; no network call is attempted. The `:3001` audit row records the call as `ok` (the refusal is a normal tool answer) |
| Open WebUI's built-in **Web Search** toggle | **Not guarded.** It calls SearXNG directly, not through the tools, so it returns empty results after SearXNG's engine timeouts |
| Every other tool, the model, agents, skills, files, beast-chat, beast-artifact | Unchanged; all local |
| `docker compose up` | `--pull never` when islanded **at launch** (images must already be local, which they are on an installed rig) |
| Open WebUI | `OFFLINE_MODE=true` when islanded at launch, and on any compose restart (`healthcheck.sh --restart`) while a live watcher says islanded. `OFFLINE_MODE` turns off its HF model auto-updates and version check; web search, the model connection and the tool server are untouched. Its mode is fixed when the container starts. A rig that launched islanded and later rejoins keeps WebUI offline until the next `./stop.sh && ./start.sh -d`; the watcher logs this |
| `healthcheck.sh` | One `ISLE Network:` line. An offline Tailscale is excused only when the last probe found no internet. A forced launch on a connected box gets the normal row |
| `./start.sh --status`, `doctor` | Report the state, why, and since when. They read the live watcher, so a one-off `--offline` on a box whose conf says `ISLAND=off` is reported correctly |

**What it is not.** Island mode does not touch tailnet publishing, binds or
auth. Loopback services stay on loopback, and a LAN that is still powered still
reaches what it reached before. Nothing about the model or its sampling changes.

## Configuration (`openbeast.conf`)

| Key | Env | Default | Meaning |
|---|---|---|---|
| `ISLAND` | `OPENBEAST_ISLAND` | `auto` | `auto` / `force` / `off`. `true`/`on`/`yes`/`1` mean `force`; `false`/`no`/`0` mean `off` (case-insensitive); anything else warns and means `auto` |
| `ISLAND_GRACE` | `OPENBEAST_ISLAND_GRACE` | `90` | Seconds of failed probes before auto mode islands (not used at launch) |
| `ISLAND_PROBES` | `OPENBEAST_ISLAND_PROBES` | `1.1.1.1:443 9.9.9.9:443 8.8.8.8:443` | TCP targets: IP literals with ports, IPv6 in brackets (`[2606:4700::1111]:443`), space-separated. Hostnames are rejected because a probe that needs DNS stalls exactly when it matters. Port 443 rather than 53 because a router that hijacks DNS would answer with the uplink dead. `doctor` warns when no entry is valid |
| — | `OPENBEAST_ISLAND_INTERVAL` | `15` | Seconds between probes (env only, for testing) |

**Proxy-only networks.** If this box reaches the internet only through a
proxy, direct TCP to those addresses fails and auto mode islands a working
network. Point `ISLAND_PROBES` at something reachable, or set `ISLAND=off`.

`OFFLINE=true` (the [air-gap](INSTALL.md) setting) is a different thing:
*"this box never has internet"*, which affects install and update. `ISLAND` is
*"the internet just went away"*, which affects runtime. They combine:
`OFFLINE=true` also starts Open WebUI in `OFFLINE_MODE`.

## How it works

```
start.sh (supervisor)
  ├─ kills a watcher a SIGKILLed earlier run left behind
  ├─ island.py init  --mode M        launch decision → island.json (exit 3 = islanded)
  │     islanded (or forced) → OFFLINE=true for this run (compose --pull never)
  │                            OPENBEAST_WEBUI_OFFLINE=true (→ OFFLINE_MODE)
  └─ island.py watch --mode M --parent=$$
        every 15s: probe, hysteresis, rewrite the file (heartbeat)
        exits when the supervisor is gone; removes its own file on exit

consumers read island.json
  mcp_server.fetch / web_search ──► island.web_refusal()  (served by :3001 too)
  start.sh --status · healthcheck.sh · doctor.sh ──► island.py status
  conf.sh ──► island.py status --webui-offline  (for compose restarts)
```

- **One state file**, `${OPENBEAST_RUN_DIR:-<repo>/.run}/island.json`, mode
  0600, written atomically on every tick.
- **When a reader trusts it.** All three must hold:
  - the heartbeat is fresh (`stale_after`, capped at 10 minutes on read)
  - the pid it names is alive
  - that pid is an `island.py` process
  
  So a dead watcher, a crashed stack or a hand-planted file all read as *not
  islanded*. A stale file must never make web tools refuse on a box whose
  network is fine.
- **Safe reads.** The file is opened non-blocking, no-follow and size-capped,
  so a FIFO or a `/dev/zero` symlink in its place can't hang the tool server.
  The reason shown to the model is one of the three strings the watcher writes,
  never free text from the file.
- **Probes** are parallel TCP connects, and the first success wins. With no
  default route a connect fails at once; with a router but no uplink it costs
  one 2 s timeout. The grace is measured on a monotonic clock, so an NTP step
  can't move it.
- **Stopping.** `stop.sh` and the supervisor's exit trap both stop the watcher
  and wait for it to exit before removing the file. A tick that was mid-probe
  can't write the file back, because the watcher stops writing once told to
  stop.
- **Worktrees and clients.**
  - OpenCode opened in a **git worktree** reads the main checkout's state when
    its own `.run` has none.
  - `agents/island.py` is stdlib-only because `mcp_server.py` imports it and
    ships to clients. A **client** has no watcher, so it is never islanded.

## Known limits

- **Autonomous sub-agents** (`start_agent`, the eval runner) call
  `agents/tools.py` directly. That file is part of the **eval era hash**
  (`evals/cache.py`), and changing it would split the era mid-campaign. So
  their web tools still fail by timeout rather than instantly:
  - `web_search` is bounded at 15 s.
  - `fetch` is bounded by its 45 s deadline after DNS resolution; the DNS
    lookup itself is bounded only by the resolver.

  The guard belongs in `tools.web_search` / `tools.fetch` and should land with
  the next deliberate era roll.
- **Open WebUI's built-in Web Search** is not guarded (see above).
- **GPU power on battery.** beast-island does not cap GPU power. On a UPS,
  `sudo nvidia-smi -pl <watts>` buys runtime at some loss of speed. It needs
  root, so it stays a manual step.
- **beast-hydra** routes to other machines over the network. beast-island
  doesn't change that; in an outage those nodes are likely dark too.

## Files

`agents/island.py` (the module, CLI and watcher) · `agents/mcp_server.py` (the
tool guard) · `start.sh` (`--offline`, launch decision, watcher, banners) ·
`stop.sh` · `scripts/lib/conf.sh` (keys, `OPENBEAST_WEBUI_OFFLINE`) ·
`docker-compose.yml` (`OFFLINE_MODE`) · `scripts/healthcheck.sh` ·
`scripts/doctor.sh` · `tests/test_island.py`.
