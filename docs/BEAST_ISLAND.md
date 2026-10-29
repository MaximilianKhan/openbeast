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
| Each `web_search` / `fetch` stalls on connect timeouts, and the model retries, burning turns and minutes | They answer at once: *"ISLAND MODE … do not retry. Everything local still works"* |
| Booting with no internet: Open WebUI auto-updates its embedding models from Hugging Face on every start and stalls | A launch with no route boots islanded: Open WebUI starts in `OFFLINE_MODE`, compose never pulls |
| The watchdog marks Tailscale DOWN and tries to restart `tailscaled` every 5 minutes | An offline tailnet is reported as *expected in island mode*, and nothing restarts |
| Nothing tells you what state you are in | `beastisland`, `./start.sh --status`, `healthcheck.sh` and `doctor` all say it |

## Using it

```bash
beastup              # ISLAND=auto (default): islands by itself, rejoins by itself
beastup-offline      # = ./start.sh -d --offline: islanded from launch, stays islanded
beastisland          # = python3 agents/island.py status
```

**Auto (the default).** A watcher process, supervised like the rest of the
stack, probes the internet every 15 s. After `ISLAND_GRACE` seconds (default
90) with no route, the rig enters island mode. It leaves island mode once two
probes in a row succeed. A short blip resets the grace, so it never flaps.

**At launch, auto skips the grace.** If `beastup` finds no route, the stack
boots islanded right away. Waiting would mean Open WebUI stalling on its
downloads. This covers the case where power comes back to the rig before the
router.

**Forced** (`--offline`, `ISLAND=force`) islands from launch and stays
islanded until the stack stops. The watcher still runs: when the internet comes
back it logs *"reachable again — staying islanded because island mode was
forced. Rejoin with: beastdown && beastup"*.

## What changes in island mode

| Piece | Behaviour |
|---|---|
| `web_search`, `fetch` (OpenCode MCP and the Open WebUI `:3001` tool server, one code path) | Return the island refusal immediately; no network call is attempted |
| Every other tool, the model, agents, skills, files, beast-chat, beast-artifact | Unchanged; all local |
| `docker compose up` | `--pull never` when islanded **at launch** (images must already be local, which they are on an installed rig) |
| Open WebUI | `OFFLINE_MODE=true` when islanded **at launch**. Its mode is fixed when the container starts, so a rig that islands mid-run keeps the WebUI it has (its models are cached by then). A rig that launched islanded and rejoins keeps WebUI offline until the next `beastdown && beastup`; the watcher logs this |
| `healthcheck.sh` (and the watchdog) | One `ISLE Network:` line; an offline Tailscale row is not counted as unhealthy and does not trigger a restart |
| `start.sh --status`, `doctor` | Report the state, why, and since when |

**What it is not.** Island mode does not touch tailnet publishing, binds or
auth. Loopback services stay on loopback, and a LAN that is still powered still
reaches what it reached before. Nothing about the model or its sampling changes.

## Configuration (`openbeast.conf`)

| Key | Env | Default | Meaning |
|---|---|---|---|
| `ISLAND` | `OPENBEAST_ISLAND` | `auto` | `auto` / `force` / `off`. `true`/`on`/`yes` mean `force`; `false`/`no`/`0` mean `off`; anything else warns and means `auto` |
| `ISLAND_GRACE` | `OPENBEAST_ISLAND_GRACE` | `90` | Seconds without a route before auto mode islands (not used at launch) |
| `ISLAND_PROBES` | `OPENBEAST_ISLAND_PROBES` | `1.1.1.1:443 9.9.9.9:443 8.8.8.8:53` | TCP targets, `IP:port`, space-separated. IP literals on purpose: a probe that needs DNS stalls exactly when it matters. `doctor` warns when none parse |

`OFFLINE=true` (the [air-gap](INSTALL.md) setting) is a different thing:
*"this box never has internet"*, which affects install and update. `ISLAND` is
*"the internet just went away"*, which affects runtime. They combine fine.

## How it works

```
start.sh (supervisor)
  ├─ island.py init  --mode M    launch decision → .run/island.json (exit 3 = islanded)
  │     islanded → OFFLINE=true for this run (compose --pull never)
  │                OPENBEAST_WEBUI_OFFLINE=true (docker-compose.yml → OFFLINE_MODE)
  └─ island.py watch --mode M    every 15s: probe, hysteresis, rewrite the file (heartbeat)

consumers read .run/island.json
  mcp_server.fetch / web_search ──► island.web_refusal()  (served by :3001 too)
  start.sh --status · healthcheck.sh · doctor.sh ──► island.py status
  conf.sh ──► launched_islanded ⇒ OPENBEAST_WEBUI_OFFLINE for healthcheck's compose restarts
```

- **One state file**, written atomically on every tick. A consumer trusts it
  only while the heartbeat is fresh (`stale_after`, which the watcher writes
  into the file). A dead watcher or a crashed stack therefore reads as *not
  islanded*. A stale file must never make web tools refuse on a box whose
  network is fine.
- **Probes** are parallel TCP connects; any success counts as online. With no
  default route a connect fails at once. With a router but no uplink it costs
  one 2 s timeout.
- `stop.sh` and the supervisor's exit trap both remove the watcher and the file.
- `agents/island.py` is stdlib-only because `mcp_server.py` imports it and
  ships to clients. A client has no watcher and no state file, so it is never
  islanded.

## Known limits

- **Autonomous sub-agents** (`start_agent`, the eval runner) call
  `agents/tools.py` directly, and that file is part of the **eval era hash**
  (`evals/cache.py`). Changing it would split the era mid-campaign, so their
  web tools still fail by timeout rather than instantly. They fail, they don't
  hang: SearXNG and the fetch path both have bounded timeouts. The one-line
  guard belongs in `tools.web_search` / `tools.fetch` and should land with the
  next deliberate era roll.
- **GPU power on battery.** beast-island does not cap GPU power. On a UPS,
  `sudo nvidia-smi -pl <watts>` buys runtime at some loss of speed. It needs
  root, so it stays a manual step.
- **beast-hydra** routes to other machines (DGX Sparks) over the network. In an
  outage those nodes are likely dark too, and hydra's own health checks handle
  that.

## Files

`agents/island.py` (the module, CLI and watcher) · `agents/mcp_server.py` (the
tool guard) · `start.sh` (`--offline`, launch decision, supervision, banners) ·
`stop.sh` · `scripts/lib/conf.sh` (keys) · `docker-compose.yml`
(`OFFLINE_MODE`) · `scripts/healthcheck.sh` · `scripts/doctor.sh` ·
`tests/test_island.py`.
