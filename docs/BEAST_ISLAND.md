# beast-island 🏝️ — the rig keeps thinking when the internet dies

> **Islanding** is the power-grid term for a microgrid that disconnects when
> the grid fails and keeps serving its local load from its own battery.
> beast-island does the same for the rig. When an outage takes the router
> down and the rig rides its UPS, the model, the tools, OpenCode and Open WebUI
> keep working. Only the internet is gone.

## Why this exists

Everything OpenBeast does is local except three tools: `web_search`, `fetch`
and, on the Open WebUI tool server, `weather` (Open-Meteo).
So an installed rig already *works* with no internet. What it did badly was
**waiting**:

| Without beast-island | With it |
|---|---|
| Each `web_search` / `fetch` / `weather` stalls for seconds per call: SearXNG engine timeouts, or DNS followed by a 30 s socket timeout. The model then retries | They answer at once: *"ISLAND MODE … do not retry"* |
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
the repo does not install any. (On spark-1, `beastup` is already taken by
the beast-spark shortcut, which starts the pair; see
[COMMANDS.md §3](COMMANDS.md#3-spark-1-start-stop-switch-changes-state).)

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
boots islanded. Waiting out the 90 s grace would mean Open WebUI stalling on
its downloads. This covers the case where power comes back to the rig before
the router. A failed launch probe is first re-tried every 2 s for up to 20 s
(`OPENBEAST_ISLAND_LAUNCH_WAIT`): a linger boot starts the user unit before
DHCP has a default route (a user manager has no `network-online.target`), and
on spark-1 the single probe lost that race by under a second, islanding an
ordinary reboot.

**Forced** (`--offline`, `ISLAND=force`) islands from launch and stays
islanded until the stack stops. This holds even if the launch decision could
not be written. The watcher still probes: when the internet becomes reachable
it logs *"the internet is reachable — staying islanded because island mode was
forced. Rejoin with: ./stop.sh && ./start.sh -d"*.

## What changes in island mode

| Piece | Behaviour |
|---|---|
| `web_search`, `fetch` (OpenCode MCP and the Open WebUI `:3001` tool server, one code path), and `weather` (`:3001` only) | Return the island refusal immediately; no network call is attempted. The `:3001` audit row records the call as `ok` (the refusal is a normal tool answer) |
| Open WebUI's built-in **Web Search** toggle | **Not guarded.** It calls SearXNG directly, not through the tools, so it returns empty results after SearXNG's engine timeouts |
| Every other tool, the model, agents, skills, files, beast-chat, beast-artifact | Unchanged; all local |
| `docker compose up` | `--pull never` when islanded **at launch** (images must already be local, which they are on an installed rig) |
| Open WebUI | `OFFLINE_MODE=true` when islanded at launch, and on any compose restart (`healthcheck.sh --restart`) while a live watcher says islanded. `OFFLINE_MODE` turns off its HF model auto-updates and version check; web search, the model connection and the tool server are untouched. Its mode is fixed when the container starts, so start.sh re-reads the watcher just before compose (a route that came back during the model wait — a boot race — starts WebUI online), and once a rig that launched islanded rejoins, the next `healthcheck.sh --restart` (the watchdog every 5 min, or the unmanaged supervisor) recreates WebUI online |
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
| — | `OPENBEAST_ISLAND_LAUNCH_WAIT` | `20` | Seconds a failed launch probe is re-tried before the run boots islanded (env only; `0` = decide on one probe). A real outage at launch costs this much extra boot time |

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
  mcp_server.fetch / web_search / weather ──► island.web_refusal()  (served by :3001 too)
  start.sh --status · healthcheck.sh · doctor.sh ──► island.py status
  conf.sh ──► island.py status --webui-offline  (for compose restarts)
```

- **One state file**, `${OPENBEAST_RUN_DIR:-<repo>/.run}/island.json`, mode
  0600, written atomically on every tick.
- **When a reader trusts it.** All three must hold:
  - the heartbeat is fresh (`stale_after`, capped at 10 minutes on read)
  - the pid it names is alive
  - that pid is an `island.py` process (its argv, from `/proc` on Linux and
    `sysctl` on macOS; an argv that cannot be read counts as *not*)
  
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

## On the Spark pair

The Spark pair (spark-1 and spark-2, [SPARK_HYDRA.md](SPARK_HYDRA.md)) is
the deployment most likely to run islanded for long, so here is what an
internet outage does there, piece by piece. **Verified** means it was
observed on the pair; the rest follows from the code and is labelled.

| Piece | What happens when the WAN goes away |
|---|---|
| The island watcher (spark-1, part of the `openbeast` command center; `ISLAND` unset in `openbeast.conf`, so `auto`) | Islands the stack after 90 s of failed probes; rejoins by itself. `healthcheck.sh` shows the `Network:` line; `python3 ~/openbeast/agents/island.py status` says why. On 2026-11-18 it read "online (watching: islands after 90s without a route)" |
| Inference (`deepseek-v41-inference` or `spark-hydra-inference`) | Unaffected: the backend listens on spark-1's loopback, rank 1 is reached over the ConnectX-7 fabric (10.100.216.x / 10.100.217.x), and the units start with `PREPARE=0`, so a restart loads from local disk. The weights, rank caches and images are all on disk. **A cold boot of both Sparks with the WAN down is unverified** for the current deployment |
| beast-gate (`:8443` on the tailnet, `:8090` on loopback) | The gate itself keeps running. Whether a client reaches `:8443` depends on Tailscale (next row) |
| Tailscale | Existing direct peer connections can survive; new paths need the coordination server. If the tailnet path fails but the LAN works, use the native ssh tunnel to the gate in the [Tailscale field guide](../skills/tailscale/references/guide.md) ([TAILSCALE_OFFLINE.md](TAILSCALE_OFFLINE.md)). `healthcheck.sh` excuses an offline Tailscale only while islanded |
| ssh between the Sparks | Unaffected: over the fabric, by IP (`max@10.100.216.2` from spark-1, `max@10.100.216.1` from spark-2) |
| ssh from the Mac | At home, by LAN IP: `ssh -o HostName=192.168.40.153 spark-1` (mDNS can fail after a reboot) |
| opencode on the Sparks | Unaffected: it talks to `127.0.0.1:8000` (spark-2 through its fabric tunnel `opencode-model-link`), with `autoupdate: false` ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md#opencode-on-the-sparks)) |
| `agent.sh` on spark-1 | Works, but its `web_search` / `fetch` are not island-guarded (Known limits below): disable them for the run ([SELF_DEVELOPMENT.md §2](SELF_DEVELOPMENT.md#2-what-works-with-the-network-off)) |
| Development | The repo, its docs, the tests and git all work locally. Branches move through the hub on spark-1 instead of GitHub ([SELF_DEVELOPMENT.md §11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable)) |
| Installing anything | pip, apt, docker pulls and Hub downloads fail. Use what was staged: wheelhouses ([PYTHON_OFFLINE.md](PYTHON_OFFLINE.md)), debs and images ([SPARK_HYDRA.md H](SPARK_HYDRA.md#h-fully-offline-operation)), an ark drive ([BEAST_ARK.md](BEAST_ARK.md)), opencode's kept tarball (`~/Documents/opencode/`) |

**Nothing needs to be done when the internet drops.** Do not restart the
stack with `--offline` just because the WAN is down: auto mode already
covers it, and a restart drops every generation in flight. `--offline`
(forced) is for a planned disconnection that should not flap.

**When power fails too.** The Sparks have no documented UPS. After power
returns, both boot, the enabled inference unit (`spark-hydra-inference`,
GLM, since 2026-11-18; lingering user manager) starts both ranks, and `openbeast` starts the command center. If the
router is still down, the launch probe fails and the stack boots islanded
(see "At launch, auto skips the grace" above). This sequence with the WAN
down has not been run end to end on the pair.

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
